"""Build an immutable run policy from authenticated definition/config inputs.

This module is intentionally a pure assembly boundary.  It does not read the
database or a registry: callers pass the exact compiled binding and the live
configuration revision that authorized the run.  This keeps the policy
captured at creation time, while making accidental version/route drift fail
closed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, TypeAlias

from pydantic import ValidationError

from pharos.harness.configrev import HarnessConfigSnapshot
from pharos.harness.contracts import (
    ActivationState,
    DefinitionError,
    ExecutionMode,
    PolicyDeniedError,
)
from pharos.harness.definitions import (
    BudgetSpec,
    ModelProfileDefinition,
    RoleDefinition,
    WorkflowDefinition,
)
from pharos.harness.policy_snapshot import (
    AgentLimits,
    AgentRoleBinding,
    AgentRoleLimits,
    CreationGates,
    EffectiveBudget,
    EntitlementSnapshot,
    RunPolicySnapshot,
)
from pharos.harness.registry import CompiledWorkflowBinding

_INTERNAL_CANARY_ENTITLEMENT = EntitlementSnapshot(
    entitlement_key="harness.canary.internal",
    entitlement_revision=1,
    decision="allow",
)

RoleLimitInput: TypeAlias = (
    Mapping[str, Any]
    | Sequence[AgentRoleLimits | Mapping[str, Any]]
    | AgentRoleLimits
)
AvailabilityInput: TypeAlias = Mapping[str, Iterable[str]] | Iterable[str]


def _deny(message: str) -> PolicyDeniedError:
    return PolicyDeniedError(message)


def _authenticated_binding(binding: CompiledWorkflowBinding) -> CompiledWorkflowBinding:
    """Re-run the binding's authentication even for forged dataclass objects."""
    if not isinstance(binding, CompiledWorkflowBinding):
        raise DefinitionError("policy requires a CompiledWorkflowBinding")
    try:
        return CompiledWorkflowBinding(
            value=binding.value,
            binding_sha256=binding.binding_sha256,
        )
    except (AttributeError, DefinitionError, TypeError, ValueError) as exc:
        raise DefinitionError("compiled workflow binding failed policy authentication") from exc


def _definition_from_record(
    record: Mapping[str, Any], definition_type: type[Any], label: str
) -> Any:
    raw = record.get("definition")
    if not isinstance(raw, Mapping):
        raise DefinitionError(f"binding {label} definition is invalid")
    try:
        return definition_type.model_validate(raw)
    except (TypeError, ValueError, ValidationError) as exc:
        raise DefinitionError(f"binding {label} definition is invalid") from exc


def _workflow_from_binding(binding: CompiledWorkflowBinding) -> WorkflowDefinition:
    record = binding.workflow
    workflow = _definition_from_record(record, WorkflowDefinition, "workflow")
    if (
        record.get("identity") != workflow.identity()
        or record.get("workflow_key") != workflow.workflow_key
        or record.get("version") != workflow.version
        or record.get("definition_sha256") != workflow.definition_hash()
    ):
        raise DefinitionError("workflow identity/hash does not match compiled binding")
    return workflow


def _route_for(
    config: HarnessConfigSnapshot,
    workflow: WorkflowDefinition,
) -> Any:
    routes = [item for item in config.routes if item.workflow_key == workflow.workflow_key]
    if len(routes) != 1:
        raise _deny(
            f"workflow {workflow.workflow_key} must have exactly one config route"
        )
    route = routes[0]
    if route.activation_state is not ActivationState.active:
        raise _deny(f"workflow {workflow.identity()} route is not active")
    if route.active_version != workflow.version:
        raise _deny(
            f"workflow {workflow.identity()} route version is {route.active_version!r}"
        )
    if workflow.workflow_key == "harness.canary":
        if route.execution_mode is not None:
            raise _deny("harness.canary requires the internal execution mode")
    elif route.execution_mode is not ExecutionMode.harness:
        raise _deny("business workflow route must use harness execution mode")
    return route


