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
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import RLock
from typing import Any

from sqlalchemy.orm import Session

from pharos.db.session import session_scope
from pharos.harness.approvals import DEFAULT_EXPIRY_SECONDS, ApprovalRepository
from pharos.harness.artifacts import ArtifactStore
from pharos.harness.contracts import (
    ArtifactSensitivity,
    AttemptErrorClass,
    AttemptState,
    GatewayError,
    NotFoundError,
    ProducerKind,
    RetryableCapabilityError,
    RunOutcome,
    RunState,
    ScopeType,
    StateError,
    StepState,
)
from pharos.harness.dispatcher import ClaimedStep, HarnessDispatcher
from pharos.harness.events import EventStore
from pharos.harness.execution_snapshots import (
    AttemptDefinitionSnapshot,
    MissingExecutionSnapshotError,
    SnapshotIntegrityError,
)
from pharos.harness.fakes import FakeClock, ModelResult
from pharos.harness.model_gateway import AttemptContext, GatewayFactory, GatewayHandle
from pharos.harness.repository import HarnessRunRepository, HarnessStepRepository, Scope, json_dump
from pharos.harness.state import HarnessStateService
from pharos.harness.tables import attempts, runs, steps
from pharos.harness.usage import LedgerConflict, UsageLedger

log = logging.getLogger(__name__)

MICROSECONDS_PER_SECOND = 1_000_000

#: A StepExpander maps workflow input -> list of physical step specs.
StepExpander = Callable[[dict], list[dict]]
#: A RunReducer maps (run row, step rows, now_us) -> (target state, outcome).
RunReducer = Callable[[dict, list[dict], int], tuple[RunState, RunOutcome | None]]
AgentOutputValidator = Callable[[Any], dict]


@dataclass(frozen=True)
class AgentOutputContract:
    """Trusted validator for one hash-bound role output contract."""

    schema_name: str
    schema_version: int
    prompt_version: str
    validator: AgentOutputValidator


