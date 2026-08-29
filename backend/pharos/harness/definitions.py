"""Immutable, versioned Workflow / Role / Capability definitions.

A definition is the frozen contract a Run executes against: its canonical JSON
and SHA-256 never change once registered. Activation -- which version receives
new Runs, and whether a workflow runs at all -- lives in the DB-backed
configuration revision, never in the definition row.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any, Literal

from pydantic import Field, StrictInt, StrictStr, model_validator

from pharos.harness.contracts import (
    ArtifactSensitivity,
    CapabilityRisk,
    DeliverySemantics,
    IdempotencyKind,
    RetryClass,
    StrictModel,
)

MAX_WORKFLOW_KEY = 64
MAX_ROLE_KEY = 64
MAX_CAPABILITY_KEY = 64
MAX_STEPS = 64
MAX_FANOUT = 200


def canonical_json(value: Any) -> str:
    """The one canonical JSON used for every definition and snapshot hash.

    Sorted keys, no whitespace, no ASCII escaping surprises: the same object
    always produces the same string across processes and restarts.
    """
    # ``allow_nan=False`` is deliberate: NaN/Infinity are not JSON and would
    # otherwise produce hashes that cannot be reproduced by another runtime.
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class BudgetSpec(StrictModel):
    """Hard bounds for one Run, Step or Attempt.

    Every field has a finite default; a definition that wants "unlimited" must
    say so by failing compile-time validation instead.
    """

    wall_seconds: float = 900.0
    model_calls: int = 24
    input_tokens: int = 300_000
    output_tokens: int = 60_000
    max_tool_calls: int = 8
    cost_micros: int = 100_000

    @classmethod
    def bounded_defaults(cls) -> BudgetSpec:
        return cls()


class RetryPolicy(StrictModel):
    max_attempts: int = 3
    backoff_seconds: float = 1.0
    backoff_factor: float = 2.0
    jitter_seconds: float = 0.5
    retry_classes: tuple[RetryClass, ...] = (
        RetryClass.rate_limited,
        RetryClass.server_transient,
        RetryClass.connect_timeout_unsent,
    )


class StepDefinition(StrictModel):
    key: str = Field(max_length=64)
    kind: Literal["deterministic", "agent", "mapped", "mapped_agent"]
    capability: str | None = None  # capability key@version for deterministic/mapped
    role: str | None = None  # role key@version for agent/mapped_agent
    depends_on: tuple[str, ...] = ()
    fan_in: Literal["all_success", "all_terminal", "min_success", "allow_partial"] | None = None
    min_success_count: int | None = None
    max_fanout: int | None = None
    optional: bool = False
    publish: bool = False
    approval_required: bool = False
    approval_on_reject: Literal["skip", "fail"] | None = None
    timeout_seconds: float | None = None
    retry: RetryPolicy | None = None
    budget: BudgetSpec = BudgetSpec.bounded_defaults()


class WorkflowDefinition(StrictModel):
    workflow_key: str = Field(max_length=MAX_WORKFLOW_KEY)
    version: int = Field(ge=1)
    input_schema: str
    output_schema: str
    scope: Literal["user", "system"] = "user"
    steps: tuple[StepDefinition, ...] = Field(max_length=MAX_STEPS)
    max_parallel_steps: int = Field(default=4, ge=1, le=64)
    default_budget: BudgetSpec = BudgetSpec.bounded_defaults()
    #: Capability keys the workflow may use at all. An agent role that wants a
    #: tool outside this set fails compile-time validation.
    allowed_capabilities: tuple[str, ...] = ()
    #: True only for internal/canary workflows that have no legacy domain
    #: writer; permits execution_mode=NULL on their route.
    internal_no_legacy_writer: bool = False

    def step(self, key: str) -> StepDefinition:
        for step in self.steps:
            if step.key == key:
                return step
        raise KeyError(key)

    def identity(self) -> str:
        return f"{self.workflow_key}@{self.version}"

    def definition_hash(self) -> str:
        return sha256_hex(self.model_dump(mode="json"))


class RoleDefinition(StrictModel):
    role_key: str = Field(max_length=MAX_ROLE_KEY)
    version: int = Field(ge=1)
    prompt_template_version: str
    input_schema: str
    output_schema: str
    model_profile: str
    # This is a trusted execution route, not a model-controlled hint.  Keep
    # the vocabulary closed so a malformed/future role cannot accidentally be
    # treated as the in-process fake runtime.
    runtime_kind: Literal["in_process_fake", "dsh"]
    capability_allowlist: tuple[str, ...] = ()
    max_turns: int = Field(default=4, ge=1)
    max_tool_calls: int = Field(default=8, ge=1)
    token_budget: BudgetSpec = BudgetSpec.bounded_defaults()
    approval_required: bool = False
    allowed_input_sensitivity: tuple[ArtifactSensitivity, ...] = (
        ArtifactSensitivity.public,
        ArtifactSensitivity.private,
    )

    def identity(self) -> str:
        return f"{self.role_key}@{self.version}"

    def canonical(self) -> dict[str, Any]:
        value = self.model_dump(mode="json")
        value["capability_allowlist"] = sorted(value["capability_allowlist"])
        value["allowed_input_sensitivity"] = sorted(value["allowed_input_sensitivity"])
        return value

    def definition_hash(self) -> str:
        return sha256_hex(self.canonical())


class CapabilityDefinition(StrictModel):
    capability_key: str = Field(max_length=MAX_CAPABILITY_KEY)
    version: int = Field(ge=1)
    action_schema: str
    observation_schema: str
    risk: CapabilityRisk = CapabilityRisk.read_public
    idempotency: IdempotencyKind = IdempotencyKind.none
    delivery: DeliverySemantics = DeliverySemantics.local_exactly_once
    retry_classes: tuple[RetryClass, ...] = ()
    timeout_seconds: float = Field(default=60.0, gt=0)
    max_output_chars: int = Field(default=200_000, gt=0)
    sensitivity: ArtifactSensitivity = ArtifactSensitivity.private
    requires_approval: bool = False

    def identity(self) -> str:
        return f"{self.capability_key}@{self.version}"

    def canonical(self) -> dict[str, Any]:
        value = self.model_dump(mode="json")
        value["retry_classes"] = sorted(value["retry_classes"])
        return value

    def definition_hash(self) -> str:
        return sha256_hex(self.canonical())


# Model profiles intentionally have a much smaller vocabulary than a generic
# provider configuration.  In particular, they contain no URL, header, key,
# or other credential material.  A route is an immutable *selection* record;
# credential resolution remains an application concern.
ModelRuntimeKind = Literal["in_process_fake", "dsh"]
ModelSelectionPolicy = Literal["fixed", "ordered_first_available"]
ModelReasoning = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]
ModelCredentialPolicy = Literal["none", "system_managed", "user_byok"]

_MAX_MODEL_KEY = 64
_MAX_MODEL_PROVIDER = 64
_MAX_MODEL_NAME = 128
_MAX_MODEL_ROUTES = 16
_MAX_MODEL_TOKENS = 1_000_000
_MODEL_ROUTE_BINDING_SCHEMA_VERSION = 1


def _model_identifier(value: str, *, field_name: str, max_length: int) -> str:
    """Validate provider/model identifiers without accepting endpoint data."""
    if (
        not value.strip()
        or value != value.strip()
        or not value.isascii()
        or any(
            char.isspace()
            or ord(char) < 32
            or ord(char) == 127
            or unicodedata.bidirectional(char)
            in {"RLO", "RLE", "LRO", "LRE", "PDF", "LRI", "RLI", "FSI", "PDI"}
            for char in value
        )
    ):
        raise ValueError(f"{field_name} must be a non-whitespace printable identifier")
    if len(value) > max_length:
        raise ValueError(f"{field_name} is too long")
    # A route is metadata, never a raw endpoint.  Model names may contain '/'
    # (for example, provider/model), so only URL delimiters are forbidden.
    if "://" in value or ":/" in value:
        raise ValueError(f"{field_name} must not contain an endpoint")
    if value.lower().startswith(("sk-", "bearer ")):
        raise ValueError(f"{field_name} must not contain credential material")
    return value


class ModelRouteDefinition(StrictModel):
    """One ordered, runtime-scoped provider/model route."""

    route_key: StrictStr = Field(
        min_length=1, max_length=_MAX_MODEL_KEY, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
    )
    priority: StrictInt = Field(ge=1, le=10_000)
    provider: StrictStr = Field(
        min_length=1,
        max_length=_MAX_MODEL_PROVIDER,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    model: StrictStr = Field(min_length=1, max_length=_MAX_MODEL_NAME)
    usage_source: Literal["official", "byok", "system_shared"]
    credential_policy: ModelCredentialPolicy
    allowed_runtime_kinds: tuple[ModelRuntimeKind, ...] = Field(min_length=1, max_length=2)
    reasoning_effort: ModelReasoning | None = None
    max_output_tokens: StrictInt | None = Field(default=None, gt=0, le=_MAX_MODEL_TOKENS)

    @model_validator(mode="after")
    def _validate_route(self) -> ModelRouteDefinition:
        if "@" in self.route_key:
            raise ValueError("route_key must not contain a version suffix")
        _model_identifier(self.provider, field_name="provider", max_length=_MAX_MODEL_PROVIDER)
        _model_identifier(self.model, field_name="model", max_length=_MAX_MODEL_NAME)
        if len(set(self.allowed_runtime_kinds)) != len(self.allowed_runtime_kinds):
            raise ValueError("allowed_runtime_kinds must not contain duplicates")
        if self.usage_source == "byok" and self.credential_policy != "user_byok":
            raise ValueError("byok usage_source requires user_byok credential_policy")
        if self.usage_source in {"official", "system_shared"}:
            if self.credential_policy == "user_byok":
                raise ValueError(
                    f"{self.usage_source} usage_source cannot use user_byok credentials"
                )
            if self.credential_policy == "none" and not (
                self.usage_source == "system_shared"
                and self.provider == "pharos-fake"
                and self.model == "pharos-fake-canary"
            ):
                raise ValueError(
                    f"{self.usage_source} usage_source requires system_managed credentials"
                )
        return self

    def canonical(self) -> dict[str, Any]:
        value = self.model_dump(mode="json")
        value["allowed_runtime_kinds"] = sorted(value["allowed_runtime_kinds"])
        return value

    def definition_hash(self) -> str:
        return sha256_hex(self.canonical())


class ModelProfileDefinition(StrictModel):
    """Immutable model routing policy referenced by a Role definition.

    ``ordered_first_available`` is ordered by the explicit route priority;
    input tuple order is not semantic and is normalized for hashing.
    """

    profile_key: StrictStr = Field(
        min_length=1, max_length=_MAX_MODEL_KEY, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
    )
    version: StrictInt = Field(ge=1, le=10_000)
    selection_policy: ModelSelectionPolicy
    routes: tuple[ModelRouteDefinition, ...] = Field(
        min_length=1, max_length=_MAX_MODEL_ROUTES
    )

    @model_validator(mode="after")
    def _validate_profile(self) -> ModelProfileDefinition:
        if "@" in self.profile_key:
            raise ValueError("profile_key must not contain a version suffix")
        route_keys = [route.route_key for route in self.routes]
        priorities = [route.priority for route in self.routes]
        if len(set(route_keys)) != len(route_keys):
            raise ValueError("model profile route_key values must be unique")
        if len(set(priorities)) != len(priorities):
            raise ValueError("model profile route priority values must be unique")
        if self.selection_policy == "fixed" and len(self.routes) != 1:
            raise ValueError("fixed model profile must contain exactly one route")
        return self

    def identity(self) -> str:
        return f"{self.profile_key}@{self.version}"

    def canonical(self) -> dict[str, Any]:
        value = self.model_dump(mode="json")
        value["routes"] = [
            route.canonical()
            for route in sorted(self.routes, key=lambda item: (item.priority, item.route_key))
        ]
        return value

    def definition_hash(self) -> str:
        return sha256_hex(self.canonical())

    def route_binding(self, route_key: str) -> dict[str, Any]:
        """Return the immutable binding envelope for one profile route.

        A route hash is a binding, rather than merely a hash of the route
        itself: the same route definition can be safely reused by two
        profiles while remaining distinguishable in an Attempt provenance
        record.  Keep this envelope deliberately free of credentials and
        endpoints; those are not fields in a ``ModelRouteDefinition`` and are
        resolved separately at execution time.
        """
        route = next((item for item in self.routes if item.route_key == route_key), None)
        if route is None:
            raise ValueError(f"model profile {self.identity()} has no route {route_key!r}")
        return {
            "schema_version": _MODEL_ROUTE_BINDING_SCHEMA_VERSION,
            "profile_key": self.profile_key,
            "profile_version": self.version,
            "profile_definition_hash": self.definition_hash(),
            "route_key": route.route_key,
            "route_definition": route.canonical(),
        }

    def route_hash(self, route_key: str) -> str:
        """Hash the canonical profile-scoped binding for ``route_key``."""
        return sha256_hex(self.route_binding(route_key))

    def routes_for_runtime(
        self, runtime_kind: ModelRuntimeKind
    ) -> tuple[ModelRouteDefinition, ...]:
        """Return runtime-compatible routes in deterministic priority order."""
        return tuple(
            sorted(
                (route for route in self.routes if runtime_kind in route.allowed_runtime_kinds),
                key=lambda item: (item.priority, item.route_key),
            )
        )

    def resolve_route(
        self,
        runtime_kind: ModelRuntimeKind,
        *,
        available_route_keys: frozenset[str] | set[str] | None = None,
    ) -> ModelRouteDefinition:
        """Resolve one route, requiring explicit availability for fallback policy."""
        candidates = self.routes_for_runtime(runtime_kind)
        if self.selection_policy == "ordered_first_available" and available_route_keys is None:
            raise ValueError(
                f"model profile {self.identity()} requires explicit route availability"
            )
        if available_route_keys is not None:
            candidates = tuple(
                route for route in candidates if route.route_key in available_route_keys
            )
        for route in candidates:
            return route
        raise ValueError(
            f"model profile {self.identity()} has no available route for {runtime_kind}"
        )


class HarnessDefinitionSet(StrictModel):
    """The legacy frozen set whose hash a config revision may reference.

    H1.5 profile storage is intentionally excluded by this temporary hard
    gate; the profile store cannot enter snapshots until the planned 0010
    contract separately freezes the profile binding/run policy while
    preserving the legacy snapshot hash.
    """

    workflows: tuple[WorkflowDefinition, ...] = ()
    roles: tuple[RoleDefinition, ...] = ()
    capabilities: tuple[CapabilityDefinition, ...] = ()

    def canonical_hash(self) -> str:
        return sha256_hex(self.model_dump(mode="json"))