def _availability_for_role(
    available_route_keys: AvailabilityInput | None,
    *,
    role_identity: str,
    profile_identity: str,
    required: bool,
) -> frozenset[str] | None:
    if available_route_keys is None:
        return None
    if isinstance(available_route_keys, Mapping):
        # A role-specific key is preferred.  A profile-specific key is useful
        # when several roles intentionally share one profile.  Neither path
        # guesses a route: the values are still an explicit caller set.
        if role_identity in available_route_keys:
            selected = available_route_keys[role_identity]
        elif profile_identity in available_route_keys:
            selected = available_route_keys[profile_identity]
        else:
            if not required:
                return None
            raise _deny(
                f"no explicit available route keys for role {role_identity}"
            )
    else:
        selected = available_route_keys
    if isinstance(selected, bytes):
        raise _deny("available route keys must be a collection of route keys")
    if isinstance(selected, str):
        selected = (selected,)
    try:
        result = frozenset(selected)
    except TypeError as exc:
        raise _deny("available route keys must be a finite collection of strings") from exc
    if any(not isinstance(item, str) for item in result):
        raise _deny("available route keys must contain strings")
    return result


def _limits_input(
    value: RoleLimitInput,
) -> dict[str, AgentRoleLimits]:
    if isinstance(value, AgentRoleLimits):
        values: Iterable[AgentRoleLimits | Mapping[str, Any]] = (value,)
    elif isinstance(value, Mapping):
        # The convenient mapping form is role identity -> limit object.  A
        # single limit mapping is deliberately not accepted: without a role
        # key it is too easy to apply the wrong cap.
        values = tuple(
            item if isinstance(item, AgentRoleLimits) else {**item, "role_identity": key}
            for key, item in value.items()
            if isinstance(item, (AgentRoleLimits, Mapping))
        )
        if len(tuple(value)) != len(tuple(values)):
            raise ValueError("role limits mapping values must be objects")
    else:
        values = value
    parsed: dict[str, AgentRoleLimits] = {}
    for item in values:
        limit = item if isinstance(item, AgentRoleLimits) else AgentRoleLimits.model_validate(item)
        if limit.role_identity in parsed:
            raise ValueError(f"duplicate role limit {limit.role_identity}")
        parsed[limit.role_identity] = limit
    return parsed


def _effective_budget(
    workflow: WorkflowDefinition,
    requested: EffectiveBudget | BudgetSpec | Mapping[str, Any] | None,
) -> EffectiveBudget:
    budget = EffectiveBudget.from_budget(
        requested if requested is not None else workflow.default_budget
    )
    maximum = EffectiveBudget.from_budget(workflow.default_budget)
    for field in (
        "wall_seconds",
        "model_calls",
        "input_tokens",
        "output_tokens",
        "max_tool_calls",
        "cost_micros",
    ):
        if getattr(budget, field) > getattr(maximum, field):
            raise _deny(f"effective budget {field} exceeds workflow bound")
    return budget


def _make_role_binding(
    role_record: Mapping[str, Any],
    *,
    available_route_keys: AvailabilityInput | None,
) -> tuple[AgentRoleBinding, RoleDefinition, Any]:
    role = _definition_from_record(role_record, RoleDefinition, "role")
    if role_record.get("identity") != role.identity():
        raise DefinitionError("role identity does not match embedded definition")
    if role_record.get("definition_sha256") != role.definition_hash():
        raise DefinitionError("role definition hash does not match embedded definition")
    profile_record = role_record.get("model_profile")
    if not isinstance(profile_record, Mapping):
        raise DefinitionError(f"role {role.identity()} has no model profile")
    profile = _definition_from_record(profile_record, ModelProfileDefinition, "model profile")
    if (
        profile_record.get("identity") != profile.identity()
        or profile_record.get("definition_sha256") != profile.definition_hash()
    ):
        raise DefinitionError("model profile identity/hash does not match embedded definition")
    from pharos.harness.registry import validate_role_model_profile

    try:
        validate_role_model_profile(role, profile)
        available = _availability_for_role(
            available_route_keys,
            role_identity=role.identity(),
            profile_identity=profile.identity(),
            required=profile.selection_policy == "ordered_first_available",
        )
        route = profile.resolve_route(role.runtime_kind, available_route_keys=available)
    except (DefinitionError, ValueError, PolicyDeniedError) as exc:
        raise DefinitionError(f"cannot resolve route for role {role.identity()}: {exc}") from exc

    binding = AgentRoleBinding.model_validate(
        {
            "role_identity": role.identity(),
            "role_definition_sha256": role.definition_hash(),
            "role_definition": role.canonical(),
            "model_profile_identity": profile.identity(),
            "model_profile_sha256": profile.definition_hash(),
            "model_profile_definition": profile.canonical(),
            "model_route_identity": route.route_key,
            "model_route_sha256": profile.route_hash(route.route_key),
        }
    )
    # A second decode is intentional: the object put into the snapshot has
    # passed the same embedded-definition checks as a restored snapshot.
    return AgentRoleBinding.model_validate(binding.model_dump(mode="python")), role, route


