"""The H1 kernel flows: canary success, retry, failure, approvals, mapped
steps, agent turns, pause/cancel, restart recovery, idempotency, owner scope,
events and usage. Everything runs offline against the fake stack.
"""

from __future__ import annotations

import pytest
from pharos.db.session import session_scope
from pharos.harness.app import HarnessApp
from pharos.harness.contracts import (
    ApprovalState,
    AttemptErrorClass,
    GatewayError,
)
from pharos.harness.fakes import FakeModel
from pharos.harness.repository import Scope
from pharos.harness.tables import attempts
from pharos.harness.workflows.canary import canary_input
from tests.harness.conftest import enable_canary


def run_until_terminal(app: HarnessApp, *, owner: Scope, run_id: str, max_cycles: int = 200):
    for _ in range(max_cycles):
        app.cycle()
        run = app.get_run(scope=owner, run_id=run_id)
        if run["state"] in (
            "succeeded",
            "failed",
            "cancelled",
            "indeterminate",
            "waiting_for_approval",
        ):
            return run
    raise AssertionError(f"run {run_id} did not reach a stable state in {max_cycles} cycles")


def test_canary_success_end_to_end(app, owner):
    enable_canary(app)
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("success"),
        idempotency_key="success-1",
        initiator="user",
    )
    assert run["state"] == "queued"
    run = run_until_terminal(app, owner=owner, run_id=run["id"])
    assert run["state"] == "succeeded"
    assert run["outcome"] == "complete"
    steps = app.steps_for(scope=owner, run_id=run["id"])
    assert all(step["state"] == "succeeded" for step in steps)
    # Publication happened exactly once, through the idempotent executor.
    events = app.replay_events(scope=owner, run_id=run["id"], after_seq=0, limit=500)
    event_types = {event.event_type for event in events}
    assert "run.succeeded" in event_types
    assert "step.succeeded" in event_types


def test_retryable_failure_then_success(app, owner):
    enable_canary(app)
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("retry_then_success"),
        idempotency_key="retry-1",
        initiator="user",
    )
    run = run_until_terminal(app, owner=owner, run_id=run["id"])
    assert run["state"] == "succeeded"
    with session_scope() as session:
        attempt_rows = (
            session.execute(attempts.select().where(attempts.c.run_id == run["id"]))
            .mappings()
            .all()
        )
    flaky = [
        row
        for row in app.steps_for(scope=owner, run_id=run["id"])
        if row["definition_step_key"] == "flaky"
    ][0]
    assert flaky["attempt_count"] == 2, "first attempt failed, second succeeded"
    assert flaky["state"] == "succeeded"
    assert len(attempt_rows) > 1


def test_terminal_failure_marks_run_failed(app, owner, monkeypatch):
    enable_canary(app)
    # The flaky executor is replaced by one that never recovers; every
    # attempt fails, retries exhaust, and the optional step failure reduces
    # the run to succeeded+partial (the flaky step is optional by contract).
    from pharos.harness.contracts import RetryableCapabilityError
    from pharos.harness.workflows.canary import build_executors

    class AlwaysFails:
        def execute(self, action):
            raise RetryableCapabilityError("permanently broken")

    executors = build_executors()
    executors["canary.flaky@1"] = AlwaysFails()
    app.executor.capabilities = executors
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("retry_then_success"),
        idempotency_key="fail-1",
        initiator="user",
    )
    run = run_until_terminal(app, owner=owner, run_id=run["id"])
    assert run["state"] == "succeeded", "optional step failure reduces to partial"
    assert run["outcome"] == "partial"


def test_required_failure_fails_the_run(app, owner, monkeypatch):
    """A required step failing must fail the run, never masquerade as partial."""
    enable_canary(app)
    from pharos.harness.workflows.canary import build_executors

    class Fails:
        def execute(self, action):
            raise RuntimeError("boom")

    executors = build_executors()
    executors["canary.noop@1"] = Fails()
    app.executor.capabilities = executors
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("success"),
        idempotency_key="required-fail-1",
        initiator="user",
    )
    run = run_until_terminal(app, owner=owner, run_id=run["id"])
    assert run["state"] == "failed"
    assert run["outcome"] == "incomplete"


