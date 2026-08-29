"""The Harness HTTP surface: auth, owner scope, idempotency, gates, SSE.

The API is a projection over the kernel; the kernel tests own the semantics.
Here we pin the wire contract: every route authenticated (the app-level route
census covers this too), foreign IDs indistinguishable from missing ones,
operator endpoints admin-only, and the run lifecycle reachable over HTTP.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pharos.api import harness as harness_api
from pharos.api.deps import current_user, get_settings, require_admin
from pharos.db.models import User
from pharos.db.session import session_scope
from pharos.harness.repository import Scope
from pharos.harness.tables import runs
from pharos.harness.workflows.canary import canary_input
from tests.harness.conftest import enable_canary

OWNER_ID = "owner" + "0" * 26 + "1"
STRANGER_ID = "stranger" + "0" * 23 + "1"


@pytest.fixture
def client(app):
    enable_canary(app, agent_steps=True)
    api_app = FastAPI()
    api_app.include_router(harness_api.router)
    api_app.dependency_overrides[current_user] = lambda: User(
        id=OWNER_ID, email="x", password_hash="x"
    )
    api_app.dependency_overrides[require_admin] = lambda: User(
        id="admin00000000000000000000000000001",
        email="x",
        password_hash="x",
        is_admin=True,
    )
    api_app.dependency_overrides[get_settings] = lambda: None
    api_app.state.harness = app
    with TestClient(api_app) as test_client:
        yield test_client, app


def _drive_to(app, run_id: str, state: str, max_cycles: int = 300) -> None:
    for _ in range(max_cycles):
        app.cycle()
        if app.get_run(scope=Scope.user(OWNER_ID), run_id=run_id)["state"] == state:
            return
    raise AssertionError(f"run never reached {state}")


def test_every_harness_route_requires_auth(app) -> None:
    app_anon = FastAPI()
    app_anon.include_router(harness_api.router)
    app_anon.state.harness = app
    anon = TestClient(app_anon)
    assert anon.post("/api/harness/runs", json={}).status_code in (401, 403)
    assert anon.get("/api/harness/runs").status_code in (401, 403)
    assert anon.get("/api/harness/workflows").status_code in (401, 403)


def test_workflow_capability_listing(client) -> None:
    http, _ = client
    response = http.get("/api/harness/workflows")
    assert response.status_code == 200
    keys = {item["workflowKey"] for item in response.json()}
    assert "harness.canary" in keys


def test_create_and_run_canary_over_http(client) -> None:
    http, app = client
    response = http.post(
        "/api/harness/runs",
        json={
            "workflowKey": "harness.canary",
            "input": canary_input("success"),
            "idempotencyKey": "http-run-1",
        },
    )
    assert response.status_code == 202
    run_id = response.json()["id"]
    _drive_to(app, run_id, "succeeded")
    detail = http.get(f"/api/harness/runs/{run_id}")
    assert detail.json()["state"] == "succeeded"
    assert detail.json()["steps"], "the detail view carries its steps"
    events = http.get(f"/api/harness/runs/{run_id}/events").json()["events"]
    assert events, "the run produced durable events"


def test_idempotent_create_returns_the_same_run(client) -> None:
    http, _ = client
    first = http.post(
        "/api/harness/runs",
        json={
            "workflowKey": "harness.canary",
            "input": canary_input("success"),
            "idempotencyKey": "http-idem-1",
        },
    )
    second = http.post(
        "/api/harness/runs",
        json={
            "workflowKey": "harness.canary",
            "input": canary_input("success"),
            "idempotencyKey": "http-idem-1",
        },
    )
    assert first.json()["id"] == second.json()["id"]


def test_foreign_run_is_404(client) -> None:
    http, app = client
    foreign = app.create_run(
        scope=Scope.user(STRANGER_ID),
        workflow_key="harness.canary",
        input=canary_input("success"),
        idempotency_key="stranger-run",
        initiator="user",
    )
    assert http.get(f"/api/harness/runs/{foreign['id']}").status_code == 404
    assert http.get("/api/harness/runs/does-not-exist").status_code == 404
    # And the caller's own list never contains the stranger's run.
    listed = http.get("/api/harness/runs").json()["runs"]
    assert all(run["id"] != foreign["id"] for run in listed)


def test_cancel_and_events_surface(client) -> None:
    http, app = client
    run = http.post(
        "/api/harness/runs",
        json={
            "workflowKey": "harness.canary",
            "input": canary_input("approval"),
            "idempotencyKey": "http-approval-1",
        },
    ).json()
    _drive_to(app, run["id"], "waiting_for_approval")
    approvals = http.get(f"/api/harness/runs/{run['id']}/approvals").json()
    assert len(approvals) == 1
    decision = http.post(
        f"/api/harness/approvals/{approvals[0]['id']}/decision",
        json={"decision": "rejected", "reason": "not now"},
    )
    assert decision.status_code == 200
    _drive_to(app, run["id"], "succeeded")
    gate = [
        step
        for step in http.get(f"/api/harness/runs/{run['id']}").json()["steps"]
        if step["definitionStepKey"] == "approval_gate"
    ][0]
    assert gate["state"] == "skipped"


def test_resume_clears_pause_request_and_releases_a_claim(client) -> None:
    """The HTTP route must use HarnessApp.resume's complete lifecycle path."""
    http, app = client
    run = http.post(
        "/api/harness/runs",
        json={
            "workflowKey": "harness.canary",
            "input": canary_input("success"),
            "idempotencyKey": "http-resume-1",
        },
    ).json()
    paused = http.post(f"/api/harness/runs/{run['id']}/pause")
    assert paused.status_code == 200
    _drive_to(app, run["id"], "paused")

    resumed = http.post(f"/api/harness/runs/{run['id']}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["state"] == "queued"
    with session_scope() as session:
        row = session.execute(runs.select().where(runs.c.id == run["id"])).mappings().one()
        assert row["pause_requested_at"] is None
        claimed = app.dispatcher.claim_due(session, now_us=app.clock.utc_epoch_us())
    assert claimed is not None and claimed.run_id == run["id"]


def test_operator_status_and_validation_are_admin_only(client) -> None:
    http, app = client
    app_anon = FastAPI()
    app_anon.include_router(harness_api.router)
    app_anon.state.harness = app
    anon = TestClient(app_anon)
    assert anon.get("/api/harness/operator/status").status_code in (401, 403)
    status = http.get("/api/harness/operator/status")
    assert status.status_code == 200
    assert status.json()["harness_enabled"] is True
    validated = http.post(
        "/api/harness/operator/config/validate",
        json={
            "snapshot": {
                "gates": {"harness_enabled": True, "experiments_enabled": True},
                "routes": [],
            }
        },
    )
    assert validated.status_code == 200
    assert validated.json()["valid"] is False
    assert any("Decision 9" in error for error in validated.json()["errors"])


async def test_sse_stream_replays_events(client) -> None:
    http, app = client
    run = http.post(
        "/api/harness/runs",
        json={
            "workflowKey": "harness.canary",
            "input": canary_input("success"),
            "idempotencyKey": "http-sse-1",
        },
    ).json()
    _drive_to(app, run["id"], "succeeded")

    # Drive the stream generator directly: TestClient cannot exercise an
    # infinite SSE tail (closing the response never reaches the generator
    # under httpx), and the contract under test is the replay itself.
    from pharos.api.harness import _event_stream

    class StubRequest:
        def __init__(self) -> None:
            self.disconnected = False

        async def is_disconnected(self) -> bool:
            return self.disconnected

    request = StubRequest()
    gen = _event_stream(app, Scope.user(OWNER_ID), run["id"], 0, request)
    lines = []
    for _ in range(3):
        try:
            lines.append(await gen.__anext__())
        except StopAsyncIteration:
            break
    await gen.aclose()
    assert lines and all(
        line.startswith("data: ") for line in lines
    ), "the SSE stream serves event lines"
