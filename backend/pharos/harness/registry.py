"""Compile trusted workflow definitions against bounded policies.

Definitions are registered by trusted code only -- never user uploads. The
registry's job is to refuse, at startup, any definition whose DAG, budget,
permissions or idempotency contract could produce unbounded work, doubled
side effects or tools the workflow never authorised.
"""

from __future__ import annotations

from pharos.harness.contracts import DefinitionError, IdempotencyKind
from pharos.harness.definitions import (
    CapabilityDefinition,
    HarnessDefinitionSet,
    RoleDefinition,
    WorkflowDefinition,
)

_NO_RETRY_IDEMPOTENCY = {IdempotencyKind.stable_key, IdempotencyKind.inherently_idempotent}


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


class Registry:
    """Trusted code registers; startup compiles; runs resolve versions."""

    def __init__(self) -> None:
        self._workflows: dict[str, WorkflowDefinition] = {}
        self._roles: dict[str, RoleDefinition] = {}
        self._capabilities: dict[str, CapabilityDefinition] = {}

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
            if existing.model_dump(mode="json") != role.model_dump(mode="json"):
                raise DefinitionError(f"{identity} is already registered with a different role")
            return
        self._roles[identity] = role

    def register_capability(self, capability: CapabilityDefinition) -> None:
        identity = capability.identity()
        existing = self._capabilities.get(identity)
        if existing is not None:
            if existing.model_dump(mode="json") != capability.model_dump(mode="json"):
                raise DefinitionError(
                    f"{identity} is already registered with a different capability"
                )
            return
        self._capabilities[identity] = capability

    # ------------------------------------------------------------- resolve

    def workflow(self, identity: str) -> WorkflowDefinition | None:
        return self._workflows.get(identity)

    def role(self, identity: str) -> RoleDefinition | None:
        return self._roles.get(identity)

    def capability(self, identity: str) -> CapabilityDefinition | None:
        return self._capabilities.get(identity)

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

    def all_workflows(self) -> list[WorkflowDefinition]:
        return sorted(self._workflows.values(), key=lambda w: w.identity())

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
        for capability in self._capabilities.values():
            if capability.timeout_seconds <= 0 or capability.max_output_chars <= 0:
                raise DefinitionError(
                    f"{capability.identity()}: timeout and output cap must be finite"
                )
