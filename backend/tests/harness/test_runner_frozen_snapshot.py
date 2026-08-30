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
from pharos.harness.contracts import AttemptState, StepState
from pharos.harness.definitions import RetryPolicy
from pharos.harness.execution_snapshots import MissingExecutionSnapshotError
from pharos.harness.fakes import ModelResult
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

    gateway_calls = len(app.gateway._model.calls)  # noqa: SLF001
    app.runner._execute_one(claimed=claimed, now_us=app.clock.utc_epoch_us())  # noqa: SLF001
    assert capability.actions == []
    assert len(app.gateway._model.calls) == gateway_calls  # noqa: SLF001
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
    calls_before = len(app.gateway._model.calls)  # noqa: SLF001
    app.runner._execute_one(claimed=claimed, now_us=app.clock.utc_epoch_us())  # noqa: SLF001
    assert len(app.gateway._model.calls) == calls_before  # noqa: SLF001
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
    calls_before = len(app.gateway._model.calls)  # noqa: SLF001
    app.runner._execute_one(claimed=claimed, now_us=app.clock.utc_epoch_us())  # noqa: SLF001
    assert len(app.gateway._model.calls) == calls_before  # noqa: SLF001
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
    assert len(app.gateway._model.calls) == 0  # noqa: SLF001


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
