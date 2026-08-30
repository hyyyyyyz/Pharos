"""Database fences for deterministic and mapped capability state changes."""

from __future__ import annotations

import pytest
from pharos.db.session import session_scope
from pharos.harness.contracts import AttemptState, RunState, StateError, StepState
from pharos.harness.repository import Scope
from pharos.harness.tables import attempts, config_head, events, runs, steps
from sqlalchemy import select


def _seed(app, *, kind: str = "deterministic", run_state: str = "running") -> dict[str, str]:
    with session_scope() as session:
        revision = session.execute(config_head.select()).mappings().one()["current_revision_id"]
        session.execute(
            runs.insert().values(
                id="cap-run",
                scope_type="user",
                scope_id="owner",
                user_id=None,
                workflow_key="capability-test",
                workflow_version=1,
                definition_sha256="definition",
                config_revision_id=revision,
                state=run_state,
                input_json="{}",
                input_sha256="input",
                budget_json=None,
                initiator="user",
                idempotency_key="capability-test",
                created_at=1,
                updated_at=1,
            )
        )
        session.execute(
            steps.insert().values(
                id="cap-step",
                run_id="cap-run",
                scope_type="user",
                scope_id="owner",
                definition_step_key="capability",
                instance_key="__singleton__",
                step_kind=kind,
                definition_json="{}",
                state="running",
                depends_on_json="[]",
                input_artifact_ids_json="[]",
                attempt_count=1,
                max_attempts=3,
                lease_owner="worker-a",
                lease_expires_at=10000,
                heartbeat_at=1,
                created_at=1,
                updated_at=1,
            )
        )
        session.execute(
            attempts.insert().values(
                id="cap-attempt",
                step_id="cap-step",
                run_id="cap-run",
                scope_type="user",
                scope_id="owner",
                attempt_no=1,
                worker_id="worker-a",
                state="running",
                role_or_capability="test.capability@1",
                lease_owner="worker-a",
                started_at=1,
                heartbeat_at=1,
            )
        )
    return {"run_id": "cap-run", "step_id": "cap-step", "attempt_id": "cap-attempt"}


def _call(service, session, ids, **kwargs):  # noqa: ANN001
    return service.finish_capability_cas(
        session,
        scope=kwargs.pop("scope", Scope.user("owner")),
        run_id=kwargs.pop("run_id", ids["run_id"]),
        step_id=kwargs.pop("step_id", ids["step_id"]),
        attempt_id=kwargs.pop("attempt_id", ids["attempt_id"]),
        attempt_no=kwargs.pop("attempt_no", 1),
        lease_owner=kwargs.pop("lease_owner", "worker-a"),
        target=kwargs.pop("target", AttemptState.succeeded),
        now_us=kwargs.pop("now_us", 2),
        **kwargs,
    )


def _rows(session):
    return (
        session.execute(select(runs).where(runs.c.id == "cap-run")).mappings().one(),
        session.execute(select(steps).where(steps.c.id == "cap-step")).mappings().one(),
        session.execute(select(attempts).where(attempts.c.id == "cap-attempt")).mappings().one(),
    )


@pytest.mark.parametrize(
    ("target", "step_target"),
    [(AttemptState.succeeded, StepState.succeeded), (AttemptState.failed, StepState.failed)],
)
def test_finish_capability_records_both_states_and_events(app, target, step_target) -> None:
    ids = _seed(app)
    service = app.state
    with session_scope() as session:
        assert _call(
            service,
            session,
            ids,
            target=target,
            attempt_values={"external_outcome": target.value},
            step_values=(
                {"error_code": "capability_failure"} if target is AttemptState.failed else {}
            ),
            payload={"source": "test"},
        )
        run, step, attempt = _rows(session)
        assert run["state"] == RunState.running.value
        assert step["state"] == step_target.value
        assert step["lease_owner"] is None
        assert attempt["state"] == target.value
        assert attempt["lease_owner"] is None
        assert session.execute(select(events).where(events.c.run_id == ids["run_id"])).all()
        assert session.execute(
            select(events.c.event_type).where(events.c.run_id == ids["run_id"])
        ).scalars().all() == [f"attempt.{target.value}", f"step.{step_target.value}"]


def test_finish_after_cancel_request_records_real_capability_result(app) -> None:
    ids = _seed(app)
    with session_scope() as session:
        session.execute(
            runs.update().where(runs.c.id == ids["run_id"]).values(cancel_requested_at=2)
        )
        assert _call(app.state, session, ids, target=AttemptState.succeeded, now_us=3)
        _, step, attempt = _rows(session)
        assert step["state"] == StepState.succeeded.value
        assert attempt["state"] == AttemptState.succeeded.value


def test_cancel_before_retry_is_rejected_without_writes_or_events(app) -> None:
    ids = _seed(app)
    with session_scope() as session:
        session.execute(
            runs.update().where(runs.c.id == ids["run_id"]).values(cancel_requested_at=2)
        )
        assert not app.state.schedule_capability_retry_cas(
            session,
            scope=Scope.user("owner"),
            run_id=ids["run_id"],
            step_id=ids["step_id"],
            attempt_id=ids["attempt_id"],
            attempt_no=1,
            lease_owner="worker-a",
            ready_at=20,
            now_us=3,
        )
        _, step, attempt = _rows(session)
        assert step["state"] == StepState.running.value
        assert attempt["state"] == AttemptState.running.value
        assert session.execute(select(events)).all() == []


