"""The immutable policy contract captured when a Harness Run is created.

The configuration head and the definition registry are mutable *sources* of
new runs.  A running run must not silently inherit a later change to either
one, so the complete effective policy is materialised as a small, typed
snapshot.  This module deliberately has no database or runtime dependencies;
the repository layer can persist ``canonical_json()`` and
``snapshot_sha256()`` as one immutable pair.

Only policy facts are represented here.  Credentials, endpoints, environment
variables, prompts, model output and chain-of-thought are intentionally not
part of the schema and therefore cannot become snapshot fields by accident.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import (
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from pharos.harness.contracts import ExecutionMode, StrictModel, UsageSource
from pharos.harness.definitions import (
    ModelProfileDefinition,
    ModelRouteDefinition,
    RoleDefinition,
    sha256_hex,
)
from pharos.harness.definitions import (
    canonical_json as _canonical_json,
)

POLICY_SNAPSHOT_SCHEMA_VERSION = 1
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
_WORKFLOW_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}@[1-9][0-9]{0,5}$"
_VERSIONED_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}@[1-9][0-9]{0,5}$"


def _safe_identifier(value: str, *, field_name: str, max_length: int = 128) -> str:
    """Accept only opaque metadata identifiers, never connection material."""
    if (
        not value
        or len(value) > max_length
        or value != value.strip()
        or not value.isascii()
        or not value[0].isalnum()
        or any(not (char.isalnum() or char in "._-@") for char in value)
        or "@" in value and value.count("@") != 1
    ):
        raise ValueError(f"{field_name} must be a safe ASCII identifier")
    lowered = value.lower()
    if "@" in value:
        key, version = value.rsplit("@", 1)
        if not key or not version.isdigit() or int(version) < 1:
            raise ValueError(f"{field_name} must be a safe ASCII identifier")
    if "://" in value or ":/" in value or lowered.startswith(("sk-", "bearer ")):
        raise ValueError(f"{field_name} must not contain endpoint or credential data")
    return value


def _gate_names() -> tuple[str, ...]:
    """Resolve the config vocabulary lazily to keep this contract acyclic."""
    from pharos.harness.configrev import GATE_NAMES

    return GATE_NAMES


def _reject_non_finite(value: float) -> float:
    """Reject values which JSON's canonical encoder cannot represent."""
    if not math.isfinite(value):
        raise ValueError("policy budget values must be finite")
    return value


class EffectiveBudget(StrictModel):
    """The run-level budget after configuration and entitlement evaluation.

    These are limits, not usage observations.  They are copied into a run
    once and cannot be increased by a later plan, route or configuration
    change.  ``StrictInt`` prevents JSON ``true``/``false`` from becoming a
    token or cost limit.
    """

    wall_seconds: StrictFloat = Field(gt=0, le=31_536_000)
    model_calls: StrictInt = Field(gt=0, le=1_000_000)
    input_tokens: StrictInt = Field(gt=0, le=1_000_000_000)
    output_tokens: StrictInt = Field(gt=0, le=1_000_000_000)
    max_tool_calls: StrictInt = Field(ge=0, le=1_000_000)
    cost_micros: StrictInt = Field(ge=0, le=10**15)

    _finite_wall_seconds = field_validator("wall_seconds")(_reject_non_finite)

    @classmethod
    def from_budget(cls, budget: Any) -> EffectiveBudget:
        """Copy a trusted ``BudgetSpec`` (or mapping) into this strict type."""
        if hasattr(budget, "model_dump"):
            budget = budget.model_dump(mode="python")
        return cls.model_validate(budget)


class AgentLimits(StrictModel):
    """Per-agent limits frozen for every attempt in this run."""

    max_turns: StrictInt = Field(gt=0, le=100_000)
    max_tool_calls: StrictInt = Field(ge=0, le=1_000_000)
    max_input_chars: StrictInt = Field(gt=0, le=100_000_000)
    max_output_tokens: StrictInt = Field(gt=0, le=1_000_000_000)


