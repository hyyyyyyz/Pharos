"""Compile trusted workflow definitions against bounded policies.

Definitions are registered by trusted code only -- never user uploads. The
registry's job is to refuse, at startup, any definition whose DAG, budget,
permissions or idempotency contract could produce unbounded work, doubled
side effects or tools the workflow never authorised.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pharos.harness.contracts import DefinitionError, IdempotencyKind
from pharos.harness.definitions import (
    CapabilityDefinition,
    HarnessDefinitionSet,
    ModelProfileDefinition,
    RoleDefinition,
    WorkflowDefinition,
    canonical_json,
    sha256_hex,
)

_NO_RETRY_IDEMPOTENCY = {IdempotencyKind.stable_key, IdempotencyKind.inherently_idempotent}
_LEGACY_CANARY_ROLE_HASH = "ecac38063f393dc3c8561269372c9c485755a0e4ae0870968d4d042cc7b8b7c1"
_INTERNAL_FAKE_ROUTE_PAIRS = frozenset(
    {
        ("pharos-fake", "pharos-fake-canary"),
        ("fake", "canary"),
    }
)


class _FrozenSequence(tuple):
    """Tuple-backed JSON array that remains friendly to legacy list checks."""

    def __eq__(self, other: object) -> bool:
        if isinstance(other, (list, tuple)):
            return tuple(self) == tuple(other)
        return super().__eq__(other)


def _freeze_json(value: Any) -> Any:
    """Recursively freeze a JSON value before exposing it to callers."""
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return _FrozenSequence(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    """Return a regular JSON-shaped copy of a frozen binding."""
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


@dataclass(frozen=True)
class CompiledWorkflowBinding:
    """The transitive, content-addressed closure of one workflow.

    A binding deliberately contains only definitions reachable from workflow
    steps: unused registry entries must not change the hash of an otherwise
    identical workflow.  The returned dictionaries are already canonical
    projections and must be treated as immutable by callers.
    """

    value: Mapping[str, Any]
    binding_sha256: str

    def __post_init__(self) -> None:
        # Do not rely on the dataclass's shallow ``frozen=True``: a caller can
        # otherwise mutate nested dicts/lists after the binding hash has been
        # authenticated.  Authenticate first, then expose only frozen values.
        if not isinstance(self.value, Mapping):
            raise DefinitionError("compiled workflow binding must be a JSON object")
        payload = _thaw_json(self.value)
        try:
            digest = sha256_hex(payload)
        except (TypeError, ValueError) as exc:
            raise DefinitionError("compiled workflow binding is not canonical JSON") from exc
        if digest != self.binding_sha256:
            raise DefinitionError("compiled workflow binding hash mismatch")
        if not isinstance(self.binding_sha256, str) or len(self.binding_sha256) != 64:
            raise DefinitionError("compiled workflow binding hash is invalid")
        _validate_binding_payload(payload)
        object.__setattr__(self, "value", _freeze_json(payload))

    @property
    def schema_version(self) -> int:
        return self.value["schema_version"]

    @property
    def workflow(self) -> dict[str, Any]:
        return self.value["workflow"]

    @property
    def capabilities(self) -> tuple[dict[str, Any], ...]:
        return tuple(self.value["capabilities"])

    @property
    def roles(self) -> tuple[dict[str, Any], ...]:
        return tuple(self.value["roles"])

    def canonical_json(self) -> str:
        from pharos.harness.definitions import canonical_json

        return canonical_json(_thaw_json(self.value))


def _validate_binding_payload(value: Mapping[str, Any]) -> None:
    """Validate the complete, typed, transitive binding envelope.

    This is deliberately independent of a live registry.  Persisted bindings
    must remain auditable after restart, and an untrusted/forged envelope must
    never be able to manufacture an empty or partial dependency closure.
    """
    required = {"schema_version", "workflow", "capabilities", "roles"}
    if set(value) != required or value.get("schema_version") != 1:
        raise DefinitionError("compiled workflow binding has an unsupported shape")
    workflow_record = value.get("workflow")
    if not isinstance(workflow_record, Mapping):
        raise DefinitionError("binding workflow record is invalid")
    _validate_definition_record(workflow_record, WorkflowDefinition, "workflow")
    workflow = WorkflowDefinition.model_validate(workflow_record["definition"])
    if (
        workflow_record.get("identity") != workflow.identity()
        or workflow_record.get("workflow_key") != workflow.workflow_key
        or workflow_record.get("version") != workflow.version
    ):
        raise DefinitionError("binding workflow identity metadata does not match definition")

    capabilities = value.get("capabilities")
    roles = value.get("roles")
    if not isinstance(capabilities, list) or not isinstance(roles, list):
        raise DefinitionError("binding closure lists are invalid")
    capability_by_id: dict[str, CapabilityDefinition] = {}
    for record in capabilities:
        if not isinstance(record, Mapping):
            raise DefinitionError("binding capability record is invalid")
        capability = _validate_definition_record(record, CapabilityDefinition, "capability")
        identity = capability.identity()
        if record.get("identity") != identity or identity in capability_by_id:
            raise DefinitionError("binding capabilities contain duplicate or mismatched identity")
        capability_by_id[identity] = capability
    role_by_id: dict[str, RoleDefinition] = {}
    profile_by_id: dict[str, ModelProfileDefinition] = {}
    for record in roles:
        if not isinstance(record, Mapping):
            raise DefinitionError("binding role record is invalid")
        role = _validate_definition_record(record, RoleDefinition, "role")
        identity = role.identity()
        if record.get("identity") != identity or identity in role_by_id:
            raise DefinitionError("binding roles contain duplicate or mismatched identity")
        allowlist = record.get("capability_allowlist")
        if allowlist != sorted(set(role.capability_allowlist)):
            raise DefinitionError(f"binding role {identity} capability allowlist is not exact")
        profile_record = record.get("model_profile")
        if not isinstance(profile_record, Mapping):
            raise DefinitionError(f"binding role {identity} has no model profile")
        profile = _validate_definition_record(
            profile_record, ModelProfileDefinition, "model profile"
        )
        if profile_record.get("identity") != profile.identity():
            raise DefinitionError(f"binding role {identity} model profile identity mismatch")
        if (
            profile.identity() in profile_by_id
            and profile_by_id[profile.identity()].definition_hash() != profile.definition_hash()
        ):
            raise DefinitionError(
                f"binding contains conflicting model profile {profile.identity()}"
            )
        profile_by_id[profile.identity()] = profile
        if role.model_profile != profile.identity() and not (
            role.identity() == "canary_actor@1"
            and role.model_profile == "canary"
            and profile.identity() == "canary@1"
        ):
            raise DefinitionError(
                f"binding role {identity} model profile does not match definition"
            )
        validate_role_model_profile(role, profile)
        for capability_id in allowlist:
            if capability_id not in capability_by_id:
                raise DefinitionError(
                    f"binding role {identity} references missing capability {capability_id}"
                )
            if capability_id not in workflow.allowed_capabilities:
                raise DefinitionError(
                    f"binding role {identity} uses capability {capability_id} "
                    "outside the workflow allowlist"
                )
        role_by_id[identity] = role

    expected_capabilities: set[str] = set()
    expected_roles: set[str] = set()
    for step in workflow.steps:
        if step.kind in ("deterministic", "mapped"):
            if step.capability is None:
                raise DefinitionError(f"binding workflow step {step.key} has no capability")
            if step.capability not in workflow.allowed_capabilities:
                raise DefinitionError(
                    f"binding workflow step {step.key} uses capability {step.capability} "
                    "outside the workflow allowlist"
                )
            expected_capabilities.add(step.capability)
        else:
            if step.role is None:
                raise DefinitionError(f"binding workflow step {step.key} has no role")
            expected_roles.add(step.role)
    for role in role_by_id.values():
        expected_capabilities.update(role.capability_allowlist)
    if set(capability_by_id) != expected_capabilities or set(role_by_id) != expected_roles:
        raise DefinitionError("binding closure is incomplete or contains unreachable definitions")
    if [item.get("identity") for item in capabilities] != sorted(capability_by_id):
        raise DefinitionError("binding capabilities are not in canonical order")
    if [item.get("identity") for item in roles] != sorted(role_by_id):
        raise DefinitionError("binding roles are not in canonical order")


def _validate_definition_record(record: Mapping[str, Any], definition_type: Any, label: str) -> Any:
    required = {"identity", "definition_sha256", "definition"}
    extras = {
        "workflow": {"workflow_key", "version"},
        "role": {"model_profile", "capability_allowlist"},
    }.get(label, set())
    allowed = required | extras
    if set(record) != allowed:
        raise DefinitionError(f"binding {label} record has an unexpected shape")
    raw = record.get("definition")
    digest = record.get("definition_sha256")
    if not isinstance(raw, Mapping) or not isinstance(digest, str):
        raise DefinitionError(f"binding {label} record has invalid metadata")
    try:
        parsed = definition_type.model_validate(raw)
    except (TypeError, ValueError) as exc:
        raise DefinitionError(f"binding {label} definition is invalid") from exc
    canonical = (
        parsed.canonical() if hasattr(parsed, "canonical") else parsed.model_dump(mode="json")
    )
    if canonical_json(canonical) != canonical_json(raw) or sha256_hex(canonical) != digest:
        raise DefinitionError(f"binding {label} definition hash/canonical form mismatch")
    return parsed


def _dependency_edges(workflow: WorkflowDefinition) -> list[tuple[str, str]]:
    return [(dependency, step.key) for step in workflow.steps for dependency in step.depends_on]


def _reject_cycle(workflow: WorkflowDefinition) -> None:
    edges = _dependency_edges(workflow)
    adjacency: dict[str, list[str]] = {step.key: [] for step in workflow.steps}
    for before, after in edges:
        adjacency[after].append(before)
    state: dict[str, int] = {}

    def visit(node: str) -> None:
        state[node] = 1
        for neighbour in adjacency[node]:
            if state.get(neighbour) == 1:
                raise DefinitionError(
                    f"{workflow.identity()}: dependency cycle at {node} -> {neighbour}"
                )
            if state.get(neighbour) == 0:
                visit(neighbour)
        state[node] = 2

    for key in adjacency:
        state.setdefault(key, 0)
    for key in list(adjacency):
        if state[key] == 0:
            visit(key)


def _reject_bad_references(workflow: WorkflowDefinition) -> None:
    keys = {step.key for step in workflow.steps}
    if len(keys) != len(workflow.steps):
        raise DefinitionError(f"{workflow.identity()}: duplicate step keys")
    for step in workflow.steps:
        for dependency in step.depends_on:
            if dependency not in keys:
                raise DefinitionError(
                    f"{workflow.identity()}: step {step.key} depends on unknown step {dependency}"
                )


def _reject_unbounded(workflow: WorkflowDefinition) -> None:
    for step in workflow.steps:
        if step.kind in ("mapped", "mapped_agent") and (
            step.max_fanout is None or step.max_fanout < 1
        ):
            raise DefinitionError(
                f"{workflow.identity()}: mapped step {step.key} needs a bounded max_fanout"
            )
        if step.timeout_seconds is None and step.budget.wall_seconds <= 0:
            raise DefinitionError(f"{workflow.identity()}: step {step.key} has no time bound")
        if step.retry is not None and step.retry.max_attempts < 1:
            raise DefinitionError(f"{workflow.identity()}: step {step.key} retry needs attempts")


def _reject_missing_fan_in(workflow: WorkflowDefinition) -> None:
    """Every consumer of a mapped step must declare its aggregation policy."""
    mapped = {step.key for step in workflow.steps if step.kind in ("mapped", "mapped_agent")}
    for step in workflow.steps:
        if not mapped.intersection(step.depends_on):
            continue
        if step.fan_in is None:
            raise DefinitionError(
                f"{workflow.identity()}: step {step.key} consumes a mapped step "
                "and must declare fan_in"
            )
        if step.fan_in == "min_success" and (
            step.min_success_count is None or step.min_success_count < 1
        ):
            raise DefinitionError(
                f"{workflow.identity()}: step {step.key} uses min_success and needs "
                "a positive min_success_count"
            )
        if step.fan_in != "min_success" and step.min_success_count is not None:
            raise DefinitionError(
                f"{workflow.identity()}: step {step.key} declares min_success_count "
                "for a non-min_success fan-in"
            )


def _reject_orphan_refs(workflow: WorkflowDefinition, registry: Registry) -> None:
    for step in workflow.steps:
        if step.kind in ("deterministic", "mapped"):
            if step.capability is None:
                raise DefinitionError(f"{workflow.identity()}: step {step.key} needs a capability")
            assert step.capability is not None
            registry.require_capability(step.capability)
            if step.capability not in workflow.allowed_capabilities:
                raise DefinitionError(
                    f"{workflow.identity()}: step {step.key} uses capability "
                    f"{step.capability} outside the workflow allowlist"
                )
        else:
            if step.role is None:
                raise DefinitionError(f"{workflow.identity()}: step {step.key} needs a role")
            role = registry.require_role(step.role)
            extra = set(role.capability_allowlist) - set(workflow.allowed_capabilities)
            if extra:
                raise DefinitionError(
                    f"{workflow.identity()}: role {step.role} uses tools the workflow "
                    f"never authorised: {sorted(extra)}"
                )


def _reject_side_effect_contracts(workflow: WorkflowDefinition, registry: Registry) -> None:
    for step in workflow.steps:
        if step.kind not in ("deterministic", "mapped"):
            continue
        assert step.capability is not None
        capability = registry.require_capability(step.capability)
        if step.publish and capability.idempotency not in _NO_RETRY_IDEMPOTENCY:
            raise DefinitionError(
                f"{workflow.identity()}: publish step {step.key} uses capability "
                f"{capability.identity()} with no idempotency strategy"
            )
        if (
            step.retry is not None
            and step.retry.max_attempts > 1
            and capability.idempotency not in _NO_RETRY_IDEMPOTENCY
            and capability.retry_classes
        ):
            raise DefinitionError(
                f"{workflow.identity()}: retryable step {step.key} runs "
                f"non-idempotent capability {capability.identity()}"
            )


def _reject_approval_gaps(workflow: WorkflowDefinition) -> None:
    for step in workflow.steps:
        if step.approval_required and step.approval_on_reject not in ("skip", "fail"):
            raise DefinitionError(
                f"{workflow.identity()}: step {step.key} requires approval but "
                "defines no reject/expire branch"
            )


def validate_role_model_profile(
    role: RoleDefinition, profile: ModelProfileDefinition
) -> None:
    """Validate one exact role/profile runtime binding without a live registry.

    The one historical exception is the H1 in-process canary role.  Keeping
    that exception keyed by both role identity and exact bare value prevents
    the old alias from becoming a general-purpose profile escape hatch.
    """
    legacy_canary = role.identity() == "canary_actor@1" and role.model_profile == "canary"
    if legacy_canary:
        if sha256_hex(role.model_dump(mode="json")) != _LEGACY_CANARY_ROLE_HASH:
            raise DefinitionError(
                "canary_actor@1 bare canary role does not match its frozen legacy definition"
            )
        if profile.identity() != "canary@1":
            raise DefinitionError("canary_actor@1 must resolve to canary@1")
    elif role.model_profile != profile.identity():
        raise DefinitionError(
            f"{role.identity()}: model profile {profile.identity()} does not match role"
        )

    if role.runtime_kind == "dsh":
        if not all("dsh" in route.allowed_runtime_kinds for route in profile.routes):
            raise DefinitionError(
                f"{role.identity()}: every route in model profile {profile.identity()} "
                "must allow dsh"
            )
        return

    if not all("in_process_fake" in route.allowed_runtime_kinds for route in profile.routes):
        raise DefinitionError(
            f"{role.identity()}: every route in model profile {profile.identity()} "
            "must allow in_process_fake"
        )
    for route in profile.routes:
        if (route.provider, route.model) not in _INTERNAL_FAKE_ROUTE_PAIRS:
            raise DefinitionError(
                f"{role.identity()}: in_process_fake profiles must use the internal fake route"
            )
        if set(route.allowed_runtime_kinds) != {"in_process_fake"}:
            raise DefinitionError(
                f"{role.identity()}: internal fake routes are in_process_fake-only"
            )


def _reject_role_profiles(registry: Registry) -> None:
    """Require every role to resolve to an exact runtime-compatible profile."""
    for role in registry._roles.values():  # noqa: SLF001 -- compiler invariant
        if role.model_profile == "canary" and role.identity() == "canary_actor@1":
            profile = registry.require_model_profile("canary@1")
        else:
            if "@" not in role.model_profile:
                raise DefinitionError(
                    f"{role.identity()}: model profile must be a versioned identity"
                )
            profile = registry.require_model_profile(role.model_profile)
        validate_role_model_profile(role, profile)


class Registry:
    """Trusted code registers; startup compiles; runs resolve versions."""

    def __init__(self) -> None:
        self._workflows: dict[str, WorkflowDefinition] = {}
        self._roles: dict[str, RoleDefinition] = {}
        self._capabilities: dict[str, CapabilityDefinition] = {}
        self._model_profiles: dict[str, ModelProfileDefinition] = {}

    # ------------------------------------------------------------- register

    def register(self, workflow: WorkflowDefinition) -> None:
        identity = workflow.identity()
        existing = self._workflows.get(identity)
        if existing is not None:
            if existing.definition_hash() != workflow.definition_hash():
                raise DefinitionError(
                    f"{identity} is already registered with a different definition hash"
                )
            return
        self._workflows[identity] = workflow

    def register_role(self, role: RoleDefinition) -> None:
        identity = role.identity()
        existing = self._roles.get(identity)
        if existing is not None:
            if existing.definition_hash() != role.definition_hash():
                raise DefinitionError(f"{identity} is already registered with a different role")
            return
        self._roles[identity] = role

    def register_capability(self, capability: CapabilityDefinition) -> None:
        identity = capability.identity()
        existing = self._capabilities.get(identity)
        if existing is not None:
            if existing.definition_hash() != capability.definition_hash():
                raise DefinitionError(
                    f"{identity} is already registered with a different capability"
                )
            return
        self._capabilities[identity] = capability

    def register_model_profile(self, profile: ModelProfileDefinition) -> None:
        identity = profile.identity()
        existing = self._model_profiles.get(identity)
        if existing is not None:
            if existing.definition_hash() != profile.definition_hash():
                raise DefinitionError(
                    f"{identity} is already registered with a different model profile hash"
                )
            return
        self._model_profiles[identity] = profile

    # ------------------------------------------------------------- resolve

    def workflow(self, identity: str) -> WorkflowDefinition | None:
        return self._workflows.get(identity)

    def role(self, identity: str) -> RoleDefinition | None:
        return self._roles.get(identity)

    def capability(self, identity: str) -> CapabilityDefinition | None:
        return self._capabilities.get(identity)

    def model_profile(self, identity: str) -> ModelProfileDefinition | None:
        """Resolve only an exact ``profile_key@version`` identity."""
        return self._model_profiles.get(identity)

    def resolve_model_profile(self, identity: str) -> ModelProfileDefinition | None:
        """Named resolver counterpart to ``require_model_profile``."""
        return self.model_profile(identity)

    def require_capability(self, identity: str) -> CapabilityDefinition:
        capability = self._capabilities.get(identity)
        if capability is None:
            raise DefinitionError(f"unknown capability {identity}")
        return capability

    def require_role(self, identity: str) -> RoleDefinition:
        role = self._roles.get(identity)
        if role is None:
            raise DefinitionError(f"unknown role {identity}")
        return role

    def require_workflow(self, identity: str) -> WorkflowDefinition:
        workflow = self._workflows.get(identity)
        if workflow is None:
            raise DefinitionError(f"unknown workflow {identity}")
        return workflow

    def require_model_profile(self, identity: str) -> ModelProfileDefinition:
        profile = self._model_profiles.get(identity)
        if profile is None:
            raise DefinitionError(f"unknown model profile {identity}")
        return profile

    def resolve_role_model_profile(self, role: RoleDefinition) -> ModelProfileDefinition:
        """Resolve a role profile, including one frozen legacy canary alias.

        ``canary_actor@1`` predates model-profile persistence and its raw role
        JSON/hash is part of the H1 contract.  It may therefore use the bare
        alias only when its exact frozen definition is present and a formal
        ``canary@1`` profile has been registered.  Every other bare alias is
        rejected; persisted bindings always contain the resolved identity.
        """
        if role.model_profile == "canary" and role.identity() == "canary_actor@1":
            if sha256_hex(role.model_dump(mode="json")) != _LEGACY_CANARY_ROLE_HASH:
                raise DefinitionError(
                    "canary_actor@1 bare canary role does not match its frozen legacy definition"
                )
            return self.require_model_profile("canary@1")
        if "@" not in role.model_profile:
            raise DefinitionError(f"{role.identity()}: model profile must be versioned")
        return self.require_model_profile(role.model_profile)

    def all_workflows(self) -> list[WorkflowDefinition]:
        return sorted(self._workflows.values(), key=lambda w: w.identity())

    def all_model_profiles(self) -> list[ModelProfileDefinition]:
        return sorted(self._model_profiles.values(), key=lambda profile: profile.identity())

    # ---------------------------------------------------------- bindings

    def compile_workflow_binding(self, identity: str) -> CompiledWorkflowBinding:
        """Compile the exact transitive definition closure for ``identity``.

        The registry is trusted application code, but references are still
        resolved explicitly here.  A missing or bare reference is an error;
        in particular, a role's model profile must resolve to a registered
        versioned profile and every capability in its allowlist must exist.
        This keeps persisted bindings independently auditable and prevents a
        later registry entry from changing an already compiled hash.
        """
        workflow = self.require_workflow(identity)

        capability_ids: set[str] = set()
        role_ids: set[str] = set()
        for step in workflow.steps:
            if step.kind in ("deterministic", "mapped"):
                if step.capability is None:
                    raise DefinitionError(
                        f"{workflow.identity()}: step {step.key} needs a capability"
                    )
                capability_ids.add(step.capability)
                if step.capability not in workflow.allowed_capabilities:
                    raise DefinitionError(
                        f"{workflow.identity()}: step {step.key} uses capability "
                        f"{step.capability} outside the workflow allowlist"
                    )
            else:
                if step.role is None:
                    raise DefinitionError(f"{workflow.identity()}: step {step.key} needs a role")
                role_ids.add(step.role)

        capabilities: dict[str, CapabilityDefinition] = {}
        for capability_id in sorted(capability_ids):
            capability = self.require_capability(capability_id)
            capabilities[capability.identity()] = capability

        roles: dict[str, RoleDefinition] = {}
        profiles: dict[str, ModelProfileDefinition] = {}
        role_profile_ids: dict[str, str] = {}
        for role_id in sorted(role_ids):
            role = self.require_role(role_id)
            roles[role.identity()] = role
            profile = self.resolve_role_model_profile(role)
            profiles[profile.identity()] = profile
            role_profile_ids[role.identity()] = profile.identity()
            for capability_id in role.capability_allowlist:
                if capability_id not in workflow.allowed_capabilities:
                    raise DefinitionError(
                        f"{workflow.identity()}: role {role.identity()} uses capability "
                        f"{capability_id} outside the workflow allowlist"
                    )
                capability = self.require_capability(capability_id)
                capabilities[capability.identity()] = capability

        def definition_record(definition: Any, *, identity_key: str) -> dict[str, Any]:
            if hasattr(definition, "canonical"):
                payload = definition.canonical()
            else:
                payload = definition.model_dump(mode="json")
            return {
                "identity": identity_key,
                "definition_sha256": definition.definition_hash(),
                "definition": payload,
            }

        workflow_record = {
            "identity": workflow.identity(),
            "workflow_key": workflow.workflow_key,
            "version": workflow.version,
            "definition_sha256": workflow.definition_hash(),
            "definition": workflow.model_dump(mode="json"),
        }
        role_records: list[dict[str, Any]] = []
        for role in roles.values():
            resolved_profile = profiles.get(role_profile_ids[role.identity()])
            assert resolved_profile is not None  # populated by the resolver above
            role_records.append(
                {
                    **definition_record(role, identity_key=role.identity()),
                    "model_profile": definition_record(
                        resolved_profile, identity_key=resolved_profile.identity()
                    ),
                    # Keep this projection explicit: consumers must not infer
                    # permissions from a model profile or role JSON shape.
                    "capability_allowlist": sorted(role.capability_allowlist),
                }
            )

        binding = {
            "schema_version": 1,
            "workflow": workflow_record,
            "capabilities": [
                definition_record(capability, identity_key=capability.identity())
                for capability in sorted(capabilities.values(), key=lambda item: item.identity())
            ],
            "roles": sorted(role_records, key=lambda item: item["identity"]),
        }
        from pharos.harness.definitions import sha256_hex

        return CompiledWorkflowBinding(value=binding, binding_sha256=sha256_hex(binding))

    def snapshot(self) -> HarnessDefinitionSet:
        """The frozen set a config revision pins by hash."""
        return HarnessDefinitionSet(
            workflows=tuple(self.all_workflows()),
            roles=tuple(sorted(self._roles.values(), key=lambda r: r.identity())),
            capabilities=tuple(sorted(self._capabilities.values(), key=lambda c: c.identity())),
        )

    # -------------------------------------------------------------- compile

    def compile(self) -> None:
        """Validate every registered definition; raise on the first problem."""
        for workflow in self.all_workflows():
            _reject_cycle(workflow)
            _reject_bad_references(workflow)
            _reject_unbounded(workflow)
            _reject_missing_fan_in(workflow)
            _reject_orphan_refs(workflow, self)
            _reject_side_effect_contracts(workflow, self)
            _reject_approval_gaps(workflow)
        for role in self._roles.values():
            if role.runtime_kind not in ("in_process_fake", "dsh"):
                raise DefinitionError(
                    f"{role.identity()}: unknown runtime_kind {role.runtime_kind!r}"
                )
            if role.max_turns < 1 or role.max_tool_calls < 1:
                raise DefinitionError(f"{role.identity()}: bounded turns/tool calls required")
            if role.token_budget.wall_seconds <= 0:
                raise DefinitionError(f"{role.identity()}: needs a finite time budget")
        _reject_role_profiles(self)
        for capability in self._capabilities.values():
            if capability.timeout_seconds <= 0 or capability.max_output_chars <= 0:
                raise DefinitionError(
                    f"{capability.identity()}: timeout and output cap must be finite"
                )


def compile_workflow_binding(registry: Registry, identity: str) -> CompiledWorkflowBinding:
    """Functional convenience wrapper for callers that do not own a Registry."""
    return registry.compile_workflow_binding(identity)
