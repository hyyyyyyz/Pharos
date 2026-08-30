"""Runner tests for the immutable execution snapshot boundary.

These tests deliberately forge the dispatcher projection and mutate live
execution rows.  A worker may receive a stale or malicious claim, but it may
only execute the authenticated Attempt snapshot read from the database.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest
from pharos.db.session import session_scope
from pharos.harness.contracts import AttemptState, GatewayError, StepState
from pharos.harness.definitions import RetryPolicy
from pharos.harness.execution_snapshots import MissingExecutionSnapshotError
from pharos.harness.fakes import ModelResult
from pharos.harness.model_gateway import FakeGatewayFactory, LegacyGatewayFactory
from pharos.harness.repository import Scope
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
        row = session.execute(
            select(attempts).where(attempts.c.id == attempt_id)
        ).mappings().one()
    return dict(row)


def test_forged_claim_projection_cannot_choose_executor_or_cross_owner(
    app, owner: Scope
) -> None:
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
        attempt = session.execute(
            select(attempts).where(attempts.c.id == claimed.attempt_id)
        ).mappings().one()
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

    def complete(self, payload):  # noqa: ANN001 - protocol spy
        self.complete_calls += 1
        return self.inner.complete(payload)

    def cancel(self) -> None:
        self.cancel_calls += 1
        self.inner.cancel()

    def close(self) -> None:
        self.close_calls += 1
        self.inner.close()


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
        context.role_definition_sha256
        == claimed.attempt_snapshot.executor_role_definition_sha256
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


def test_runner_cleans_up_when_complete_and_close_both_fail(app, owner) -> None:
    run, claimed = _claim_agent(app, owner, "per-attempt-double-failure")

    class CompleteAndCloseFailure:
        def complete(self, payload):  # noqa: ANN001 - protocol spy
            raise GatewayError("provider unavailable")

        def cancel(self) -> None:
            return None

        def close(self) -> None:
            raise RuntimeError("reap failed")

    app.executor.gateway_factory = LegacyGatewayFactory(
        gateway_factory=CompleteAndCloseFailure
    )
    app.runner._execute_one(claimed=claimed, now_us=app.clock.utc_epoch_us())  # noqa: SLF001

    assert app.runner.active_attempt_count == 0
    assert _attempt_row(claimed.attempt_id)["state"] == AttemptState.failed.value
    with session_scope() as session:
        totals = app.usage.totals(session, run_id=run["id"])
    assert totals["released_reservations"] == totals["reserved_reservations"]


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

    app.executor.gateway_factory = LegacyGatewayFactory(
        gateway_factory=ResultThenCloseFailure
    )
    app.runner._execute_one(  # noqa: SLF001 -- cleanup/publication boundary test
        claimed=claimed,
        now_us=app.clock.utc_epoch_us(),
    )

    assert app.runner.active_attempt_count == 0
    assert _attempt_row(claimed.attempt_id)["state"] == AttemptState.failed.value
    with session_scope() as session:
        assert (
            session.execute(
                select(artifacts.c.id).where(artifacts.c.run_id == run["id"])
            ).all()
            == []
        )
        totals = app.usage.totals(session, run_id=run["id"])
    assert totals["released_reservations"] == totals["reserved_reservations"]


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
            session.execute(
                select(artifacts.c.id).where(artifacts.c.run_id == run["id"])
            ).all()
            == []
        )
        totals = app.usage.totals(session, run_id=run["id"])
    assert totals["released_reservations"] == totals["reserved_reservations"]


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
            session.execute(
                select(artifacts.c.id).where(artifacts.c.run_id == run["id"])
            ).all()
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
        artifact = session.execute(
            select(artifacts).where(artifacts.c.run_id == run2["id"])
        ).mappings().one()
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
        artifact = session.execute(
            select(artifacts).where(artifacts.c.run_id == run["id"])
        ).mappings().one()
    assert artifact["artifact_type"] == f"agent.{snapshot.executor_identity}"
    assert artifact["provider"] == snapshot.provider
    assert artifact["model"] == snapshot.model


def test_retry_policy_comes_from_frozen_db_snapshot_not_claim_projection(
    app, owner: Scope
) -> None:
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
        step = session.execute(
            select(steps).where(steps.c.id == claimed.step_id)
        ).mappings().one()
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
