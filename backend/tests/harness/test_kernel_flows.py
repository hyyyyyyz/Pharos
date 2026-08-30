"""The H1 kernel flows: canary success, retry, failure, approvals, mapped
steps, agent turns, pause/cancel, restart recovery, idempotency, owner scope,
events and usage. Everything runs offline against the fake stack.
"""

from __future__ import annotations

import pytest
from pharos.db.session import session_scope
from pharos.harness.app import HarnessApp
from pharos.harness.artifacts import ArtifactStore
from pharos.harness.contracts import (
    ApprovalConflictError,
    ApprovalState,
    AttemptErrorClass,
    AttemptState,
    GatewayError,
    RunOutcome,
    RunState,
    StateError,
    StepState,
)
from pharos.harness.dispatcher import ClaimedStep
from pharos.harness.fakes import FakeModel, ModelResult
from pharos.harness.model_gateway import FakeGatewayFactory
from pharos.harness.repository import Scope
from pharos.harness.tables import artifacts, attempts
from pharos.harness.usage import UsageLedger
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
    flaky_key = next(key for key in executors if key[0] == "canary.flaky@1")
    executors[flaky_key] = AlwaysFails()
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
    noop_key = next(key for key in executors if key[0] == "canary.noop@1")
    executors[noop_key] = Fails()
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
    with pytest.raises(ApprovalConflictError):
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
    with pytest.raises(ApprovalConflictError):
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

    agent_step = next(
        step
        for step in app.steps_for(scope=owner, run_id=run["id"])
        if step["definition_step_key"] == "actor_turn"
    )
    assert agent_step["output_artifact_id"] is not None
    with session_scope() as session:
        artifact_rows = (
            session.execute(artifacts.select().where(artifacts.c.run_id == run["id"]))
            .mappings()
            .all()
        )
    agent_artifacts = [row for row in artifact_rows if row["step_id"] == agent_step["id"]]
    assert len(agent_artifacts) == 1
    assert agent_artifacts[0]["schema_name"] == "canary.actor_out"
    assert agent_artifacts[0]["role_prompt_version"] == "canary-actor-zh@1"
    assert agent_artifacts[0]["provider"] == "fake"
    assert agent_artifacts[0]["model"] == "canary"
    with session_scope() as session:
        agent_attempt = (
            session.execute(attempts.select().where(attempts.c.step_id == agent_step["id"]))
            .mappings()
            .one()
        )
    assert agent_attempt["input_sha256"] == run["input_sha256"]
    assert agent_attempt["output_sha256"] == agent_artifacts[0]["content_sha256"]


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
    assert usage["released_reservations"] == 0
    assert usage["settled_reservations"] == 0
    assert usage["pending_reservations"] >= 1


def test_agent_invalid_typed_output_fails_without_artifact(app, owner):
    enable_canary(app, agent_steps=True)
    app.fake_model = FakeModel(
        clock=app.clock,
        script=[
            ModelResult(
                output={
                    "ok": False,
                    "workflow": "harness.canary",
                    "step": "actor_turn",
                }
            )
        ],
    )
    app.gateway_factory = FakeGatewayFactory(app.fake_model)
    app.executor.gateway_factory = app.gateway_factory
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("agent"),
        idempotency_key="agent-invalid-output-1",
        initiator="user",
    )
    run = run_until_terminal(app, owner=owner, run_id=run["id"])
    assert run["state"] == "succeeded"
    actor_step = next(
        step
        for step in app.steps_for(scope=owner, run_id=run["id"])
        if step["definition_step_key"] == "actor_turn"
    )
    assert actor_step["state"] == "failed"
    assert actor_step["output_artifact_id"] is None
    with session_scope() as session:
        assert (
            session.execute(artifacts.select().where(artifacts.c.run_id == run["id"]))
            .mappings()
            .all()
            == []
        )
        usage = app.usage.totals(session, run_id=run["id"])
    assert usage["settled_reservations"] == usage["reserved_reservations"]
    assert usage["released_reservations"] == 0
    assert usage["pending_reservations"] == 0


