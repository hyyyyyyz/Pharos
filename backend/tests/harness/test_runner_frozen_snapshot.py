"""Runner tests for the immutable execution snapshot boundary.

These tests deliberately forge the dispatcher projection and mutate live
execution rows.  A worker may receive a stale or malicious claim, but it may
only execute the authenticated Attempt snapshot read from the database.
"""

from __future__ import annotations

import json
from dataclasses import replace
from threading import Event, Thread
from threading import enumerate as enumerate_threads
from time import monotonic, sleep
from typing import Any

import pytest
from pharos.db.session import session_scope
from pharos.harness.approvals import DEFAULT_EXPIRY_SECONDS, ApprovalRepository
from pharos.harness.contracts import (
    ApprovalState,
    AttemptErrorClass,
    AttemptState,
    DeliveryState,
    GatewayError,
    StepState,
)
from pharos.harness.definitions import RetryPolicy
from pharos.harness.execution_snapshots import MissingExecutionSnapshotError
from pharos.harness.fakes import ModelResult
from pharos.harness.model_gateway import (
    FakeGatewayFactory,
    GatewayKnownFailure,
    LegacyGatewayFactory,
)
from pharos.harness.repository import HarnessAttemptRepository, Scope
from pharos.harness.tables import artifacts, attempts, steps, usage_events
from pharos.harness.workflows.canary import canary_input
from sqlalchemy import select
from tests.harness.conftest import enable_canary


class RecordingCapability:
    def __init__(self) -> None:
        self.actions: list[dict[str, Any]] = []

    def execute(self, action: dict[str, Any]) -> None:
        self.actions.append(action)


def _create_run(app, owner: Scope, *, mode: str, key: str) -> dict:
    return app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input(mode),
        idempotency_key=key,
        initiator="operator",
    )


def _claim_target(app, owner: Scope, run_id: str, target: str):
    """Execute prerequisites one at a time and stop with ``target`` leased."""
    app.dispatcher.claim_batch = 1
    for _ in range(20):
        with session_scope() as session:
            claimed = app.dispatcher.claim_due(session, now_us=app.clock.utc_epoch_us(), limit=1)
            if claimed is None:
                continue
        if (
            claimed.run_id == run_id
            and claimed.scope_type == owner.scope_type.value
            and claimed.scope_id == owner.scope_id
            and claimed.definition_step_key == target
        ):
            return claimed
        app.runner._execute_one(  # noqa: SLF001 -- test drives one leased step
            claimed=claimed, now_us=app.clock.utc_epoch_us()
        )
        app.runner.reduce_all(now_us=app.clock.utc_epoch_us())
    raise AssertionError(f"{target} was not claimed")


def _attempt_row(attempt_id: str) -> dict[str, Any]:
    with session_scope() as session:
        row = session.execute(select(attempts).where(attempts.c.id == attempt_id)).mappings().one()
    return dict(row)


def test_forged_claim_projection_cannot_choose_executor_or_cross_owner(app, owner: Scope) -> None:
    """Only the fresh DB snapshot can select a capability and its identity."""
    enable_canary(app)
    capability = RecordingCapability()
    run = _create_run(app, owner, mode="success", key="frozen-forged-claim")
    claimed = _claim_target(app, owner, run["id"], "start")
    digest = claimed.attempt_snapshot.executor_capability_definition_sha256
    app.executor.capabilities[("canary.noop@1", digest)] = capability

    forged = replace(
        claimed,
        definition_json=json.dumps({"kind": "agent", "role": "evil@1"}),
        step_kind="agent",
        role="evil@1",
        runtime_kind="dsh",
        attempt_snapshot=replace(
            claimed.attempt_snapshot,
            executor_identity="evil@1",
            runtime_kind="dsh",
        ),
    )
    app.runner._execute_one(claimed=forged, now_us=app.clock.utc_epoch_us())  # noqa: SLF001
    assert len(capability.actions) == 1

    # A claim with a foreign owner or successor attempt number cannot borrow
    # this valid snapshot.  It must fail closed before any executor call.
    foreign = replace(claimed, scope_id="stranger000000000000000000000001")
    successor = replace(claimed, attempt_no=claimed.attempt_no + 1)
    app.runner._execute_one(claimed=foreign, now_us=app.clock.utc_epoch_us())  # noqa: SLF001
    app.runner._execute_one(claimed=successor, now_us=app.clock.utc_epoch_us())  # noqa: SLF001
    assert len(capability.actions) == 1


def test_runner_rejects_a_valid_claim_owned_by_another_worker(app, owner: Scope) -> None:
    enable_canary(app)
    capability = RecordingCapability()
    run = _create_run(app, owner, mode="success", key="foreign-worker-claim")
    claimed = _claim_target(app, owner, run["id"], "start")
    digest = claimed.attempt_snapshot.executor_capability_definition_sha256
    assert digest is not None
    app.executor.capabilities[(claimed.attempt_snapshot.executor_identity, digest)] = capability

    original_worker = app.dispatcher.worker_id
    app.dispatcher.worker_id = "different-worker"
    try:
        app.runner._execute_one(  # noqa: SLF001 -- explicit ownership boundary
            claimed=claimed,
            now_us=app.clock.utc_epoch_us(),
        )
    finally:
        app.dispatcher.worker_id = original_worker

    assert capability.actions == []
    assert _attempt_row(claimed.attempt_id)["state"] == AttemptState.leased.value


def test_missing_attempt_snapshot_fails_closed_without_side_effects(
    app, owner: Scope, monkeypatch: pytest.MonkeyPatch
) -> None:
    enable_canary(app)
    capability = RecordingCapability()
    run = _create_run(app, owner, mode="success", key="frozen-missing-snapshot")
    claimed = _claim_target(app, owner, run["id"], "start")
    digest = claimed.attempt_snapshot.executor_capability_definition_sha256
    app.executor.capabilities[("canary.noop@1", digest)] = capability

    def missing_snapshot(*args, **kwargs):  # noqa: ANN002, ANN003
        raise MissingExecutionSnapshotError("test missing Attempt snapshot")

    # Production guards make snapshots write-once and undeletable.  Simulate
    # the equivalent storage outage/corruption at the read boundary instead
    # of disabling those guards in a test.
    monkeypatch.setattr(app.dispatcher.execution_snapshots, "read_attempt", missing_snapshot)

    gateway_calls = len(app.fake_model.calls)
    app.runner._execute_one(claimed=claimed, now_us=app.clock.utc_epoch_us())  # noqa: SLF001
    assert capability.actions == []
    assert len(app.fake_model.calls) == gateway_calls
    with session_scope() as session:
        attempt = (
            session.execute(select(attempts).where(attempts.c.id == claimed.attempt_id))
            .mappings()
            .one()
        )
        artifact_rows = session.execute(
            select(artifacts.c.id).where(artifacts.c.run_id == run["id"])
        ).all()
        usage_rows = session.execute(
            select(usage_events.c.id).where(usage_events.c.run_id == run["id"])
        ).all()
    assert attempt["state"] == AttemptState.leased.value
    assert artifact_rows == []
    assert usage_rows == []


def test_capability_executor_requires_the_frozen_definition_hash(app, owner: Scope) -> None:
    enable_canary(app)
    run = _create_run(app, owner, mode="success", key="frozen-capability-hash")
    claimed = _claim_target(app, owner, run["id"], "start")
    digest = claimed.attempt_snapshot.executor_capability_definition_sha256
    wrong = RecordingCapability()
    app.executor.capabilities.pop(("canary.noop@1", digest))
    app.executor.capabilities[("canary.noop@1", "0" * 64)] = wrong
    app.runner._execute_one(claimed=claimed, now_us=app.clock.utc_epoch_us())  # noqa: SLF001
    assert wrong.actions == []
    assert _attempt_row(claimed.attempt_id)["state"] == AttemptState.failed.value

    # The exact identity/hash tuple is sufficient; no live registry lookup is
    # needed and no hash-free capability key is accepted.
    run2 = _create_run(app, owner, mode="success", key="frozen-capability-hash-2")
    claimed2 = _claim_target(app, owner, run2["id"], "start")
    exact = RecordingCapability()
    digest2 = claimed2.attempt_snapshot.executor_capability_definition_sha256
    app.executor.capabilities[("canary.noop@1", digest2)] = exact
    app.runner._execute_one(claimed=claimed2, now_us=app.clock.utc_epoch_us())  # noqa: SLF001
    assert len(exact.actions) == 1


