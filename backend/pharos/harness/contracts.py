"""Core Harness enums, strict models, and typed errors.

Everything here is a *contract*, not behaviour: the closed vocabularies that
wire, schema, database and UI must agree on, and the error taxonomy callers
map to HTTP codes. Nothing in this module touches the database or the network.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """The one model every wire/domain contract derives from.

    ``extra="forbid"`` means an unknown field is a validation error, so a
    model that invents a field, or a client that sends one, fails loudly
    instead of silently dropping or smuggling data.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class ScopeType(StrEnum):
    user = "user"
    system = "system"


class RunState(StrEnum):
    queued = "queued"
    running = "running"
    waiting_for_approval = "waiting_for_approval"
    waiting_for_input = "waiting_for_input"
    paused = "paused"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    indeterminate = "indeterminate"


RUN_TERMINAL_STATES = frozenset(
    {RunState.succeeded, RunState.failed, RunState.cancelled, RunState.indeterminate}
)


class RunOutcome(StrEnum):
    complete = "complete"
    partial = "partial"
    incomplete = "incomplete"


class StepState(StrEnum):
    pending = "pending"
    ready = "ready"
    leased = "leased"
    running = "running"
    waiting_for_approval = "waiting_for_approval"
    waiting_for_input = "waiting_for_input"
    retry_scheduled = "retry_scheduled"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    skipped = "skipped"
    indeterminate = "indeterminate"


STEP_TERMINAL_STATES = frozenset(
    {
        StepState.succeeded,
        StepState.failed,
        StepState.cancelled,
        StepState.skipped,
        StepState.indeterminate,
    }
)


class AttemptState(StrEnum):
    leased = "leased"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    timed_out = "timed_out"
    cancelled = "cancelled"
    abandoned = "abandoned"
    blocked = "blocked"
    indeterminate = "indeterminate"


class DeliveryState(StrEnum):
    """Durable evidence for one Attempt's provider-delivery boundary."""

    NOT_STARTED = "not_started"
    UNKNOWN = "unknown"
    SENT = "sent"
    ACKNOWLEDGED = "acknowledged"
    RECONCILED = "reconciled"


ATTEMPT_TERMINAL_STATES = frozenset(
    {
        AttemptState.succeeded,
        AttemptState.failed,
        AttemptState.timed_out,
        AttemptState.cancelled,
        AttemptState.abandoned,
        AttemptState.blocked,
        AttemptState.indeterminate,
    }
)


class ApprovalState(StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    expired = "expired"
    cancelled = "cancelled"


class ActivationState(StrEnum):
    active = "active"
    deprecated = "deprecated"
    disabled = "disabled"


class ExecutionMode(StrEnum):
    legacy = "legacy"
    shadow = "shadow"
    harness = "harness"


class ArtifactSensitivity(StrEnum):
    public = "public"
    private = "private"
    local_only = "local_only"
    secret = "secret"


class CapabilityRisk(StrEnum):
    read_public = "read_public"
    read_private = "read_private"
    write_private = "write_private"
    external_side_effect = "external_side_effect"
    compute = "compute"


class IdempotencyKind(StrEnum):
    none = "none"
    stable_key = "stable_key"
    inherently_idempotent = "inherently_idempotent"


class DeliverySemantics(StrEnum):
    local_exactly_once = "local_exactly_once"
    provider_idempotent = "provider_idempotent"
    external_at_least_once = "external_at_least_once"


class RetryClass(StrEnum):
    rate_limited = "rate_limited"
    server_transient = "server_transient"
    connect_timeout_unsent = "connect_timeout_unsent"
    schema_repair = "schema_repair"


class AttemptErrorClass(StrEnum):
    validation = "validation"
    configuration = "configuration"
    auth = "auth"
    policy = "policy"
    provider = "provider"
    timeout = "timeout"
    budget = "budget"
    cancelled = "cancelled"
    bug = "bug"
    indeterminate = "indeterminate"


class ArtifactQuality(StrEnum):
    valid = "valid"
    partial = "partial"
    insufficient_evidence = "insufficient_evidence"
    invalid = "invalid"


class EvidenceLevel(StrEnum):
    metadata_only = "metadata_only"
    abstract_only = "abstract_only"
    unlocated = "unlocated"
    page = "page"


class ProducerKind(StrEnum):
    rule_summary = "rule_summary"
    model_inference = "model_inference"
    human_note = "human_note"
    quote = "quote"
    deterministic = "deterministic"


class UsageSource(StrEnum):
    official = "official"
    byok = "byok"
    system_shared = "system_shared"


class UsageKind(StrEnum):
    model_tokens = "model_tokens"
    search_request = "search_request"
    download_bytes = "download_bytes"
    translation_pages = "translation_pages"
    compute_ms = "compute_ms"


class WaitingReason(StrEnum):
    budget = "budget"
    configuration = "configuration"
    device_offline = "device_offline"
    user_input = "user_input"
    credential = "credential"


class Initiator(StrEnum):
    user = "user"
    schedule = "schedule"
    operator = "operator"
    child_run = "child_run"


class HarnessError(RuntimeError):
    """Base class for typed domain errors."""


class DefinitionError(HarnessError):
    """A workflow/role/capability definition failed compile-time validation."""


class StateError(HarnessError):
    """An illegal state transition was attempted."""


class NotFoundError(HarnessError):
    """An owner-scoped row does not exist (indistinguishable from foreign)."""


class StaleConfigError(HarnessError):
    """A writer ran against a config revision that is no longer current."""


class ConfigIntegrityError(HarnessError):
    """Persisted configuration is missing, corrupt, or internally invalid."""


class UnavailableError(HarnessError):
    """The Harness surface is gated off (flags, emergency stop)."""


class PolicyDeniedError(HarnessError):
    """A policy denied an action (deny beats ask beats allow)."""


class ApprovalConflictError(HarnessError):
    """An approval grant no longer matches the resource it was made for."""


class BudgetExhaustedError(HarnessError):
    """A hard budget bound was reached."""


class LeaseConflictError(HarnessError):
    """A lease/claim CAS lost its race."""


class RetryableCapabilityError(HarnessError):
    """A capability failed in a class its retry policy may retry."""


class IdempotencyConflictError(HarnessError):
    """The same idempotency key arrived with different input."""


class GatewayError(HarnessError):
    """The model gateway could not complete a call."""

    error_class: AttemptErrorClass = AttemptErrorClass.provider


class IndeterminateGatewayError(GatewayError):
    """The request may have reached the provider; outcome unknown."""

    error_class = AttemptErrorClass.indeterminate
