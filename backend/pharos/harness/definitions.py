"""Immutable, versioned Workflow / Role / Capability definitions.

A definition is the frozen contract a Run executes against: its canonical JSON
and SHA-256 never change once registered. Activation -- which version receives
new Runs, and whether a workflow runs at all -- lives in the DB-backed
configuration revision, never in the definition row.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import Field

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
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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


class HarnessDefinitionSet(StrictModel):
    """The complete frozen set whose hash a config revision may reference."""

    workflows: tuple[WorkflowDefinition, ...] = ()
    roles: tuple[RoleDefinition, ...] = ()
    capabilities: tuple[CapabilityDefinition, ...] = ()

    def canonical_hash(self) -> str:
        return sha256_hex(self.model_dump(mode="json"))