def test_cancel_request_after_claim_prevents_executor_dispatch(app, owner: Scope) -> None:
    enable_canary(app)
    capability = RecordingCapability()
    run = _create_run(app, owner, mode="success", key="cancel-after-claim")
    claimed = _claim_target(app, owner, run["id"], "start")
    digest = claimed.attempt_snapshot.executor_capability_definition_sha256
    app.executor.capabilities[("canary.noop@1", digest)] = capability

    app.cancel(scope=owner, run_id=run["id"])
    app.runner._execute_one(claimed=claimed, now_us=app.clock.utc_epoch_us())  # noqa: SLF001

    assert capability.actions == []
    assert _attempt_row(claimed.attempt_id)["state"] == AttemptState.cancelled.value
    step = next(
        row for row in app.steps_for(scope=owner, run_id=run["id"]) if row["id"] == claimed.step_id
    )
    assert step["state"] == StepState.cancelled.value


def test_cancel_during_started_capability_waits_for_its_local_safe_boundary(
    app, owner: Scope
) -> None:
    """A started local side effect finishes truthfully before Run cancellation."""
    enable_canary(app)
    run = _create_run(app, owner, mode="success", key="cancel-running-capability")
    claimed = _claim_target(app, owner, run["id"], "start")
    started = Event()
    release = Event()
    errors: list[BaseException] = []

    class BlockingCapability:
        def execute(self, action):  # noqa: ANN001, ANN201 - test seam
            started.set()
            assert release.wait(timeout=2)
            return {"ok": True}

    digest = claimed.attempt_snapshot.executor_capability_definition_sha256
    assert digest is not None
    app.executor.capabilities[(claimed.attempt_snapshot.executor_identity, digest)] = (
        BlockingCapability()
    )

    def execute() -> None:
        try:
            app.runner._execute_one(  # noqa: SLF001 -- exercise the worker boundary
                claimed=claimed,
                now_us=app.clock.utc_epoch_us(),
            )
        except BaseException as error:  # pragma: no cover - asserted below
            errors.append(error)

    worker = Thread(target=execute)
    worker.start()
    assert started.wait(timeout=2)
    assert app.runner.active_local_attempt_ids == (claimed.attempt_id,)

    app.cancel(scope=owner, run_id=run["id"])
    assert app.runner.apply_pending_control(now_us=app.clock.utc_epoch_us()) == 0
    assert _attempt_row(claimed.attempt_id)["state"] == AttemptState.running.value

    release.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert errors == []
    assert app.runner.active_local_attempt_count == 0
    assert _attempt_row(claimed.attempt_id)["state"] == AttemptState.succeeded.value

    assert app.runner.apply_pending_control(now_us=app.clock.utc_epoch_us()) == 1
    assert app.get_run(scope=owner, run_id=run["id"])["state"] == "cancelled"


def test_blocking_capability_heartbeats_past_its_original_lease(app, owner: Scope) -> None:
    """Another worker's reaper cannot erase a live synchronous side effect."""
    enable_canary(app)
    app.dispatcher.lease_seconds = 0.3
    app.dispatcher.heartbeat_seconds = 0.02
    run = _create_run(app, owner, mode="success", key="heartbeat-running-capability")
    claimed = _claim_target(app, owner, run["id"], "start")
    started = Event()
    release = Event()
    errors: list[BaseException] = []

    class BlockingCapability:
        def execute(self, action):  # noqa: ANN001, ANN201 - test seam
            started.set()
            assert release.wait(timeout=3)
            return {"ok": True}

    digest = claimed.attempt_snapshot.executor_capability_definition_sha256
    assert digest is not None
    app.executor.capabilities[(claimed.attempt_snapshot.executor_identity, digest)] = (
        BlockingCapability()
    )

    def execute() -> None:
        try:
            app.runner._execute_one(  # noqa: SLF001 -- exercise the worker boundary
                claimed=claimed,
                now_us=app.clock.utc_epoch_us(),
            )
        except BaseException as error:  # pragma: no cover - asserted below
            errors.append(error)

    worker = Thread(target=execute)
    worker.start()
    assert started.wait(timeout=2)

    original_expiry = None
    with session_scope() as session:
        original_expiry = session.execute(
            select(steps.c.lease_expires_at).where(steps.c.id == claimed.step_id)
        ).scalar_one()
    assert original_expiry is not None

    # Move deterministic time beyond the original lease in small increments;
    # after each move, wait only until the real heartbeat thread has renewed
    # the durable expiry at that clock value.
    while app.clock.utc_epoch_us() <= original_expiry + 300_000:
        app.clock.advance(0.12)
        deadline = monotonic() + 1.0
        while True:
            with session_scope() as session:
                lease_expires_at = session.execute(
                    select(steps.c.lease_expires_at).where(steps.c.id == claimed.step_id)
                ).scalar_one()
            if (
                lease_expires_at is not None
                and lease_expires_at >= app.clock.utc_epoch_us() + 290_000
            ):
                break
            assert monotonic() < deadline, "heartbeat did not renew the local capability lease"
            sleep(0.005)

    with session_scope() as session:
        assert app.dispatcher.reap_expired(session, now_us=app.clock.utc_epoch_us()) == []
    assert _attempt_row(claimed.attempt_id)["state"] == AttemptState.running.value

    release.set()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert errors == []
    assert _attempt_row(claimed.attempt_id)["state"] == AttemptState.succeeded.value
    assert app.runner.active_local_attempt_count == 0


def test_grant_expiring_between_lookup_and_consume_reopens_before_side_effect(
    app, owner: Scope, monkeypatch
) -> None:  # noqa: ANN001
    # This test advances the fake clock through the approval expiry window;
    # keep the independently tested worker lease live across that jump.
    app.dispatcher.lease_seconds = DEFAULT_EXPIRY_SECONDS + 60
    enable_canary(app)
    run = _create_run(app, owner, mode="approval", key="approval-consume-rollback")
    first = _claim_target(app, owner, run["id"], "approval_gate")
    app.runner._execute_one(  # noqa: SLF001 -- open the real approval boundary
        claimed=first,
        now_us=app.clock.utc_epoch_us(),
    )
    app.runner.reduce_all(now_us=app.clock.utc_epoch_us())
    pending = app.pending_approvals(scope=owner, run_id=run["id"])
    assert len(pending) == 1
    app.decide_approval(
        scope=owner,
        approval_id=pending[0]["id"],
        decision=ApprovalState.approved,
        resolver_user_id=owner.scope_id,
        reason="test rollback",
    )
    app.runner.reduce_all(now_us=app.clock.utc_epoch_us())
    successor = _claim_target(app, owner, run["id"], "approval_gate")
    capability = RecordingCapability()
    digest = successor.attempt_snapshot.executor_capability_definition_sha256
    assert digest is not None
    app.executor.capabilities[(successor.attempt_snapshot.executor_identity, digest)] = capability

    original_lookup = ApprovalRepository.approved_for_step

    def expire_after_lookup(self, session, **kwargs):  # noqa: ANN001, ANN003
        rows = original_lookup(self, session, **kwargs)
        app.clock.advance(DEFAULT_EXPIRY_SECONDS + 1)
        return rows

    monkeypatch.setattr(ApprovalRepository, "approved_for_step", expire_after_lookup)
    app.runner._execute_one(  # noqa: SLF001 -- verify the transactional boundary
        claimed=successor,
        now_us=app.clock.utc_epoch_us(),
    )

    assert capability.actions == []
    with session_scope() as session:
        attempt = (
            session.execute(attempts.select().where(attempts.c.id == successor.attempt_id))
            .mappings()
            .one()
        )
        step = (
            session.execute(steps.select().where(steps.c.id == successor.step_id)).mappings().one()
        )
    assert attempt["state"] == AttemptState.blocked.value
    assert step["state"] == StepState.waiting_for_approval.value
    assert len(app.pending_approvals(scope=owner, run_id=run["id"])) == 1
    assert app.runner.active_local_attempt_count == 0


def _claim_agent(app, owner: Scope, key: str):
    enable_canary(app, agent_steps=True)
    run = _create_run(app, owner, mode="agent", key=key)
    return run, _claim_target(app, owner, run["id"], "actor_turn")


