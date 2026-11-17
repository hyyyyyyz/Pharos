"""Owner, gate and delivery fences for the DB-bound DSH adapter."""

from __future__ import annotations

import json

import pytest
from pharos.db.session import session_scope
from pharos.harness.configrev import HarnessConfigSnapshot, WorkflowRoute
from pharos.harness.contracts import ActivationState, DeliveryState
from pharos.harness.dsh_gateway import DshLaunch
from pharos.harness.dsh_persistence import DshPersistenceAdapter, DshPersistenceError
from pharos.harness.execution_snapshots import ExecutionSnapshotStore
from pharos.harness.model_gateway import AttemptContext
from pharos.harness.policy_builder import build_run_policy
from pharos.harness.policy_snapshot import AgentLimits
from pharos.harness.repository import HarnessDefinitionRepository, now_iso, sha256_text
from pharos.harness.tables import attempts, config_head, runs, steps

SHA = "a" * 64
_SEEDED_CONTEXT: dict[str, object] = {}


def _context(attempt_id: str = "attempt-1", **updates: object) -> AttemptContext:
    values: dict[str, object] = {
        "run_id": "run-1",
        "step_id": "step-1",
        "attempt_id": attempt_id,
        "attempt_no": 1,
        "scope_type": "user",
        "scope_id": "owner",
        "lease_owner": "worker-a",
        "workflow_key": "harness.canary",
        "workflow_version": 2,
        "workflow_definition_sha256": SHA,
        "definition_binding_sha256": SHA,
        "run_policy_sha256": SHA,
        "role": "canary_dsh_actor@1",
        "runtime_kind": "dsh",
        "role_definition_sha256": SHA,
        "model_profile_identity": "pharos-fake-canary@1",
        "model_profile_sha256": SHA,
        "model_route_key": "pharos-fake-canary-dsh",
        "model_route_sha256": SHA,
        "usage_source": "system_shared",
        "input_sha256": SHA,
        "deadline_at_us": 1_700_000_001_000_000,
        "provider": "pharos-fake",
        "model": "pharos-fake-canary",
    }
    values.update(_SEEDED_CONTEXT)
    values.update(updates)
    return AttemptContext(**values)  # type: ignore[arg-type]


def _launch(context: AttemptContext) -> DshLaunch:
    return DshLaunch(
        runtime_session_id=context.attempt_id,
        deadline_at=context.deadline_at_us,
        upstream_commit="d" * 40,
        runtime_hash="b" * 64,
        profile_hash="c" * 64,
        policy_hash="e" * 64,
    )