def _role_limits(
    roles: Sequence[tuple[AgentRoleBinding, RoleDefinition, Any]],
    agent_limits: AgentLimits,
    requested: RoleLimitInput | None,
) -> tuple[AgentRoleLimits, ...]:
    actual = {binding.role_identity for binding, _, _ in roles}
    if requested is None:
        explicit: dict[str, AgentRoleLimits] = {}
    else:
        explicit = _limits_input(requested)
        if set(explicit) != actual:
            raise ValueError("role limits must contain exactly the binding roles")

    result: list[AgentRoleLimits] = []
    for binding, role, route in roles:
        limit = explicit.get(binding.role_identity)
        route_output = route.max_output_tokens or 1_000_000_000
        if limit is None:
            limit = AgentRoleLimits(
                role_identity=binding.role_identity,
                max_turns=min(agent_limits.max_turns, role.max_turns),
                max_tool_calls=min(agent_limits.max_tool_calls, role.max_tool_calls),
                max_input_chars=agent_limits.max_input_chars,
                max_output_tokens=min(
                    agent_limits.max_output_tokens,
                    role.token_budget.output_tokens,
                    route_output,
                ),
            )
        else:
            if limit.max_turns > min(agent_limits.max_turns, role.max_turns):
                raise _deny(f"role {role.identity()} max_turns exceeds its bounds")
            if limit.max_tool_calls > min(agent_limits.max_tool_calls, role.max_tool_calls):
                raise _deny(f"role {role.identity()} max_tool_calls exceeds its bounds")
            if limit.max_input_chars > agent_limits.max_input_chars:
                raise _deny(f"role {role.identity()} max_input_chars exceeds its bounds")
            if limit.max_output_tokens > min(
                agent_limits.max_output_tokens, role.token_budget.output_tokens, route_output
            ):
                raise _deny(f"role {role.identity()} max_output_tokens exceeds its bounds")
        result.append(limit)
    return tuple(sorted(result, key=lambda item: item.role_identity))


