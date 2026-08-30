"""Focused H1.5 ModelProfile contract tests."""

from __future__ import annotations

import hashlib
import math
from typing import Any

import pytest
from pharos.harness.app import HarnessApp
from pharos.harness.configrev import bootstrap_snapshot
from pharos.harness.contracts import ArtifactSensitivity, DefinitionError, RetryClass
from pharos.harness.definitions import (
    BudgetSpec,
    CapabilityDefinition,
    ModelProfileDefinition,
    ModelRouteDefinition,
    RoleDefinition,
    canonical_json,
)
from pharos.harness.registry import Registry
from pharos.harness.workflows.canary import (
    CANARY_DSH_MODEL_PROFILE,
    CANARY_LEGACY_MODEL_PROFILE,
    canary_dsh_workflow,
    canary_workflow,
)
from pydantic import ValidationError


def _route(**overrides) -> ModelRouteDefinition:
    values: dict[str, Any] = {
        "route_key": "primary",
        "priority": 1,
        "provider": "pharos-fake",
        "model": "pharos-fake-canary",
        "usage_source": "system_shared",
        "credential_policy": "none",
        "allowed_runtime_kinds": ("dsh",),
        "reasoning_effort": None,
        "max_output_tokens": 1_000,
    }
    values.update(overrides)
    return ModelRouteDefinition(**values)


def _profile(*routes: ModelRouteDefinition, **overrides) -> ModelProfileDefinition:
    values: dict[str, Any] = {
        "profile_key": "reader",
        "version": 1,
        "selection_policy": "ordered_first_available",
        "routes": routes or (_route(),),
    }
    values.update(overrides)
    return ModelProfileDefinition(**values)


def test_profile_identity_hash_and_route_order_are_stable() -> None:
    first = _profile(_route(route_key="backup", priority=2), _route())
    second = _profile(_route(), _route(route_key="backup", priority=2))
    assert first.identity() == "reader@1"
    assert first.definition_hash() == second.definition_hash()
    assert first.resolve_route("dsh", available_route_keys={"primary"}).route_key == "primary"


def test_route_runtime_order_is_canonical_and_hash_self_verifying() -> None:
    first = _route(allowed_runtime_kinds=("dsh", "in_process_fake"))
    second = _route(allowed_runtime_kinds=("in_process_fake", "dsh"))
    assert first.canonical() == second.canonical()
    assert first.definition_hash() == second.definition_hash()
    expected = hashlib.sha256(canonical_json(first.canonical()).encode("utf-8")).hexdigest()
    assert first.definition_hash() == expected


def test_route_binding_is_a_profile_scoped_canonical_envelope() -> None:
    profile = _profile()
    binding = profile.route_binding("primary")
    assert binding == {
        "schema_version": 1,
        "profile_key": "reader",
        "profile_version": 1,
        "profile_definition_hash": profile.definition_hash(),
        "route_key": "primary",
        "route_definition": _route().canonical(),
    }
    assert profile.route_hash("primary") == hashlib.sha256(
        canonical_json(binding).encode("utf-8")
    ).hexdigest()
    assert profile.route_hash("primary") == (
        "29b888b207a82398bb1b13c865aff41a93aa15e29f48fda9710bb7d94f6b5999"
    )
    assert profile.route_hash("primary") == profile.route_hash("primary")


def test_route_binding_changes_when_profile_identity_changes() -> None:
    route = _route()
    first = _profile(route, profile_key="reader")
    second = _profile(route, profile_key="writer")
    assert first.route_binding("primary")["route_definition"] == second.route_binding(
        "primary"
    )["route_definition"]
    assert first.route_hash("primary") != second.route_hash("primary")
    assert first.route_binding("primary")["profile_definition_hash"] != second.route_binding(
        "primary"
    )["profile_definition_hash"]


def test_route_binding_reorders_routes_stably_and_tracks_route_tampering() -> None:
    first = _profile(_route(route_key="backup", priority=2), _route(route_key="primary"))
    second = _profile(_route(route_key="primary"), _route(route_key="backup", priority=2))
    assert first.route_hash("primary") == second.route_hash("primary")
    tampered = first.model_copy(
        update={
            "routes": tuple(
                route.model_copy(update={"max_output_tokens": 999})
                if route.route_key == "primary"
                else route
                for route in first.routes
            )
        }
    )
    assert tampered.route_binding("primary")["route_definition"] != first.route_binding(
        "primary"
    )["route_definition"]
    assert tampered.route_hash("primary") != first.route_hash("primary")