@dataclass
class StepExecutor:
    """What the runner needs to execute claimed steps."""

    gateway_factory: GatewayFactory | None = None
    artifacts: ArtifactStore = field(default_factory=ArtifactStore)
    agent_output_contracts: dict[tuple[str, str], AgentOutputContract] = field(
        default_factory=dict
    )
    capabilities: dict[tuple[str, str], Any] = field(default_factory=dict)
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
        # A handle is registered only after ``open`` succeeds and before the
        # first completion byte is sent.  The registry is intentionally
        # attempt-scoped: cancellation must never reach a sibling handle.
        self._active_handles: dict[str, GatewayHandle] = {}
        self._active_handles_lock = RLock()

    @property
    def active_attempt_ids(self) -> tuple[str, ...]:
        """A stable, read-only view for the future cancel coordinator."""
        with self._active_handles_lock:
            return tuple(sorted(self._active_handles))

    @property
    def active_attempt_count(self) -> int:
        with self._active_handles_lock:
            return len(self._active_handles)

    def cancel_active_attempt(self, attempt_id: str) -> bool:
        """Cancel exactly one currently registered Attempt handle.

        The potentially blocking delegate call is made outside the registry
        lock.  The handle itself serialises cancel versus complete, while the
        registry lock only protects lookup and sibling isolation.
        """
        with self._active_handles_lock:
            handle = self._active_handles.get(attempt_id)
        if handle is None:
            return False
        try:
            handle.cancel()
        except Exception:  # noqa: BLE001 -- terminal/late cancellation is safe
            return False
        return True

    def _register_handle(self, attempt_id: str, handle: GatewayHandle) -> None:
        with self._active_handles_lock:
            if attempt_id in self._active_handles:
                raise StateError(f"Attempt {attempt_id} already has an active gateway handle")
            self._active_handles[attempt_id] = handle

    def _unregister_handle(self, attempt_id: str, handle: GatewayHandle) -> None:
        with self._active_handles_lock:
            # Identity comparison is deliberate: a late callback cannot
            # unregister a successor handle that reused the same Attempt key.
            if self._active_handles.get(attempt_id) is handle:
                del self._active_handles[attempt_id]

    # ------------------------------------------------------------- activation

    def activate_run(
        self,
        session: Session,
        *,
        scope: Scope,
        run: dict,
        now_us: int,
        expanded_steps: list[dict] | None = None,
    ) -> list[dict]:
        """Expand the run's physical steps from the trusted expander.

        Idempotent: re-activation for the same run and input yields the same
        step set; the (run_id, definition_step_key, instance_key) unique
        constraint is the second line of defence.
        """
        workflow_identity = f"{run['workflow_key']}@{run['workflow_version']}"
        expander = self.executor.expanders.get(workflow_identity)
        if expander is None:
            raise StateError(f"no trusted expander for {workflow_identity}")
        input = json.loads(run["input_json"])
        expansion = expanded_steps if expanded_steps is not None else expander(input)
        created: list[dict] = []
        for entry in expansion:
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
                    # Dispatcher claim_due exhausts its FIFO keyset scan;
                    # None therefore means no currently claimable row remains,
                    # not that an earlier gated/tampered page hid work.
                    break
            self._execute_one(claimed=claimed, now_us=now_us)
            executed += 1
            now_us = self.executor.clock.utc_epoch_us()
            self.reduce_all(now_us=now_us)
        return executed

    def _execute_one(self, *, claimed: ClaimedStep, now_us: int) -> None:
        authenticated = self._read_execution_snapshot(claimed)
        if authenticated is None:
            # A claim is not an execution authority.  Legacy, corrupt, or
            # cross-owner snapshots stop here before any capability, gateway,
            # usage, approval, or artifact side effect is attempted.
            return
        attempt_snapshot, _ = authenticated
        # Re-fence the exact lease before any approval lookup, registry lookup,
        # usage reservation, or external executor call.  A forged claim
        # projection cannot borrow a valid snapshot from another lease.
        with session_scope() as session:
            if not self._claim_is_active(
                session,
                claimed=claimed,
                attempt_state=AttemptState.leased,
                step_state=StepState.leased,
            ):
                return
        step_definition = attempt_snapshot.step_definition
        try:
            if step_definition.approval_required:
                self._execute_approved_or_request(
                    claimed=claimed,
                    now_us=now_us,
                )
            elif step_definition.kind in ("deterministic", "mapped"):
                self._run_deterministic(
                    claimed=claimed,
                    now_us=now_us,
                )
            else:
                self._run_agent_step(claimed=claimed, now_us=now_us)
        except GatewayError as error:
            self._classify_gateway_failure(claimed=claimed, error=error, now_us=now_us)
        except StateError:
            log.warning("step %s is no longer in a claimable state; skipping", claimed.step_id)
        except Exception:  # noqa: BLE001 -- nothing may escape the worker loop
            log.exception("step %s crashed", claimed.step_id)
            self._mark_crashed(claimed=claimed, now_us=now_us)

    def _read_execution_snapshot(
        self, claimed: ClaimedStep
    ) -> tuple[AttemptDefinitionSnapshot, dict] | None:
        """Re-authenticate a claim in a fresh short-lived DB session.

        Dispatcher projections are intentionally treated as hints only.  The
        snapshot store rechecks owner, parent identity, frozen workflow
        binding, physical Step definition and all hashes before this method
        returns an execution context.
        """
        try:
            with session_scope() as session:
                snapshot = self.dispatcher.execution_snapshots.read_attempt(
                    session,
                    scope=claimed.scope_type,
                    scope_id=claimed.scope_id,
                    attempt_id=claimed.attempt_id,
                    require_for_execution=True,
                )
                if snapshot is None or (
                    snapshot.attempt_id != claimed.attempt_id
                    or snapshot.run_id != claimed.run_id
                    or snapshot.step_id != claimed.step_id
                    or snapshot.scope_type != claimed.scope_type
                    or snapshot.scope_id != claimed.scope_id
                    or snapshot.attempt_no != claimed.attempt_no
                    or snapshot.definition_step_key != claimed.definition_step_key
                    or snapshot.instance_key != claimed.instance_key
                ):
                    raise SnapshotIntegrityError(
                        "claimed identity does not match execution snapshot"
                    )
                # read_attempt -> read_run has already authenticated the
                # canonical input and owner binding.  Fetch the input only
                # after that verification and use the same scoped parent.
                fresh_run = self.run_repository.require(
                    session,
                    scope=Scope(
                        scope_type=ScopeType(snapshot.scope_type),
                        scope_id=snapshot.scope_id,
                    ),
                    run_id=snapshot.run_id,
                )
                return snapshot, fresh_run
        except (
            MissingExecutionSnapshotError,
            SnapshotIntegrityError,
            NotFoundError,
            ValueError,
            TypeError,
        ):
            log.warning("step %s failed closed at execution snapshot boundary", claimed.step_id)
            return None

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
        self,
        *,
        claimed: ClaimedStep,
        now_us: int,
    ) -> None:
        """Consume an existing approved grant, or open a new approval request.

        A successor attempt never re-asks once the owner has approved the
        canonical (action, resource, request) tuple; it consumes the grant
        exactly once and executes.
        """
        authenticated = self._read_execution_snapshot(claimed)
        if authenticated is None:
            return
        snapshot, run = authenticated
        if not snapshot.step_definition.approval_required:
            raise SnapshotIntegrityError("approval entry does not match the frozen step")

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
            self._run_deterministic(
                claimed=claimed,
                now_us=now_us,
            )
            return
        self._open_approval(
            claimed=claimed, run=run, approval_request=approval_request, now_us=now_us
        )

    def _schedule_retry_or_fail(
        self,
        *,
        claimed: ClaimedStep,
        error: RetryableCapabilityError,
        snapshot: AttemptDefinitionSnapshot,
        now_us: int,
    ) -> None:
        """Retry only within policy: attempts and backoff, never blind re-runs."""
        with session_scope() as session:
            # Retry policy is authenticated in the Attempt snapshot.  The
            # physical Step columns are duplicated expansion metadata, not a
            # source of execution policy.
            policy = snapshot.retry_policy
            max_attempts = snapshot.max_attempts
            if policy and claimed.attempt_no < max_attempts:
                backoff = float(
                    policy.backoff_seconds
                    * (float(policy.backoff_factor) ** (claimed.attempt_no - 1))
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
        self,
        *,
        claimed: ClaimedStep,
        now_us: int,
    ) -> None:
        authenticated = self._read_execution_snapshot(claimed)
        if authenticated is None:
            return
        snapshot, run = authenticated
        if snapshot.step_definition.kind not in ("deterministic", "mapped"):
            raise SnapshotIntegrityError(
                "deterministic entry does not match the frozen step"
            )
        capability_key = snapshot.executor_identity
        capability_hash = snapshot.executor_capability_definition_sha256
        capability = (
            self.executor.capabilities.get((capability_key, capability_hash))
            if capability_hash is not None
            else None
        )
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
                claimed=claimed,
                error=error,
                snapshot=snapshot,
                now_us=self.executor.clock.utc_epoch_us(),
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

    def _run_agent_step(
        self,
        *,
        claimed: ClaimedStep,
        now_us: int,
    ) -> None:
        authenticated = self._read_execution_snapshot(claimed)
        if authenticated is None:
            return
        snapshot, run = authenticated
        if snapshot.step_definition.kind not in ("agent", "mapped_agent"):
            raise SnapshotIntegrityError("agent entry does not match the frozen step")
        provider = snapshot.provider
        model = snapshot.model
        usage_source = snapshot.usage_source
        if not all(
            isinstance(value, str) and value
            for value in (provider, model, usage_source)
        ):
            raise SnapshotIntegrityError("agent snapshot has incomplete model route metadata")
        assert isinstance(usage_source, str)
        contract = self._agent_contract(snapshot)
        if contract is None:
            # Do not reserve usage or call the gateway when the role contract
            # is absent, keyed by the wrong role hash, or disagrees with the
            # authenticated role definition.
            with session_scope() as session:
                if self._claim_is_active(
                    session,
                    claimed=claimed,
                    attempt_state=AttemptState.leased,
                    step_state=StepState.leased,
                ):
                    self.state.transition_attempt(
                        session,
                        attempt_id=claimed.attempt_id,
                        target=AttemptState.running,
                        now_us=now_us,
                    )
                    self.state.transition_step(
                        session,
                        step_id=claimed.step_id,
                        target=StepState.running,
                        now_us=now_us,
                    )
            self._finish_agent_failure(
                claimed=claimed,
                reservation_id="",
                error_class=AttemptErrorClass.configuration,
                error_message="no authenticated output contract for role",
                now_us=now_us,
            )
            return
        # A finish may be replayed after the worker has already committed.  Do
        # this check before reserving usage: the output reference is the
        # durable idempotency marker for the whole local finish operation.
        with session_scope() as session:
            if not self._claim_is_active(
                session,
                claimed=claimed,
                attempt_state=AttemptState.leased,
                step_state=StepState.leased,
            ):
                return

        # The v2 canary is a claim-only DSH seam in this slice.  Do not route
        # a trusted DSH role through the legacy in-process fake gateway while
        # the actual sidecar adapter is still intentionally absent.
        factory = self.executor.gateway_factory
        if snapshot.runtime_kind == "dsh" or factory is None:
            with session_scope() as session:
                if not self._claim_is_active(
                    session,
                    claimed=claimed,
                    attempt_state=AttemptState.leased,
                    step_state=StepState.leased,
                ):
                    return
                self.state.transition_attempt(
                    session,
                    attempt_id=claimed.attempt_id,
                    target=AttemptState.running,
                    now_us=now_us,
                )
                self.state.transition_step(
                    session,
                    step_id=claimed.step_id,
                    target=StepState.running,
                    now_us=now_us,
                )
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
                    lease_owner=None,
                    lease_expires_at=None,
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
                source=usage_source,
                amount=10,
                cost_micros=0,
                now_us=now_us,
            )
        handle: GatewayHandle | None = None
        call_error: BaseException | None = None
        try:
            context = self._attempt_context(
                snapshot=snapshot,
                claimed=claimed,
                workflow_key=run["workflow_key"],
                input_sha256=run["input_sha256"],
                now_us=now_us,
            )
            # Opening happens only after the lease/running transition and
            # reservation.  Registration is immediate, before complete, so a
            # concurrent cancel coordinator can address this exact Attempt.
            handle = factory.open(context)
            if handle.context != context:
                raise StateError("gateway factory returned a mismatched Attempt context")
            self._register_handle(claimed.attempt_id, handle)
            result = handle.complete(
                {
                    "workflow": run["workflow_key"],
                    "step": claimed.definition_step_key,
                    "input": input,
                }
            )
        except BaseException as error:
            # Defer classification until cleanup has completed.  In
            # particular, cleanup must not hide a typed gateway error.
            call_error = error

        close_error: BaseException | None = None
        if handle is not None:
            try:
                # Reap/cleanup is part of the Attempt boundary and precedes
                # validation and Artifact publication.
                handle.close()
            except BaseException as error:
                close_error = error
            finally:
                # Remove only after cleanup; identity fencing prevents a late
                # callback from unregistering a successor handle.
                self._unregister_handle(claimed.attempt_id, handle)

        if close_error is not None:
            # Never persist the exception text: a delegate may embed paths,
            # provider payloads, or credentials.  Cleanup failure still blocks
            # publication below even when a typed call error takes precedence.
            log.error(
                "agent gateway handle cleanup failed for %s (%s)",
                claimed.attempt_id,
                type(close_error).__name__,
            )
        if call_error is not None:
            if isinstance(call_error, GatewayError):
                with session_scope() as session:
                    self.executor.usage.release(
                        session,
                        reservation_id=reservation or "",
                        now_us=self.executor.clock.utc_epoch_us(),
                    )
                raise call_error
            if not isinstance(call_error, Exception):
                raise call_error
            log.error(
                "agent gateway raised an untyped local error (%s)",
                type(call_error).__name__,
            )
            self._finish_agent_failure(
                claimed=claimed,
                reservation_id=reservation or "",
                error_class=AttemptErrorClass.bug,
                error_message="model gateway raised an unexpected local error",
                now_us=self.executor.clock.utc_epoch_us(),
            )
            return
        if close_error is not None:
            self._finish_agent_failure(
                claimed=claimed,
                reservation_id=reservation or "",
                error_class=AttemptErrorClass.bug,
                error_message="model gateway handle cleanup failed",
                now_us=self.executor.clock.utc_epoch_us(),
            )
            return

        try:
            self._validate_agent_output(
                result=result,
                snapshot=snapshot,
                contract=contract,
            )
        except (TypeError, ValueError) as error:
            # Validation happens before ArtifactStore.create.  Consequently a
            # malformed model result can never leave a publishable Artifact.
            self._finish_agent_failure(
                claimed=claimed,
                reservation_id=reservation or "",
                error_class=AttemptErrorClass.validation,
                error_message=str(error),
                now_us=self.executor.clock.utc_epoch_us(),
            )
            return

        try:
            self._finish_agent_success(
                claimed=claimed,
                result=result,
                reservation_id=reservation or "",
                now_us=self.executor.clock.utc_epoch_us(),
            )
        except Exception:  # noqa: BLE001 -- failed commit is terminal and explicit
            log.exception("agent Artifact finish transaction failed")
            self._finish_agent_failure(
                claimed=claimed,
                reservation_id=reservation or "",
                error_class=AttemptErrorClass.bug,
                error_message="agent finish transaction failed",
                now_us=self.executor.clock.utc_epoch_us(),
            )

    def _attempt_context(
        self,
        *,
        snapshot: AttemptDefinitionSnapshot,
        claimed: ClaimedStep,
        workflow_key: str,
        input_sha256: str,
        now_us: int,
    ) -> AttemptContext:
        """Build a strict per-Attempt route/deadline from authenticated data."""
        if type(now_us) is not int or now_us <= 0:
            raise SnapshotIntegrityError("agent clock must be a positive epoch microsecond")
        seconds = snapshot.timeout_seconds
        if seconds is None:
            seconds = snapshot.step_definition.budget.wall_seconds
        if type(seconds) not in (int, float) or isinstance(seconds, bool):
            raise SnapshotIntegrityError("agent timeout must be a finite positive number")
        if not math.isfinite(float(seconds)) or float(seconds) <= 0:
            raise SnapshotIntegrityError("agent timeout must be a finite positive number")
        duration_us = int(float(seconds) * MICROSECONDS_PER_SECOND)
        if duration_us < 1:
            raise SnapshotIntegrityError("agent timeout is below clock precision")
        deadline_at_us = now_us + duration_us
        try:
            return AttemptContext(
                run_id=claimed.run_id,
                step_id=claimed.step_id,
                attempt_id=claimed.attempt_id,
                attempt_no=claimed.attempt_no,
                scope_type=claimed.scope_type,
                scope_id=claimed.scope_id,
                lease_owner=claimed.lease_owner,
                workflow_key=workflow_key,
                workflow_version=int(snapshot.policy_snapshot.workflow_identity.rsplit("@", 1)[1]),
                workflow_definition_sha256=snapshot.policy_snapshot.workflow_definition_sha256,
                definition_binding_sha256=snapshot.definition_binding_sha256,
                run_policy_sha256=snapshot.run_policy_sha256,
                role=snapshot.executor_identity,
                runtime_kind=snapshot.runtime_kind,
                role_definition_sha256=snapshot.executor_role_definition_sha256 or "",
                model_profile_identity=snapshot.model_profile_identity or "",
                model_profile_sha256=snapshot.model_profile_sha256 or "",
                model_route_key=snapshot.model_route_key or "",
                model_route_sha256=snapshot.model_route_sha256 or "",
                usage_source=snapshot.usage_source or "",
                input_sha256=input_sha256,
                deadline_at_us=deadline_at_us,
                provider=snapshot.provider or "",
                model=snapshot.model or "",
            )
        except (TypeError, ValueError) as error:
            raise SnapshotIntegrityError("agent Attempt context is invalid") from error

    def _agent_contract(
        self, snapshot: AttemptDefinitionSnapshot
    ) -> AgentOutputContract | None:
        role = snapshot.executor_identity
        role_hash = snapshot.executor_role_definition_sha256
        role_definition = snapshot.role_definition
        if role_hash is None or role_definition is None:
            return None
        contract = self.executor.agent_output_contracts.get((role, role_hash))
        if contract is None:
            return None
        output_schema = role_definition.output_schema.rsplit("@", 1)
        if len(output_schema) != 2 or not output_schema[1].isdigit():
            return None
        if (
            contract.schema_name,
            contract.schema_version,
            contract.prompt_version,
        ) != (
            output_schema[0],
            int(output_schema[1]),
            role_definition.prompt_template_version,
        ):
            return None
        return contract

    def _validate_agent_output(
        self,
        *,
        result: ModelResult,
        snapshot: AttemptDefinitionSnapshot,
        contract: AgentOutputContract,
    ) -> dict:
        """Validate the smallest strict output contract available in H1.

        The trusted canary role owns a Pydantic ``StrictModel`` contract.  The
        role identity selects that validator; a model response cannot choose
        its own schema.  Unknown role identities fail closed rather than being
        guessed.  H1.5 can add more role validators without changing the
        transaction below.
        """
        if not isinstance(result, ModelResult):
            result = ModelResult.model_validate(result)
        if result.error is not None:
            raise ValueError("agent completion reported an error")
        if result.finish_reason != "stop":
            raise ValueError("agent completion did not finish normally")
        if snapshot.executor_kind != "role" or snapshot.role_definition is None:
            raise ValueError("agent step has no authenticated role definition")
        output: Any = result.output
        if isinstance(output, str):
            try:
                output = json.loads(output)
            except json.JSONDecodeError as error:
                raise ValueError("agent output is not valid JSON") from error
        try:
            output = contract.validator(output)
            if not isinstance(output, dict):
                raise TypeError("agent output validator must return a JSON object")
            # allow_nan=False closes a subtle non-JSON path even after schema
            # validation, before the canonical content hash is computed.
            json.dumps(output, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as error:
            # Validation errors may echo the rejected model payload.  That
            # payload can contain private paper text, so the durable Attempt
            # stores only this stable classification and never ``str(error)``.
            raise ValueError("agent output does not match its role schema") from error
        return output

    def _finish_agent_success(
        self,
        *,
        claimed: ClaimedStep,
        result: ModelResult,
        reservation_id: str,
        now_us: int,
    ) -> None:
        """Atomically publish the typed output and finish its attempt/step."""
        authenticated = self._read_execution_snapshot(claimed)
        if authenticated is None:
            with session_scope() as session:
                self._release_reservation(
                    session, reservation_id=reservation_id, now_us=now_us
                )
            return
        snapshot, run = authenticated
        contract = self._agent_contract(snapshot)
        if contract is None:
            raise ValueError("no authenticated output contract")
        if snapshot.executor_kind != "role":
            raise ValueError("agent step has no authenticated role identity")
        # Re-validate at the publication boundary.  A future asynchronous
        # callback may carry only the raw model result; it can never inject a
        # pre-validated payload or an alternate role contract into Artifact
        # provenance.
        typed_output = self._validate_agent_output(
            result=result,
            snapshot=snapshot,
            contract=contract,
        )
        role = snapshot.executor_identity
        input_artifact_ids: list[str] = []
        with session_scope() as session:
            if not self._claim_is_active(
                session,
                claimed=claimed,
                attempt_state=AttemptState.running,
                step_state=StepState.running,
            ):
                self._release_reservation(
                    session, reservation_id=reservation_id, now_us=now_us
                )
                return
            current_step = (
                session.execute(steps.select().where(steps.c.id == claimed.step_id))
                .mappings()
                .first()
            )
            if current_step is None or current_step["output_artifact_id"] is not None:
                # A prior committed finish won the race.  Never settle a new
                # reservation or create a second artifact on replay.
                self._release_reservation(
                    session, reservation_id=reservation_id, now_us=now_us
                )
                return
            raw_inputs = current_step["input_artifact_ids_json"] or "[]"
            parsed_inputs = json.loads(raw_inputs)
            if not (
                isinstance(parsed_inputs, list)
                and all(isinstance(item, str) and item for item in parsed_inputs)
            ):
                raise ValueError("agent step has malformed input artifact lineage")
            input_artifact_ids = parsed_inputs
            scope = _scope_of(claimed)
            for artifact_id in input_artifact_ids:
                # A lineage edge may point to an earlier Run, but it may never
                # cross owner scope or name an invented Artifact.
                self.executor.artifacts.require(
                    session,
                    scope=scope,
                    artifact_id=artifact_id,
                )

            artifact = self.executor.artifacts.create(
                session,
                scope=scope,
                run_id=claimed.run_id,
                step_id=claimed.step_id,
                artifact_type=f"agent.{role}",
                schema_name=contract.schema_name,
                schema_version=contract.schema_version,
                content=typed_output,
                producer_kind=ProducerKind.model_inference,
                now_us=now_us,
                sensitivity=ArtifactSensitivity.private,
                workflow_key=run["workflow_key"],
                workflow_version=run["workflow_version"],
                role_prompt_version=snapshot.role_definition.prompt_template_version
                if snapshot.role_definition is not None
                else contract.prompt_version,
                provider=snapshot.provider,
                model=snapshot.model,
                input_artifact_ids=input_artifact_ids,
                input_sha256=run["input_sha256"],
                quality_status="valid",
            )
            # Artifact insertion, usage settlement, and both terminal state
            # transitions share this session/transaction. Any exception rolls
            # back all four writes, including the artifact row.
            self.executor.usage.settle(
                session,
                reservation_id=reservation_id,
                actual=result.output_tokens,
                now_us=now_us,
            )
            self.state.transition_attempt(
                session,
                attempt_id=claimed.attempt_id,
                target=AttemptState.succeeded,
                now_us=now_us,
                role_or_capability=role,
                model_prompt_version=contract.prompt_version,
                input_sha256=run["input_sha256"],
                output_sha256=artifact["content_sha256"],
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cost_micros=result.cost_micros,
                provider_request_id=result.provider_request_id,
            )
            self.state.transition_step(
                session,
                step_id=claimed.step_id,
                target=StepState.succeeded,
                now_us=now_us,
                output_artifact_id=artifact["id"],
                lease_owner=None,
                lease_expires_at=None,
            )

    def _finish_agent_failure(
        self,
        *,
        claimed: ClaimedStep,
        reservation_id: str,
        error_class: AttemptErrorClass,
        error_message: str,
        now_us: int,
    ) -> None:
        with session_scope() as session:
            active = self._claim_is_active(
                session,
                claimed=claimed,
                attempt_state=AttemptState.running,
                step_state=StepState.running,
            )
            self._release_reservation(
                session, reservation_id=reservation_id, now_us=now_us
            )
            # A late result from an expired/retried Attempt owns only its own
            # reservation.  It must never fail or complete the newer Attempt.
            if not active:
                return
            self.state.transition_attempt(
                session,
                attempt_id=claimed.attempt_id,
                target=AttemptState.failed,
                now_us=now_us,
                error_class=error_class.value,
                error_message=error_message[:500],
            )
            self.state.transition_step(
                session,
                step_id=claimed.step_id,
                target=StepState.failed,
                now_us=now_us,
                error_code=error_class.value,
                error_message=error_message[:500],
                lease_owner=None,
                lease_expires_at=None,
            )

    def _claim_is_active(
        self,
        session: Session,
        *,
        claimed: ClaimedStep,
        attempt_state: AttemptState,
        step_state: StepState,
    ) -> bool:
        """Fence a finish to the exact Attempt and lease that produced it."""
        attempt = (
            session.execute(attempts.select().where(attempts.c.id == claimed.attempt_id))
            .mappings()
            .first()
        )
        step = (
            session.execute(steps.select().where(steps.c.id == claimed.step_id))
            .mappings()
            .first()
        )
        return bool(
            attempt is not None
            and step is not None
            and attempt["step_id"] == claimed.step_id
            and attempt["run_id"] == claimed.run_id
            and attempt["scope_type"] == claimed.scope_type
            and attempt["scope_id"] == claimed.scope_id
            and attempt["attempt_no"] == claimed.attempt_no
            and attempt["state"] == attempt_state.value
            and attempt["lease_owner"] == claimed.lease_owner
            and step["run_id"] == claimed.run_id
            and step["scope_type"] == claimed.scope_type
            and step["scope_id"] == claimed.scope_id
            and step["state"] == step_state.value
            and step["lease_owner"] == claimed.lease_owner
            and step["attempt_count"] == claimed.attempt_no
        )

    def _release_reservation(
        self, session: Session, *, reservation_id: str, now_us: int
    ) -> None:
        if not reservation_id:
            return
        # Finish callbacks can be replayed after another callback already
        # settled/released the same reservation.  The ledger remains strict;
        # only this explicit replay boundary treats an already-spent row as a
        # no-op.
        with contextlib.suppress(LedgerConflict):
            self.executor.usage.release(
                session,
                reservation_id=reservation_id,
                now_us=now_us,
            )

    def _classify_gateway_failure(
        self, *, claimed: ClaimedStep, error: GatewayError, now_us: int
    ) -> None:
        with session_scope() as session:
            if not self._claim_is_active(
                session,
                claimed=claimed,
                attempt_state=AttemptState.running,
                step_state=StepState.running,
            ):
                return
            if error.error_class == AttemptErrorClass.indeterminate:
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
        workflow_identity = f"{run['workflow_key']}@{run['workflow_version']}"
        reducer = self.executor.run_reducers.get(workflow_identity)
        if reducer is None:
            raise StateError(f"no trusted reducer for {workflow_identity}")
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