def test_agent_nonterminal_finish_is_rejected_without_persisting_payload(app, owner):
    enable_canary(app, agent_steps=True)
    private_marker = "PRIVATE-PAPER-TEXT-MUST-NOT-ENTER-ERRORS"
    app.fake_model = FakeModel(
        clock=app.clock,
        script=[
            ModelResult(
                finish_reason="max-tokens",
                output={
                    "ok": True,
                    "workflow": "harness.canary",
                    "step": "actor_turn",
                    "unexpected": private_marker,
                },
            )
        ],
    )
    app.gateway_factory = FakeGatewayFactory(app.fake_model)
    app.executor.gateway_factory = app.gateway_factory
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("agent"),
        idempotency_key="agent-nonterminal-finish-1",
        initiator="user",
    )
    run = run_until_terminal(app, owner=owner, run_id=run["id"])
    actor_step = next(
        step
        for step in app.steps_for(scope=owner, run_id=run["id"])
        if step["definition_step_key"] == "actor_turn"
    )
    assert actor_step["state"] == "failed"
    assert actor_step["output_artifact_id"] is None
    assert private_marker not in (actor_step["error_message"] or "")
    with session_scope() as session:
        attempt = (
            session.execute(attempts.select().where(attempts.c.step_id == actor_step["id"]))
            .mappings()
            .one()
        )
        assert private_marker not in (attempt["error_message"] or "")
        assert (
            session.execute(artifacts.select().where(artifacts.c.run_id == run["id"]))
            .mappings()
            .all()
            == []
        )


def test_agent_finish_artifact_failure_rolls_back_artifact_and_settlement(app, owner):
    enable_canary(app, agent_steps=True)

    class FailingArtifactStore(ArtifactStore):
        def create(self, *args, **kwargs):  # noqa: ANN002, ANN003
            super().create(*args, **kwargs)
            raise RuntimeError("injected artifact commit failure")

    app.executor.artifacts = FailingArtifactStore()
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("agent"),
        idempotency_key="agent-artifact-failure-1",
        initiator="user",
    )
    run = run_until_terminal(app, owner=owner, run_id=run["id"])
    actor_step = next(
        step
        for step in app.steps_for(scope=owner, run_id=run["id"])
        if step["definition_step_key"] == "actor_turn"
    )
    assert actor_step["state"] == "failed"
    assert actor_step["output_artifact_id"] is None
    with session_scope() as session:
        assert (
            session.execute(artifacts.select().where(artifacts.c.run_id == run["id"]))
            .mappings()
            .all()
            == []
        )
        usage = app.usage.totals(session, run_id=run["id"])
    assert usage["settled_reservations"] == usage["reserved_reservations"]
    assert usage["released_reservations"] == 0
    assert usage["pending_reservations"] == 0


def test_agent_finish_usage_failure_requires_reconciliation(app, owner):
    enable_canary(app, agent_steps=True)

    class FailingUsageLedger(UsageLedger):
        def settle(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise RuntimeError("injected usage settle failure")

    app.executor.usage = FailingUsageLedger()
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("agent"),
        idempotency_key="agent-usage-failure-1",
        initiator="user",
    )
    run = run_until_terminal(app, owner=owner, run_id=run["id"])
    assert run["state"] == "indeterminate"
    actor_step = next(
        step
        for step in app.steps_for(scope=owner, run_id=run["id"])
        if step["definition_step_key"] == "actor_turn"
    )
    assert actor_step["state"] == "indeterminate"
    assert actor_step["output_artifact_id"] is None
    with session_scope() as session:
        assert (
            session.execute(artifacts.select().where(artifacts.c.run_id == run["id"]))
            .mappings()
            .all()
            == []
        )
        usage = app.executor.usage.totals(session, run_id=run["id"])
    assert usage["settled_reservations"] == 0
    assert usage["released_reservations"] == 0
    assert usage["pending_reservations"] == usage["reserved_reservations"]