class SpyHandle:
    def __init__(self, inner) -> None:  # noqa: ANN001 - protocol spy
        self.inner = inner
        self.context = inner.context
        self.complete_calls = 0
        self.cancel_calls = 0
        self.close_calls = 0

    @property
    def delivery_state(self):  # noqa: ANN201 - protocol spy
        return self.inner.delivery_state

    def complete(self, payload):  # noqa: ANN001 - protocol spy
        self.complete_calls += 1
        return self.inner.complete(payload)

    def cancel(self) -> None:
        self.cancel_calls += 1
        self.inner.cancel()

    def close(self) -> None:
        self.close_calls += 1
        self.inner.close()

    def retry_cleanup(self) -> None:
        self.close_calls += 1
        self.inner.retry_cleanup()


class SpyFactory:
    def __init__(self, source) -> None:  # noqa: ANN001 - protocol spy
        self.inner = FakeGatewayFactory(source)
        self.handles: list[SpyHandle] = []

    def open(self, context):  # noqa: ANN001 - protocol spy
        handle = SpyHandle(self.inner.open(context))
        self.handles.append(handle)
        return handle


def test_agent_opens_one_scoped_handle_with_frozen_context_and_closes_once(app, owner) -> None:
    run, claimed = _claim_agent(app, owner, "per-attempt-context")
    factory = SpyFactory(app.fake_model)
    app.executor.gateway_factory = factory

    start = app.clock.utc_epoch_us()
    app.runner._execute_one(claimed=claimed, now_us=start)  # noqa: SLF001

    assert len(factory.handles) == 1
    handle = factory.handles[0]
    context = handle.context
    assert context.run_id == run["id"]
    assert context.step_id == claimed.step_id
    assert context.attempt_id == claimed.attempt_id
    assert context.attempt_no == claimed.attempt_no
    assert context.scope_type == owner.scope_type.value
    assert context.scope_id == owner.scope_id
    assert context.lease_owner == claimed.lease_owner
    assert context.workflow_key == "harness.canary"
    assert context.workflow_version == 1
    assert (
        context.workflow_definition_sha256
        == claimed.attempt_snapshot.policy_snapshot.workflow_definition_sha256
    )
    assert context.definition_binding_sha256 == claimed.attempt_snapshot.definition_binding_sha256
    assert context.run_policy_sha256 == claimed.attempt_snapshot.run_policy_sha256
    assert context.runtime_kind == claimed.attempt_snapshot.runtime_kind
    assert (
        context.role_definition_sha256 == claimed.attempt_snapshot.executor_role_definition_sha256
    )
    assert context.model_profile_identity == claimed.attempt_snapshot.model_profile_identity
    assert context.model_profile_sha256 == claimed.attempt_snapshot.model_profile_sha256
    assert context.model_route_key == claimed.attempt_snapshot.model_route_key
    assert context.model_route_sha256 == claimed.attempt_snapshot.model_route_sha256
    assert context.usage_source == claimed.attempt_snapshot.usage_source
    assert context.input_sha256 == run["input_sha256"]
    assert context.role == claimed.attempt_snapshot.executor_identity
    assert context.provider == claimed.attempt_snapshot.provider
    assert context.model == claimed.attempt_snapshot.model
    assert context.deadline_at_us == start + 30 * 1_000_000
    assert handle.complete_calls == 1
    assert handle.close_calls == 1
    assert app.runner.active_attempt_count == 0


def _agent_heartbeat_is_alive(attempt_id: str) -> bool:
    name = f"pharos-heartbeat-{attempt_id[:12]}"
    return any(thread.name == name and thread.is_alive() for thread in enumerate_threads())


def test_agent_initial_heartbeat_failure_never_opens_gateway(
    app, owner, monkeypatch
) -> None:  # noqa: ANN001
    run, claimed = _claim_agent(app, owner, "agent-initial-heartbeat-fence")
    factory = SpyFactory(app.fake_model)
    app.executor.gateway_factory = factory
    monkeypatch.setattr(app.dispatcher, "heartbeat", lambda *args, **kwargs: False)

    app.runner._execute_one(  # noqa: SLF001 -- exercise the worker boundary
        claimed=claimed,
        now_us=app.clock.utc_epoch_us(),
    )

    assert factory.handles == []
    attempt = _attempt_row(claimed.attempt_id)
    assert attempt["state"] == AttemptState.timed_out.value
    assert attempt["error_class"] == AttemptErrorClass.timeout.value
    assert not _agent_heartbeat_is_alive(claimed.attempt_id)
    with session_scope() as session:
        totals = app.usage.totals(session, run_id=run["id"])
    assert totals["released_reservations"] == totals["reserved_reservations"]
    assert totals["pending_reservations"] == 0


@pytest.mark.parametrize("blocked_phase", ["open", "complete", "close"])
def test_agent_heartbeats_across_entire_gateway_lifecycle(
    app,
    owner,
    blocked_phase: str,
) -> None:
    """Factory creation, provider wait and cleanup all retain the durable lease."""
    app.dispatcher.lease_seconds = 0.3
    app.dispatcher.heartbeat_seconds = 0.02
    run, claimed = _claim_agent(app, owner, f"agent-heartbeat-{blocked_phase}")
    entered = Event()
    release = Event()
    errors: list[BaseException] = []

    class BlockingHandle:
        def __init__(self, context) -> None:  # noqa: ANN001
            self.context = context
            self._delivery_state = DeliveryState.NOT_STARTED

        @property
        def delivery_state(self) -> DeliveryState:
            return self._delivery_state

        def complete(self, payload):  # noqa: ANN001, ANN201
            self._delivery_state = DeliveryState.SENT
            if blocked_phase == "complete":
                entered.set()
                assert release.wait(timeout=3)
            self._delivery_state = DeliveryState.ACKNOWLEDGED
            raise GatewayKnownFailure(
                "known provider failure",
                error_class=AttemptErrorClass.provider,
                result=ModelResult(output={"ok": False}),
            )

        def cancel(self) -> None:
            release.set()

        def close(self) -> None:
            if blocked_phase == "close":
                entered.set()
                assert release.wait(timeout=3)

        def retry_cleanup(self) -> None:
            return None

    class BlockingFactory:
        def open(self, context):  # noqa: ANN001, ANN201
            if blocked_phase == "open":
                entered.set()
                assert release.wait(timeout=3)
            return BlockingHandle(context)

    app.executor.gateway_factory = BlockingFactory()
    with session_scope() as session:
        original_expiry = session.execute(
            select(steps.c.lease_expires_at).where(steps.c.id == claimed.step_id)
        ).scalar_one()
    assert original_expiry is not None

    def execute() -> None:
        try:
            app.runner._execute_one(  # noqa: SLF001 -- worker boundary
                claimed=claimed,
                now_us=app.clock.utc_epoch_us(),
            )
        except BaseException as error:  # pragma: no cover - asserted below
            errors.append(error)

    worker = Thread(target=execute)
    worker.start()
    assert entered.wait(timeout=2)
    assert _agent_heartbeat_is_alive(claimed.attempt_id)

    while app.clock.utc_epoch_us() <= original_expiry + 300_000:
        app.clock.advance(0.12)
        deadline = monotonic() + 1.0
        while True:
            with session_scope() as session:
                lease_expires_at = session.execute(
                    select(steps.c.lease_expires_at).where(steps.c.id == claimed.step_id)
                ).scalar_one()
            if (
                lease_expires_at is not None
                and lease_expires_at >= app.clock.utc_epoch_us() + 290_000
            ):
                break
            assert monotonic() < deadline, f"heartbeat stopped during factory.{blocked_phase}"
            sleep(0.005)

    with session_scope() as session:
        assert app.dispatcher.reap_expired(session, now_us=app.clock.utc_epoch_us()) == []

    release.set()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert errors == []
    attempt = _attempt_row(claimed.attempt_id)
    assert attempt["state"] == AttemptState.failed.value
    assert attempt["error_class"] == AttemptErrorClass.provider.value
    assert not _agent_heartbeat_is_alive(claimed.attempt_id)