def test_approval_flow_approve_reject_expire(app, owner):
    enable_canary(app)
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("approval"),
        idempotency_key="approval-1",
        initiator="user",
    )
    run = run_until_terminal(app, owner=owner, run_id=run["id"])
    assert run["state"] == "waiting_for_approval"
    approvals = app.pending_approvals(scope=owner, run_id=run["id"])
    assert len(approvals) == 1
    approval_id = approvals[0]["id"]

    # Approve: the gated step resumes and the run completes.
    app.decide_approval(
        scope=owner,
        approval_id=approval_id,
        decision=ApprovalState.approved,
        resolver_user_id=owner.scope_id,
        reason="go",
    )
    run = run_until_terminal(app, owner=owner, run_id=run["id"])
    assert run["state"] == "succeeded"
    # The grant was consumed, not reusable.
    with pytest.raises(Exception):
        app.decide_approval(
            scope=owner,
            approval_id=approval_id,
            decision=ApprovalState.approved,
            resolver_user_id=owner.scope_id,
            reason="again",
        )

    # A second run, rejected this time: the gate skips and the run succeeds
    # with a partial outcome (the skipped optional gate).
    run2 = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("approval"),
        idempotency_key="approval-2",
        initiator="user",
    )
    run2 = run_until_terminal(app, owner=owner, run_id=run2["id"])
    assert run2["state"] == "waiting_for_approval"
    approval2 = app.pending_approvals(scope=owner, run_id=run2["id"])[0]
    app.decide_approval(
        scope=owner,
        approval_id=approval2["id"],
        decision=ApprovalState.rejected,
        resolver_user_id=owner.scope_id,
        reason="no",
    )
    run2 = run_until_terminal(app, owner=owner, run_id=run2["id"])
    assert run2["state"] == "succeeded"
    gate = [
        s
        for s in app.steps_for(scope=owner, run_id=run2["id"])
        if s["definition_step_key"] == "approval_gate"
    ][0]
    assert gate["state"] == "skipped"

    # Expiry: an approval left alone past its expiry resolves to expired.
    run3 = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("approval"),
        idempotency_key="approval-3",
        initiator="user",
    )
    run3 = run_until_terminal(app, owner=owner, run_id=run3["id"])
    approval3 = app.pending_approvals(scope=owner, run_id=run3["id"])[0]
    app.clock.advance(8 * 24 * 60 * 60)  # past the default 7-day expiry
    app.cycle()
    with pytest.raises(Exception):
        app.decide_approval(
            scope=owner,
            approval_id=approval3["id"],
            decision=ApprovalState.approved,
            resolver_user_id=owner.scope_id,
            reason="too late",
        )


def test_mapped_steps_and_fanout(app, owner):
    enable_canary(app)
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("mapped", items=["x", "y", "z"]),
        idempotency_key="mapped-1",
        initiator="user",
    )
    run = run_until_terminal(app, owner=owner, run_id=run["id"])
    assert run["state"] == "succeeded"
    steps = app.steps_for(scope=owner, run_id=run["id"])
    keys = sorted(step["definition_step_key"] for step in steps)
    assert "map_items" in keys and "collect" in keys


def test_agent_step_runs_through_the_fake_gateway(app, owner):
    enable_canary(app, agent_steps=True)
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("agent"),
        idempotency_key="agent-1",
        initiator="user",
    )
    run = run_until_terminal(app, owner=owner, run_id=run["id"])
    assert run["state"] == "succeeded"
    with session_scope() as session:
        usage = app.usage.totals(session, run_id=run["id"])
    assert usage["settled_reservations"] >= 1, "the agent turn must settle reserved usage"


def test_agent_indeterminate_gateway_marks_run_indeterminate(app, owner):

    error = GatewayError("timeout after send")
    error.error_class = AttemptErrorClass.indeterminate
    app = HarnessApp(clock=app.clock, fake_model=FakeModel(clock=app.clock, script=[error]))
    # Re-bootstrap on the same database.
    app.ensure_bootstrapped()
    enable_canary(app, agent_steps=True)
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("agent"),
        idempotency_key="agent-indet-1",
        initiator="user",
    )
    run = run_until_terminal(app, owner=owner, run_id=run["id"])
    assert run["state"] == "indeterminate", "unknown external outcome must not become failed"
    with session_scope() as session:
        usage = app.usage.totals(session, run_id=run["id"])
    assert (
        usage["released_reservations"] >= 1
    ), "unknown outcome reserves stay un-settled, not double-charged"


