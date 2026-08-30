"""Contract tests for the immutable Run/Attempt execution snapshot seam."""

from __future__ import annotations

import json
import uuid

import pytest
from pharos.db.session import session_scope
from pharos.harness.configrev import decode_snapshot_payload
from pharos.harness.contracts import NotFoundError, StepState
from pharos.harness.execution_snapshots import (
    ExecutionSnapshotStore,
    MissingExecutionSnapshotError,
    SnapshotConflictError,
    SnapshotIntegrityError,
)
from pharos.harness.policy_builder import build_run_policy
from pharos.harness.policy_snapshot import AgentLimits, RunPolicySnapshot
from pharos.harness.registry import CompiledWorkflowBinding
from pharos.harness.repository import HarnessDefinitionRepository, HarnessRunRepository
from pharos.harness.tables import attempts, steps
from sqlalchemy import select
from tests.harness.conftest import enable_canary


def _policy(
    app, run: dict, binding: CompiledWorkflowBinding
) -> RunPolicySnapshot:
    with session_scope() as session:
        revision = app.config_service.get_revision(session, run["config_revision_id"])
        assert revision is not None
    return build_run_policy(
        binding,
        decode_snapshot_payload(json.loads(revision["snapshot_json"])),
        config_revision_id=run["config_revision_id"],
        config_revision_sha256=revision["snapshot_sha256"],
        agent_limits=AgentLimits(
            max_turns=4,
            max_tool_calls=8,
            max_input_chars=200_000,
            max_output_tokens=60_000,
        ),
    )


def _run(app, owner, *, mode: str = "success"):
    enable_canary(app, agent_steps=True)
    with session_scope() as session:
        current = app.config_service.current_validated(session)
        assert current is not None
        workflow = app.registry.require_workflow("harness.canary@1")
        run = HarnessRunRepository().create(
            session,
            scope=owner,
            workflow=workflow,
            config_revision_id=current.revision_id,
            input={"mode": mode, "note": "snapshot"},
            idempotency_key=f"snapshot-run-{mode}",
            initiator="user",
            now_us=app.clock.utc_epoch_us(),
        )
        binding = HarnessDefinitionRepository().persist_workflow_binding(
            session,
            registry=app.registry,
            workflow=workflow,
            now="2026-08-30T00:00:00+00:00",
        )
    return run, binding


def _activate(app, session, owner, run: dict) -> None:
    created = app.runner.activate_run(
        session, scope=owner, run=run, now_us=app.clock.utc_epoch_us()
    )
    for step in created:
        if json.loads(step["depends_on_json"] or "[]"):
            continue
        app.state.transition_step(
            session,
            step_id=step["id"],
            target=StepState.ready,
            now_us=app.clock.utc_epoch_us(),
            ready_at=app.clock.utc_epoch_us(),
        )


def test_run_snapshot_is_canonical_idempotent_and_owner_scoped(app, owner):
    run, binding = _run(app, owner)
    policy = _policy(app, run, binding)
    store = ExecutionSnapshotStore()
    with session_scope() as session:
        first = store.write_run(
            session,
            scope=owner,
            run_id=run["id"],
            workflow_key="harness.canary",
            workflow_version=1,
            workflow_definition_sha256=policy.workflow_definition_sha256,
            definition_binding_sha256=binding.binding_sha256,
            policy_snapshot=policy,
        )
        second = store.write_run(
            session,
            scope=owner,
            run_id=run["id"],
            workflow_key="harness.canary",
            workflow_version=1,
            workflow_definition_sha256=policy.workflow_definition_sha256,
            definition_binding_sha256=binding.binding_sha256,
            policy_snapshot=policy,
        )
        assert first == second
        assert store.read_run(session, scope=owner, run_id=run["id"]) == first
        with pytest.raises(SnapshotConflictError):
            store.write_run(
                session,
                scope=owner,
                run_id=run["id"],
                workflow_key="harness.canary",
                workflow_version=1,
                workflow_definition_sha256=policy.workflow_definition_sha256,
                definition_binding_sha256=binding.binding_sha256,
                policy_snapshot=policy.model_copy(update={"max_parallel_steps": 3}),
            )
        with pytest.raises(NotFoundError):
            store.read_run(
                session,
                scope="user",
                scope_id="stranger000000000000000000000001",
                run_id=run["id"],
            )


