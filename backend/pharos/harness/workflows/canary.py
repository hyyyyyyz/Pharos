"""The internal canary workflows: frozen H1/H1.5 routes and typed v3.

The canary is the H1 proof vehicle. It never reads real papers, never calls
the network, never writes legacy domain tables; it exercises the kernel --
deterministic steps, retryable failure, agent turns, approvals, mapped
fan-out, publication and usage accounting -- entirely through fakes, so an
operator canary can run with no real model and no real money.

``expand`` and ``reduce`` are the *trusted* half of the workflow: code that
maps frozen input into physical steps and steps into a run reduction. The
registry compiles the definition; these two functions stay in lockstep with
it by construction (the same module owns both).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, StrictStr, model_validator

from pharos.harness.capabilities import (
    CapabilityContract,
    CapabilitySchema,
    TrustedCapabilityRegistry,
    TypedCapabilityError,
)
from pharos.harness.contracts import (
    ArtifactSensitivity,
    AttemptErrorClass,
    CapabilityRisk,
    DeliverySemantics,
    DeliveryState,
    RetryableCapabilityError,
    RetryClass,
    RunOutcome,
    RunState,
    StepState,
    StrictModel,
)
from pharos.harness.definitions import (
    BudgetSpec,
    CapabilityDefinition,
    IdempotencyKind,
    ModelProfileDefinition,
    ModelRouteDefinition,
    RetryPolicy,
    RoleDefinition,
    StepDefinition,
    WorkflowDefinition,
)

SINGLETON = "__singleton__"

#: The modes the canary accepts; anything else fails validation at run start.
MODES = frozenset(
    {"success", "retry_then_success", "terminal_failure", "approval", "mapped", "agent"}
)

CANARY_KEY = "harness.canary"
CANARY_V1_IDENTITY = f"{CANARY_KEY}@1"
CANARY_V2_IDENTITY = f"{CANARY_KEY}@2"
CANARY_V3_IDENTITY = f"{CANARY_KEY}@3"
CANARY_ACTOR_ROLE = "canary_actor@1"
CANARY_DSH_ACTOR_ROLE = "canary_dsh_actor@1"
CANARY_FAKE_PROVIDER = "fake"
CANARY_FAKE_MODEL = "canary"
CANARY_DSH_PROVIDER = "pharos-fake"
CANARY_DSH_MODEL = "pharos-fake-canary"
CANARY_TYPED_ACTION_SCHEMA = "canary.action@2"
CANARY_TYPED_OBSERVATION_SCHEMA = "canary.observation@2"
CANARY_TYPED_MAPPED_SCHEMA = "canary.mapped_item@1"
CANARY_LEGACY_MODEL_PROFILE = ModelProfileDefinition(
    profile_key="canary",
    version=1,
    selection_policy="fixed",
    routes=(
        ModelRouteDefinition(
            route_key="legacy-in-process-canary",
            priority=1,
            provider=CANARY_FAKE_PROVIDER,
            model=CANARY_FAKE_MODEL,
            usage_source="system_shared",
            credential_policy="none",
            allowed_runtime_kinds=("in_process_fake",),
            reasoning_effort=None,
            max_output_tokens=1000,
        ),
    ),
)
CANARY_DSH_MODEL_PROFILE = ModelProfileDefinition(
    profile_key="pharos-fake-canary",
    version=1,
    selection_policy="fixed",
    routes=(
        ModelRouteDefinition(
            route_key="pharos-fake-canary-dsh",
            priority=1,
            provider=CANARY_DSH_PROVIDER,
            model=CANARY_DSH_MODEL,
            usage_source="system_shared",
            credential_policy="none",
            allowed_runtime_kinds=("dsh",),
            reasoning_effort=None,
            max_output_tokens=1000,
        ),
    ),
)


class CanaryActorOutput(StrictModel):
    """The closed typed output contract for the deterministic Agent canary."""

    ok: Literal[True]
    workflow: Literal["harness.canary"]
    step: Literal["actor_turn"]


class CanaryRunInput(StrictModel):
    """The bounded input carried into the isolated typed canary route."""

    mode: Literal[
        "success",
        "retry_then_success",
        "terminal_failure",
        "approval",
        "mapped",
        "agent",
    ]
    note: StrictStr = Field(min_length=1, max_length=2_000)
    # Run input crosses JSON where arrays are lists. The schema immediately
    # canonicalizes this value into immutable ValidatedPayload bytes.
    items: list[StrictStr] = Field(default_factory=list, max_length=8)


class CanaryCapabilityAction(StrictModel):
    """The exact payload the runner may expose to a canary capability."""

    workflow_key: Literal["harness.canary"]
    step_key: Literal[
        "start",
        "flaky",
        "approval_gate",
        "map_items",
        "collect",
        "publish",
        "finish",
    ]
    input: CanaryRunInput


class CanaryCapabilityObservation(StrictModel):
    """Closed union of every deterministic canary result shape."""

    ok: Literal[True] | None = None
    key: StrictStr | None = Field(default=None, max_length=512)
    attempt_recovered: Literal[True] | None = None
    published: Literal[True] | None = None
    publication_key: StrictStr | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def _require_one_exact_result_shape(self) -> CanaryCapabilityObservation:
        noop = (
            self.ok is True
            and self.attempt_recovered is None
            and self.published is None
            and self.publication_key is None
        )
        recovered = (
            self.ok is True
            and self.key is None
            and self.attempt_recovered is True
            and self.published is None
            and self.publication_key is None
        )
        published = (
            self.ok is None
            and self.key is None
            and self.attempt_recovered is None
            and self.published is True
            and self.publication_key is not None
        )
        if sum((noop, recovered, published)) != 1:
            raise ValueError("canary observation is not one declared result shape")
        return self


class CanaryMappedItem(StrictModel):
    """One mapped canary item; the stable key lives in its outer envelope."""

    value: StrictStr = Field(min_length=1, max_length=512)


def validate_canary_actor_output(value: Any) -> dict:
    """Validate and normalize one fake Agent result for Artifact storage."""
    return CanaryActorOutput.model_validate(value).model_dump(mode="json")


def resolve_canary_model_profile(profile: str) -> tuple[str, str]:
    """Legacy compatibility resolver; DSH authority is the Registry profile."""
    if profile == "canary":
        route = CANARY_LEGACY_MODEL_PROFILE.resolve_route("in_process_fake")
        return route.provider, route.model
    if profile == CANARY_DSH_MODEL_PROFILE.identity():
        route = CANARY_DSH_MODEL_PROFILE.resolve_route("dsh")
        return route.provider, route.model
    raise ValueError(f"unknown canary model profile {profile!r}")


def canary_model_profiles() -> list[ModelProfileDefinition]:
    """Return trusted model profiles used by the canary routes."""
    return [CANARY_LEGACY_MODEL_PROFILE, CANARY_DSH_MODEL_PROFILE]


def canary_capabilities() -> list[CapabilityDefinition]:
    """The frozen H1 definitions; never change these v1 contracts in place."""
    return [
        CapabilityDefinition(
            capability_key="canary.noop",
            version=1,
            action_schema="canary.action@1",
            observation_schema="canary.observation@1",
            risk=CapabilityRisk.read_public,
            idempotency=IdempotencyKind.inherently_idempotent,
        ),
        CapabilityDefinition(
            capability_key="canary.flaky",
            version=1,
            action_schema="canary.action@1",
            observation_schema="canary.observation@1",
            risk=CapabilityRisk.read_public,
            idempotency=IdempotencyKind.stable_key,
        ),
        CapabilityDefinition(
            capability_key="canary.publish",
            version=1,
            action_schema="canary.action@1",
            observation_schema="canary.observation@1",
            risk=CapabilityRisk.write_private,
            idempotency=IdempotencyKind.stable_key,
            sensitivity=ArtifactSensitivity.private,
        ),
    ]


def canary_typed_capabilities() -> list[CapabilityDefinition]:
    """The isolated v2 capability contracts used only by harness.canary@3.

    V1 remains the H1 compatibility canary.  V2 makes retry and delivery
    policy explicit so the typed runner never has to infer safety from an
    executor exception.  In particular, publication declares no retry class:
    a caller may replay its stable key deliberately, but the Harness may not
    blindly retry an outcome whose external boundary is unknown.
    """

    return [
        CapabilityDefinition(
            capability_key="canary.noop",
            version=2,
            action_schema=CANARY_TYPED_ACTION_SCHEMA,
            observation_schema=CANARY_TYPED_OBSERVATION_SCHEMA,
            risk=CapabilityRisk.read_public,
            idempotency=IdempotencyKind.inherently_idempotent,
            delivery=DeliverySemantics.local_exactly_once,
            retry_classes=(),
        ),
        CapabilityDefinition(
            capability_key="canary.flaky",
            version=2,
            action_schema=CANARY_TYPED_ACTION_SCHEMA,
            observation_schema=CANARY_TYPED_OBSERVATION_SCHEMA,
            risk=CapabilityRisk.read_public,
            idempotency=IdempotencyKind.stable_key,
            delivery=DeliverySemantics.local_exactly_once,
            retry_classes=(RetryClass.connect_timeout_unsent,),
        ),
        CapabilityDefinition(
            capability_key="canary.publish",
            version=2,
            action_schema=CANARY_TYPED_ACTION_SCHEMA,
            observation_schema=CANARY_TYPED_OBSERVATION_SCHEMA,
            risk=CapabilityRisk.write_private,
            idempotency=IdempotencyKind.stable_key,
            delivery=DeliverySemantics.local_exactly_once,
            retry_classes=(),
            sensitivity=ArtifactSensitivity.private,
        ),
    ]


def canary_typed_schemas() -> tuple[CapabilitySchema, ...]:
    """Return the closed code-owned schemas for the v2 capability set."""

    return (
        CapabilitySchema(
            identity=CANARY_TYPED_ACTION_SCHEMA,
            kind="action",
            model=CanaryCapabilityAction,
            validator_id="canary.action.validator@2",
        ),
        CapabilitySchema(
            identity=CANARY_TYPED_OBSERVATION_SCHEMA,
            kind="observation",
            model=CanaryCapabilityObservation,
            validator_id="canary.observation.validator@2",
        ),
        CapabilitySchema(
            identity=CANARY_TYPED_MAPPED_SCHEMA,
            kind="mapped",
            model=CanaryMappedItem,
            validator_id="canary.mapped-item.validator@1",
        ),
    )


def build_canary_capability_registry() -> TrustedCapabilityRegistry:
    """Seal the exact schema/definition pairs the typed canary may execute."""

    return TrustedCapabilityRegistry(
        schemas=canary_typed_schemas(),
        capabilities=canary_typed_capabilities(),
    )


def canary_roles() -> list[RoleDefinition]:
    return [
        RoleDefinition(
            role_key="canary_actor",
            version=1,
            prompt_template_version="canary-actor-zh@1",
            input_schema="canary.actor_in@1",
            output_schema="canary.actor_out@1",
            model_profile="canary",
            runtime_kind="in_process_fake",
            capability_allowlist=(),
            max_turns=2,
            max_tool_calls=2,
            token_budget=BudgetSpec(
                wall_seconds=30, model_calls=2, input_tokens=1000, output_tokens=1000
            ),
        ),
        RoleDefinition(
            role_key="canary_dsh_actor",
            version=1,
            prompt_template_version="canary-actor-zh@1",
            input_schema="canary.actor_in@1",
            output_schema="canary.actor_out@1",
            model_profile="pharos-fake-canary@1",
            runtime_kind="dsh",
            capability_allowlist=(),
            max_turns=2,
            max_tool_calls=2,
            token_budget=BudgetSpec(
                wall_seconds=30, model_calls=2, input_tokens=1000, output_tokens=1000
            ),
        ),
    ]


def _canary_workflow(
    *, version: int, actor_role: str, capability_version: int = 1
) -> WorkflowDefinition:
    capability_identities = tuple(
        f"canary.{key}@{capability_version}" for key in ("noop", "flaky", "publish")
    )
    return WorkflowDefinition(
        workflow_key=CANARY_KEY,
        version=version,
        input_schema="harness.canary_input@1",
        output_schema="harness.canary_output@1",
        internal_no_legacy_writer=True,
        allowed_capabilities=capability_identities,
        max_parallel_steps=2,
        default_budget=BudgetSpec(
            wall_seconds=60, model_calls=6, input_tokens=4_000, output_tokens=4_000, cost_micros=0
        ),
        steps=(
            StepDefinition(
                key="start",
                kind="deterministic",
                capability=f"canary.noop@{capability_version}",
                timeout_seconds=30,
            ),
            StepDefinition(
                key="flaky",
                kind="deterministic",
                capability=f"canary.flaky@{capability_version}",
                depends_on=("start",),
                optional=True,
                timeout_seconds=30,
                retry=RetryPolicy(
                    max_attempts=3,
                    backoff_seconds=0,
                    jitter_seconds=0,
                    retry_classes=(
                        (RetryClass.connect_timeout_unsent,)
                        if capability_version >= 2
                        else RetryPolicy().retry_classes
                    ),
                ),
            ),
            StepDefinition(
                key="approval_gate",
                kind="deterministic",
                capability=f"canary.publish@{capability_version}",
                depends_on=("flaky",),
                optional=True,
                approval_required=True,
                approval_on_reject="skip",
                timeout_seconds=30,
            ),
            StepDefinition(
                key="map_items",
                kind="mapped",
                capability=f"canary.noop@{capability_version}",
                depends_on=("flaky",),
                optional=True,
                max_fanout=8,
                timeout_seconds=30,
            ),
            StepDefinition(
                key="collect",
                kind="deterministic",
                capability=f"canary.noop@{capability_version}",
                depends_on=("approval_gate", "map_items"),
                fan_in="allow_partial",
                timeout_seconds=30,
            ),
            StepDefinition(
                key="actor_turn",
                kind="agent",
                role=actor_role,
                depends_on=("collect",),
                optional=True,
                timeout_seconds=30,
            ),
            StepDefinition(
                key="publish",
                kind="deterministic",
                capability=f"canary.publish@{capability_version}",
                depends_on=("actor_turn",),
                publish=True,
                timeout_seconds=30,
            ),
            StepDefinition(
                key="finish",
                kind="deterministic",
                capability=f"canary.noop@{capability_version}",
                depends_on=("publish",),
                timeout_seconds=30,
            ),
        ),
    )


def canary_workflow() -> WorkflowDefinition:
    """The original in-process fake canary, kept byte-for-byte compatible."""
    return _canary_workflow(version=1, actor_role=CANARY_ACTOR_ROLE)


def canary_dsh_workflow() -> WorkflowDefinition:
    """The inactive DSH-runtime canary route used by the H1.5 gate tests."""
    return _canary_workflow(version=2, actor_role=CANARY_DSH_ACTOR_ROLE)


def canary_typed_workflow() -> WorkflowDefinition:
    """The inactive typed-Artifact canary; operators must opt into v3."""

    return _canary_workflow(
        version=3,
        actor_role=CANARY_ACTOR_ROLE,
        capability_version=2,
    )


def _step_entry(key: str, definition: StepDefinition) -> dict:
    return {
        "definition_step_key": key,
        "instance_key": SINGLETON,
        "step_kind": definition.kind,
        "definition": definition.model_dump(mode="json"),
        "depends_on": list(definition.depends_on),
        "fan_in": definition.fan_in,
        "min_success_count": definition.min_success_count,
        "max_attempts": definition.retry.max_attempts if definition.retry else 3,
        "timeout_seconds": definition.timeout_seconds,
        "retry_policy": (definition.retry.model_dump(mode="json") if definition.retry else None),
        "approval_required": definition.approval_required,
    }


def expand(input: dict) -> list[dict]:
    """The trusted expansion: which physical steps this input produces.

    ``mode`` selects the interesting spine; ``items`` feeds the mapped step.
    Unselected optional steps are skipped at expansion time (never created),
    so the same run never pays for work its mode does not request.
    """
    mode = input.get("mode") or "success"
    if mode not in MODES:
        raise ValueError(f"unknown canary mode {mode!r}")
    workflow = canary_workflow()
    steps: list[dict] = []
    for key in (
        "start",
        "flaky" if mode == "retry_then_success" else None,
        "approval_gate" if mode == "approval" else None,
        "map_items" if mode == "mapped" else None,
        "collect",
        "actor_turn" if mode == "agent" else None,
        "publish",
        "finish",
    ):
        if key is None:
            continue
        definition = workflow.step(key)
        entry = _step_entry(key, definition)
        if key == "map_items":
            entry["instance_key"] = "batch"
            entry["definition"] = {
                **entry["definition"],
                "expand_items": list(input.get("items") or [])[:8],
            }
        steps.append(entry)
    return steps


def expand_dsh(input: dict) -> list[dict]:
    """Expand the v2 canary while retaining the same typed step contract."""
    # The shape is intentionally shared with v1; only the frozen workflow
    # version/role identity changes.  This prevents v1/v2 lookup mixing.
    mode = input.get("mode") or "success"
    if mode != "agent":
        raise ValueError("harness.canary@2 only accepts agent mode")
    workflow = canary_dsh_workflow()
    steps: list[dict] = []
    for key in (
        "start",
        "flaky" if mode == "retry_then_success" else None,
        "approval_gate" if mode == "approval" else None,
        "map_items" if mode == "mapped" else None,
        "collect",
        "actor_turn" if mode == "agent" else None,
        "publish",
        "finish",
    ):
        if key is None:
            continue
        definition = workflow.step(key)
        entry = _step_entry(key, definition)
        if key == "map_items":
            entry["instance_key"] = "batch"
            entry["definition"] = {
                **entry["definition"],
                "expand_items": list(input.get("items") or [])[:8],
            }
        steps.append(entry)
    return steps


def expand_typed(input: dict) -> list[dict]:
    """Expand the opt-in v3 route without reusing a v1 frozen definition."""

    mode = input.get("mode") or "success"
    if mode not in MODES:
        raise ValueError(f"unknown canary mode {mode!r}")
    workflow = canary_typed_workflow()
    steps: list[dict] = []
    for key in (
        "start",
        "flaky" if mode == "retry_then_success" else None,
        "approval_gate" if mode == "approval" else None,
        "map_items" if mode == "mapped" else None,
        "collect",
        "actor_turn" if mode == "agent" else None,
        "publish",
        "finish",
    ):
        if key is None:
            continue
        definition = workflow.step(key)
        entry = _step_entry(key, definition)
        if key == "map_items":
            entry["instance_key"] = "batch"
            entry["definition"] = {
                **entry["definition"],
                "expand_items": [{"value": value} for value in list(input.get("items") or [])[:8]],
            }
        steps.append(entry)
    return steps


def reduce(run: dict, step_rows: list[dict], now_us: int) -> tuple[RunState, RunOutcome | None]:
    """The deterministic run reduction for the canary.

    Follows the architecture's safety order: every leased/running Attempt must
    first resolve its delivery boundary; then an indeterminate external outcome
    wins over a later cancel request. Otherwise cancel/wait/failure/terminal
    work reduces normally.
    """
    states = [StepState(row["state"]) for row in step_rows]
    if not states:
        if run.get("cancel_requested_at") is not None:
            return RunState.cancelled, RunOutcome.incomplete
        return RunState.queued, None
    required_keys = {"start", "collect", "publish", "finish"}
    required = [row for row in step_rows if row["definition_step_key"] in required_keys]
    optional = [row for row in step_rows if row["definition_step_key"] not in required_keys]

    if any(row["state"] in (StepState.leased.value, StepState.running.value) for row in step_rows):
        return RunState.running, None
    if any(StepState(row["state"]) == StepState.indeterminate for row in step_rows):
        return RunState.indeterminate, RunOutcome.incomplete
    if run.get("cancel_requested_at") is not None:
        return RunState.cancelled, RunOutcome.incomplete
    if any(row["state"] == StepState.waiting_for_approval.value for row in step_rows):
        return RunState.waiting_for_approval, None
    # Optional DSH actor steps can still block the run: publish depends on
    # them, and treating an unavailable runtime as an optional skip would let
    # the run appear to progress while its required successor waits forever.
    if any(row["state"] == StepState.waiting_for_input.value for row in step_rows):
        return RunState.waiting_for_input, None
    if any(row["state"] in (StepState.retry_scheduled.value,) for row in step_rows):
        return RunState.running, None
    for row in required:
        if row["state"] == StepState.failed.value:
            return RunState.failed, RunOutcome.incomplete
    for row in required:
        if row["state"] not in (
            StepState.succeeded.value,
            StepState.skipped.value,
        ):
            return RunState.running, None
    # Optional terminal failures/skips contribute to a partial outcome; an
    # optional step that is still working keeps the run running.
    partial = any(
        row["state"] in (StepState.failed.value, StepState.skipped.value) for row in optional
    )
    for row in optional:
        if row["state"] not in (
            StepState.succeeded.value,
            StepState.failed.value,
            StepState.skipped.value,
            StepState.cancelled.value,
        ):
            return RunState.running, None
    return RunState.succeeded, RunOutcome.partial if partial else RunOutcome.complete


def canary_input(mode: str, **extra) -> dict:
    """A valid canary input envelope for the given mode."""
    input = {"mode": mode, "note": "kernel canary", "items": extra.get("items", ["a", "b"])}
    input.update({k: v for k, v in extra.items() if k != "items"})
    return input


class _NoopExecutor:
    """Succeeds deterministically; the canary's plain deterministic spine."""

    def execute(self, action: dict) -> dict:
        return {"ok": True, "key": action.get("idempotency_key")}