class AgentRoleLimits(StrictModel):
    """The effective limits of one role used by the workflow."""

    role_identity: StrictStr = Field(min_length=3, max_length=128, pattern=_WORKFLOW_PATTERN)
    max_turns: StrictInt = Field(gt=0, le=100_000)
    max_tool_calls: StrictInt = Field(ge=0, le=1_000_000)
    max_input_chars: StrictInt = Field(gt=0, le=100_000_000)
    max_output_tokens: StrictInt = Field(gt=0, le=1_000_000_000)


class AgentRoleBinding(StrictModel):
    """The complete model/accounting binding for one agent role.

    Usage is intentionally attached to the role route.  A run may contain
    several roles backed by different providers or accounting sources; a
    single run-level ``usage_source`` would be ambiguous and unsafe.
    """

    role_identity: StrictStr = Field(
        min_length=3, max_length=128, pattern=_WORKFLOW_PATTERN
    )
    role_definition_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    role_definition: RoleDefinition
    model_profile_identity: StrictStr = Field(
        min_length=3, max_length=128, pattern=_VERSIONED_IDENTIFIER_PATTERN
    )
    model_profile_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    model_profile_definition: ModelProfileDefinition
    model_route_identity: StrictStr = Field(
        min_length=1, max_length=64, pattern=_IDENTIFIER_PATTERN
    )
    model_route_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)

    @field_validator(
        "role_identity",
        "model_profile_identity",
        "model_route_identity",
        mode="before",
    )
    @classmethod
    def _validate_safe_identifiers(cls, value: object, info: Any) -> object:
        if isinstance(value, str):
            return _safe_identifier(value, field_name=info.field_name)
        return value

    @field_validator(
        "role_definition_sha256",
        "model_profile_sha256",
        "model_route_sha256",
        mode="before",
    )
    @classmethod
    def _reject_hash_payloads(cls, value: object, info: Any) -> object:
        if isinstance(value, str) and (
            "://" in value or value.lower().startswith(("sk-", "bearer "))
        ):
            raise ValueError(f"{info.field_name} must be a digest")
        return value

    @model_validator(mode="after")
    def _validate_definition_binding(self) -> AgentRoleBinding:
        if (
            self.role_identity != self.role_definition.identity()
            or self.role_definition_sha256 != self.role_definition.definition_hash()
        ):
            raise ValueError("role identity/hash does not match the embedded definition")
        if (
            self.model_profile_identity != self.model_profile_definition.identity()
            or self.model_profile_sha256
            != self.model_profile_definition.definition_hash()
        ):
            raise ValueError("model profile identity/hash does not match its definition")

        from pharos.harness.registry import validate_role_model_profile

        validate_role_model_profile(self.role_definition, self.model_profile_definition)
        routes = [
            route
            for route in self.model_profile_definition.routes
            if route.route_key == self.model_route_identity
        ]
        if (
            len(routes) != 1
            or self.model_profile_definition.route_hash(self.model_route_identity)
            != self.model_route_sha256
        ):
            raise ValueError("model route identity/hash does not match the selected profile route")
        return self

    @property
    def route(self) -> ModelRouteDefinition:
        return next(
            route
            for route in self.model_profile_definition.routes
            if route.route_key == self.model_route_identity
        )

    @property
    def provider(self) -> str:
        return self.route.provider

    @property
    def model(self) -> str:
        return self.route.model

    @property
    def usage_source(self) -> UsageSource:
        return UsageSource(self.route.usage_source)

    def canonical(self) -> dict[str, Any]:
        value = self.model_dump(mode="json")
        value["role_definition"] = self.role_definition.canonical()
        value["model_profile_definition"] = self.model_profile_definition.canonical()
        return value


class EntitlementSnapshot(StrictModel):
    """Non-secret entitlement decision used to authorize this run.

    An entitlement key is a server-side plan/entitlement identifier, never a
    bearer token.  The actual credential, if any, is resolved at execution
    time and is not part of this immutable record.
    """

    entitlement_key: StrictStr = Field(
        min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN
    )
    entitlement_revision: StrictInt = Field(gt=0, le=1_000_000_000)
    decision: Literal["allow", "deny"]