def test_replaying_agent_finish_does_not_duplicate_artifact_or_usage(app, owner):
    enable_canary(app, agent_steps=True)
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("agent"),
        idempotency_key="agent-replay-finish-1",
        initiator="user",
    )
    run = run_until_terminal(app, owner=owner, run_id=run["id"])
    actor_step = next(
        step
        for step in app.steps_for(scope=owner, run_id=run["id"])
        if step["definition_step_key"] == "actor_turn"
    )
    with session_scope() as session:
        attempt = (
            session.execute(attempts.select().where(attempts.c.step_id == actor_step["id"]))
            .mappings()
            .first()
        )
    # A terminal step is the durable finish fence; reusing its claimed input
    # must be a no-op and must not reserve or settle another ledger row.
    before_artifacts = len(app.artifacts_for(scope=owner, run_id=run["id"]))
    with session_scope() as session:
        before_usage = app.usage.totals(session, run_id=run["id"])
    app.runner._run_agent_step(  # noqa: SLF001 -- explicit replay contract test
        claimed=ClaimedStep(
            step_id=actor_step["id"],
            attempt_id=attempt["id"],
            run_id=run["id"],
            scope_type=owner.scope_type.value,
            scope_id=owner.scope_id,
            definition_step_key=actor_step["definition_step_key"],
            instance_key=actor_step["instance_key"],
            step_kind=actor_step["step_kind"],
            definition_json=actor_step["definition_json"],
            attempt_no=attempt["attempt_no"],
            lease_owner=attempt["lease_owner"] or app.dispatcher.worker_id,
        ),
        now_us=app.clock.utc_epoch_us(),
    )
    with session_scope() as session:
        after_usage = app.usage.totals(session, run_id=run["id"])
    assert len(app.artifacts_for(scope=owner, run_id=run["id"])) == before_artifacts
    assert after_usage == before_usage