def test_pause_request_still_allows_retry_safe_boundary(app) -> None:
    ids = _seed(app)
    with session_scope() as session:
        session.execute(
            runs.update().where(runs.c.id == ids["run_id"]).values(pause_requested_at=2)
        )
        assert app.state.schedule_capability_retry_cas(
            session,
            scope=Scope.user("owner"),
            run_id=ids["run_id"],
            step_id=ids["step_id"],
            attempt_id=ids["attempt_id"],
            attempt_no=1,
            lease_owner="worker-a",
            ready_at=20,
            now_us=3,
        )
        _, step, attempt = _rows(session)
        assert step["state"] == StepState.retry_scheduled.value
        assert step["ready_at"] == 20
        assert step["lease_owner"] is None
        assert attempt["state"] == AttemptState.failed.value
        assert attempt["retryable"] == 1
        assert attempt["lease_owner"] is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"attempt_no": 2},
        {"lease_owner": "worker-b"},
        {"scope": Scope.user("stranger")},
    ],
)
def test_stale_generation_owner_or_scope_has_no_effect(app, kwargs) -> None:
    ids = _seed(app)
    with session_scope() as session:
        assert not _call(app.state, session, ids, **kwargs)
        _, step, attempt = _rows(session)
        assert step["state"] == StepState.running.value
        assert attempt["state"] == AttemptState.running.value
        assert session.execute(select(events)).all() == []


def test_terminal_run_has_no_effect_or_event(app) -> None:
    ids = _seed(app, run_state=RunState.succeeded.value)
    with session_scope() as session:
        assert not _call(app.state, session, ids)
        _, step, attempt = _rows(session)
        assert step["state"] == StepState.running.value
        assert attempt["state"] == AttemptState.running.value
        assert session.execute(select(events)).all() == []


def test_repeated_finish_is_idempotent_no_op(app) -> None:
    ids = _seed(app)
    with session_scope() as session:
        assert _call(app.state, session, ids)
        assert not _call(app.state, session, ids)
        assert session.execute(select(events.c.event_type)).scalars().all() == [
            "attempt.succeeded",
            "step.succeeded",
        ]


def test_reserved_finish_values_fail_closed(app) -> None:
    ids = _seed(app)
    with session_scope() as session:
        with pytest.raises(StateError, match="reserved"):
            _call(app.state, session, ids, attempt_values={"state": "succeeded", "id": "evil"})
        _, step, attempt = _rows(session)
        assert step["state"] == StepState.running.value
        assert attempt["state"] == AttemptState.running.value


@pytest.mark.parametrize(
    "step_values",
    [
        {"definition_step_key": "forged"},
        {"instance_key": "forged"},
        {"step_kind": "agent"},
        {"output_artifact_id": "unpublished"},
    ],
)
def test_capability_finish_cannot_rewrite_step_identity_or_publish(app, step_values) -> None:
    ids = _seed(app)
    with session_scope() as session:
        with pytest.raises(StateError, match="reserved"):
            _call(app.state, session, ids, step_values=step_values)
        _, step, attempt = _rows(session)
        assert step["state"] == StepState.running.value
        assert attempt["state"] == AttemptState.running.value


def test_invalid_capability_target_fails_closed(app) -> None:
    ids = _seed(app)
    with session_scope() as session:
        with pytest.raises(StateError, match="succeeded or failed"):
            _call(app.state, session, ids, target=AttemptState.cancelled)
        with pytest.raises(StateError, match="succeeded or failed"):
            _call(app.state, session, ids, target="succeeded")
        assert session.execute(select(events)).all() == []


def test_repeated_retry_schedule_is_an_event_free_no_op(app) -> None:
    ids = _seed(app)
    kwargs = {
        "scope": Scope.user("owner"),
        "run_id": ids["run_id"],
        "step_id": ids["step_id"],
        "attempt_id": ids["attempt_id"],
        "attempt_no": 1,
        "lease_owner": "worker-a",
        "ready_at": 20,
        "now_us": 3,
    }
    with session_scope() as session:
        assert app.state.schedule_capability_retry_cas(session, **kwargs)
        assert not app.state.schedule_capability_retry_cas(session, **kwargs)
        assert session.execute(select(events.c.event_type)).scalars().all() == [
            "attempt.failed",
            "step.retry_scheduled",
        ]


@pytest.mark.parametrize("ready_at", [True, 2, 3.5])
def test_retry_schedule_rejects_invalid_ready_boundary(app, ready_at) -> None:  # noqa: ANN001
    ids = _seed(app)
    with session_scope() as session:
        with pytest.raises(StateError, match="ready_at"):
            app.state.schedule_capability_retry_cas(
                session,
                scope=Scope.user("owner"),
                run_id=ids["run_id"],
                step_id=ids["step_id"],
                attempt_id=ids["attempt_id"],
                attempt_no=1,
                lease_owner="worker-a",
                ready_at=ready_at,
                now_us=3,
            )
        _, step, attempt = _rows(session)
        assert step["state"] == StepState.running.value
        assert attempt["state"] == AttemptState.running.value
        assert session.execute(select(events)).all() == []


def test_terminal_run_reduction_is_fenced_by_active_step(app) -> None:
    ids = _seed(app)
    with session_scope() as session:
        assert not app.state.reduce_run_cas(
            session,
            scope=Scope.user("owner"),
            run_id=ids["run_id"],
            expected_state=RunState.running,
            target=RunState.succeeded,
            outcome="complete",
            now_us=2,
        )
        assert session.execute(select(events)).all() == []
        session.execute(
            steps.update()
            .where(steps.c.id == ids["step_id"])
            .values(state=StepState.succeeded.value)
        )
        assert app.state.reduce_run_cas(
            session,
            scope=Scope.user("owner"),
            run_id=ids["run_id"],
            expected_state=RunState.running,
            target=RunState.succeeded,
            outcome="complete",
            now_us=3,
        )