class CreationGates(StrictModel):
    """Typed, deeply immutable copy of the configuration gate decision."""

    harness_enabled: StrictBool
    dispatcher_enabled: StrictBool
    canary_enabled: StrictBool
    agent_steps_enabled: StrictBool
    agent_runtime_enabled: StrictBool
    domain_publish_enabled: StrictBool
    fulltext_enabled: StrictBool
    desktop_bridge_enabled: StrictBool
    experiments_enabled: StrictBool

    @model_validator(mode="after")
    def _validate_dependencies(self) -> CreationGates:
        if self.experiments_enabled:
            raise ValueError("experiments_enabled=1 is permanently denied")
        if not self.harness_enabled:
            enabled = [
                name
                for name in _gate_names()
                if name != "harness_enabled" and getattr(self, name)
            ]
            if enabled:
                raise ValueError("harness_enabled=0 conflicts with " + ", ".join(enabled))
        if not self.dispatcher_enabled and (
            self.canary_enabled or self.agent_steps_enabled or self.agent_runtime_enabled
        ):
            raise ValueError("canary/agent gates require dispatcher_enabled=1")
        if self.agent_runtime_enabled and not self.agent_steps_enabled:
            raise ValueError("agent_runtime_enabled requires agent_steps_enabled=1")
        return self