def test_route_binding_unknown_route_fails_closed() -> None:
    profile = _profile()
    with pytest.raises(ValueError, match="has no route"):
        profile.route_binding("missing")
    with pytest.raises(ValueError, match="has no route"):
        profile.route_hash("missing")


def test_ordered_profile_requires_explicit_availability_and_uses_priority() -> None:
    profile = _profile(
        _route(route_key="backup", priority=2), _route(route_key="primary", priority=1)
    )
    with pytest.raises(ValueError, match="explicit route availability"):
        profile.resolve_route("dsh")
    assert profile.resolve_route(
        "dsh", available_route_keys={"backup"}
    ).route_key == "backup"
    assert profile.resolve_route(
        "dsh", available_route_keys={"primary", "backup"}
    ).route_key == "primary"


@pytest.mark.parametrize(
    "value",
    [
        "fallback_any",
    ],
)
def test_profile_selection_policy_is_closed(value: object) -> None:
    with pytest.raises(ValidationError):
        _profile(selection_policy=value)


@pytest.mark.parametrize(
    "field, value",
    [
        ("usage_source", "secret"),
        ("credential_policy", "credential_url"),
        ("reasoning_effort", "chain_of_thought"),
        ("allowed_runtime_kinds", ("web",)),
    ],
)
def test_route_vocabularies_are_closed(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _route(**{field: value})


@pytest.mark.parametrize("value", [0, -1, 1_000_001, True])
def test_profile_max_output_tokens_is_strict_and_bounded(value: object) -> None:
    with pytest.raises(ValidationError):
        _route(max_output_tokens=value)


def test_route_keys_and_priorities_are_unique_and_fixed_is_single_route() -> None:
    with pytest.raises(ValidationError, match="route_key"):
        _profile(_route(), _route(route_key="primary", priority=2))
    with pytest.raises(ValidationError, match="priority"):
        _profile(_route(), _route(route_key="other", priority=1))
    with pytest.raises(ValidationError, match="exactly one"):
        _profile(_route(), _route(route_key="other", priority=2), selection_policy="fixed")


def test_profile_has_no_raw_endpoint_or_secret_fields() -> None:
    with pytest.raises(ValidationError):
        _route(endpoint="https://provider.invalid", api_key="secret")
    for value in (
        "model\nwith-control",
        "model\x7f",
        "model\u202ehidden",
        "https://provider.invalid/model",
        "https:/provider.invalid/model",
        "sk-secret-model",
        "Bearer secret-model",
        "secret=abc",
        "api_key=abc",
        "token:abc",
        "provider?token=abc",
        "模型",
    ):
        with pytest.raises(ValidationError):
            _route(model=value)
    # Provider identifiers are stricter ASCII identifiers; model names may
    # retain the provider/model spelling used by common model registries.
    assert _route(model="org/model", credential_policy="system_managed").model == "org/model"


@pytest.mark.parametrize(
    ("usage_source", "credential_policy"),
    [
        ("byok", "system_managed"),
        ("byok", "none"),
        ("official", "none"),
        ("official", "user_byok"),
        ("system_shared", "user_byok"),
    ],
)
def test_usage_and_credential_policy_must_match(
    usage_source: str, credential_policy: str
) -> None:
    with pytest.raises(ValidationError):
        _route(usage_source=usage_source, credential_policy=credential_policy)


def test_valid_usage_and_credential_policy_combinations() -> None:
    assert _route(usage_source="byok", credential_policy="user_byok")
    assert _route(usage_source="official", credential_policy="system_managed")
    assert _route(usage_source="system_shared", credential_policy="system_managed")
    # The deterministic canary has no credential and is the only exempt
    # system_shared/none route.
    assert _route(provider="pharos-fake", credential_policy="none")
    with pytest.raises(ValidationError):
        _route(provider="other-fake", credential_policy="none")


def test_legacy_credentialless_canary_route_is_in_process_only() -> None:
    values = {
        "route_key": "legacy-canary",
        "priority": 1,
        "provider": "fake",
        "model": "canary",
        "usage_source": "system_shared",
        "credential_policy": "none",
        "allowed_runtime_kinds": ("in_process_fake",),
    }
    assert ModelRouteDefinition.model_validate(values).provider == "fake"
    with pytest.raises(ValidationError):
        ModelRouteDefinition.model_validate(
            {**values, "allowed_runtime_kinds": ("dsh",)}
        )


@pytest.mark.parametrize("field", ["profile_key", "route_key", "provider"])
@pytest.mark.parametrize("value", ["with space", "全角", "bad@1", "https://provider.invalid"])
def test_ascii_identifiers_are_strict(field: str, value: str) -> None:
    if field == "profile_key":
        with pytest.raises(ValidationError):
            _profile(profile_key=value)
    elif field == "route_key":
        with pytest.raises(ValidationError):
            _route(route_key=value)
    else:
        with pytest.raises(ValidationError):
            _route(provider=value)


def test_canonical_json_rejects_non_json_numbers() -> None:
    with pytest.raises(ValueError):
        canonical_json({"value": math.nan})


def test_dsh_role_requires_exact_registered_versioned_profile() -> None:
    role = RoleDefinition(
        role_key="reader",
        version=1,
        prompt_template_version="reader@1",
        input_schema="reader.in@1",
        output_schema="reader.out@1",
        model_profile="reader@1",
        runtime_kind="dsh",
    )
    registry = Registry()
    registry.register_role(role)
    with pytest.raises(DefinitionError, match="unknown model profile"):
        registry.compile()
    registry.register_model_profile(_profile())
    registry.compile()


def test_dsh_role_profile_must_have_dsh_route() -> None:
    profile = _profile(
        _route(allowed_runtime_kinds=("in_process_fake",)),
        selection_policy="fixed",
    )
    role = RoleDefinition(
        role_key="reader",
        version=1,
        prompt_template_version="reader@1",
        input_schema="reader.in@1",
        output_schema="reader.out@1",
        model_profile="reader@1",
        runtime_kind="dsh",
    )
    registry = Registry()
    registry.register_role(role)
    registry.register_model_profile(profile)
    with pytest.raises(DefinitionError, match="every route.*allow dsh"):
        registry.compile()


def test_dsh_role_rejects_profile_with_non_dsh_fallback_route() -> None:
    profile = _profile(
        _route(route_key="primary", priority=1, allowed_runtime_kinds=("dsh",)),
        _route(
            route_key="fallback",
            priority=2,
            allowed_runtime_kinds=("in_process_fake",),
        ),
    )
    role = RoleDefinition(
        role_key="reader",
        version=1,
        prompt_template_version="reader@1",
        input_schema="reader.in@1",
        output_schema="reader.out@1",
        model_profile="reader@1",
        runtime_kind="dsh",
    )
    registry = Registry()
    registry.register_role(role)
    registry.register_model_profile(profile)
    with pytest.raises(DefinitionError, match="every route.*allow dsh"):
        registry.compile()


def test_in_process_versioned_role_requires_registered_profile_route() -> None:
    role = RoleDefinition(
        role_key="reader",
        version=1,
        prompt_template_version="reader@1",
        input_schema="reader.in@1",
        output_schema="reader.out@1",
        model_profile="reader@1",
        runtime_kind="in_process_fake",
    )
    registry = Registry()
    registry.register_role(role)
    with pytest.raises(DefinitionError, match="unknown model profile"):
        registry.compile()
    registry.register_model_profile(
        _profile(_route(allowed_runtime_kinds=("dsh",)), selection_policy="fixed")
    )
    with pytest.raises(DefinitionError, match="in_process_fake"):
        registry.compile()


def test_ordered_internal_fake_profile_compiles_without_route_resolution() -> None:
    role = RoleDefinition(
        role_key="reader",
        version=1,
        prompt_template_version="reader@1",
        input_schema="reader.in@1",
        output_schema="reader.out@1",
        model_profile="reader@1",
        runtime_kind="in_process_fake",
    )
    profile = _profile(
        _route(route_key="backup", priority=2, allowed_runtime_kinds=("in_process_fake",)),
        _route(route_key="primary", priority=1, allowed_runtime_kinds=("in_process_fake",)),
    )
    registry = Registry()
    registry.register_role(role)
    registry.register_model_profile(profile)
    registry.compile()


def test_in_process_profile_cannot_hide_real_fallback_after_fake_route() -> None:
    role = RoleDefinition(
        role_key="reader",
        version=1,
        prompt_template_version="reader@1",
        input_schema="reader.in@1",
        output_schema="reader.out@1",
        model_profile="reader@1",
        runtime_kind="in_process_fake",
    )
    profile = _profile(
        _route(route_key="primary", priority=1, allowed_runtime_kinds=("in_process_fake",)),
        _route(
            route_key="fallback",
            priority=2,
            provider="openai",
            model="gpt-4.1",
            usage_source="official",
            credential_policy="system_managed",
            allowed_runtime_kinds=("in_process_fake",),
        ),
    )
    registry = Registry()
    registry.register_role(role)
    registry.register_model_profile(profile)
    with pytest.raises(DefinitionError, match="internal fake route"):
        registry.compile()


def test_role_and_capability_duplicate_registration_uses_canonical_hash() -> None:
    role = RoleDefinition(
        role_key="reader",
        version=1,
        prompt_template_version="reader@1",
        input_schema="reader.in@1",
        output_schema="reader.out@1",
        model_profile="reader@1",
        runtime_kind="in_process_fake",
        capability_allowlist=("b@1", "a@1"),
    )
    capability = CapabilityDefinition(
        capability_key="reader",
        version=1,
        action_schema="reader.action@1",
        observation_schema="reader.observation@1",
        retry_classes=(RetryClass.server_transient, RetryClass.rate_limited),
    )
    registry = Registry()
    registry.register_role(role)
    registry.register_role(
        role.model_copy(update={"capability_allowlist": ("a@1", "b@1")})
    )
    registry.register_capability(capability)
    registry.register_capability(
        capability.model_copy(
            update={"retry_classes": (RetryClass.rate_limited, RetryClass.server_transient)}
        )
    )
    with pytest.raises(DefinitionError):
        registry.register_role(role.model_copy(update={"output_schema": "changed@1"}))
    with pytest.raises(DefinitionError):
        registry.register_capability(
            capability.model_copy(update={"observation_schema": "changed@1"})
        )


def test_bare_profile_compatibility_is_only_canary_actor() -> None:
    registry = Registry()
    registry.register_role(
        RoleDefinition(
            role_key="not_canary",
            version=1,
            prompt_template_version="p@1",
            input_schema="in@1",
            output_schema="out@1",
            model_profile="canary",
            runtime_kind="in_process_fake",
        )
    )
    with pytest.raises(DefinitionError, match="versioned identity"):
        registry.compile()


def test_legacy_canary_alias_is_bound_to_the_complete_frozen_role() -> None:
    registry = Registry()
    role = RoleDefinition(
        role_key="canary_actor",
        version=1,
        prompt_template_version="canary-actor-zh@1",
        input_schema="canary.actor_in@1",
        output_schema="canary.actor_out@1",
        model_profile="canary",
        runtime_kind="in_process_fake",
        max_turns=2,
        max_tool_calls=2,
        token_budget=BudgetSpec(
            wall_seconds=30,
            model_calls=2,
            input_tokens=1000,
            output_tokens=1000,
        ),
    )
    registry.register_model_profile(CANARY_LEGACY_MODEL_PROFILE)
    registry.register_role(role)
    registry.compile()
    registry = Registry()
    registry.register_model_profile(CANARY_LEGACY_MODEL_PROFILE)
    registry.register_role(role.model_copy(update={"prompt_template_version": "changed@1"}))
    with pytest.raises(DefinitionError, match="frozen legacy definition"):
        registry.compile()


def test_in_process_profile_rejects_real_provider_routes() -> None:
    profile = _profile(
        _route(
            provider="openai",
            model="gpt-4.1",
            usage_source="official",
            credential_policy="system_managed",
            allowed_runtime_kinds=("in_process_fake",),
        ),
        selection_policy="fixed",
    )
    role = RoleDefinition(
        role_key="reader",
        version=1,
        prompt_template_version="reader@1",
        input_schema="reader.in@1",
        output_schema="reader.out@1",
        model_profile="reader@1",
        runtime_kind="in_process_fake",
    )
    registry = Registry()
    registry.register_role(role)
    registry.register_model_profile(profile)
    with pytest.raises(DefinitionError, match="internal fake route"):
        registry.compile()


def test_canary_profile_is_registered_but_not_in_definition_snapshot() -> None:
    app = HarnessApp()
    assert app.registry.require_model_profile("canary@1") == CANARY_LEGACY_MODEL_PROFILE
    assert app.registry.require_model_profile("pharos-fake-canary@1") == CANARY_DSH_MODEL_PROFILE
    snapshot_json = app.registry.snapshot().model_dump_json()
    assert '"model_profiles"' not in snapshot_json
    assert '"routes"' not in snapshot_json
    assert canary_workflow().definition_hash() == (
        "28b38f56b1acefb62aeebece55a9c8515320f59b6cdf58fa43858f43ee9bf477"
    )
    assert canary_dsh_workflow().definition_hash() == (
        "0dc6d73a651c1eca4b29398faa78f570c80b5b94673f153120936ab5a0211dbf"
    )


def test_profile_snapshot_exclusion_is_temporary_and_runtime_gate_stays_off(monkeypatch) -> None:
    monkeypatch.delenv("PHAROS_HARNESS_AGENT_RUNTIME_ENABLED", raising=False)
    app = HarnessApp()
    snapshot = bootstrap_snapshot(app.registry)
    assert snapshot.gates["agent_runtime_enabled"] is False
    assert "model_profiles" not in snapshot.canonical()


def test_role_and_capability_hashes_are_content_based() -> None:
    role = RoleDefinition(
        role_key="r",
        version=1,
        prompt_template_version="p@1",
        input_schema="in@1",
        output_schema="out@1",
        model_profile="reader@1",
        runtime_kind="in_process_fake",
    )
    capability = CapabilityDefinition(
        capability_key="c",
        version=1,
        action_schema="a@1",
        observation_schema="o@1",
    )
    assert role.definition_hash() == role.model_copy().definition_hash()
    assert capability.definition_hash() == capability.model_copy().definition_hash()


def test_role_and_capability_collection_fields_are_hash_order_independent() -> None:
    role = RoleDefinition(
        role_key="r",
        version=1,
        prompt_template_version="p@1",
        input_schema="in@1",
        output_schema="out@1",
        model_profile="reader@1",
        runtime_kind="in_process_fake",
        capability_allowlist=("b@1", "a@1"),
        allowed_input_sensitivity=(ArtifactSensitivity.private, ArtifactSensitivity.public),
    )
    reordered_role = role.model_copy(
        update={
            "capability_allowlist": ("a@1", "b@1"),
            "allowed_input_sensitivity": (ArtifactSensitivity.public, ArtifactSensitivity.private),
        }
    )
    capability = CapabilityDefinition(
        capability_key="c",
        version=1,
        action_schema="a@1",
        observation_schema="o@1",
        retry_classes=(RetryClass.server_transient, RetryClass.rate_limited),
    )
    reordered_capability = capability.model_copy(
        update={"retry_classes": (RetryClass.rate_limited, RetryClass.server_transient)}
    )
    assert role.definition_hash() == reordered_role.definition_hash()
    assert capability.definition_hash() == reordered_capability.definition_hash()
    for definition in (role, reordered_role, capability, reordered_capability):
        expected = hashlib.sha256(
            canonical_json(definition.canonical()).encode("utf-8")
        ).hexdigest()
        assert definition.definition_hash() == expected
    assert role.canonical() == reordered_role.canonical()
    assert capability.canonical() == reordered_capability.canonical()


def test_profile_registry_is_idempotent_for_canonical_route_order_and_snapshot_is_unchanged(
) -> None:
    registry = Registry()
    before = registry.snapshot().canonical_hash()
    profile = _profile(
        _route(route_key="backup", priority=2), _route(route_key="primary", priority=1)
    )
    registry.register_model_profile(profile)
    assert registry.snapshot().canonical_hash() == before
    registry.register_model_profile(
        _profile(_route(route_key="primary", priority=1), _route(route_key="backup", priority=2))
    )
    with pytest.raises(DefinitionError, match="different model profile hash"):
        registry.register_model_profile(
            _profile(
                _route(route_key="primary", priority=1),
                _route(route_key="backup", priority=2, max_output_tokens=999),
            )
        )
