"""Contract tests for the immutable Run/Attempt execution snapshot seam."""

from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from pharos.db.session import session_scope
from pharos.harness.configrev import bootstrap_snapshot, decode_snapshot_payload
from pharos.harness.contracts import IdempotencyConflictError, NotFoundError, StepState
from pharos.harness.execution_snapshots import (
    ExecutionSnapshotStore,
    MissingExecutionSnapshotError,
    SnapshotConflictError,
    SnapshotIntegrityError,
)
from pharos.harness.policy_builder import build_run_policy
from pharos.harness.policy_snapshot import AgentLimits, RunPolicySnapshot
from pharos.harness.registry import CompiledWorkflowBinding
from pharos.harness.repository import (
    HarnessDefinitionRepository,
    HarnessRunRepository,
    now_iso,
)
from pharos.harness.tables import attempts, runs, steps
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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


def test_unvalidated_policy_model_copy_is_rejected(app, owner):
    run, binding = _run(app, owner)
    policy = _policy(app, run, binding)
    forged_gates = policy.creation_gates.model_copy(update={"experiments_enabled": True})
    forged = policy.model_copy(update={"creation_gates": forged_gates})
    with session_scope() as session, pytest.raises(
        SnapshotIntegrityError, match="invalid RunPolicySnapshot"
    ):
        ExecutionSnapshotStore().write_run(
            session,
            scope=owner,
            run_id=run["id"],
            workflow_key="harness.canary",
            workflow_version=1,
            workflow_definition_sha256=policy.workflow_definition_sha256,
            definition_binding_sha256=binding.binding_sha256,
            policy_snapshot=forged,
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
        claimed_snapshot = store.read_attempt(
            session, scope=owner, attempt_id=claimed.attempt_id, require_for_execution=True
        )
        assert claimed_snapshot is not None
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
        assert result == claimed_snapshot


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


def test_attempt_step_identity_is_frozen_after_claim(app, owner):
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
        with pytest.raises(IntegrityError, match="frozen by attempt snapshot"):
            session.execute(
                steps.update()
                .where(steps.c.id == claimed.step_id)
                .values(timeout_seconds="999")
            )


def test_terminal_attempt_keeps_its_immutable_snapshot(app, owner):
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
        assert result == store.read_attempt(
            session, scope=owner, attempt_id=claimed.attempt_id, require_for_execution=True
        )


def test_create_run_persists_snapshot_and_replays_without_reactivation(app, owner):
    enable_canary(app, agent_steps=True)
    first = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input={"mode": "success", "note": "app transaction"},
        idempotency_key="app-snapshot-1",
        initiator="user",
    )
    with session_scope() as session:
        snapshot = ExecutionSnapshotStore().read_run(
            session, scope=owner, run_id=first["id"], require_for_execution=True
        )
        assert snapshot is not None
        step_count = session.execute(
            select(steps.c.id).where(steps.c.run_id == first["id"])
        ).fetchall()
    replay = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input={"mode": "success", "note": "app transaction"},
        idempotency_key="app-snapshot-1",
        initiator="user",
    )
    assert replay["id"] == first["id"]
    with session_scope() as session:
        assert len(
            session.execute(select(steps.c.id).where(steps.c.run_id == first["id"])).fetchall()
        ) == len(step_count)


def test_create_run_uses_one_expansion_for_policy_and_persistence(app, owner):
    enable_canary(app, agent_steps=True)
    identity = "harness.canary@1"
    original = app.executor.expanders[identity]
    calls = 0

    def counted(input: dict) -> list[dict]:
        nonlocal calls
        calls += 1
        return original(input)

    app.executor.expanders[identity] = counted
    app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input={"mode": "success", "note": "single expansion"},
        idempotency_key="single-expansion",
        initiator="user",
    )
    assert calls == 1


def test_create_run_conflicting_input_is_rejected_by_atomic_insert(app, owner):
    enable_canary(app, agent_steps=True)
    app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input={"mode": "success", "note": "first"},
        idempotency_key="app-conflict-1",
        initiator="user",
    )
    with pytest.raises(IdempotencyConflictError):
        app.create_run(
            scope=owner,
            workflow_key="harness.canary",
            input={"mode": "success", "note": "different"},
            idempotency_key="app-conflict-1",
            initiator="user",
        )