def test_agent_late_lease_loss_cannot_publish_after_reaper_wins(
    app, owner, monkeypatch
) -> None:  # noqa: ANN001
    app.dispatcher.lease_seconds = 0.2
    app.dispatcher.heartbeat_seconds = 0.01
    run, claimed = _claim_agent(app, owner, "agent-heartbeat-lost")
    entered = Event()
    release = Event()
    lease_lost = Event()

    class BlockingDelegate:
        def complete(self, payload):  # noqa: ANN001, ANN201
            entered.set()
            assert release.wait(timeout=3)
            return ModelResult(
                output={
                    "ok": True,
                    "workflow": "harness.canary",
                    "step": "actor_turn",
                }
            )

        def cancel(self) -> None:
            release.set()

        def close(self) -> None:
            return None

    app.executor.gateway_factory = LegacyGatewayFactory(gateway_factory=BlockingDelegate)
    real_heartbeat = app.dispatcher.heartbeat
    calls = 0

    def lose_after_initial(session, *, attempt_id: str, now_us: int) -> bool:  # noqa: ANN001
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_heartbeat(session, attempt_id=attempt_id, now_us=now_us)
        lease_lost.set()
        return False

    monkeypatch.setattr(app.dispatcher, "heartbeat", lose_after_initial)
    worker = Thread(
        target=app.runner._execute_one,  # noqa: SLF001 -- lease race boundary
        kwargs={"claimed": claimed, "now_us": app.clock.utc_epoch_us()},
    )
    worker.start()
    assert entered.wait(timeout=2)
    assert lease_lost.wait(timeout=2)

    app.clock.advance(0.25)
    with session_scope() as session:
        reaped = app.dispatcher.reap_expired(session, now_us=app.clock.utc_epoch_us())

    release.set()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert [row["id"] for row in reaped] == [claimed.attempt_id]
    assert not _agent_heartbeat_is_alive(claimed.attempt_id)
    assert _attempt_row(claimed.attempt_id)["state"] == AttemptState.abandoned.value
    step = next(
        row
        for row in app.steps_for(scope=owner, run_id=run["id"])
        if row["id"] == claimed.step_id
    )
    assert step["state"] == StepState.indeterminate.value
    with session_scope() as session:
        assert (
            session.execute(select(artifacts.c.id).where(artifacts.c.run_id == run["id"])).all()
            == []
        )
        totals = app.usage.totals(session, run_id=run["id"])
    assert totals["settled_reservations"] == totals["reserved_reservations"]
    assert totals["pending_reservations"] == 0


def test_expired_agent_cannot_publish_before_reaper_runs(app, owner) -> None:
    """Lease expiry itself removes publication authority; reaping need not win first."""

    app.dispatcher.lease_seconds = 0.2
    app.dispatcher.heartbeat_seconds = 0.01
    run, claimed = _claim_agent(app, owner, "agent-expired-before-reaper")
    inner = FakeGatewayFactory(app.fake_model)

    class ExpiringAcknowledgedHandle:
        def __init__(self, context) -> None:  # noqa: ANN001
            self.context = context
            self._inner = inner.open(context)

        @property
        def delivery_state(self) -> DeliveryState:
            return self._inner.delivery_state

        def complete(self, payload):  # noqa: ANN001, ANN201
            result = self._inner.complete(payload)
            app.clock.advance(0.25)
            return result

        def cancel(self) -> None:
            self._inner.cancel()

        def close(self) -> None:
            self._inner.close()

        def retry_cleanup(self) -> None:
            self._inner.retry_cleanup()

    class ExpiringFactory:
        def open(self, context):  # noqa: ANN001, ANN201
            return ExpiringAcknowledgedHandle(context)

    app.executor.gateway_factory = ExpiringFactory()
    app.runner._execute_one(  # noqa: SLF001 -- expiry/publication boundary
        claimed=claimed,
        now_us=app.clock.utc_epoch_us(),
    )

    attempt = _attempt_row(claimed.attempt_id)
    step = next(
        row
        for row in app.steps_for(scope=owner, run_id=run["id"])
        if row["id"] == claimed.step_id
    )
    assert attempt["state"] == AttemptState.running.value
    assert step["state"] == StepState.running.value
    assert step["lease_expires_at"] <= app.clock.utc_epoch_us()
    with session_scope() as session:
        assert (
            session.execute(select(artifacts.c.id).where(artifacts.c.run_id == run["id"])).all()
            == []
        )
        totals = app.usage.totals(session, run_id=run["id"])
        reaped = app.dispatcher.reap_expired(
            session,
            now_us=app.clock.utc_epoch_us(),
        )
    assert totals["settled_reservations"] == totals["reserved_reservations"]
    assert totals["pending_reservations"] == 0
    assert [row["id"] for row in reaped] == [claimed.attempt_id]
    assert _attempt_row(claimed.attempt_id)["state"] == AttemptState.abandoned.value


def test_expired_agent_keeps_unknown_delivery_usage_pending_before_reaper(app, owner) -> None:
    app.dispatcher.lease_seconds = 0.2
    app.dispatcher.heartbeat_seconds = 0.01
    run, claimed = _claim_agent(app, owner, "agent-expired-unknown-delivery")

    class ExpiringSentHandle:
        def __init__(self, context) -> None:  # noqa: ANN001
            self.context = context
            self._delivery_state = DeliveryState.NOT_STARTED

        @property
        def delivery_state(self) -> DeliveryState:
            return self._delivery_state

        def complete(self, payload):  # noqa: ANN001, ANN201
            self._delivery_state = DeliveryState.SENT
            app.clock.advance(0.25)
            raise GatewayError("provider outcome unknown")

        def cancel(self) -> None:
            return None

        def close(self) -> None:
            return None

        def retry_cleanup(self) -> None:
            return None

    class ExpiringFactory:
        def open(self, context):  # noqa: ANN001, ANN201
            return ExpiringSentHandle(context)

    app.executor.gateway_factory = ExpiringFactory()
    app.runner._execute_one(  # noqa: SLF001 -- expiry/reconciliation boundary
        claimed=claimed,
        now_us=app.clock.utc_epoch_us(),
    )

    assert _attempt_row(claimed.attempt_id)["state"] == AttemptState.running.value
    with session_scope() as session:
        assert (
            session.execute(select(artifacts.c.id).where(artifacts.c.run_id == run["id"])).all()
            == []
        )
        totals = app.usage.totals(session, run_id=run["id"])
        reaped = app.dispatcher.reap_expired(
            session,
            now_us=app.clock.utc_epoch_us(),
        )
    assert totals["settled_reservations"] == 0
    assert totals["released_reservations"] == 0
    assert totals["pending_reservations"] == totals["reserved_reservations"]
    assert [row["id"] for row in reaped] == [claimed.attempt_id]
    assert _attempt_row(claimed.attempt_id)["state"] == AttemptState.abandoned.value


def test_runner_cancel_active_attempt_targets_only_exact_handle(app) -> None:
    factory = SpyFactory(app.fake_model)
    first = SpyHandle(factory.inner.open(_context_for_test("attempt-a")))
    second = SpyHandle(factory.inner.open(_context_for_test("attempt-b")))
    app.runner._register_handle("attempt-a", first)  # noqa: SLF001
    app.runner._register_handle("attempt-b", second)  # noqa: SLF001

    assert app.runner.cancel_active_attempt("attempt-a") is True
    assert first.cancel_calls == 1
    assert second.cancel_calls == 0
    assert app.runner.active_attempt_ids == ("attempt-a", "attempt-b")
    assert app.runner.cancel_active_attempt("missing") is False
    app.runner._unregister_handle("attempt-a", first)  # noqa: SLF001
    app.runner._unregister_handle("attempt-b", second)  # noqa: SLF001
    first.close()
    second.close()


def test_runner_cancel_run_handles_reaches_cleanup_failed_handle_without_locking(app) -> None:
    factory = SpyFactory(app.fake_model)
    cleanup = SpyHandle(factory.inner.open(_context_for_test("attempt-cleanup")))
    sibling = SpyHandle(factory.inner.open(_context_for_test("attempt-sibling")))
    app.runner._register_handle("attempt-cleanup", cleanup)  # noqa: SLF001
    app.runner._register_handle("attempt-sibling", sibling)  # noqa: SLF001
    app.runner._mark_cleanup_failed("attempt-cleanup", cleanup)  # noqa: SLF001

    signalled = app.runner.cancel_run_handles(scope=Scope.user("owner-test"), run_id="run-test")

    assert signalled == ("attempt-cleanup", "attempt-sibling")
    assert cleanup.cancel_calls == 1
    assert sibling.cancel_calls == 1
    assert app.runner.retry_failed_cleanup("attempt-cleanup") is True
    app.runner._unregister_handle("attempt-sibling", sibling)  # noqa: SLF001
    sibling.close()


