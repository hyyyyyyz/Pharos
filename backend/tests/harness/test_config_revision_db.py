"""DB-backed configuration revisions: CAS, bootstrap env, emergency stop.

The head is the single authority; these tests pin the races the architecture
calls out -- two operators applying with the same expected revision, env
bootstrap never overriding an existing head, and the deny-only emergency stop
never blocking the read/export path.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from pharos.db.session import session_scope
from pharos.harness.configrev import (
    EMERGENCY_STOP_ENV,
    GATE_NAMES,
    HarnessConfigSnapshot,
    WorkflowRoute,
    emergency_stop_active,
)
from pharos.harness.contracts import (
    ActivationState,
    StaleConfigError,
    UnavailableError,
)
from pharos.harness.repository import now_iso
from pharos.harness.tables import config_revisions
from pydantic import ValidationError
from sqlalchemy import select
from tests.harness.conftest import enable_canary


def _snapshot(gates=None, routes=None) -> HarnessConfigSnapshot:  # noqa: ANN001
    base_gates = {
        "harness_enabled": True,
        "dispatcher_enabled": True,
        "canary_enabled": False,
        "agent_steps_enabled": False,
        "agent_runtime_enabled": False,
        "domain_publish_enabled": False,
        "fulltext_enabled": False,
        "desktop_bridge_enabled": False,
        "experiments_enabled": False,
    }
    if gates:
        base_gates.update(gates)
    return HarnessConfigSnapshot(
        gates=base_gates,
        routes=routes
        or (
            WorkflowRoute(
                workflow_key="harness.canary",
                active_version=1,
                activation_state=ActivationState.active,
                execution_mode=None,
            ),
        ),
        actor="test-operator",
        reason="test",
    )


def test_bootstrap_head_exists_after_startup(app):
    with session_scope() as session:
        head = app.config_service.current(session)
    assert head is not None and head["current_revision_id"] is not None


def test_double_operator_cas_only_one_wins(app):
    enable_canary(app)
    with session_scope() as session:
        head = app.config_service.current(session)
        expected = head["current_revision_id"]
    snapshot = _snapshot(gates={"canary_enabled": True})
    with session_scope() as session:
        first = app.config_service.apply(
            session,
            snapshot=snapshot,
            expected_head_revision=expected,
            actor="op-a",
            reason="turn canary on",
            now=now_iso(),
        )
    with pytest.raises(StaleConfigError, match="expected"), session_scope() as session:
        app.config_service.apply(
            session,
            snapshot=snapshot,
            expected_head_revision=expected,
            actor="op-b",
            reason="turn canary on (stale)",
            now=now_iso(),
        )
    # The loser's revision was rolled back: only the winner's revision exists
    # beyond the bootstrap one.
    with session_scope() as session:
        head = app.config_service.current(session)
    assert head["current_revision_id"] == first


def test_env_does_not_override_existing_head(app, monkeypatch):
    """Once a head exists, PHAROS_HARNESS_* env values are inert."""
    monkeypatch.setenv("PHAROS_HARNESS_ENABLED", "1")
    monkeypatch.setenv("PHAROS_HARNESS_DISPATCHER_ENABLED", "1")
    snapshot = app.ensure_bootstrapped()
    assert (
        snapshot.gates["harness_enabled"] is False
    ), "env must not override a persisted config head"


def test_hashed_pre_runtime_snapshot_is_decoded_without_rewriting(app):
    """The additive gate must not invalidate immutable pre-0008 revisions."""
    enable_canary(app)
    with session_scope() as session:
        revision_id = app.config_service.current(session)["current_revision_id"]
        row = session.execute(
            select(config_revisions).where(config_revisions.c.id == revision_id)
        ).mappings().one()
        payload = json.loads(row["snapshot_json"])
        payload["gates"].pop("agent_runtime_enabled")
        legacy_raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        legacy_hash = hashlib.sha256(legacy_raw.encode()).hexdigest()
        session.execute(
            config_revisions.update()
            .where(config_revisions.c.id == revision_id)
            .values(snapshot_json=legacy_raw, snapshot_sha256=legacy_hash)
        )

    snapshot = app.current_snapshot()
    assert snapshot is not None and snapshot.gates["agent_runtime_enabled"] is False
    with session_scope() as session:
        row = session.execute(
            select(config_revisions).where(config_revisions.c.id == revision_id)
        ).mappings().one()
    assert row["snapshot_json"] == legacy_raw
    assert row["snapshot_sha256"] == legacy_hash


def test_emergency_stop_denies_new_runs_only(monkeypatch):
    from pharos.harness.app import HarnessApp
    from pharos.harness.fakes import FakeClock

    harness = HarnessApp(clock=FakeClock())
    harness.ensure_bootstrapped()
    enable_canary(harness)
    owner = __import__("pharos.harness.repository", fromlist=["Scope"]).Scope.user(
        "owner000000000000000000000000000001"
    )
    monkeypatch.setenv(EMERGENCY_STOP_ENV, "1")
    assert emergency_stop_active()
    from pharos.harness.workflows.canary import canary_input

    with pytest.raises(UnavailableError, match="emergency stop"):
        harness.create_run(
            scope=owner,
            workflow_key="harness.canary",
            input=canary_input("success"),
            idempotency_key="stop-1",
            initiator="user",
        )
    monkeypatch.delenv(EMERGENCY_STOP_ENV)


def test_validator_rejects_invalid_revision_before_persist(app):
    enable_canary(app)
    snapshot = _snapshot(gates={"experiments_enabled": True})
    with pytest.raises(StaleConfigError, match="Decision 9"), session_scope() as session:
        app.config_service.apply(
            session,
            snapshot=snapshot,
            expected_head_revision=None,
            actor="op",
            reason="must never persist",
            now=now_iso(),
        )
    with session_scope() as session:
        current = app.config_service.current_snapshot(session)
    assert current is not None and current.gates["experiments_enabled"] is False


def test_rollback_revision_restores_safe_default(app):
    enable_canary(app)
    with session_scope() as session:
        revision = app.config_service.rollback(
            session, reason="rollback rehearsal", actor="op", now=now_iso()
        )
    assert revision is not None
    snapshot = app.current_snapshot()
    assert snapshot.gates["harness_enabled"] is False
    assert snapshot.gates["canary_enabled"] is False


def test_stale_config_cannot_start_runs(app, owner):
    """A run started under an older revision cannot cross a cutover."""
    enable_canary(app)
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input={"mode": "approval", "note": "fence", "items": ["a"]},
        idempotency_key="fence-1",
        initiator="user",
    )
    # Roll back the head: the canary route is now disabled.
    with session_scope() as session:
        app.config_service.rollback(session, reason="cutover", actor="op", now=now_iso())
    with pytest.raises(UnavailableError):
        app.create_run(
            scope=owner,
            workflow_key="harness.canary",
            input={"mode": "success", "note": "fence", "items": ["a"]},
            idempotency_key="fence-2",
            initiator="user",
        )
    # Existing run remains readable.
    assert app.get_run(scope=owner, run_id=run["id"])["id"] == run["id"]


def test_snapshot_rejects_unknown_and_missing_gates():
    gates = {name: False for name in GATE_NAMES}
    with pytest.raises(ValidationError, match="unknown gate"):
        HarnessConfigSnapshot(gates={**gates, "typo_enabled": False}, routes=())
    with pytest.raises(ValidationError, match="missing gate"):
        HarnessConfigSnapshot(
            gates={name: value for name, value in gates.items() if name != GATE_NAMES[0]},
            routes=(),
        )


def test_snapshot_hash_is_independent_of_route_order():
    gates = {name: False for name in GATE_NAMES}
    first = HarnessConfigSnapshot(
        gates=gates,
        routes=(
            WorkflowRoute(workflow_key="z.workflow"),
            WorkflowRoute(workflow_key="a.workflow"),
        ),
    )
    second = HarnessConfigSnapshot(
        gates=dict(reversed(tuple(gates.items()))),
        routes=tuple(reversed(first.routes)),
    )
    assert first.snapshot_hash() == second.snapshot_hash()
    assert [route["workflow_key"] for route in first.canonical()["routes"]] == [
        "a.workflow",
        "z.workflow",
    ]