def test_idempotent_replay_does_not_require_the_current_route_to_remain_active(app, owner):
    enable_canary(app, agent_steps=True)
    request = {
        "scope": owner,
        "workflow_key": "harness.canary",
        "input": {"mode": "success", "note": "replay after disable"},
        "idempotency_key": "replay-after-disable",
        "initiator": "user",
    }
    first = app.create_run(**request)
    with session_scope() as session:
        head = app.config_service.current_validated(session)
        assert head is not None
        app.config_service.apply(
            session,
            snapshot=bootstrap_snapshot(
                app.registry, actor="test-operator", reason="disable before replay"
            ),
            expected_head_revision=head.revision_id,
            actor="test-operator",
            reason="disable before replay",
            now=now_iso(),
        )
    assert app.create_run(**request)["id"] == first["id"]
    with pytest.raises(IdempotencyConflictError):
        app.create_run(**{**request, "input": {"mode": "success", "note": "changed"}})


def test_legacy_idempotent_replay_is_not_backfilled_or_activated(app, owner):
    enable_canary(app, agent_steps=True)
    workflow = app.registry.require_workflow("harness.canary@1")
    with session_scope() as session:
        current = app.config_service.current_validated(session)
        assert current is not None
        legacy = HarnessRunRepository().create(
            session,
            scope=owner,
            workflow=workflow,
            config_revision_id=current.revision_id,
            input={"mode": "success", "note": "legacy"},
            idempotency_key="legacy-replay-1",
            initiator="user",
            now_us=app.clock.utc_epoch_us(),
        )
    replay = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input={"mode": "success", "note": "legacy"},
        idempotency_key="legacy-replay-1",
        initiator="user",
    )
    assert replay["id"] == legacy["id"]
    with session_scope() as session:
        assert ExecutionSnapshotStore().read_run(
            session, scope=owner, run_id=legacy["id"]
        ) is None
        assert (
            session.execute(select(steps.c.id).where(steps.c.run_id == legacy["id"])).first()
            is None
        )


def test_create_run_snapshot_failure_rolls_back_run_and_steps(app, owner, monkeypatch):
    enable_canary(app, agent_steps=True)

    def fail(*args, **kwargs):  # noqa: ANN002, ANN003
        raise SnapshotIntegrityError("test snapshot failure")

    monkeypatch.setattr(app.execution_snapshots, "write_run", fail)
    with pytest.raises(SnapshotIntegrityError, match="test snapshot failure"):
        app.create_run(
            scope=owner,
            workflow_key="harness.canary",
            input={"mode": "success", "note": "rollback"},
            idempotency_key="snapshot-rollback-1",
            initiator="user",
        )
    with session_scope() as session:
        assert session.execute(
            select(runs.c.id).where(
                runs.c.scope_type == owner.scope_type.value,
                runs.c.scope_id == owner.scope_id,
                runs.c.idempotency_key == "snapshot-rollback-1",
            )
        ).first() is None
        assert session.execute(
            select(steps.c.id).where(steps.c.definition_step_key == "start")
        ).first() is None


def test_create_once_has_one_idempotency_winner_under_concurrency(app, owner):
    enable_canary(app, agent_steps=True)
    workflow = app.registry.require_workflow("harness.canary@1")
    with session_scope() as session:
        current = app.config_service.current_validated(session)
        assert current is not None
        revision_id = current.revision_id
    barrier = threading.Barrier(2)

    def create() -> tuple[str, bool]:
        barrier.wait()
        with session_scope() as session:
            row, created = HarnessRunRepository().create_once(
                session,
                scope=owner,
                workflow=workflow,
                config_revision_id=revision_id,
                input={"mode": "success", "note": "race"},
                idempotency_key="concurrent-create-once",
                initiator="user",
                now_us=app.clock.utc_epoch_us(),
            )
            return row["id"], created

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(), range(2)))
    assert {run_id for run_id, _ in results} == {results[0][0]}
    assert sorted(created for _, created in results) == [False, True]


def test_create_run_concurrency_returns_one_fully_snapshotted_run(app, owner):
    enable_canary(app, agent_steps=True)
    barrier = threading.Barrier(2)

    def create() -> dict:
        barrier.wait(timeout=5)
        return app.create_run(
            scope=owner,
            workflow_key="harness.canary",
            input={"mode": "success", "note": "full transaction race"},
            idempotency_key="concurrent-create-run",
            initiator="user",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(), range(2)))
    assert {run["id"] for run in results} == {results[0]["id"]}
    with session_scope() as session:
        snapshot = ExecutionSnapshotStore().read_run(
            session, scope=owner, run_id=results[0]["id"], require_for_execution=True
        )
        assert snapshot is not None
        assert len(
            session.execute(
                select(steps.c.id).where(steps.c.run_id == results[0]["id"])
            ).fetchall()
        ) == 4