def test_legacy_run_is_readable_but_execution_fails_closed(app, owner):
    run, _ = _run(app, owner)
    with session_scope() as session:
        store = ExecutionSnapshotStore()
        assert store.read_run(session, scope=owner, run_id=run["id"]) is None
        with pytest.raises(MissingExecutionSnapshotError):
            store.read_run(session, scope=owner, run_id=run["id"], require_for_execution=True)


def test_activated_legacy_run_cannot_be_backfilled(app, owner):
    run, binding = _run(app, owner)
    policy = _policy(app, run, binding)
    with session_scope() as session:
        _activate(app, session, owner, run)
        with pytest.raises(SnapshotIntegrityError, match="backfilled"):
            ExecutionSnapshotStore().write_run(
                session,
                scope=owner,
                run_id=run["id"],
                workflow_key="harness.canary",
                workflow_version=1,
                workflow_definition_sha256=policy.workflow_definition_sha256,
                definition_binding_sha256=binding.binding_sha256,
                policy_snapshot=policy,
            )


def test_forged_policy_hash_is_rejected(app, owner):
    run, binding = _run(app, owner)
    policy = _policy(app, run, binding)
    store = ExecutionSnapshotStore()
    with session_scope() as session, pytest.raises(SnapshotIntegrityError):
        store.write_run(
            session,
            scope=owner,
            run_id=run["id"],
            workflow_key="harness.canary",
            workflow_version=1,
            workflow_definition_sha256=policy.workflow_definition_sha256,
            definition_binding_sha256=binding.binding_sha256,
            policy_snapshot=policy,
            policy_snapshot_sha256="0" * 64,
        )


def test_capability_attempt_shape_is_strict_and_legacy_attempt_fails_closed(app, owner):
    run, binding = _run(app, owner)
    policy = _policy(app, run, binding)
    store = ExecutionSnapshotStore()
    with session_scope() as session:
        store.write_run(
            session,
            scope=owner,
            run_id=run["id"],
            workflow_key="harness.canary",
            workflow_version=1,
            workflow_definition_sha256=policy.workflow_definition_sha256,
            definition_binding_sha256=binding.binding_sha256,
            policy_snapshot=policy,
        )
        _activate(app, session, owner, run)
        claimed = app.dispatcher.claim_due(session, now_us=app.clock.utc_epoch_us(), limit=1)
        assert claimed is not None
        assert store.read_attempt(
            session, scope=owner, attempt_id=claimed.attempt_id
        ) is None
        with pytest.raises(MissingExecutionSnapshotError):
            store.read_attempt(
                session,
                scope=owner,
                attempt_id=claimed.attempt_id,
                require_for_execution=True,
            )
        cap = app.registry.require_capability("canary.noop@1")
        result = store.write_attempt(
            session,
            scope=owner,
            attempt_id=claimed.attempt_id,
            run_id=run["id"],
            step_id=claimed.step_id,
            attempt_no=claimed.attempt_no,
            lease_owner=claimed.lease_owner,
            definition_binding_sha256=binding.binding_sha256,
            run_policy_sha256=policy.policy_hash(),
            executor_kind="capability",
            executor_identity=cap.identity(),
            executor_capability_key=cap.capability_key,
            executor_capability_version=cap.version,
            executor_capability_definition_sha256=cap.definition_hash(),
        )
        assert result.runtime_kind == "deterministic"
        assert store.read_attempt(session, scope=owner, attempt_id=claimed.attempt_id) == result