def _seed(app, session, *, state: str = "running") -> None:
    workflow = app.registry.require_workflow("harness.canary@2")
    input_value = {"mode": "agent"}
    input_json = json.dumps(input_value, separators=(",", ":"), sort_keys=True)
    revision = session.execute(config_head.select()).mappings().one()["current_revision_id"]
    binding = HarnessDefinitionRepository().persist_workflow_binding(
        session, registry=app.registry, workflow=workflow, now="2026-08-30T00:00:00+00:00"
    )
    current = app.config_service.current_validated(session)
    assert current is not None
    policy = build_run_policy(
        binding,
        current.snapshot,
        config_revision_id=current.revision_id,
        config_revision_sha256=current.snapshot_sha256,
        agent_limits=AgentLimits(
            max_turns=4, max_tool_calls=8, max_input_chars=200_000, max_output_tokens=60_000
        ),
    )
    actor_role = policy.role_bindings[0]
    profile = actor_role.model_profile_definition
    session.execute(
        runs.insert().values(
            id="run-1", scope_type="user", scope_id="owner", user_id=None,
            workflow_key="harness.canary", workflow_version=2,
            definition_sha256=workflow.definition_hash(), config_revision_id=revision,
            state="queued", input_json=input_json, input_sha256=sha256_text(input_json),
            budget_json=None, initiator="user", idempotency_key="key", created_at=1, updated_at=1,
        )
    )
    store = ExecutionSnapshotStore()
    store.write_run(
        session, scope="user", scope_id="owner", run_id="run-1",
        workflow_key=workflow.workflow_key, workflow_version=workflow.version,
        workflow_definition_sha256=workflow.definition_hash(),
        definition_binding_sha256=binding.binding_sha256, policy_snapshot=policy,
    )
    from pharos.harness.workflows.canary import expand_dsh

    actor_step = next(
        item
        for item in expand_dsh(input_value)
        if item["definition_step_key"] == "actor_turn"
    )
    session.execute(
        steps.insert().values(
            id="step-1", run_id="run-1", scope_type="user", scope_id="owner",
            definition_step_key=actor_step["definition_step_key"],
            instance_key=actor_step["instance_key"], step_kind=actor_step["step_kind"],
            definition_json=json.dumps(
                actor_step["definition"], separators=(",", ":"), sort_keys=True
            ),
            state="leased",
            depends_on_json=json.dumps(actor_step["depends_on"], separators=(",", ":")),
            input_artifact_ids_json="[]", attempt_count=1,
            max_attempts=actor_step["max_attempts"], lease_owner="worker-a",
            lease_expires_at=10_000,
            fan_in=actor_step["fan_in"], min_success_count=actor_step["min_success_count"],
            timeout_seconds=actor_step["timeout_seconds"], retry_policy_json=None,
            heartbeat_at=1, created_at=1, updated_at=1,
        )
    )
    session.execute(
        attempts.insert().values(
            id="attempt-1", step_id="step-1", run_id="run-1", scope_type="user", scope_id="owner",
            attempt_no=1, worker_id="worker-a", state="leased", lease_owner="worker-a",
            started_at=1, heartbeat_at=1,
        )
    )
    store.write_attempt(
        session, scope="user", scope_id="owner", attempt_id="attempt-1", run_id="run-1",
        step_id="step-1", attempt_no=1, lease_owner="worker-a",
        definition_binding_sha256=binding.binding_sha256, run_policy_sha256=policy.policy_hash(),
        executor_kind="role", executor_identity=actor_role.role_identity,
        executor_role_key=actor_role.role_definition.role_key,
        executor_role_version=actor_role.role_definition.version,
        executor_role_definition_sha256=actor_role.role_definition_sha256,
        model_profile_identity=actor_role.model_profile_identity,
        model_profile_key=profile.profile_key, model_profile_version=profile.version,
        model_profile_sha256=actor_role.model_profile_sha256,
        model_route_key=actor_role.model_route_identity,
        model_route_sha256=actor_role.model_route_sha256, provider=actor_role.provider,
        model=actor_role.model, usage_source=actor_role.usage_source.value,
    )
    session.execute(steps.update().where(steps.c.id == "step-1").values(state=state))
    session.execute(attempts.update().where(attempts.c.id == "attempt-1").values(state=state))
    _SEEDED_CONTEXT.update(
        workflow_definition_sha256=workflow.definition_hash(),
        definition_binding_sha256=binding.binding_sha256,
        run_policy_sha256=policy.policy_hash(), role=actor_role.role_identity,
        role_definition_sha256=actor_role.role_definition_sha256, runtime_kind="dsh",
        model_profile_identity=actor_role.model_profile_identity,
        model_profile_sha256=actor_role.model_profile_sha256,
        model_route_key=actor_role.model_route_identity,
        model_route_sha256=actor_role.model_route_sha256,
        usage_source=actor_role.usage_source.value, provider=actor_role.provider,
        model=actor_role.model, input_sha256=sha256_text(input_json),
        reasoning_effort=actor_role.route.reasoning_effort,
        max_output_tokens=actor_role.route.max_output_tokens,
        max_input_tokens=actor_role.role_definition.token_budget.input_tokens,
        deadline_at_us=2 + int(float(actor_step["timeout_seconds"]) * 1_000_000),
    )


def _adapter(app, clock) -> DshPersistenceAdapter:
    import pharos.db.session as dbs

    assert dbs._SessionLocal is not None
    return DshPersistenceAdapter(
        dbs._SessionLocal, config_service=app.config_service, clock=clock
    )


def _enable_dsh(app) -> None:
    with session_scope() as session:
        head = app.config_service.current(session)
        snapshot = HarnessConfigSnapshot(
            gates={
                "harness_enabled": True, "dispatcher_enabled": True, "canary_enabled": True,
                "agent_steps_enabled": True, "agent_runtime_enabled": True,
                "domain_publish_enabled": False, "fulltext_enabled": False,
                "desktop_bridge_enabled": False, "experiments_enabled": False,
            },
            routes=(WorkflowRoute(workflow_key="harness.canary", active_version=2,
                                  activation_state=ActivationState.active, execution_mode=None),),
            actor="test", reason="dsh persistence test",
        )
        app.config_service.apply(session, snapshot=snapshot,
                                 expected_head_revision=head["current_revision_id"],
                                 actor="test", reason="dsh persistence test", now=now_iso())


def test_reserve_attach_and_delivery_write_only_expected_rows(app, clock):
    _enable_dsh(app)
    with session_scope() as session:
        _seed(app, session)
    adapter = _adapter(app, clock)
    clock.set(2)
    context = _context()
    adapter.reserve_launch(context, _launch(context))
    adapter.attach_pid(context, 1234)
    assert adapter.observe_delivery(context, DeliveryState.SENT) is True
    assert adapter.observe_delivery(context, DeliveryState.ACKNOWLEDGED) is True
    with session_scope() as session:
        row = (
            session.execute(attempts.select().where(attempts.c.id == "attempt-1"))
            .mappings()
            .one()
        )
        assert row["runtime_session_id"] == context.attempt_id
        assert row["child_pid"] == 1234
        assert row["delivery_state"] == "acknowledged"


