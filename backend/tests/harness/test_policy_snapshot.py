"""Contract tests for the immutable RunPolicySnapshot@1 envelope."""

from __future__ import annotations

import json
import math

import pytest
from pharos.harness.definitions import (
    ModelProfileDefinition,
    ModelRouteDefinition,
    RoleDefinition,
)
from pharos.harness.policy_snapshot import (
    AgentLimits,
    AgentRoleBinding,
    AgentRoleLimits,
    EffectiveBudget,
    EntitlementSnapshot,
    RunPolicySnapshot,
)
from pydantic import ValidationError

HASH_A = "a" * 64
HASH_B = "b" * 64


def _role_binding(
    role_identity: str = "reader@1",
    *,
    provider: str = "deepseek",
    model: str = "deepseek-chat",
    usage_source: str = "system_shared",
) -> dict[str, object]:
    role_key, raw_version = role_identity.rsplit("@", 1)
    credential_policy = "user_byok" if usage_source == "byok" else "system_managed"
    route = ModelRouteDefinition(
        route_key="primary",
        priority=1,
        provider=provider,
        model=model,
        usage_source=usage_source,
        credential_policy=credential_policy,
        allowed_runtime_kinds=("dsh",),
    )
    profile = ModelProfileDefinition(
        profile_key="research",
        version=1,
        selection_policy="fixed",
        routes=(route,),
    )
    role = RoleDefinition(
        role_key=role_key,
        version=int(raw_version),
        prompt_template_version="research-role@1",
        input_schema="research.input@1",
        output_schema="research.output@1",
        model_profile=profile.identity(),
        runtime_kind="dsh",
        max_turns=4,
        max_tool_calls=1,
    )
    return {
        "role_identity": role.identity(),
        "role_definition_sha256": role.definition_hash(),
        "role_definition": role.canonical(),
        "model_profile_identity": profile.identity(),
        "model_profile_sha256": profile.definition_hash(),
        "model_profile_definition": profile.canonical(),
        "model_route_identity": route.route_key,
        "model_route_sha256": profile.route_hash(route.route_key),
    }


def _snapshot(**overrides: object) -> RunPolicySnapshot:
    values: dict[str, object] = {
        "workflow_identity": "literature.discovery@1",
        "workflow_definition_sha256": HASH_A,
        "definition_binding_sha256": HASH_B,
        "config_revision_id": "revision-20260830-1",
        "config_revision_sha256": "c" * 64,
        "execution_mode": "harness",
        "max_parallel_steps": 4,
        "creation_gates": {
            "harness_enabled": True,
            "dispatcher_enabled": True,
            "canary_enabled": True,
            "agent_steps_enabled": True,
            "agent_runtime_enabled": True,
            "domain_publish_enabled": True,
            "fulltext_enabled": False,
            "desktop_bridge_enabled": False,
            "experiments_enabled": False,
        },
        "effective_budget": {
            "wall_seconds": 900.0,
            "model_calls": 24,
            "input_tokens": 300_000,
            "output_tokens": 60_000,
            "max_tool_calls": 8,
            "cost_micros": 100_000,
        },
        "agent_limits": {
            "max_turns": 4,
            "max_tool_calls": 0,
            "max_input_chars": 2_000_000,
            "max_output_tokens": 60_000,
        },
        "entitlement": {
            "entitlement_key": "researcher",
            "entitlement_revision": 3,
            "decision": "allow",
        },
    }
    values.update(overrides)
    return RunPolicySnapshot.model_validate(values)


def test_snapshot_is_typed_and_has_all_authorization_facts() -> None:
    snapshot = _snapshot()
    assert snapshot.schema_version == 1
    assert snapshot.workflow_identity == "literature.discovery@1"
    assert snapshot.execution_mode.value == "harness"
    assert snapshot.config_revision_sha256 == "c" * 64
    assert snapshot.creation_gates.agent_runtime_enabled is True
    assert snapshot.effective_budget.model_calls == 24
    assert snapshot.agent_limits.max_turns == 4
    assert snapshot.entitlement.entitlement_revision == 3