def test_role_attempt_is_cross_bound_to_policy_profile_and_route(app, owner):
    run, binding = _run(app, owner, mode="agent")
    policy = _policy(app, run, binding)
    role_binding = policy.role_bindings[0]
    role = role_binding.role_definition
    profile = role_binding.model_profile_definition
    store = ExecutionSnapshotStore()
    attempt_id = uuid.uuid4().hex

    with session_scope() as session:
        store.write_run(
            session,
            scope=owner,
            run_id=run["id"],
            workflow_key="harness.canary",
            workflow_version=1,
            workflow_definition_sha256=policy.workflow_definition_sha256,
            definition_binding_sha256=binding.binding_sha256,
            policy_snapshot=policy,
        )
        _activate(app, session, owner, run)
        step = (
            session.execute(
                select(steps).where(
                    steps.c.run_id == run["id"],
                    steps.c.definition_step_key == "actor_turn",
                )
            )
            .mappings()
            .one()
        )
        session.execute(
            steps.update()
            .where(steps.c.id == step["id"])
            .values(state="leased", lease_owner="snapshot-test")
        )
        session.execute(
            attempts.insert().values(
                id=attempt_id,
                step_id=step["id"],
                run_id=run["id"],
                scope_type=owner.scope_type.value,
                scope_id=owner.scope_id,
                attempt_no=1,
                state="leased",
                worker_id="snapshot-test",
                lease_owner="snapshot-test",
                started_at=app.clock.utc_epoch_us(),
                heartbeat_at=app.clock.utc_epoch_us(),
            )
        )
        result = store.write_attempt(
            session,
            scope=owner,
            attempt_id=attempt_id,
            run_id=run["id"],
            step_id=step["id"],
            attempt_no=1,
            lease_owner="snapshot-test",
            definition_binding_sha256=binding.binding_sha256,
            run_policy_sha256=policy.policy_hash(),
            executor_kind="role",
            executor_identity=role_binding.role_identity,
            executor_role_key=role.role_key,
            executor_role_version=role.version,
            executor_role_definition_sha256=role_binding.role_definition_sha256,
            model_profile_identity=role_binding.model_profile_identity,
            model_profile_key=profile.profile_key,
            model_profile_version=profile.version,
            model_profile_sha256=role_binding.model_profile_sha256,
            model_route_key=role_binding.model_route_identity,
            model_route_sha256=role_binding.model_route_sha256,
            provider=role_binding.provider,
            model=role_binding.model,
            usage_source=role_binding.usage_source.value,
        )
        assert result.runtime_kind == "in_process_fake"
        assert result.provider == role_binding.provider
        assert store.read_attempt(session, scope=owner, attempt_id=attempt_id) == result


def test_attempt_rejects_a_step_tampered_after_claim(app, owner):
    run, binding = _run(app, owner)
    policy = _policy(app, run, binding)
    store = ExecutionSnapshotStore()
    with session_scope() as session:
        store.write_run(
            session,
            scope=owner,
            run_id=run["id"],
            workflow_key="harness.canary",
            workflow_version=1,
            workflow_definition_sha256=policy.workflow_definition_sha256,
            definition_binding_sha256=binding.binding_sha256,
            policy_snapshot=policy,
        )
        _activate(app, session, owner, run)
        claimed = app.dispatcher.claim_due(session, now_us=app.clock.utc_epoch_us(), limit=1)
        assert claimed is not None
        session.execute(
            steps.update().where(steps.c.id == claimed.step_id).values(timeout_seconds="999")
        )
        cap = app.registry.require_capability("canary.noop@1")
        with pytest.raises(SnapshotIntegrityError, match="timeout"):
            store.write_attempt(
                session,
                scope=owner,
                attempt_id=claimed.attempt_id,
                run_id=run["id"],
                step_id=claimed.step_id,
                attempt_no=claimed.attempt_no,
                lease_owner=claimed.lease_owner,
                definition_binding_sha256=binding.binding_sha256,
                run_policy_sha256=policy.policy_hash(),
                executor_kind="capability",
                executor_identity=cap.identity(),
                executor_capability_key=cap.capability_key,
                executor_capability_version=cap.version,
                executor_capability_definition_sha256=cap.definition_hash(),
            )


def test_terminal_attempt_cannot_receive_a_late_snapshot(app, owner):
    run, binding = _run(app, owner)
    policy = _policy(app, run, binding)
    store = ExecutionSnapshotStore()
    with session_scope() as session:
        store.write_run(
            session,
            scope=owner,
            run_id=run["id"],
            workflow_key="harness.canary",
            workflow_version=1,
            workflow_definition_sha256=policy.workflow_definition_sha256,
            definition_binding_sha256=binding.binding_sha256,
            policy_snapshot=policy,
        )
        _activate(app, session, owner, run)
        claimed = app.dispatcher.claim_due(session, now_us=app.clock.utc_epoch_us(), limit=1)
        assert claimed is not None
        session.execute(
            attempts.update()
            .where(attempts.c.id == claimed.attempt_id)
            .values(state="failed")
        )
        cap = app.registry.require_capability("canary.noop@1")
        with pytest.raises(SnapshotIntegrityError, match="active claim owner"):
            store.write_attempt(
                session,
                scope=owner,
                attempt_id=claimed.attempt_id,
                run_id=run["id"],
                step_id=claimed.step_id,
                attempt_no=claimed.attempt_no,
                lease_owner=claimed.lease_owner,
                definition_binding_sha256=binding.binding_sha256,
                run_policy_sha256=policy.policy_hash(),
                executor_kind="capability",
                executor_identity=cap.identity(),
                executor_capability_key=cap.capability_key,
                executor_capability_version=cap.version,
                executor_capability_definition_sha256=cap.definition_hash(),
            )