def test_gate_cut_race_and_wrong_identity_fail_without_raw_error(app, clock):
    _enable_dsh(app)
    with session_scope() as session:
        _seed(app, session)
    adapter = _adapter(app, clock)
    clock.set(2)
    context = _context()
    with session_scope() as session:
        head = app.config_service.current(session)
        assert head is not None
        app.config_service.rollback(session, actor="test", reason="cut", now=now_iso())
    with pytest.raises(DshPersistenceError, match="^DSH persistence operation failed$"):
        adapter.reserve_launch(context, _launch(context))
    assert all(value is None for value in _rows(app, "runtime_session_id"))

    _enable_dsh(app)
    with pytest.raises(DshPersistenceError) as error:
        adapter.reserve_launch(_context(lease_owner="wrong-owner"), _launch(context))
    assert str(error.value) == "DSH persistence operation failed"


@pytest.mark.parametrize(
    "updates",
    [
        {"scope_id": "other-owner"},
        {"run_id": "other-run"},
        {"step_id": "other-step"},
        {"attempt_no": 2},
        {"lease_owner": "worker-b"},
    ],
)
def test_reservation_requires_exact_owner_and_attempt_generation(app, clock, updates):
    _enable_dsh(app)
    with session_scope() as session:
        _seed(app, session)
    clock.set(2)
    adapter = _adapter(app, clock)
    context = _context(**updates)
    with pytest.raises(DshPersistenceError) as error:
        adapter.reserve_launch(context, _launch(context))
    assert str(error.value) == "DSH persistence operation failed"
    assert all(value is None for value in _rows(app, "runtime_session_id"))


def _rows(app, field: str) -> list[object]:
    with session_scope() as session:
        return [row[field] for row in session.execute(attempts.select()).mappings()]


def test_attach_requires_reservation_and_delivery_false_or_exception_is_sanitized(app, clock):
    _enable_dsh(app)
    with session_scope() as session:
        _seed(app, session)
    adapter = _adapter(app, clock)
    clock.set(2)
    context = _context()
    with pytest.raises(DshPersistenceError):
        adapter.attach_pid(context, 1234)
    adapter.reserve_launch(context, _launch(context))
    adapter.attach_pid(context, 1234)
    assert (
        adapter.observe_delivery(_context(lease_owner="stale-owner"), DeliveryState.SENT)
        is False
    )
    assert adapter.observe_delivery(context, DeliveryState.SENT) is True
    with pytest.raises(DshPersistenceError):
        adapter.observe_delivery(context, "not-a-delivery-state")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field",
    [
        "workflow_key",
        "workflow_version",
        "workflow_definition_sha256",
        "definition_binding_sha256",
        "run_policy_sha256",
        "role",
        "role_definition_sha256",
        "runtime_kind",
        "model_profile_identity",
        "model_profile_sha256",
        "model_route_key",
        "model_route_sha256",
        "usage_source",
        "provider",
        "model",
        "input_sha256",
        "reasoning_effort",
        "max_output_tokens",
        "max_input_tokens",
        "deadline_at_us",
    ],
)
def test_forged_context_is_rejected_before_launch_side_effects(app, clock, field):
    _enable_dsh(app)
    with session_scope() as session:
        _seed(app, session)
    adapter = _adapter(app, clock)
    clock.set(2)
    replacements: dict[str, object] = {
        "workflow_key": "other.workflow",
        "workflow_version": 1,
        "workflow_definition_sha256": "f" * 64,
        "definition_binding_sha256": "f" * 64,
        "run_policy_sha256": "f" * 64,
        "role": "other_role@1",
        "role_definition_sha256": "f" * 64,
        "runtime_kind": "in_process_fake",
        "model_profile_identity": "other-profile@1",
        "model_profile_sha256": "f" * 64,
        "model_route_key": "other-route",
        "model_route_sha256": "f" * 64,
        "usage_source": "official",
        "provider": "other-provider",
        "model": "other-model",
        "input_sha256": "f" * 64,
        "reasoning_effort": "high",
        "max_output_tokens": 1001,
        "max_input_tokens": 1001,
        "deadline_at_us": 30_000_003,
    }
    context = _context(**{field: replacements[field]})
    with pytest.raises(DshPersistenceError, match="^DSH persistence operation failed$"):
        adapter.reserve_launch(context, _launch(context))
    assert all(value is None for value in _rows(app, "runtime_session_id"))