class _FlakyExecutor:
    """Fails the first attempt of a retry mode, then succeeds.

    The runner's idempotency key carries ``attemptN``, so this stays
    deterministic per attempt without any external state. The raised error is
    the typed retryable class, which is what the runner's retry policy keys on.
    """

    def execute(self, action: dict) -> dict:
        key = str(action.get("idempotency_key") or "")
        if key.endswith("attempt1"):
            raise RetryableCapabilityError("simulated transient failure")
        return {"ok": True, "attempt_recovered": True}


class _TypedFlakyExecutor(_FlakyExecutor):
    """The v2 retry signal is safe only before any delivery boundary."""

    def __init__(self, contract: CapabilityContract) -> None:
        self._contract = contract
        self._failed_once: set[str] = set()

    def execute(self, action: dict) -> Any:
        checked = self._contract.validate_action(action)
        key = str(checked.idempotency_key or "")
        if key not in self._failed_once:
            self._failed_once.add(key)
            return self._contract.fail(
                checked,
                TypedCapabilityError(
                    error_class=AttemptErrorClass.timeout,
                    code="canary_transient_unsent",
                    message="simulated definitely-unsent transient failure",
                    delivery_state=DeliveryState.NOT_STARTED,
                    retry_class=RetryClass.connect_timeout_unsent,
                ),
            )
        return {"ok": True, "attempt_recovered": True}


