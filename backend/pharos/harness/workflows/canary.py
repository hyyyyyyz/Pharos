"""The internal canary workflow: harness.canary@1.

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

from pharos.harness.contracts import (
    ArtifactSensitivity,
    CapabilityRisk,
    RetryableCapabilityError,
    RunOutcome,
    RunState,
    StepState,
)
from pharos.harness.definitions import (
    BudgetSpec,
    CapabilityDefinition,
    IdempotencyKind,
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


def canary_capabilities() -> list[CapabilityDefinition]:
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


def canary_roles() -> list[RoleDefinition]:
    return [
        RoleDefinition(
            role_key="canary_actor",
            version=1,
            prompt_template_version="canary-actor-zh@1",
            input_schema="canary.actor_in@1",
            output_schema="canary.actor_out@1",
            model_profile="canary",
            capability_allowlist=(),
            max_turns=2,
            max_tool_calls=2,
            token_budget=BudgetSpec(
                wall_seconds=30, model_calls=2, input_tokens=1000, output_tokens=1000
            ),
        )
    ]


def canary_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        workflow_key=CANARY_KEY,
        version=1,
        input_schema="harness.canary_input@1",
        output_schema="harness.canary_output@1",
        internal_no_legacy_writer=True,
        allowed_capabilities=("canary.noop@1", "canary.flaky@1", "canary.publish@1"),
        max_parallel_steps=2,
        default_budget=BudgetSpec(
            wall_seconds=60, model_calls=6, input_tokens=4_000, output_tokens=4_000, cost_micros=0
        ),
        steps=(
            StepDefinition(
                key="start",
                kind="deterministic",
                capability="canary.noop@1",
                timeout_seconds=30,
            ),
            StepDefinition(
                key="flaky",
                kind="deterministic",
                capability="canary.flaky@1",
                depends_on=("start",),
                optional=True,
                timeout_seconds=30,
                retry=RetryPolicy(max_attempts=3, backoff_seconds=0, jitter_seconds=0),
            ),
            StepDefinition(
                key="approval_gate",
                kind="deterministic",
                capability="canary.publish@1",
                depends_on=("flaky",),
                optional=True,
                approval_required=True,
                approval_on_reject="skip",
                timeout_seconds=30,
            ),
            StepDefinition(
                key="map_items",
                kind="mapped",
                capability="canary.noop@1",
                depends_on=("flaky",),
                optional=True,
                max_fanout=8,
                timeout_seconds=30,
            ),
            StepDefinition(
                key="collect",
                kind="deterministic",
                capability="canary.noop@1",
                depends_on=("approval_gate", "map_items"),
                fan_in="allow_partial",
                timeout_seconds=30,
            ),
            StepDefinition(
                key="actor_turn",
                kind="agent",
                role="canary_actor@1",
                depends_on=("collect",),
                optional=True,
                timeout_seconds=30,
            ),
            StepDefinition(
                key="publish",
                kind="deterministic",
                capability="canary.publish@1",
                depends_on=("actor_turn",),
                publish=True,
                timeout_seconds=30,
            ),
            StepDefinition(
                key="finish",
                kind="deterministic",
                capability="canary.noop@1",
                depends_on=("publish",),
                timeout_seconds=30,
            ),
        ),
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


def reduce(run: dict, step_rows: list[dict], now_us: int) -> tuple[RunState, RunOutcome | None]:
    """The deterministic run reduction for the canary.

    Follows the architecture's reduction order: cancel wins; any indeterminate
    step makes the run indeterminate (an unknown external outcome is never
    silently downgraded); waiting states surface before running; required
    failure fails; everything terminal succeeds, with partial outcome when an
    optional branch failed or was skipped.
    """
    if run.get("cancel_requested_at") is not None:
        return RunState.cancelled, RunOutcome.incomplete
    states = [StepState(row["state"]) for row in step_rows]
    if not states:
        return RunState.queued, None
    required_keys = {"start", "collect", "publish", "finish"}
    required = [row for row in step_rows if row["definition_step_key"] in required_keys]
    optional = [row for row in step_rows if row["definition_step_key"] not in required_keys]

    if any(StepState(row["state"]) == StepState.indeterminate for row in step_rows):
        return RunState.indeterminate, RunOutcome.incomplete
    if any(row["state"] == StepState.waiting_for_approval.value for row in step_rows):
        return RunState.waiting_for_approval, None
    if any(row["state"] == StepState.waiting_for_input.value for row in required):
        return RunState.waiting_for_input, None
    if any(
        row["state"]
        in (
            StepState.leased.value,
            StepState.running.value,
            StepState.retry_scheduled.value,
        )
        for row in step_rows
    ):
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


def build_executors() -> dict:
    """The capability executors the runner resolves the canary to."""
    return {
        "canary.noop@1": _NoopExecutor(),
        "canary.flaky@1": _FlakyExecutor(),
        "canary.publish@1": _PublishExecutor(),
    }
