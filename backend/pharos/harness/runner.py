"""The bounded execution loop for claimed steps.

One worker process claims due steps from the database, executes them through
the canary's trusted expander/executor set, and writes every transition back
through the state service in short transactions. No step ever runs inside a
DB transaction; no retry decision is made from a guess. The runner checks
bounds and delegates -- it never invents policy.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from pharos.db.session import session_scope
from pharos.harness.approvals import DEFAULT_EXPIRY_SECONDS, ApprovalRepository
from pharos.harness.contracts import (
    AttemptErrorClass,
    AttemptState,
    GatewayError,
    RetryableCapabilityError,
    RunOutcome,
    RunState,
    ScopeType,
    StateError,
    StepState,
)
from pharos.harness.dispatcher import ClaimedStep, HarnessDispatcher
from pharos.harness.events import EventStore
from pharos.harness.fakes import FakeClock
from pharos.harness.model_gateway import ModelGateway
from pharos.harness.repository import HarnessRunRepository, HarnessStepRepository, Scope, json_dump
from pharos.harness.state import HarnessStateService
from pharos.harness.tables import runs, steps
from pharos.harness.usage import UsageLedger

log = logging.getLogger(__name__)

MICROSECONDS_PER_SECOND = 1_000_000

#: A StepExpander maps workflow input -> list of physical step specs.
StepExpander = Callable[[dict], list[dict]]
#: A RunReducer maps (run row, step rows, now_us) -> (target state, outcome).
RunReducer = Callable[[dict, list[dict], int], tuple[RunState, RunOutcome | None]]


@dataclass
class StepExecutor:
    """What the runner needs to execute claimed steps."""

    gateway: ModelGateway | None = None
    capabilities: dict[str, Any] = field(default_factory=dict)
    state: HarnessStateService = field(default_factory=HarnessStateService)
    usage: UsageLedger = field(default_factory=UsageLedger)
    events: EventStore = field(default_factory=EventStore)
    clock: FakeClock = field(default_factory=FakeClock)
    expanders: dict[str, StepExpander] = field(default_factory=dict)
    run_reducers: dict[str, RunReducer] = field(default_factory=dict)


def _scope_of(claimed: ClaimedStep) -> Scope:
    return Scope(scope_type=ScopeType(claimed.scope_type), scope_id=claimed.scope_id)


class HarnessRunner:
    """Claims, executes, reduces. One worker, database-backed."""

    def __init__(self, dispatcher: HarnessDispatcher, executor: StepExecutor) -> None:
        self.dispatcher = dispatcher
        self.executor = executor
        self.state = executor.state
        self.run_repository = HarnessRunRepository()
        self.step_repository = HarnessStepRepository()

    # ------------------------------------------------------------- activation

    def activate_run(self, session: Session, *, scope: Scope, run: dict, now_us: int) -> list[dict]:
        """Expand the run's physical steps from the trusted expander.

        Idempotent: re-activation for the same run and input yields the same
        step set; the (run_id, definition_step_key, instance_key) unique
        constraint is the second line of defence.
        """
        workflow_key = run["workflow_key"]
        expander = self.executor.expanders.get(workflow_key)
        if expander is None:
            raise StateError(f"no trusted expander for {workflow_key}")
        input = json.loads(run["input_json"])
        created: list[dict] = []
        for entry in expander(input):
            row = self.step_repository.expand(
                session,
                scope=scope,
                run_id=run["id"],
                definition_step_key=entry["definition_step_key"],
                instance_key=entry["instance_key"],
                step_kind=entry["step_kind"],
                definition_json=json_dump(entry["definition"]),
                depends_on_json=json_dump(entry.get("depends_on", [])),
                fan_in=entry.get("fan_in"),
                min_success_count=entry.get("min_success_count"),
                max_attempts=entry.get("max_attempts", 3),
                timeout_seconds=entry.get("timeout_seconds"),
                retry_policy_json=(
                    json_dump(entry.get("retry_policy")) if entry.get("retry_policy") else None
                ),
                now_us=now_us,
            )
            created.append(row)
        return created

    # ------------------------------------------------------------------ loop

    def tick(self, *, now_us: int) -> int:
        """One dispatcher pass: claim and execute up to a batch."""
        executed = 0
        while executed < self.dispatcher.claim_batch:
            with session_scope() as session:
                claimed = self.dispatcher.claim_due(session, now_us=now_us, limit=1)
                if claimed is None:
                    break
                run = self.run_repository.require(
                    session, scope=_scope_of(claimed), run_id=claimed.run_id
                )
            if run is None:
                continue
            self._execute_one(claimed=claimed, run=run, now_us=now_us)
            executed += 1
            now_us = self.executor.clock.utc_epoch_us()
            self.reduce_all(now_us=now_us)
        return executed

    def _execute_one(self, *, claimed: ClaimedStep, run: dict, now_us: int) -> None:
        step_def = json.loads(claimed.definition_json)
        try:
            if step_def.get("approval_required"):
                self._execute_approved_or_request(
                    claimed=claimed, run=run, step_def=step_def, now_us=now_us
                )
            elif step_def.get("kind") in ("deterministic", "mapped"):
                self._run_deterministic(claimed=claimed, run=run, step_def=step_def, now_us=now_us)
            else:
                self._run_agent_step(claimed=claimed, run=run, now_us=now_us)
        except GatewayError as error:
            self._classify_gateway_failure(claimed=claimed, error=error, now_us=now_us)
        except StateError:
            log.warning("step %s is no longer in a claimable state; skipping", claimed.step_id)
        except Exception:  # noqa: BLE001 -- nothing may escape the worker loop
            log.exception("step %s crashed", claimed.step_id)
            self._mark_crashed(claimed=claimed, now_us=now_us)

    def _approval_request(self, claimed: ClaimedStep, run: dict) -> dict:
        """The canonical request whose hash binds grant, step and attempt."""
        input = json.loads(run["input_json"])
        return {
            "action": "harness.canary.proceed",
            "resource": {"run_id": claimed.run_id, "step_key": claimed.definition_step_key},
            "request": {"mode": input.get("mode"), "note": input.get("note", "")},
            "effect_summary": {"writes": "a canary artifact only"},
        }

    def _execute_approved_or_request(
        self, *, claimed: ClaimedStep, run: dict, step_def: dict, now_us: int
    ) -> None:
        """Consume an existing approved grant, or open a new approval request.

        A successor attempt never re-asks once the owner has approved the
        canonical (action, resource, request) tuple; it consumes the grant
        exactly once and executes.
        """
        from pharos.harness.definitions import sha256_hex

        approval_request = self._approval_request(claimed, run)
        # The stored grant hashes exactly {action, resource, request} (see
        # ApprovalRepository.request); effect_summary is display-only and must
        # not split the hash between requester and consumer.
        request_hash = sha256_hex(
            {key: approval_request[key] for key in ("action", "resource", "request")}
        )
        scope = _scope_of(claimed)
        with session_scope() as session:
            grants = ApprovalRepository().approved_for_step(
                session, scope=scope, step_id=claimed.step_id
            )
            matching = [grant for grant in grants if grant["request_hash"] == request_hash]
        if matching:
            with session_scope() as session:
                ApprovalRepository().consume(
                    session,
                    scope=scope,
                    approval_id=matching[0]["id"],
                    step_id=claimed.step_id,
                    request_hash=request_hash,
                    consuming_attempt_id=claimed.attempt_id,
                    now_us=now_us,
                )
            self._run_deterministic(claimed=claimed, run=run, step_def=step_def, now_us=now_us)
            return
        self._open_approval(
            claimed=claimed, run=run, approval_request=approval_request, now_us=now_us
        )

    def _schedule_retry_or_fail(
        self, *, claimed: ClaimedStep, error: RetryableCapabilityError, now_us: int
    ) -> None:
        """Retry only within policy: attempts and backoff, never blind re-runs."""
        with session_scope() as session:
            step = (
                session.execute(steps.select().where(steps.c.id == claimed.step_id))
                .mappings()
                .first()
            )
            if step is None:
                return
            retry_json = step["retry_policy_json"]
            policy = json.loads(retry_json) if retry_json else None
            max_attempts = int(step["max_attempts"] or 3)
            if policy and claimed.attempt_no < max_attempts:
                backoff = float(
                    policy.get("backoff_seconds", 1.0)
                    * (float(policy.get("backoff_factor", 2.0)) ** (claimed.attempt_no - 1))
                )
                self.state.transition_attempt(
                    session,
                    attempt_id=claimed.attempt_id,
                    target=AttemptState.failed,
                    now_us=now_us,
                    error_class=AttemptErrorClass.provider.value,
                    error_message=str(error)[:500],
                    retryable=1,
                )
                self.state.transition_step(
                    session,
                    step_id=claimed.step_id,
                    target=StepState.retry_scheduled,
                    now_us=now_us,
                    error_code="retryable_failure",
                    ready_at=now_us + int(backoff * MICROSECONDS_PER_SECOND),
                    lease_owner=None,
                )
            else:
                self.state.transition_attempt(
                    session,
                    attempt_id=claimed.attempt_id,
                    target=AttemptState.failed,
                    now_us=now_us,
                    error_class=AttemptErrorClass.provider.value,
                    error_message=str(error)[:500],
                )
                self.state.transition_step(
                    session,
                    step_id=claimed.step_id,
                    target=StepState.failed,
                    now_us=now_us,
                    error_code="retries_exhausted",
                )

    def _mark_crashed(self, *, claimed: ClaimedStep, now_us: int) -> None:
        with session_scope() as session:
            with contextlib.suppress(StateError):
                self.state.transition_attempt(
                    session,
                    attempt_id=claimed.attempt_id,
                    target=AttemptState.failed,
                    now_us=now_us,
                    error_class=AttemptErrorClass.bug.value,
                    error_message="runner crash",
                )
            with contextlib.suppress(StateError):
                self.state.transition_step(
                    session,
                    step_id=claimed.step_id,
                    target=StepState.failed,
                    now_us=now_us,
                    error_code="runner_crash",
                )

    def _run_deterministic(
        self, *, claimed: ClaimedStep, run: dict, step_def: dict, now_us: int
    ) -> None:
        capability_key = step_def.get("capability") or ""
        capability = self.executor.capabilities.get(capability_key)
        if capability is None:
            with session_scope() as session:
                self.state.transition_attempt(
                    session,
                    attempt_id=claimed.attempt_id,
                    target=AttemptState.failed,
                    now_us=now_us,
                    error_class=AttemptErrorClass.configuration.value,
                    error_message=f"unknown capability {capability_key}",
                )
                self.state.transition_step(
                    session,
                    step_id=claimed.step_id,
                    target=StepState.failed,
                    now_us=now_us,
                    error_code="unknown_capability",
                )
            return
        with session_scope() as session:
            self.state.transition_attempt(
                session, attempt_id=claimed.attempt_id, target=AttemptState.running, now_us=now_us
            )
            self.state.transition_step(
                session, step_id=claimed.step_id, target=StepState.running, now_us=now_us
            )
        action = {
            "idempotency_key": (
                f"{claimed.run_id}:{claimed.definition_step_key}:"
                f"{claimed.instance_key}:attempt{claimed.attempt_no}"
            ),
            "workflow_key": run["workflow_key"],
            "step_key": claimed.definition_step_key,
            "input": json.loads(run["input_json"]),
        }
        try:
            capability.execute(action)
        except RetryableCapabilityError as error:
            self._schedule_retry_or_fail(
                claimed=claimed, error=error, now_us=self.executor.clock.utc_epoch_us()
            )
            return
        except Exception as error:  # noqa: BLE001 -- capability outcome is typed in the DB
            with session_scope() as session:
                self.state.transition_attempt(
                    session,
                    attempt_id=claimed.attempt_id,
                    target=AttemptState.failed,
                    now_us=self.executor.clock.utc_epoch_us(),
                    error_class=AttemptErrorClass.bug.value,
                    error_message=str(error)[:500],
                )
                self.state.transition_step(
                    session,
                    step_id=claimed.step_id,
                    target=StepState.failed,
                    now_us=self.executor.clock.utc_epoch_us(),
                    error_code="capability_error",
                    error_message=str(error)[:500],
                )
            return
        with session_scope() as session:
            self.state.transition_attempt(
                session,
                attempt_id=claimed.attempt_id,
                target=AttemptState.succeeded,
                now_us=self.executor.clock.utc_epoch_us(),
            )
            self.state.transition_step(
                session,
                step_id=claimed.step_id,
                target=StepState.succeeded,
                now_us=self.executor.clock.utc_epoch_us(),
            )

    def _open_approval(
        self, *, claimed: ClaimedStep, run: dict, approval_request: dict, now_us: int
    ) -> None:
        with session_scope() as session:
            ApprovalRepository().request(
                session,
                scope=_scope_of(claimed),
                run_id=claimed.run_id,
                step_id=claimed.step_id,
                requesting_attempt_id=claimed.attempt_id,
                action=approval_request["action"],
                resource=approval_request["resource"],
                request=approval_request["request"],
                effect_summary=approval_request["effect_summary"],
                now_us=now_us,
                expires_at_us=now_us + DEFAULT_EXPIRY_SECONDS * MICROSECONDS_PER_SECOND,
            )
            # The attempt ran and produced a request: leased -> running ->
            # blocked, never leased -> blocked (a blocked attempt has executed).
            self.state.transition_attempt(
                session, attempt_id=claimed.attempt_id, target=AttemptState.running, now_us=now_us
            )
            self.state.transition_attempt(
                session, attempt_id=claimed.attempt_id, target=AttemptState.blocked, now_us=now_us
            )
            # Likewise the step: it executed and now waits, so it passes
            # through running rather than jumping straight from leased.
            self.state.transition_step(
                session, step_id=claimed.step_id, target=StepState.running, now_us=now_us
            )
            self.state.transition_step(
                session,
                step_id=claimed.step_id,
                target=StepState.waiting_for_approval,
                now_us=now_us,
                lease_owner=None,
                lease_expires_at=None,
            )

    def _run_agent_step(self, *, claimed: ClaimedStep, run: dict, now_us: int) -> None:
        gateway = self.executor.gateway
        if gateway is None:
            with session_scope() as session:
                self.state.transition_attempt(
                    session,
                    attempt_id=claimed.attempt_id,
                    target=AttemptState.blocked,
                    now_us=now_us,
                    error_class=AttemptErrorClass.configuration.value,
                )
                self.state.transition_step(
                    session,
                    step_id=claimed.step_id,
                    target=StepState.waiting_for_input,
                    now_us=now_us,
                    waiting_reason="configuration",
                )
            return
        with session_scope() as session:
            self.state.transition_attempt(
                session, attempt_id=claimed.attempt_id, target=AttemptState.running, now_us=now_us
            )
            self.state.transition_step(
                session, step_id=claimed.step_id, target=StepState.running, now_us=now_us
            )
        input = json.loads(run["input_json"])
        reservation: str | None = None
        with session_scope() as session:
            reservation = self.executor.usage.reserve(
                session,
                scope=_scope_of(claimed),
                run_id=claimed.run_id,
                step_id=claimed.step_id,
                attempt_id=claimed.attempt_id,
                kind="model_tokens",
                source="system_shared",
                amount=10,
                cost_micros=0,
                now_us=now_us,
            )
        try:
            result = gateway.complete(
                {
                    "workflow": run["workflow_key"],
                    "step": claimed.definition_step_key,
                    "input": input,
                }
            )
        except GatewayError as error:
            with session_scope() as session:
                self.executor.usage.release(
                    session,
                    reservation_id=reservation or "",
                    now_us=self.executor.clock.utc_epoch_us(),
                )
            raise error
        with session_scope() as session:
            self.executor.usage.settle(
                session,
                reservation_id=reservation or "",
                actual=result.output_tokens,
                now_us=self.executor.clock.utc_epoch_us(),
            )
            self.state.transition_attempt(
                session,
                attempt_id=claimed.attempt_id,
                target=AttemptState.succeeded,
                now_us=self.executor.clock.utc_epoch_us(),
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cost_micros=result.cost_micros,
                provider_request_id=result.provider_request_id,
            )
            self.state.transition_step(
                session,
                step_id=claimed.step_id,
                target=StepState.succeeded,
                now_us=self.executor.clock.utc_epoch_us(),
            )

    def _classify_gateway_failure(
        self, *, claimed: ClaimedStep, error: GatewayError, now_us: int
    ) -> None:
        if error.error_class == AttemptErrorClass.indeterminate:
            with session_scope() as session:
                self.state.transition_attempt(
                    session,
                    attempt_id=claimed.attempt_id,
                    target=AttemptState.indeterminate,
                    now_us=now_us,
                    external_outcome="indeterminate",
                    error_class=AttemptErrorClass.indeterminate.value,
                    error_message=str(error)[:500],
                )
                self.state.transition_step(
                    session,
                    step_id=claimed.step_id,
                    target=StepState.indeterminate,
                    now_us=now_us,
                    error_code="external_outcome_unknown",
                )
            return
        with session_scope() as session:
            self.state.transition_attempt(
                session,
                attempt_id=claimed.attempt_id,
                target=AttemptState.failed,
                now_us=now_us,
                error_class=error.error_class.value,
                error_message=str(error)[:500],
            )
            self.state.transition_step(
                session,
                step_id=claimed.step_id,
                target=StepState.failed,
                now_us=now_us,
                error_code=error.error_class.value,
                error_message=str(error)[:500],
            )

    # ------------------------------------------------------------- reduction

    def reduce_all(self, *, now_us: int) -> None:
        with session_scope() as session:
            rows = (
                session.execute(
                    runs.select().where(
                        runs.c.state.in_(
                            [
                                RunState.queued.value,
                                RunState.running.value,
                                RunState.waiting_for_approval.value,
                            ]
                        )
                    )
                )
                .mappings()
                .all()
            )
            for run in rows:
                scope = Scope(scope_type=ScopeType(run["scope_type"]), scope_id=run["scope_id"])
                self._activate_dependents(session, run_id=run["id"], scope=scope, now_us=now_us)
                self._reduce_one(session, run=dict(run), scope=scope, now_us=now_us)

    def _activate_dependents(self, session, *, run_id: str, scope: Scope, now_us: int) -> int:
        """Make pending steps ready when their dependencies are terminal.

        Runs inside the reduction pass, so every path that completes a step
        (success, failure, skip, cancel, approval resolution) activates its
        dependents in the same deterministic place. Idempotent: a step whose
        dependencies are not yet terminal simply stays pending.
        """
        rows = self.step_repository.for_run(session, scope=scope, run_id=run_id)
        by_key: dict[str, list[dict]] = {}
        for row in rows:
            by_key.setdefault(row["definition_step_key"], []).append(row)
        activated = 0
        for row in rows:
            if row["state"] != "pending":
                continue
            deps = json.loads(row["depends_on_json"] or "[]")
            satisfied = True
            for dep in deps:
                dep_rows = by_key.get(dep, [])
                # A dependency the expander never created (an unselected
                # optional branch) counts as satisfied: its absence is the
                # branch not being taken, not an unmet requirement.
                if not dep_rows:
                    continue
                if not all(
                    dep_row["state"]
                    in ("succeeded", "failed", "skipped", "cancelled", "indeterminate")
                    for dep_row in dep_rows
                ):
                    satisfied = False
                    break
            if satisfied:
                self.state.transition_step(
                    session,
                    step_id=row["id"],
                    target=StepState.ready,
                    now_us=now_us,
                    ready_at=now_us,
                )
                activated += 1
        return activated

    def _reduce_one(self, session, *, run: dict, scope: Scope, now_us: int) -> None:  # noqa: ANN001
        reducer = self.executor.run_reducers.get(run["workflow_key"])
        if reducer is None:
            raise StateError(f"no trusted reducer for {run['workflow_key']}")
        step_rows = self.step_repository.for_run(session, scope=scope, run_id=run["id"])
        target, outcome = reducer(run, step_rows, now_us)
        if target.value == run["state"]:
            return
        if target in (
            RunState.succeeded,
            RunState.failed,
            RunState.cancelled,
            RunState.indeterminate,
        ):
            self.state.reduce_run(
                session,
                run_id=run["id"],
                target=target,
                outcome=outcome.value if outcome else None,
                now_us=now_us,
            )
        else:
            self.state.transition_run(session, run_id=run["id"], target=target, now_us=now_us)

    # ---------------------------------------------------------------- control

    def apply_pending_control(self, *, now_us: int) -> int:
        """Turn persistent pause/cancel requests into states."""
        changed = 0
        # Cancel: requests win over every other state.
        with session_scope() as session:
            rows = (
                session.execute(
                    runs.select().where(
                        runs.c.cancel_requested_at.is_not(None),
                        runs.c.state.in_(
                            [
                                "queued",
                                "running",
                                "waiting_for_approval",
                                "waiting_for_input",
                                "paused",
                            ]
                        ),
                    )
                )
                .mappings()
                .all()
            )
            for run in rows:
                scope = Scope(scope_type=ScopeType(run["scope_type"]), scope_id=run["scope_id"])
                pending_steps = [
                    row
                    for row in self.step_repository.for_run(session, scope=scope, run_id=run["id"])
                    if row["state"]
                    in (
                        "pending",
                        "ready",
                        "retry_scheduled",
                        "waiting_for_approval",
                        "waiting_for_input",
                    )
                ]
                for step in pending_steps:
                    self.state.transition_step(
                        session,
                        step_id=step["id"],
                        target=StepState.cancelled,
                        now_us=now_us,
                        skip_reason="run_cancelled",
                    )
                self.state.reduce_run(
                    session,
                    run_id=run["id"],
                    target=RunState.cancelled,
                    outcome=RunOutcome.incomplete.value,
                    now_us=now_us,
                )
                changed += 1
        # Pause: only when the run has no active step left; the dispatcher
        # never claims new steps for a paused run, so this is a safe boundary.
        with session_scope() as session:
            rows = (
                session.execute(
                    runs.select().where(
                        runs.c.pause_requested_at.is_not(None),
                        runs.c.cancel_requested_at.is_(None),
                        runs.c.state.in_(["queued", "running"]),
                    )
                )
                .mappings()
                .all()
            )
            for run in rows:
                scope = Scope(scope_type=ScopeType(run["scope_type"]), scope_id=run["scope_id"])
                active = [
                    row
                    for row in self.step_repository.for_run(session, scope=scope, run_id=run["id"])
                    if row["state"] in ("leased", "running", "retry_scheduled")
                ]
                if active:
                    continue
                self.state.transition_run(
                    session, run_id=run["id"], target=RunState.paused, now_us=now_us
                )
                changed += 1
        return changed
