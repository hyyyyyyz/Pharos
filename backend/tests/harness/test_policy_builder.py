"""Policy assembly tests: no live registry/config state is consulted."""

from __future__ import annotations

import pytest
from pharos.harness.app import HarnessApp
from pharos.harness.configrev import HarnessConfigSnapshot, WorkflowRoute
from pharos.harness.contracts import (
    ActivationState,
    DefinitionError,
    ExecutionMode,
    PolicyDeniedError,
)
from pharos.harness.definitions import (
    BudgetSpec,
    ModelProfileDefinition,
    ModelRouteDefinition,
    RoleDefinition,
    StepDefinition,
    WorkflowDefinition,
)
from pharos.harness.policy_builder import build_run_policy
from pharos.harness.policy_snapshot import AgentLimits
from pharos.harness.registry import CompiledWorkflowBinding, Registry


def _gates() -> dict[str, bool]:
    return {
        "harness_enabled": True,
        "dispatcher_enabled": True,
        "canary_enabled": True,
        "agent_steps_enabled": True,
        "agent_runtime_enabled": True,
        "domain_publish_enabled": True,
        "fulltext_enabled": False,
        "desktop_bridge_enabled": False,
        "experiments_enabled": False,
    }


def _registry(*, selection_policy: str = "fixed") -> Registry:
    registry = Registry()
    routes: tuple[ModelRouteDefinition, ...] = (
        ModelRouteDefinition(
            route_key="primary",
            priority=1,
            provider="provider-a",
            model="model-a",
            usage_source="official",
            credential_policy="system_managed",
            allowed_runtime_kinds=("dsh",),
            max_output_tokens=200,
        ),
    )
    if selection_policy == "ordered_first_available":
        routes += (
            ModelRouteDefinition(
                route_key="backup",
                priority=2,
                provider="provider-b",
                model="model-b",
                usage_source="byok",
                credential_policy="user_byok",
                allowed_runtime_kinds=("dsh",),
                max_output_tokens=100,
            ),
        )
    registry.register_model_profile(
        ModelProfileDefinition(
            profile_key="test-profile",
            version=1,
            selection_policy=selection_policy,  # type: ignore[arg-type]
            routes=routes,
        )
    )
    registry.register_role(
        RoleDefinition(
            role_key="actor",
            version=1,
            prompt_template_version="test.prompt@1",
            input_schema="test.input@1",
            output_schema="test.output@1",
            model_profile="test-profile@1",
            runtime_kind="dsh",
            max_turns=3,
            max_tool_calls=2,
            token_budget=BudgetSpec(output_tokens=150),
        )
    )
    registry.register(
        WorkflowDefinition(
            workflow_key="test.workflow",
            version=1,
            input_schema="test.input@1",
            output_schema="test.output@1",
            internal_no_legacy_writer=True,
            steps=(StepDefinition(key="run", kind="agent", role="actor@1"),),
        )
    )
    registry.compile()
    return registry


def _config(
    *,
    state: ActivationState = ActivationState.active,
    version: int | None = 1,
    mode: ExecutionMode | None = ExecutionMode.harness,
    gates: dict[str, bool] | None = None,
):
    return HarnessConfigSnapshot(
        gates=gates or _gates(),
        routes=(
            WorkflowRoute(
                workflow_key="test.workflow",
                active_version=version,
                activation_state=state,
                execution_mode=mode,
            ),
        ),
    )


def _limits() -> AgentLimits:
    return AgentLimits(
        max_turns=4,
        max_tool_calls=4,
        max_input_chars=10_000,
        max_output_tokens=10_000,
    )


def _build(registry: Registry, config: HarnessConfigSnapshot | None = None, **kwargs):
    binding = registry.compile_workflow_binding("test.workflow@1")
    return build_run_policy(
        binding,
        config or _config(),
        config_revision_id="revision-1",
        config_revision_sha256="a" * 64,
        agent_limits=_limits(),
        entitlement={
            "entitlement_key": "researcher",
            "entitlement_revision": 3,
            "decision": "allow",
        },
        **kwargs,
    )


def test_builder_derives_budget_parallel_and_route_accounting() -> None:
    policy = _build(_registry())
    assert policy.effective_budget == policy.effective_budget.from_budget(
        {**policy.effective_budget.model_dump(mode="python")}
    )
    assert policy.max_parallel_steps == 4
    assert policy.role_bindings[0].provider == "provider-a"
    assert policy.role_bindings[0].model == "model-a"
    assert policy.role_bindings[0].usage_source.value == "official"
    assert policy.role_bindings[0].model_route_sha256 == (
        policy.role_bindings[0].model_profile_definition.route_hash("primary")
    )
    assert policy.canonical_json() == policy.canonical_json()