class _PublishExecutor:
    """Idempotent publication: never duplicates a publication key."""

    def __init__(self) -> None:
        self.published: dict[str, dict] = {}

    def execute(self, action: dict) -> dict:
        key = str(action.get("idempotency_key") or "")
        if key in self.published:
            return self.published[key]
        result = {"published": True, "publication_key": key}
        self.published[key] = result
        return result


def build_executors() -> dict[tuple[str, str], Any]:
    """Resolve both frozen v1 and isolated typed v2 capability executors."""
    definitions = {
        cap.identity(): cap for cap in (*canary_capabilities(), *canary_typed_capabilities())
    }
    typed_registry = build_canary_capability_registry()
    typed_flaky = definitions["canary.flaky@2"]
    return {
        (identity, definition.definition_hash()): executor
        for identity, definition, executor in (
            ("canary.noop@1", definitions["canary.noop@1"], _NoopExecutor()),
            ("canary.flaky@1", definitions["canary.flaky@1"], _FlakyExecutor()),
            ("canary.publish@1", definitions["canary.publish@1"], _PublishExecutor()),
            ("canary.noop@2", definitions["canary.noop@2"], _NoopExecutor()),
            (
                "canary.flaky@2",
                typed_flaky,
                _TypedFlakyExecutor(
                    typed_registry.require(
                        identity=typed_flaky.identity(),
                        definition_sha256=typed_flaky.definition_hash(),
                    )
                ),
            ),
            ("canary.publish@2", definitions["canary.publish@2"], _PublishExecutor()),
        )
    }