class RunPolicySnapshot(StrictModel):
    """Versioned, canonical policy captured at Run creation time.

    ``schema_version`` is intentionally a literal.  A future incompatible
    shape must fail closed rather than being interpreted as version 1 by an
    older worker.
    """

    schema_version: Literal[1] = 1
    workflow_identity: StrictStr = Field(
        min_length=3, max_length=128, pattern=_WORKFLOW_PATTERN
    )
    activation_state: Literal["active"] = "active"
    workflow_definition_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    definition_binding_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    config_revision_id: StrictStr = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    config_revision_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    # Internal canary routes may have a NULL route mode in the config table.
    # The canonical snapshot never emits NULL: it emits the explicit
    # ``internal`` discriminator for that one case.
    execution_mode: ExecutionMode | None
    max_parallel_steps: StrictInt = Field(gt=0, le=64)
    creation_gates: CreationGates
    effective_budget: EffectiveBudget
    agent_limits: AgentLimits
    role_limits: tuple[AgentRoleLimits, ...] = ()
    role_bindings: tuple[AgentRoleBinding, ...] = ()
    entitlement: EntitlementSnapshot

    @model_validator(mode="before")
    @classmethod
    def _decode_internal_mode(cls, value: object) -> object:
        if isinstance(value, dict) and value.get("execution_mode") == "internal":
            value = dict(value)
            value["execution_mode"] = None
        return value

    @field_validator("role_limits")
    @classmethod
    def _validate_role_limits(
        cls, value: tuple[AgentRoleLimits, ...]
    ) -> tuple[AgentRoleLimits, ...]:
        identities = [item.role_identity for item in value]
        if len(set(identities)) != len(identities):
            raise ValueError("role_limits must not contain duplicate role identities")
        return tuple(sorted(value, key=lambda item: item.role_identity))

    @field_validator("role_bindings")
    @classmethod
    def _validate_role_bindings(
        cls, value: tuple[AgentRoleBinding, ...]
    ) -> tuple[AgentRoleBinding, ...]:
        identities = [item.role_identity for item in value]
        if len(set(identities)) != len(identities):
            raise ValueError("role_bindings must not contain duplicate role identities")
        return tuple(sorted(value, key=lambda item: item.role_identity))

    @field_validator(
        "workflow_definition_sha256",
        "definition_binding_sha256",
        "config_revision_id",
        mode="before",
    )
    @classmethod
    def _reject_suspicious_text(cls, value: object) -> object:
        """Keep hash/revision fields metadata-only and free of secret blobs."""
        if isinstance(value, str):
            lowered = value.lower()
            if "://" in value or lowered.startswith(("sk-", "bearer ")):
                raise ValueError(
                    "policy identity fields must not contain endpoint or credential data"
                )
        return value

    @model_validator(mode="after")
    def _validate_entitlement_and_budget(self) -> RunPolicySnapshot:
        if self.entitlement.decision != "allow":
            raise ValueError("a RunPolicySnapshot can only authorize an allowed entitlement")
        if self.execution_mode is None and not self.workflow_identity.startswith(
            "harness.canary@"
        ):
            raise ValueError("only the internal canary may have a NULL execution mode")
        limit_roles = {item.role_identity for item in self.role_limits}
        binding_roles = {item.role_identity for item in self.role_bindings}
        if limit_roles != binding_roles:
            raise ValueError("role_limits and role_bindings must contain the exact same roles")

        gates = self.creation_gates
        if (
            self.execution_mode in (ExecutionMode.shadow, ExecutionMode.harness)
            and not gates.harness_enabled
        ):
            raise ValueError("non-legacy execution requires harness_enabled=1")
        if (
            self.execution_mode in (ExecutionMode.shadow, ExecutionMode.harness)
            and not gates.dispatcher_enabled
        ):
            raise ValueError("non-legacy execution requires dispatcher_enabled=1")
        if self.execution_mode == ExecutionMode.harness and not gates.domain_publish_enabled:
            raise ValueError("harness execution requires domain_publish_enabled=1")
        return self

    def canonical(self) -> dict[str, Any]:
        """Return the only representation permitted for persistence/hash."""
        value = self.model_dump(mode="json")
        value["execution_mode"] = (
            self.execution_mode.value if self.execution_mode is not None else "internal"
        )
        value["creation_gates"] = {
            name: bool(getattr(self.creation_gates, name)) for name in _gate_names()
        }
        value["role_limits"] = [
            item.model_dump(mode="json")
            for item in sorted(self.role_limits, key=lambda item: item.role_identity)
        ]
        value["role_bindings"] = [
            item.canonical()
            for item in sorted(self.role_bindings, key=lambda item: item.role_identity)
        ]
        return value

    def canonical_json(self) -> str:
        """Serialize deterministically; NaN/Infinity are rejected."""
        return _canonical_json(self.canonical())

    def snapshot_sha256(self) -> str:
        """Hash the canonical snapshot envelope, not an incidental encoding."""
        return sha256_hex(self.canonical())

    def policy_hash(self) -> str:
        """Named alias used by repository code when storing ``policy_sha256``."""
        return self.snapshot_sha256()

    @classmethod
    def from_canonical(cls, value: object) -> RunPolicySnapshot:
        """Strictly decode persisted JSON/object data.

        ``extra='forbid'`` comes from ``StrictModel``.  Callers must verify a
        separately persisted hash before accepting the returned object.
        """
        return cls.model_validate(value)

    @classmethod
    def from_canonical_json(cls, value: str) -> RunPolicySnapshot:
        """Decode one JSON object without accepting trailing/ambiguous data."""
        import json

        if not isinstance(value, str):
            raise ValueError("policy snapshot JSON must be a string")

        def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError(f"duplicate JSON key {key!r} is forbidden")
                result[key] = item
            return result

        parsed = json.loads(
            value,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant {token} is forbidden")
            ),
        )
        if not isinstance(parsed, dict):
            raise ValueError("policy snapshot JSON must contain one object")
        snapshot = cls.from_canonical(parsed)
        if value != snapshot.canonical_json():
            raise ValueError("policy snapshot JSON is not canonical")
        return snapshot


__all__ = [
    "AgentLimits",
    "AgentRoleBinding",
    "AgentRoleLimits",
    "CreationGates",
    "EntitlementSnapshot",
    "EffectiveBudget",
    "POLICY_SNAPSHOT_SCHEMA_VERSION",
    "RunPolicySnapshot",
]