def test_canonical_json_and_hash_are_stable_across_input_mapping_order() -> None:
    first = _snapshot()
    second = RunPolicySnapshot.model_validate(
        {
            "entitlement": {
                "decision": "allow",
                "entitlement_revision": 3,
                "entitlement_key": "researcher",
            },
            "agent_limits": {
                "max_output_tokens": 60_000,
                "max_input_chars": 2_000_000,
                "max_tool_calls": 0,
                "max_turns": 4,
            },
            "effective_budget": {
                "cost_micros": 100_000,
                "max_tool_calls": 8,
                "output_tokens": 60_000,
                "input_tokens": 300_000,
                "model_calls": 24,
                "wall_seconds": 900,
            },
            "execution_mode": "harness",
            "max_parallel_steps": 4,
            "creation_gates": {
                "experiments_enabled": False,
                "desktop_bridge_enabled": False,
                "fulltext_enabled": False,
                "domain_publish_enabled": True,
                "agent_runtime_enabled": True,
                "agent_steps_enabled": True,
                "canary_enabled": True,
                "dispatcher_enabled": True,
                "harness_enabled": True,
            },
            "config_revision_id": "revision-20260830-1",
            "config_revision_sha256": "c" * 64,
            "definition_binding_sha256": HASH_B,
            "workflow_definition_sha256": HASH_A,
            "workflow_identity": "literature.discovery@1",
        }
    )
    assert first.canonical_json() == second.canonical_json()
    assert first.snapshot_sha256() == second.snapshot_sha256()
    assert json.loads(first.canonical_json()) == first.canonical()


def test_snapshot_can_round_trip_only_through_canonical_shape() -> None:
    snapshot = _snapshot()
    restored = RunPolicySnapshot.from_canonical_json(snapshot.canonical_json())
    assert restored == snapshot
    with pytest.raises(ValueError, match="one object"):
        RunPolicySnapshot.from_canonical_json("[]")
    with pytest.raises(ValueError, match="non-finite"):
        RunPolicySnapshot.from_canonical_json('{"value":NaN}')
    with pytest.raises(ValueError, match="duplicate"):
        RunPolicySnapshot.from_canonical_json(
            snapshot.canonical_json().replace(
                '"schema_version":1,', '"schema_version":1,"schema_version":1,'
            )
        )
    with pytest.raises(ValueError, match="not canonical"):
        RunPolicySnapshot.from_canonical_json(" " + snapshot.canonical_json())


def test_internal_canary_mode_is_explicit_in_canonical_json() -> None:
    snapshot = _snapshot(workflow_identity="harness.canary@1", execution_mode=None)
    canonical = snapshot.canonical()
    assert canonical["execution_mode"] == "internal"
    assert RunPolicySnapshot.from_canonical(canonical).execution_mode is None
    with pytest.raises(ValidationError, match="only the internal canary"):
        _snapshot(execution_mode=None)


def test_unknown_fields_fail_closed_at_every_nesting_level() -> None:
    with pytest.raises(ValidationError):
        _snapshot(unexpected="future-field")
    with pytest.raises(ValidationError):
        _snapshot(
            agent_limits={
                "max_turns": 4,
                "max_tool_calls": 0,
                "max_input_chars": 100,
                "max_output_tokens": 100,
                "shell": True,
            }
        )
    with pytest.raises(ValidationError):
        _snapshot(
            entitlement={
                "entitlement_key": "researcher",
                "entitlement_revision": 3,
                "decision": "allow",
                "api_key": "secret",
            }
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 2),
        ("workflow_identity", "literature.discovery"),
        ("workflow_definition_sha256", "not-a-hash"),
        ("definition_binding_sha256", "B" * 64),
        ("config_revision_id", "https://example.invalid/revision"),
        ("config_revision_sha256", "not-a-hash"),
        ("execution_mode", "future"),
        ("activation_state", "deprecated"),
    ],
)
def test_identity_and_vocabularies_are_closed(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _snapshot(**{field: value})


def test_deny_entitlement_cannot_authorize_a_run() -> None:
    with pytest.raises(ValidationError, match="only authorize"):
        _snapshot(
            entitlement={
                "entitlement_key": "researcher",
                "entitlement_revision": 3,
                "decision": "deny",
            }
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("wall_seconds", math.nan),
        ("wall_seconds", math.inf),
        ("model_calls", True),
        ("input_tokens", 0),
        ("output_tokens", -1),
        ("cost_micros", -1),
    ],
)
def test_effective_budget_is_finite_strict_and_bounded(field: str, value: object) -> None:
    budget = {
        "wall_seconds": 900.0,
        "model_calls": 24,
        "input_tokens": 300_000,
        "output_tokens": 60_000,
        "max_tool_calls": 8,
        "cost_micros": 100_000,
    }
    budget[field] = value
    with pytest.raises(ValidationError):
        _snapshot(effective_budget=budget)


def test_effective_budget_accepts_existing_budget_spec_without_weakening_types() -> None:
    budget = EffectiveBudget.from_budget(
        {
            "wall_seconds": 5,
            "model_calls": 1,
            "input_tokens": 10,
            "output_tokens": 10,
            "max_tool_calls": 0,
            "cost_micros": 0,
        }
    )
    assert budget.wall_seconds == 5.0
    with pytest.raises(ValidationError):
        EffectiveBudget.from_budget({"wall_seconds": 5, "model_calls": True})