@pytest.mark.parametrize(
    "config",
    [
        _config(state=ActivationState.disabled),
        _config(version=2),
        _config(mode=ExecutionMode.shadow),
    ],
)
def test_builder_requires_active_matching_route(config: HarnessConfigSnapshot) -> None:
    with pytest.raises(PolicyDeniedError):
        _build(_registry(), config)


def test_builder_requires_master_and_agent_runtime_gates() -> None:
    master_off = _gates()
    master_off.update(
        harness_enabled=False,
        dispatcher_enabled=False,
        canary_enabled=False,
        agent_steps_enabled=False,
        agent_runtime_enabled=False,
        domain_publish_enabled=False,
    )
    with pytest.raises(PolicyDeniedError, match="harness and dispatcher"):
        _build(_registry(), _config(gates=master_off))

    runtime_off = _gates()
    runtime_off["agent_runtime_enabled"] = False
    with pytest.raises(PolicyDeniedError, match="agent_runtime_enabled"):
        _build(_registry(), _config(gates=runtime_off))


def test_ordered_fallback_requires_explicit_availability_and_is_deterministic() -> None:
    registry = _registry(selection_policy="ordered_first_available")
    with pytest.raises(DefinitionError, match="route"):
        _build(registry)
    first = _build(registry, available_route_keys={"actor@1": {"backup", "primary"}})
    second = _build(registry, available_route_keys={"actor@1": {"primary", "backup"}})
    assert first.role_bindings[0].model == "model-a"
    assert first.canonical_json() == second.canonical_json()


def test_forged_binding_is_reauthenticated() -> None:
    registry = _registry()
    binding = registry.compile_workflow_binding("test.workflow@1")
    forged = CompiledWorkflowBinding.__new__(CompiledWorkflowBinding)
    object.__setattr__(forged, "value", binding.value)
    object.__setattr__(forged, "binding_sha256", "0" * 64)
    with pytest.raises(DefinitionError, match="authentication"):
        build_run_policy(
            forged,
            _config(),
            config_revision_id="revision-1",
            config_revision_sha256="a" * 64,
            agent_limits=_limits(),
            entitlement={
                "entitlement_key": "researcher",
                "entitlement_revision": 3,
                "decision": "allow",
            },
        )


def test_role_limits_are_exact_and_bounded() -> None:
    registry = _registry()
    with pytest.raises(ValueError, match="exactly"):
        _build(registry, role_limits=())
    with pytest.raises(PolicyDeniedError, match="max_turns"):
        _build(
            registry,
            role_limits={
                "actor@1": {
                    "max_turns": 4,
                    "max_tool_calls": 1,
                    "max_input_chars": 10_000,
                    "max_output_tokens": 100,
                }
            },
        )


def test_business_workflow_without_entitlement_fails_closed() -> None:
    registry = _registry()
    binding = registry.compile_workflow_binding("test.workflow@1")
    with pytest.raises(PolicyDeniedError, match="explicit.*entitlement"):
        build_run_policy(
            binding,
            _config(),
            config_revision_id="revision-1",
            config_revision_sha256="a" * 64,
            agent_limits=_limits(),
        )


@pytest.mark.parametrize(
    ("identity", "version", "model"),
    [
        ("harness.canary@1", 1, "canary"),
        ("harness.canary@2", 2, "pharos-fake-canary"),
    ],
)
def test_legacy_and_dsh_canary_bindings_use_internal_entitlement(
    identity: str, version: int, model: str
) -> None:
    app = HarnessApp()
    binding = app.registry.compile_workflow_binding(identity)
    config = HarnessConfigSnapshot(
        gates=_gates(),
        routes=(
            WorkflowRoute(
                workflow_key="harness.canary",
                active_version=version,
                activation_state=ActivationState.active,
                execution_mode=None,
            ),
        ),
    )
    policy = build_run_policy(
        binding,
        config,
        config_revision_id="revision-1",
        config_revision_sha256="a" * 64,
        agent_limits=_limits(),
    )
    assert policy.entitlement.entitlement_key == "harness.canary.internal"
    assert policy.role_bindings[0].model == model