def test_retry_failed_cleanup_persists_exact_reap_proof(
    app, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context_for_test("attempt-reaped")
    calls: list[dict[str, object]] = []

    class ReapedHandle:
        reaped_child_pid = 4321

        def __init__(self) -> None:
            self.context = context

        def retry_cleanup(self) -> None:
            return None

    def record(_self, _session, **values):  # noqa: ANN001, ANN202 - repository spy
        calls.append(values)
        return True

    monkeypatch.setattr(HarnessAttemptRepository, "record_child_reaped", record)
    handle = ReapedHandle()
    app.runner._mark_cleanup_failed(context.attempt_id, handle)  # type: ignore[arg-type] # noqa: SLF001

    assert app.runner.retry_failed_cleanup(context.attempt_id) is True
    assert calls == [
        {
            "scope": Scope.user("owner-test"),
            "run_id": "run-test",
            "step_id": "step-test",
            "attempt_id": "attempt-reaped",
            "attempt_no": 1,
            "runtime_session_id": "attempt-reaped",
            "child_pid": 4321,
        }
    ]
    assert app.runner.cleanup_failed_attempt_ids == ()


def test_runner_cleans_up_when_complete_and_close_both_fail(app, owner) -> None:
    run, claimed = _claim_agent(app, owner, "per-attempt-double-failure")

    class CompleteAndCloseFailure:
        def complete(self, payload):  # noqa: ANN001 - protocol spy
            raise GatewayError("provider unavailable")

        def cancel(self) -> None:
            return None

        def close(self) -> None:
            raise RuntimeError("reap failed")

    app.executor.gateway_factory = LegacyGatewayFactory(gateway_factory=CompleteAndCloseFailure)
    app.runner._execute_one(claimed=claimed, now_us=app.clock.utc_epoch_us())  # noqa: SLF001

    assert app.runner.active_attempt_count == 0
    assert app.runner.cleanup_failed_attempt_ids == (claimed.attempt_id,)
    attempt = _attempt_row(claimed.attempt_id)
    assert attempt["state"] == AttemptState.indeterminate.value
    assert attempt["delivery_state"] == DeliveryState.SENT.value
    with session_scope() as session:
        totals = app.usage.totals(session, run_id=run["id"])
    assert totals["released_reservations"] == 0
    assert totals["pending_reservations"] == totals["reserved_reservations"]


def test_cleanup_failure_after_a_result_blocks_artifact_publication(app, owner) -> None:
    run, claimed = _claim_agent(app, owner, "per-attempt-cleanup-before-publish")

    class ResultThenCloseFailure:
        def complete(self, payload):  # noqa: ANN001 - protocol spy
            return ModelResult(
                output={
                    "ok": True,
                    "workflow": "harness.canary",
                    "step": "actor_turn",
                }
            )

        def cancel(self) -> None:
            return None

        def close(self) -> None:
            raise RuntimeError("reap failed")

    app.executor.gateway_factory = LegacyGatewayFactory(gateway_factory=ResultThenCloseFailure)
    app.runner._execute_one(  # noqa: SLF001 -- cleanup/publication boundary test
        claimed=claimed,
        now_us=app.clock.utc_epoch_us(),
    )

    assert app.runner.active_attempt_count == 0
    assert app.runner.cleanup_failed_attempt_ids == (claimed.attempt_id,)
    attempt = _attempt_row(claimed.attempt_id)
    assert attempt["state"] == AttemptState.indeterminate.value
    assert attempt["delivery_state"] == DeliveryState.ACKNOWLEDGED.value
    with session_scope() as session:
        assert (
            session.execute(select(artifacts.c.id).where(artifacts.c.run_id == run["id"])).all()
            == []
        )
        totals = app.usage.totals(session, run_id=run["id"])
    assert totals["released_reservations"] == 0
    assert totals["settled_reservations"] == totals["reserved_reservations"]
    assert totals["pending_reservations"] == 0
    assert attempt["input_tokens"] == 10
    assert attempt["output_tokens"] == 20
    assert attempt["external_outcome"] == "runtime_cleanup_required"


def test_factory_open_failure_releases_usage_without_registering_a_handle(app, owner) -> None:
    run, claimed = _claim_agent(app, owner, "per-attempt-open-failure")

    class OpenFailure:
        def open(self, context):  # noqa: ANN001 - protocol spy
            raise RuntimeError("runtime did not start")

    app.executor.gateway_factory = OpenFailure()
    app.runner._execute_one(  # noqa: SLF001 -- factory failure boundary test
        claimed=claimed,
        now_us=app.clock.utc_epoch_us(),
    )

    assert app.runner.active_attempt_count == 0
    assert _attempt_row(claimed.attempt_id)["state"] == AttemptState.failed.value
    with session_scope() as session:
        assert (
            session.execute(select(artifacts.c.id).where(artifacts.c.run_id == run["id"])).all()
            == []
        )
        totals = app.usage.totals(session, run_id=run["id"])
    assert totals["released_reservations"] == totals["reserved_reservations"]


def test_call_error_delivery_phase_is_authoritative_over_stale_handle_phase(app, owner) -> None:
    """An adapter may expose delivery evidence only on its raised error."""
    run, claimed = _claim_agent(app, owner, "error-only-delivery-phase")

    class ErrorOnlyPhaseHandle:
        # This intentionally remains at the default phase.  The raised
        # transport error is the only delivery evidence available to the
        # runner, as in older adapter implementations.
        delivery_state = DeliveryState.NOT_STARTED

        def __init__(self, context) -> None:  # noqa: ANN001 - protocol spy
            self.context = context

        def complete(self, payload):  # noqa: ANN001 - protocol spy
            error = GatewayError("provider outcome is unknown")
            error.delivery_state = DeliveryState.UNKNOWN
            raise error

        def cancel(self) -> None:
            return None

        def close(self) -> None:
            return None

    class ErrorOnlyPhaseFactory:
        def open(self, context):  # noqa: ANN001 - protocol spy
            return ErrorOnlyPhaseHandle(context)

    app.executor.gateway_factory = ErrorOnlyPhaseFactory()
    app.runner._execute_one(claimed=claimed, now_us=app.clock.utc_epoch_us())  # noqa: SLF001

    attempt = _attempt_row(claimed.attempt_id)
    assert attempt["state"] == AttemptState.indeterminate.value
    assert attempt["delivery_state"] == DeliveryState.UNKNOWN.value
    with session_scope() as session:
        totals = app.usage.totals(session, run_id=run["id"])
    assert totals["released_reservations"] == 0
    assert totals["pending_reservations"] == totals["reserved_reservations"]


def test_cleanup_error_delivery_phase_is_retained_when_call_was_unsent(app, owner) -> None:
    """Cleanup can discover that an apparently unsent call crossed delivery."""
    run, claimed = _claim_agent(app, owner, "cleanup-only-delivery-phase")

    class CleanupPhaseError(RuntimeError):
        delivery_state = DeliveryState.UNKNOWN

    class CleanupOnlyPhaseHandle:
        delivery_state = DeliveryState.NOT_STARTED

        def __init__(self, context) -> None:  # noqa: ANN001 - protocol spy
            self.context = context

        def complete(self, payload):  # noqa: ANN001 - protocol spy
            raise GatewayError("provider failed before delivery")

        def cancel(self) -> None:
            return None

        def close(self) -> None:
            raise CleanupPhaseError("cleanup could not prove delivery boundary")

    class CleanupOnlyPhaseFactory:
        def open(self, context):  # noqa: ANN001 - protocol spy
            return CleanupOnlyPhaseHandle(context)

    app.executor.gateway_factory = CleanupOnlyPhaseFactory()
    app.runner._execute_one(claimed=claimed, now_us=app.clock.utc_epoch_us())  # noqa: SLF001

    attempt = _attempt_row(claimed.attempt_id)
    assert attempt["state"] == AttemptState.indeterminate.value
    assert attempt["delivery_state"] == DeliveryState.UNKNOWN.value
    assert app.runner.cleanup_failed_attempt_ids == (claimed.attempt_id,)
    with session_scope() as session:
        totals = app.usage.totals(session, run_id=run["id"])
    assert totals["released_reservations"] == 0
    assert totals["pending_reservations"] == totals["reserved_reservations"]


def test_unsent_call_releases_usage_but_cleanup_failure_stays_indeterminate(app, owner) -> None:
    """NOT_STARTED avoids spend, while failed cleanup retains reconciliation."""
    run, claimed = _claim_agent(app, owner, "unsent-cleanup-failure")

    class UnsentCleanupFailureHandle:
        delivery_state = DeliveryState.NOT_STARTED

        def __init__(self, context) -> None:  # noqa: ANN001 - protocol spy
            self.context = context

        def complete(self, payload):  # noqa: ANN001 - protocol spy
            raise GatewayError("provider was not invoked")

        def cancel(self) -> None:
            return None

        def close(self) -> None:
            raise RuntimeError("private directory cleanup failed")

    class UnsentCleanupFailureFactory:
        def open(self, context):  # noqa: ANN001 - protocol spy
            return UnsentCleanupFailureHandle(context)

    app.executor.gateway_factory = UnsentCleanupFailureFactory()
    app.runner._execute_one(claimed=claimed, now_us=app.clock.utc_epoch_us())  # noqa: SLF001

    attempt = _attempt_row(claimed.attempt_id)
    assert attempt["state"] == AttemptState.indeterminate.value
    assert attempt["delivery_state"] == DeliveryState.NOT_STARTED.value
    assert attempt["external_outcome"] == "runtime_cleanup_required"
    assert app.runner.cleanup_failed_attempt_ids == (claimed.attempt_id,)
    with session_scope() as session:
        totals = app.usage.totals(session, run_id=run["id"])
    assert totals["released_reservations"] == totals["reserved_reservations"]
    assert totals["pending_reservations"] == 0


def test_delivery_phase_discovered_by_clean_close_is_not_lost(app, owner) -> None:
    """Some transports publish their final delivery observation while closing."""
    run, claimed = _claim_agent(app, owner, "close-discovers-delivery")

    class CloseDiscoversPhaseHandle:
        def __init__(self, context) -> None:  # noqa: ANN001 - protocol spy
            self.context = context
            self.delivery_state = DeliveryState.NOT_STARTED

        def complete(self, payload):  # noqa: ANN001 - protocol spy
            raise GatewayError("provider outcome is unknown")

        def cancel(self) -> None:
            return None

        def close(self) -> None:
            self.delivery_state = DeliveryState.UNKNOWN

    class CloseDiscoversPhaseFactory:
        def open(self, context):  # noqa: ANN001 - protocol spy
            return CloseDiscoversPhaseHandle(context)

    app.executor.gateway_factory = CloseDiscoversPhaseFactory()
    app.runner._execute_one(claimed=claimed, now_us=app.clock.utc_epoch_us())  # noqa: SLF001

    attempt = _attempt_row(claimed.attempt_id)
    assert attempt["state"] == AttemptState.indeterminate.value
    assert attempt["delivery_state"] == DeliveryState.UNKNOWN.value
    with session_scope() as session:
        totals = app.usage.totals(session, run_id=run["id"])
    assert totals["released_reservations"] == 0
    assert totals["pending_reservations"] == totals["reserved_reservations"]


def test_explicit_none_result_is_indeterminate_without_losing_usage(app, owner) -> None:
    app.fake_model.script = [None]
    run, claimed = _claim_agent(app, owner, "explicit-none-result")

    app.runner._execute_one(claimed=claimed, now_us=app.clock.utc_epoch_us())  # noqa: SLF001

    attempt = _attempt_row(claimed.attempt_id)
    assert attempt["state"] == AttemptState.indeterminate.value
    assert attempt["error_class"] == "indeterminate"
    assert attempt["delivery_state"] == DeliveryState.ACKNOWLEDGED.value
    with session_scope() as session:
        assert (
            session.execute(select(artifacts.c.id).where(artifacts.c.run_id == run["id"])).all()
            == []
        )
        totals = app.usage.totals(session, run_id=run["id"])
    assert totals["released_reservations"] == 0
    assert totals["settled_reservations"] == 0
    assert totals["pending_reservations"] == totals["reserved_reservations"]


def test_acknowledged_known_failure_settles_usage_without_publishing(app, owner) -> None:
    run, claimed = _claim_agent(app, owner, "known-delivered-failure")
    result = ModelResult(
        output=None,
        input_tokens=11,
        output_tokens=4,
        provider_request_id="provider-request-1",
    )

    class KnownFailureHandle:
        delivery_state = DeliveryState.ACKNOWLEDGED

        def __init__(self, context) -> None:  # noqa: ANN001 - protocol spy
            self.context = context

        def complete(self, payload):  # noqa: ANN001 - protocol spy
            raise GatewayKnownFailure(
                "validated provider failure",
                error_class=AttemptErrorClass.provider,
                result=result,
            )

        def cancel(self) -> None:
            return None

        def close(self) -> None:
            return None

    class KnownFailureFactory:
        def open(self, context):  # noqa: ANN001 - protocol spy
            return KnownFailureHandle(context)

    app.executor.gateway_factory = KnownFailureFactory()
    app.runner._execute_one(claimed=claimed, now_us=app.clock.utc_epoch_us())  # noqa: SLF001

    attempt = _attempt_row(claimed.attempt_id)
    assert attempt["state"] == AttemptState.failed.value
    assert attempt["delivery_state"] == DeliveryState.ACKNOWLEDGED.value
    assert attempt["error_class"] == AttemptErrorClass.provider.value
    assert attempt["input_tokens"] == 11
    assert attempt["output_tokens"] == 4
    assert attempt["provider_request_id"] == "provider-request-1"
    with session_scope() as session:
        assert (
            session.execute(select(artifacts.c.id).where(artifacts.c.run_id == run["id"])).all()
            == []
        )
        totals = app.usage.totals(session, run_id=run["id"])
    assert totals["settled_reservations"] == totals["reserved_reservations"]
    assert totals["released_reservations"] == 0
    assert totals["pending_reservations"] == 0


def test_result_without_acknowledged_delivery_cannot_publish(app, owner) -> None:
    run, claimed = _claim_agent(app, owner, "result-without-ack")

    class PrematureHandle:
        delivery_state = DeliveryState.SENT

        def __init__(self, context) -> None:  # noqa: ANN001 - protocol spy
            self.context = context

        def complete(self, payload):  # noqa: ANN001 - protocol spy
            return ModelResult(
                output={
                    "ok": True,
                    "workflow": "harness.canary",
                    "step": "actor_turn",
                }
            )

        def cancel(self) -> None:
            return None

        def close(self) -> None:
            return None

    class PrematureFactory:
        def open(self, context):  # noqa: ANN001 - protocol spy
            return PrematureHandle(context)

    app.executor.gateway_factory = PrematureFactory()
    app.runner._execute_one(claimed=claimed, now_us=app.clock.utc_epoch_us())  # noqa: SLF001

    attempt = _attempt_row(claimed.attempt_id)
    assert attempt["state"] == AttemptState.indeterminate.value
    assert attempt["delivery_state"] == DeliveryState.SENT.value
    with session_scope() as session:
        assert (
            session.execute(select(artifacts.c.id).where(artifacts.c.run_id == run["id"])).all()
            == []
        )
        totals = app.usage.totals(session, run_id=run["id"])
    assert totals["pending_reservations"] == totals["reserved_reservations"]


def test_successful_call_with_untrusted_phase_and_close_failure_is_unknown(app, owner) -> None:
    run, claimed = _claim_agent(app, owner, "untrusted-phase-close-failure")

    class BadCloseHandle:
        delivery_state = DeliveryState.NOT_STARTED

        def __init__(self, context) -> None:  # noqa: ANN001 - protocol spy
            self.context = context

        def complete(self, payload):  # noqa: ANN001 - protocol spy
            return ModelResult(output={"ok": True})

        def cancel(self) -> None:
            return None

        def close(self) -> None:
            raise RuntimeError("cleanup proof unavailable")

    class BadCloseFactory:
        def open(self, context):  # noqa: ANN001 - protocol spy
            return BadCloseHandle(context)

    app.executor.gateway_factory = BadCloseFactory()
    app.runner._execute_one(claimed=claimed, now_us=app.clock.utc_epoch_us())  # noqa: SLF001

    attempt = _attempt_row(claimed.attempt_id)
    assert attempt["state"] == AttemptState.indeterminate.value
    assert attempt["delivery_state"] == DeliveryState.UNKNOWN.value
    assert app.runner.cleanup_failed_attempt_ids == (claimed.attempt_id,)
    with session_scope() as session:
        totals = app.usage.totals(session, run_id=run["id"])
    assert totals["released_reservations"] == 0
    assert totals["pending_reservations"] == totals["reserved_reservations"]


def test_cancel_before_delivery_cancels_attempt_and_releases_usage(app, owner) -> None:
    run, claimed = _claim_agent(app, owner, "cancel-before-delivery")
    entered = Event()
    released = Event()

    class PreSendHandle:
        context = None
        delivery_state = DeliveryState.NOT_STARTED

        def __init__(self, context) -> None:  # noqa: ANN001 - protocol spy
            self.context = context
            self.cancelled = False

        def complete(self, payload):  # noqa: ANN001 - protocol spy
            entered.set()
            assert released.wait(2)
            raise RuntimeError("cancelled before provider dispatch")

        def cancel(self) -> None:
            self.cancelled = True
            released.set()

        def close(self) -> None:
            return None

    class PreSendFactory:
        def open(self, context):  # noqa: ANN001 - protocol spy
            return PreSendHandle(context)

    app.executor.gateway_factory = PreSendFactory()
    worker = Thread(
        target=app.runner._execute_one,  # noqa: SLF001 - concurrency boundary test
        kwargs={"claimed": claimed, "now_us": app.clock.utc_epoch_us()},
    )
    worker.start()
    assert entered.wait(2)
    app.cancel(scope=owner, run_id=run["id"])
    worker.join(2)
    assert not worker.is_alive()

    attempt = _attempt_row(claimed.attempt_id)
    assert attempt["state"] == AttemptState.cancelled.value
    assert app.runner.active_attempt_count == 0
    with session_scope() as session:
        totals = app.usage.totals(session, run_id=run["id"])
    assert totals["released_reservations"] == totals["reserved_reservations"]
    assert totals["pending_reservations"] == 0


def test_cancel_after_delivery_is_indeterminate_and_keeps_usage_pending(app, owner) -> None:
    run, claimed = _claim_agent(app, owner, "cancel-after-delivery")
    entered = Event()
    cancelled = Event()
    released = Event()

    class BlockingDelegate:
        def complete(self, payload):  # noqa: ANN001 - protocol spy
            entered.set()
            assert released.wait(2)
            return ModelResult(
                output={
                    "ok": True,
                    "workflow": "harness.canary",
                    "step": "actor_turn",
                }
            )

        def cancel(self) -> None:
            cancelled.set()

        def close(self) -> None:
            return None

    app.executor.gateway_factory = LegacyGatewayFactory(gateway_factory=BlockingDelegate)
    worker = Thread(
        target=app.runner._execute_one,  # noqa: SLF001 - concurrency boundary test
        kwargs={"claimed": claimed, "now_us": app.clock.utc_epoch_us()},
    )
    worker.start()
    assert entered.wait(2)
    app.cancel(scope=owner, run_id=run["id"])
    assert cancelled.wait(2)
    app.runner.reduce_all(now_us=app.clock.utc_epoch_us())
    assert app.get_run(scope=owner, run_id=run["id"])["state"] == "running"
    released.set()
    worker.join(2)
    assert not worker.is_alive()

    attempt = _attempt_row(claimed.attempt_id)
    assert attempt["state"] == AttemptState.indeterminate.value
    assert attempt["delivery_state"] == DeliveryState.SENT.value
    assert app.runner.active_attempt_count == 0
    with session_scope() as session:
        totals = app.usage.totals(session, run_id=run["id"])
    assert totals["released_reservations"] == 0
    assert totals["settled_reservations"] == 0
    assert totals["pending_reservations"] == totals["reserved_reservations"]

    app.runner.apply_pending_control(now_us=app.clock.utc_epoch_us())
    terminal = app.get_run(scope=owner, run_id=run["id"])
    assert terminal["state"] == "indeterminate"


def test_cancel_after_result_before_publication_suppresses_artifact_and_settles_usage(
    app, owner
) -> None:
    run, claimed = _claim_agent(app, owner, "cancel-before-publication")
    close_entered = Event()
    release_close = Event()

    class CloseBarrierDelegate:
        def complete(self, payload):  # noqa: ANN001 - protocol spy
            return ModelResult(
                output={
                    "ok": True,
                    "workflow": "harness.canary",
                    "step": "actor_turn",
                }
            )

        def cancel(self) -> None:
            return None

        def close(self) -> None:
            close_entered.set()
            assert release_close.wait(2)

    app.executor.gateway_factory = LegacyGatewayFactory(gateway_factory=CloseBarrierDelegate)
    worker = Thread(
        target=app.runner._execute_one,  # noqa: SLF001 - publication race test
        kwargs={"claimed": claimed, "now_us": app.clock.utc_epoch_us()},
    )
    worker.start()
    assert close_entered.wait(2)
    app.cancel(scope=owner, run_id=run["id"])
    release_close.set()
    worker.join(2)
    assert not worker.is_alive()

    attempt = _attempt_row(claimed.attempt_id)
    assert attempt["state"] == AttemptState.cancelled.value
    assert attempt["delivery_state"] == DeliveryState.ACKNOWLEDGED.value
    with session_scope() as session:
        assert (
            session.execute(select(artifacts.c.id).where(artifacts.c.run_id == run["id"])).all()
            == []
        )
        totals = app.usage.totals(session, run_id=run["id"])
    assert totals["settled_reservations"] == totals["reserved_reservations"]
    assert totals["released_reservations"] == 0
    assert totals["pending_reservations"] == 0


def test_pending_cancel_recovers_no_local_leased_generation(app, owner: Scope) -> None:
    """A restart can cancel an untouched current Attempt without a handle."""
    run, claimed = _claim_agent(app, owner, "cancel-no-local-leased")

    app.cancel(scope=owner, run_id=run["id"])
    app.runner.apply_pending_control(now_us=app.clock.utc_epoch_us())

    attempt = _attempt_row(claimed.attempt_id)
    assert attempt["state"] == AttemptState.cancelled.value
    step = next(
        row for row in app.steps_for(scope=owner, run_id=run["id"]) if row["id"] == claimed.step_id
    )
    assert step["state"] == StepState.cancelled.value


def test_pending_cancel_does_not_guess_a_reserved_runtime_is_unsent(app, owner: Scope) -> None:
    """A launch reservation is external evidence even before PID attachment."""
    run, claimed = _claim_agent(app, owner, "cancel-no-local-leased-runtime")
    with session_scope() as session:
        session.execute(
            attempts.update()
            .where(attempts.c.id == claimed.attempt_id)
            .values(runtime_session_id="reserved-runtime", delivery_state="not_started")
        )

    app.cancel(scope=owner, run_id=run["id"])
    app.runner.apply_pending_control(now_us=app.clock.utc_epoch_us())

    attempt = _attempt_row(claimed.attempt_id)
    assert attempt["state"] == AttemptState.indeterminate.value
    assert attempt["delivery_state"] == DeliveryState.UNKNOWN.value


def test_pending_cancel_marks_no_local_running_generation_indeterminate(app, owner: Scope) -> None:
    """A running Attempt without delivery proof must retain a reconciliation boundary."""
    run, claimed = _claim_agent(app, owner, "cancel-no-local-running")
    with session_scope() as session:
        app.state.transition_attempt(
            session,
            attempt_id=claimed.attempt_id,
            target=AttemptState.running,
            now_us=app.clock.utc_epoch_us(),
        )
        app.state.transition_step(
            session,
            step_id=claimed.step_id,
            target=StepState.running,
            now_us=app.clock.utc_epoch_us(),
        )
        reservation_id = app.usage.reserve(
            session,
            scope=owner,
            run_id=run["id"],
            step_id=claimed.step_id,
            attempt_id=claimed.attempt_id,
            kind="model_tokens",
            source="system_shared",
            amount=10,
            cost_micros=0,
            now_us=app.clock.utc_epoch_us(),
        )

    app.cancel(scope=owner, run_id=run["id"])
    app.runner.apply_pending_control(now_us=app.clock.utc_epoch_us())

    attempt = _attempt_row(claimed.attempt_id)
    assert attempt["state"] == AttemptState.indeterminate.value
    assert attempt["delivery_state"] == DeliveryState.UNKNOWN.value
    with session_scope() as session:
        pending = session.execute(
            select(usage_events.c.id).where(
                usage_events.c.reservation_id == reservation_id,
                usage_events.c.op == "reserve",
            )
        ).all()
        totals = app.usage.totals(session, run_id=run["id"])
    assert len(pending) == 1
    assert totals["pending_reservations"] == 1


def _context_for_test(attempt_id: str):
    from pharos.harness.model_gateway import AttemptContext

    return AttemptContext(
        run_id="run-test",
        step_id="step-test",
        attempt_id=attempt_id,
        attempt_no=1,
        scope_type="user",
        scope_id="owner-test",
        lease_owner="worker-test",
        workflow_key="harness.canary",
        workflow_version=1,
        workflow_definition_sha256="a" * 64,
        definition_binding_sha256="b" * 64,
        run_policy_sha256="c" * 64,
        role="canary_actor@1",
        runtime_kind="in_process_fake",
        role_definition_sha256="d" * 64,
        model_profile_identity="canary_profile@1",
        model_profile_sha256="e" * 64,
        model_route_key="default",
        model_route_sha256="f" * 64,
        usage_source="system_shared",
        input_sha256="1" * 64,
        deadline_at_us=1_700_000_001_000_000,
        provider="fake",
        model="canary",
    )


@pytest.mark.parametrize(
    "field,value",
    [("schema_name", "forged.schema"), ("prompt_version", "forged-prompt@9")],
)
def test_agent_contract_mismatch_fails_before_usage_or_gateway(
    app, owner: Scope, field: str, value: str
) -> None:
    run, claimed = _claim_agent(app, owner, f"frozen-agent-contract-{field}")
    key = (
        claimed.attempt_snapshot.executor_identity,
        claimed.attempt_snapshot.executor_role_definition_sha256,
    )
    original = app.executor.agent_output_contracts[key]
    app.executor.agent_output_contracts[key] = replace(original, **{field: value})
    calls_before = len(app.fake_model.calls)
    app.runner._execute_one(claimed=claimed, now_us=app.clock.utc_epoch_us())  # noqa: SLF001
    assert len(app.fake_model.calls) == calls_before
    assert _attempt_row(claimed.attempt_id)["state"] == AttemptState.failed.value
    with session_scope() as session:
        assert (
            session.execute(select(artifacts.c.id).where(artifacts.c.run_id == run["id"])).all()
            == []
        )


def test_agent_contract_key_is_role_hash_bound_and_route_provenance_is_frozen(
    app, owner: Scope
) -> None:
    run, claimed = _claim_agent(app, owner, "frozen-agent-role-hash")
    snapshot = claimed.attempt_snapshot
    correct_key = (snapshot.executor_identity, snapshot.executor_role_definition_sha256)
    original = app.executor.agent_output_contracts[correct_key]
    app.executor.agent_output_contracts.pop(correct_key)
    app.executor.agent_output_contracts[(snapshot.executor_identity, "0" * 64)] = original
    calls_before = len(app.fake_model.calls)
    app.runner._execute_one(claimed=claimed, now_us=app.clock.utc_epoch_us())  # noqa: SLF001
    assert len(app.fake_model.calls) == calls_before
    assert _attempt_row(claimed.attempt_id)["state"] == AttemptState.failed.value
    app.executor.agent_output_contracts.pop((snapshot.executor_identity, "0" * 64))
    app.executor.agent_output_contracts[correct_key] = original

    # A new run proves a forged claim-side Attempt snapshot cannot override the
    # authenticated provider/model recorded by the frozen route.  The runner
    # re-reads the DB snapshot before invoking the agent entry point.
    run2, claimed2 = _claim_agent(app, owner, "frozen-agent-provenance")
    snapshot2 = claimed2.attempt_snapshot
    forged_claim = replace(
        claimed2,
        attempt_snapshot=replace(
            snapshot2,
            provider="evil-provider",
            model="evil-model",
            usage_source="evil-budget",
        ),
    )
    app.runner._run_agent_step(  # noqa: SLF001 -- forged snapshot is an input boundary test
        claimed=forged_claim, now_us=app.clock.utc_epoch_us()
    )
    with session_scope() as session:
        artifact = (
            session.execute(select(artifacts).where(artifacts.c.run_id == run2["id"]))
            .mappings()
            .one()
        )
    assert artifact["provider"] == snapshot2.provider
    assert artifact["model"] == snapshot2.model


def test_finish_callback_reauthenticates_snapshot_and_contract(app, owner: Scope) -> None:
    """A future async callback cannot supply its own provenance or validator."""
    run, claimed = _claim_agent(app, owner, "frozen-agent-finish-boundary")
    snapshot = claimed.attempt_snapshot
    now_us = app.clock.utc_epoch_us()
    with session_scope() as session:
        app.state.transition_attempt(
            session,
            attempt_id=claimed.attempt_id,
            target=AttemptState.running,
            now_us=now_us,
        )
        app.state.transition_step(
            session,
            step_id=claimed.step_id,
            target=StepState.running,
            now_us=now_us,
        )
        reservation_id = app.usage.reserve(
            session,
            scope=owner,
            run_id=run["id"],
            step_id=claimed.step_id,
            attempt_id=claimed.attempt_id,
            kind="model_tokens",
            source=snapshot.usage_source,
            amount=10,
            cost_micros=0,
            now_us=now_us,
        )
    forged = replace(
        claimed,
        role="evil@1",
        runtime_kind="dsh",
        attempt_snapshot=replace(
            snapshot,
            executor_identity="evil@1",
            provider="evil-provider",
            model="evil-model",
            usage_source="official",
        ),
    )
    result = ModelResult(
        output={"ok": True, "workflow": "harness.canary", "step": "actor_turn"},
        input_tokens=2,
        output_tokens=3,
    )
    app.runner._finish_agent_success(  # noqa: SLF001 -- callback boundary test
        claimed=forged,
        result=result,
        reservation_id=reservation_id,
        now_us=now_us,
    )
    with session_scope() as session:
        artifact = (
            session.execute(select(artifacts).where(artifacts.c.run_id == run["id"]))
            .mappings()
            .one()
        )
    assert artifact["artifact_type"] == f"agent.{snapshot.executor_identity}"
    assert artifact["provider"] == snapshot.provider
    assert artifact["model"] == snapshot.model


def test_retry_policy_comes_from_frozen_db_snapshot_not_claim_projection(app, owner: Scope) -> None:
    enable_canary(app)
    run = _create_run(app, owner, mode="retry_then_success", key="frozen-retry-policy")
    claimed = _claim_target(app, owner, run["id"], "flaky")
    forged = replace(
        claimed,
        attempt_snapshot=replace(
            claimed.attempt_snapshot,
            retry_policy=RetryPolicy(max_attempts=1, backoff_seconds=999),
            max_attempts=1,
        ),
    )
    now_us = app.clock.utc_epoch_us()
    app.runner._execute_one(claimed=forged, now_us=now_us)  # noqa: SLF001
    with session_scope() as session:
        step = session.execute(select(steps).where(steps.c.id == claimed.step_id)).mappings().one()
    assert step["state"] == StepState.retry_scheduled.value
    assert step["ready_at"] == now_us


def test_dsh_snapshot_never_falls_back_to_fake_gateway(app, owner: Scope) -> None:
    from tests.harness.test_dsh_canary_gates import _activate

    _activate(app, version=2, runtime=True)
    run = _create_run(app, owner, mode="agent", key="frozen-dsh-no-fallback")
    app.dispatcher.claim_batch = 2
    # The first two passes advance the deterministic spine and claim actor_turn.
    app.runner.tick(now_us=app.clock.utc_epoch_us())
    app.runner.tick(now_us=app.clock.utc_epoch_us())
    actor = next(
        row
        for row in app.steps_for(scope=owner, run_id=run["id"])
        if row["definition_step_key"] == "actor_turn"
    )
    assert actor["state"] == StepState.waiting_for_input.value
    assert len(app.fake_model.calls) == 0


def test_frozen_step_view_returns_copies_not_mutable_policy_authority(app, owner: Scope) -> None:
    enable_canary(app)
    run = _create_run(app, owner, mode="mapped", key="frozen-view-copies")
    claimed = _claim_target(app, owner, run["id"], "map_items")
    snapshot = claimed.attempt_snapshot
    dynamic = json.loads(snapshot.dynamic_metadata_json)
    dynamic["expand_items"] = ["forged"]
    copied_definition = snapshot.step_definition.model_copy(update={"capability": "evil@1"})
    assert copied_definition.capability == "evil@1"
    with session_scope() as session:
        reread = app.execution_snapshots.read_attempt(
            session,
            scope=owner,
            attempt_id=claimed.attempt_id,
            require_for_execution=True,
        )
    assert json.loads(reread.dynamic_metadata_json) == {"expand_items": ["a", "b"]}
    assert reread.step_definition.capability == "canary.noop@1"