def test_role_limits_are_canonicalized_by_role_identity() -> None:
    roles = (
        {
            "role_identity": "writer@1",
            "max_turns": 2,
            "max_tool_calls": 0,
            "max_input_chars": 100,
            "max_output_tokens": 10,
        },
        {
            "role_identity": "reader@1",
            "max_turns": 3,
            "max_tool_calls": 1,
            "max_input_chars": 200,
            "max_output_tokens": 20,
        },
    )
    first = _snapshot(
        role_limits=roles,
        role_bindings=tuple(_role_binding(role["role_identity"]) for role in roles),
    )
    second = _snapshot(
        role_limits=tuple(reversed(first.role_limits)),
        role_bindings=tuple(reversed(first.role_bindings)),
    )
    assert first.canonical()["role_limits"] == second.canonical()["role_limits"]
    assert first.snapshot_sha256() == second.snapshot_sha256()


def test_role_limits_and_route_accounting_bindings_must_match_exactly() -> None:
    role = {
        "role_identity": "reader@1",
        "max_turns": 3,
        "max_tool_calls": 1,
        "max_input_chars": 200,
        "max_output_tokens": 20,
    }
    binding = _role_binding()
    snapshot = _snapshot(role_limits=(role,), role_bindings=(binding,))
    assert snapshot.role_bindings[0].usage_source.value == "system_shared"
    with pytest.raises(ValidationError, match="exact same roles"):
        _snapshot(role_limits=(role,))
    with pytest.raises(ValidationError, match="exact same roles"):
        _snapshot(role_bindings=(binding,))


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider", "https://api.example"),
        ("model", "sk-secret"),
        ("model", "secret=abc"),
    ],
)
def test_role_route_binding_rejects_endpoints_and_credentials(field: str, value: str) -> None:
    binding = _role_binding()
    profile = binding["model_profile_definition"]
    assert isinstance(profile, dict)
    routes = profile["routes"]
    assert isinstance(routes, list)
    routes[0][field] = value
    role = {
        "role_identity": "reader@1",
        "max_turns": 3,
        "max_tool_calls": 1,
        "max_input_chars": 200,
        "max_output_tokens": 20,
    }
    with pytest.raises(ValidationError):
        _snapshot(role_limits=(role,), role_bindings=(binding,))


def test_role_route_binding_accepts_registry_compatible_model_path() -> None:
    role = {
        "role_identity": "reader@1",
        "max_turns": 3,
        "max_tool_calls": 1,
        "max_input_chars": 200,
        "max_output_tokens": 20,
    }
    binding = _role_binding(model="research/deepseek-chat")
    snapshot = _snapshot(role_limits=(role,), role_bindings=(binding,))
    assert snapshot.role_bindings[0].model == "research/deepseek-chat"


def test_role_route_hash_is_cryptographically_bound_to_embedded_definition() -> None:
    role = {
        "role_identity": "reader@1",
        "max_turns": 3,
        "max_tool_calls": 1,
        "max_input_chars": 200,
        "max_output_tokens": 20,
    }
    binding = _role_binding()
    binding["model_route_sha256"] = HASH_B
    with pytest.raises(ValidationError, match="route identity/hash"):
        _snapshot(role_limits=(role,), role_bindings=(binding,))


def test_duplicate_role_limits_fail_closed() -> None:
    role = {
        "role_identity": "reader@1",
        "max_turns": 3,
        "max_tool_calls": 1,
        "max_input_chars": 200,
        "max_output_tokens": 20,
    }
    with pytest.raises(ValidationError, match="duplicate role"):
        _snapshot(role_limits=(role, role))


def test_policy_contract_does_not_expose_secret_or_reasoning_fields() -> None:
    fields = set(RunPolicySnapshot.model_fields)
    assert not fields.intersection(
        {"api_key", "token", "headers", "env", "prompt", "chain_of_thought"}
    )
    assert set(EntitlementSnapshot.model_fields) == {
        "entitlement_key",
        "entitlement_revision",
        "decision",
    }
    assert set(AgentLimits.model_fields) == {
        "max_turns",
        "max_tool_calls",
        "max_input_chars",
        "max_output_tokens",
    }
    assert set(AgentRoleLimits.model_fields) == {
        "role_identity",
        "max_turns",
        "max_tool_calls",
        "max_input_chars",
        "max_output_tokens",
    }
    assert set(AgentRoleBinding.model_fields) == {
        "role_identity",
        "role_definition_sha256",
        "role_definition",
        "model_profile_identity",
        "model_profile_sha256",
        "model_profile_definition",
        "model_route_identity",
        "model_route_sha256",
    }