def build_run_policy(
    binding: CompiledWorkflowBinding,
    config_snapshot: HarnessConfigSnapshot,
    *,
    config_revision_id: str,
    config_revision_sha256: str,
    workflow_identity: str | None = None,
    agent_limits: AgentLimits | Mapping[str, Any] | None = None,
    role_limits: RoleLimitInput | None = None,
    effective_budget: EffectiveBudget | BudgetSpec | Mapping[str, Any] | None = None,
    available_route_keys: AvailabilityInput | None = None,
    entitlement: EntitlementSnapshot | Mapping[str, Any] | None = None,
    selected_step_keys: Sequence[str] | None = None,
) -> RunPolicySnapshot:
    """Materialise one deterministic, canonical policy for a new run.

    ``config_revision_id`` and ``config_revision_sha256`` are deliberately
    required arguments.  The builder never invents them from a mutable or
    caller-ambiguous config object.
    """
    authenticated = _authenticated_binding(binding)
    workflow = _workflow_from_binding(authenticated)
    if workflow_identity is not None and workflow_identity != workflow.identity():
        raise DefinitionError("workflow identity does not match compiled binding")
    if selected_step_keys is None:
        selected_keys = tuple(step.key for step in workflow.steps)
    else:
        if isinstance(selected_step_keys, (str, bytes)):
            raise DefinitionError("selected_step_keys must be a sequence of exact step keys")
        selected_keys = tuple(selected_step_keys)
        if any(not isinstance(key, str) or not key for key in selected_keys):
            raise DefinitionError("selected_step_keys contains an invalid step key")
        if len(selected_keys) != len(set(selected_keys)):
            raise DefinitionError("selected_step_keys contains duplicates")
    workflow_steps = {step.key: step for step in workflow.steps}
    if not set(selected_keys) <= set(workflow_steps):
        raise DefinitionError("selected_step_keys contains a step outside the workflow")
    required_steps = {step.key for step in workflow.steps if not step.optional}
    if not required_steps <= set(selected_keys):
        raise DefinitionError("selected_step_keys omits a required workflow step")
    selected_steps = tuple(workflow_steps[key] for key in selected_keys)

    try:
        config = HarnessConfigSnapshot.model_validate(
            config_snapshot.model_dump(mode="python")
            if isinstance(config_snapshot, HarnessConfigSnapshot)
            else config_snapshot
        )
        gates = CreationGates.model_validate(config.gates)
    except (AttributeError, TypeError, ValueError, ValidationError) as exc:
        raise _deny(f"invalid typed creation gates/config snapshot: {exc}") from exc
    route = _route_for(config, workflow)
    if not gates.harness_enabled or not gates.dispatcher_enabled:
        raise _deny("new Harness runs require harness and dispatcher gates")
    if workflow.workflow_key == "harness.canary" and not gates.canary_enabled:
        raise _deny("new canary runs require canary_enabled")

    if entitlement is None:
        if workflow.workflow_key != "harness.canary":
            raise _deny("business workflow requires an explicit allowed entitlement")
        effective_entitlement = _INTERNAL_CANARY_ENTITLEMENT
    else:
        try:
            effective_entitlement = (
                entitlement
                if isinstance(entitlement, EntitlementSnapshot)
                else EntitlementSnapshot.model_validate(entitlement)
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise _deny(f"invalid entitlement decision: {exc}") from exc
    if effective_entitlement.decision != "allow":
        raise _deny("entitlement decision is not allow")
    if (
        effective_entitlement.entitlement_key == _INTERNAL_CANARY_ENTITLEMENT.entitlement_key
        and workflow.workflow_key != "harness.canary"
    ):
        raise _deny("the internal canary entitlement is not valid for business workflows")

    if agent_limits is None:
        raise _deny("agent_limits must be explicitly bounded")
    try:
        effective_agent_limits = (
            agent_limits
            if isinstance(agent_limits, AgentLimits)
            else AgentLimits.model_validate(agent_limits)
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise _deny(f"invalid agent limits: {exc}") from exc

    budget = _effective_budget(workflow, effective_budget)
    role_bindings: list[tuple[AgentRoleBinding, RoleDefinition, Any]] = []
    expected_roles = {
        step.role
        for step in workflow.steps
        if step.kind in ("agent", "mapped_agent") and step.role is not None
    }
    binding_roles = {
        record.get("identity")
        for record in authenticated.roles
        if isinstance(record, Mapping)
    }
    if binding_roles != expected_roles:
        raise DefinitionError("compiled binding role set does not match workflow steps")
    for role_record in authenticated.roles:
        role_bindings.append(
            _make_role_binding(
                role_record,
                available_route_keys=available_route_keys,
            )
        )
    role_bindings.sort(key=lambda item: item[0].role_identity)
    selected_roles = {
        step.role
        for step in selected_steps
        if step.kind in ("agent", "mapped_agent") and step.role is not None
    }
    if selected_roles and not gates.agent_steps_enabled:
        raise _deny("agent workflows require agent_steps_enabled")
    selected_role_definitions = {
        binding.role_identity: role for binding, role, _ in role_bindings
    }
    if any(
        selected_role_definitions[identity].runtime_kind == "dsh"
        for identity in selected_roles
    ) and not gates.agent_runtime_enabled:
        raise _deny("DSH roles require agent_runtime_enabled")
    limits = _role_limits(role_bindings, effective_agent_limits, role_limits)

    try:
        return RunPolicySnapshot.model_validate(
            {
                "workflow_identity": workflow.identity(),
                "workflow_definition_sha256": workflow.definition_hash(),
                "definition_binding_sha256": authenticated.binding_sha256,
                "config_revision_id": config_revision_id,
                "config_revision_sha256": config_revision_sha256,
                "execution_mode": route.execution_mode,
                "max_parallel_steps": workflow.max_parallel_steps,
                "creation_gates": gates,
                "effective_budget": budget,
                "agent_limits": effective_agent_limits,
                "role_limits": limits,
                "role_bindings": tuple(item[0] for item in role_bindings),
                "entitlement": effective_entitlement,
            }
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise _deny(f"assembled run policy is invalid: {exc}") from exc


# Naming aliases keep the assembly boundary discoverable to callers that use
# either the object name or the operation name.
build_policy = build_run_policy
build_run_policy_snapshot = build_run_policy


__all__ = [
    "AvailabilityInput",
    "RoleLimitInput",
    "build_policy",
    "build_run_policy",
    "build_run_policy_snapshot",
]