def test_late_agent_finish_cannot_mutate_a_newer_attempt(app, owner):
    enable_canary(app, agent_steps=True)
    app.dispatcher.claim_batch = 1
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("agent"),
        idempotency_key="agent-stale-attempt-1",
        initiator="user",
    )
    # Execute start and collect, leaving actor_turn ready for a controlled
    # two-attempt race below.
    app.cycle()
    app.cycle()
    with session_scope() as session:
        stale = app.dispatcher.claim_due(session, now_us=app.clock.utc_epoch_us(), limit=1)
    assert stale is not None and stale.definition_step_key == "actor_turn"
    with session_scope() as session:
        app.state.transition_attempt(
            session,
            attempt_id=stale.attempt_id,
            target=AttemptState.running,
            now_us=app.clock.utc_epoch_us(),
        )
        app.state.transition_step(
            session,
            step_id=stale.step_id,
            target=StepState.running,
            now_us=app.clock.utc_epoch_us(),
        )
        stale_reservation = app.usage.reserve(
            session,
            scope=owner,
            run_id=run["id"],
            step_id=stale.step_id,
            attempt_id=stale.attempt_id,
            kind="model_tokens",
            source="system_shared",
            amount=10,
            cost_micros=0,
            now_us=app.clock.utc_epoch_us(),
        )
        app.state.transition_attempt(
            session,
            attempt_id=stale.attempt_id,
            target=AttemptState.failed,
            now_us=app.clock.utc_epoch_us(),
        )
        app.state.transition_step(
            session,
            step_id=stale.step_id,
            target=StepState.retry_scheduled,
            now_us=app.clock.utc_epoch_us(),
            ready_at=app.clock.utc_epoch_us(),
            lease_owner=None,
            lease_expires_at=None,
        )
        app.dispatcher.activate_retries(session, now_us=app.clock.utc_epoch_us())
    with session_scope() as session:
        current = app.dispatcher.claim_due(session, now_us=app.clock.utc_epoch_us(), limit=1)
    assert current is not None and current.step_id == stale.step_id
    with session_scope() as session:
        app.state.transition_attempt(
            session,
            attempt_id=current.attempt_id,
            target=AttemptState.running,
            now_us=app.clock.utc_epoch_us(),
        )
        app.state.transition_step(
            session,
            step_id=current.step_id,
            target=StepState.running,
            now_us=app.clock.utc_epoch_us(),
        )

    result = ModelResult(output={"ok": True, "workflow": "harness.canary", "step": "actor_turn"})
    app.runner._finish_agent_success(  # noqa: SLF001 -- inject stale callback
        claimed=stale,
        result=result,
        reservation_id=stale_reservation,
        now_us=app.clock.utc_epoch_us(),
    )
    app.runner._finish_agent_failure(  # noqa: SLF001 -- inject stale callback
        claimed=stale,
        reservation_id=stale_reservation,
        error_class=AttemptErrorClass.bug,
        error_message="late failure",
        now_us=app.clock.utc_epoch_us(),
    )
    step = next(
        row for row in app.steps_for(scope=owner, run_id=run["id"]) if row["id"] == current.step_id
    )
    with session_scope() as session:
        current_attempt = (
            session.execute(attempts.select().where(attempts.c.id == current.attempt_id))
            .mappings()
            .one()
        )
        usage = app.usage.totals(session, run_id=run["id"])
    assert step["state"] == "running"
    assert step["output_artifact_id"] is None
    assert current_attempt["state"] == "running"
    assert app.artifacts_for(scope=owner, run_id=run["id"]) == []
    assert usage["settled_reservations"] == 1
    assert usage["released_reservations"] == 0
    assert usage["pending_reservations"] == 0


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
    app.pause(scope=owner, run_id=run["id"])
    app.cycle()
    run = app.get_run(scope=owner, run_id=run["id"])
    assert run["state"] == "paused", "pause takes effect at a safe boundary"
    assert app.pause(scope=owner, run_id=run["id"])["state"] == RunState.paused.value
    app.resume(scope=owner, run_id=run["id"])
    run = run_until_terminal(app, owner=owner, run_id=run["id"])
    assert run["state"] == "succeeded"


def test_pause_after_claim_lets_owned_step_reach_its_safe_boundary(app, owner):
    """Pause fences new claims, but does not strand a lease already granted."""
    enable_canary(app)
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("success"),
        idempotency_key="pause-after-claim",
        initiator="user",
    )
    with session_scope() as session:
        claimed = app.dispatcher.claim_due(session, now_us=app.clock.utc_epoch_us(), limit=1)
    assert claimed is not None

    app.pause(scope=owner, run_id=run["id"])
    app.runner._execute_one(claimed=claimed, now_us=app.clock.utc_epoch_us())  # noqa: SLF001
    with session_scope() as session:
        attempt = (
            session.execute(attempts.select().where(attempts.c.id == claimed.attempt_id))
            .mappings()
            .one()
        )
    assert attempt["state"] == AttemptState.succeeded.value

    assert app.runner.apply_pending_control(now_us=app.clock.utc_epoch_us()) == 1
    assert app.get_run(scope=owner, run_id=run["id"])["state"] == RunState.paused.value