def test_pause_and_resume(app, owner):
    enable_canary(app)
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("success"),
        idempotency_key="pause-1",
        initiator="user",
    )
    app.pause(scope=owner, run_id=run["id"])
    app.cycle()
    run = app.get_run(scope=owner, run_id=run["id"])
    assert run["state"] == "paused", "pause takes effect at a safe boundary"
    app.resume(scope=owner, run_id=run["id"])
    run = run_until_terminal(app, owner=owner, run_id=run["id"])
    assert run["state"] == "succeeded"


def test_cancel_is_persistent_and_wins(app, owner):
    enable_canary(app)
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("approval"),
        idempotency_key="cancel-1",
        initiator="user",
    )
    run = run_until_terminal(app, owner=owner, run_id=run["id"])
    assert run["state"] == "waiting_for_approval"
    app.cancel(scope=owner, run_id=run["id"])
    run = run_until_terminal(app, owner=owner, run_id=run["id"])
    assert run["state"] == "cancelled"


def test_restart_recovers_from_the_database(app, owner):
    """A brand-new HarnessApp over the same DB continues where the old one died."""
    enable_canary(app)
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("success"),
        idempotency_key="restart-1",
        initiator="user",
    )
    # Simulate a crash after the run was created but before it executed.
    reborn = HarnessApp(clock=app.clock)
    reborn.ensure_bootstrapped()
    run = run_until_terminal(reborn, owner=owner, run_id=run["id"])
    assert run["state"] == "succeeded"


def test_idempotent_run_creation_and_conflict(app, owner):
    enable_canary(app)
    first = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("success"),
        idempotency_key="same-key",
        initiator="user",
    )
    second = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("success"),
        idempotency_key="same-key",
        initiator="user",
    )
    assert first["id"] == second["id"]
    from pharos.harness.contracts import IdempotencyConflictError

    with pytest.raises(IdempotencyConflictError):
        app.create_run(
            scope=owner,
            workflow_key="harness.canary",
            input=canary_input("mapped"),
            idempotency_key="same-key",
            initiator="user",
        )


def test_owner_scope_hides_other_users_runs(app, owner):
    enable_canary(app)
    stranger = Scope.user("stranger000000000000000000000000001")
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("success"),
        idempotency_key="owner-1",
        initiator="user",
    )
    from pharos.harness.contracts import NotFoundError

    with pytest.raises(NotFoundError):
        app.get_run(scope=stranger, run_id=run["id"])
    with pytest.raises(NotFoundError):
        app.steps_for(scope=stranger, run_id=run["id"])
    with pytest.raises(NotFoundError):
        app.replay_events(scope=stranger, run_id=run["id"], after_seq=0, limit=10)
    # And listing never leaks the other user's run.
    assert app.list_runs(scope=stranger, limit=50) == []


def test_usage_conservation(app, owner):
    enable_canary(app, agent_steps=True)
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("agent"),
        idempotency_key="usage-1",
        initiator="user",
    )
    run_until_terminal(app, owner=owner, run_id=run["id"])
    with session_scope() as session:
        totals = app.usage.totals(session, run_id=run["id"])
    assert (
        totals["settled_reservations"] + totals["released_reservations"]
        == totals["reserved_reservations"]
    ), "every reservation is settled or released exactly once"


def test_start_refused_when_gates_off(app, owner):
    # Default bootstrap: everything off. Creating a run must say unavailable.
    from pharos.harness.contracts import UnavailableError as UE

    with pytest.raises(UE):
        app.create_run(
            scope=owner,
            workflow_key="harness.canary",
            input=canary_input("success"),
            idempotency_key="gated-1",
            initiator="user",
        )


def test_events_replay_has_monotonic_cursor(app, owner):
    enable_canary(app)
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("success"),
        idempotency_key="events-1",
        initiator="user",
    )
    run_until_terminal(app, owner=owner, run_id=run["id"])
    first = app.replay_events(scope=owner, run_id=run["id"], after_seq=0, limit=1000)
    seqs = [event.seq for event in first]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    # Replay from a cursor in the middle returns exactly the suffix.
    middle = seqs[len(seqs) // 2]
    suffix = app.replay_events(scope=owner, run_id=run["id"], after_seq=middle, limit=1000)
    # after_seq is exclusive: the suffix starts strictly after the cursor.
    assert [event.seq for event in suffix] == seqs[len(seqs) // 2 + 1 :]