def test_pause_can_settle_over_a_scheduled_retry_and_resume_it(app, owner):
    enable_canary(app)
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("retry_then_success"),
        idempotency_key="pause-scheduled-retry",
        initiator="user",
    )
    app.dispatcher.claim_batch = 1
    claimed = None
    for _ in range(20):
        with session_scope() as session:
            candidate = app.dispatcher.claim_due(session, now_us=app.clock.utc_epoch_us(), limit=1)
        assert candidate is not None
        if candidate.definition_step_key == "flaky":
            claimed = candidate
            break
        app.runner._execute_one(  # noqa: SLF001 -- drive prerequisites to the retry step
            claimed=candidate,
            now_us=app.clock.utc_epoch_us(),
        )
        app.runner.reduce_all(now_us=app.clock.utc_epoch_us())
    assert claimed is not None
    app.runner._execute_one(claimed=claimed, now_us=app.clock.utc_epoch_us())  # noqa: SLF001
    step = next(
        row for row in app.steps_for(scope=owner, run_id=run["id"]) if row["id"] == claimed.step_id
    )
    assert step["state"] == StepState.retry_scheduled.value

    app.pause(scope=owner, run_id=run["id"])
    assert app.runner.apply_pending_control(now_us=app.clock.utc_epoch_us()) == 1
    assert app.get_run(scope=owner, run_id=run["id"])["state"] == RunState.paused.value
    assert (
        next(
            row
            for row in app.steps_for(scope=owner, run_id=run["id"])
            if row["id"] == claimed.step_id
        )["state"]
        == StepState.retry_scheduled.value
    )

    app.resume(scope=owner, run_id=run["id"])
    app.clock.advance(10)
    with session_scope() as session:
        assert app.dispatcher.activate_retries(session, now_us=app.clock.utc_epoch_us()) == 1
    assert (
        next(
            row
            for row in app.steps_for(scope=owner, run_id=run["id"])
            if row["id"] == claimed.step_id
        )["state"]
        == StepState.ready.value
    )


def test_cancelled_paused_run_cannot_be_revived(app, owner):
    enable_canary(app)
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("success"),
        idempotency_key="resume-cancel-race",
        initiator="user",
    )
    app.pause(scope=owner, run_id=run["id"])
    app.cycle()
    assert app.get_run(scope=owner, run_id=run["id"])["state"] == RunState.paused.value

    app.cancel(scope=owner, run_id=run["id"])
    with pytest.raises(StateError, match="changed while it was being resumed"):
        app.resume(scope=owner, run_id=run["id"])
    run = run_until_terminal(app, owner=owner, run_id=run["id"])
    assert run["state"] == RunState.cancelled.value


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
    assert len(app.pending_approvals(scope=owner, run_id=run["id"])) == 1
    app.cancel(scope=owner, run_id=run["id"])
    assert app.pending_approvals(scope=owner, run_id=run["id"]) == []
    run = run_until_terminal(app, owner=owner, run_id=run["id"])
    assert run["state"] == "cancelled"


def test_waiting_approval_rejects_pause_without_stranding_a_request(app, owner):
    enable_canary(app)
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("approval"),
        idempotency_key="pause-waiting-approval",
        initiator="user",
    )
    run = run_until_terminal(app, owner=owner, run_id=run["id"])
    assert run["state"] == RunState.waiting_for_approval.value

    with pytest.raises(StateError, match="cannot be paused"):
        app.pause(scope=owner, run_id=run["id"])

    unchanged = app.get_run(scope=owner, run_id=run["id"])
    assert unchanged["state"] == RunState.waiting_for_approval.value
    assert unchanged["pause_requested_at"] is None


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
        totals["settled_reservations"]
        + totals["released_reservations"]
        + totals["pending_reservations"]
        == totals["reserved_reservations"]
    ), "every reservation is settled, released, or pending reconciliation"


def test_run_reducer_waits_for_sibling_before_terminal_indeterminate():
    from pharos.harness.workflows.canary import reduce

    run = {"cancel_requested_at": 1}
    target, outcome = reduce(
        run,
        [
            {"definition_step_key": "actor_turn", "state": "indeterminate"},
            {"definition_step_key": "start", "state": "running"},
        ],
        1,
    )
    assert target is RunState.running
    assert outcome is None

    target, outcome = reduce(
        run,
        [
            {"definition_step_key": "actor_turn", "state": "indeterminate"},
            {"definition_step_key": "start", "state": "succeeded"},
        ],
        1,
    )
    assert target is RunState.indeterminate
    assert outcome is RunOutcome.incomplete


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
