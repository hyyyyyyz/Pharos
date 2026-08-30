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

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from pharos.db.session import session_scope
from pharos.harness.approvals import DEFAULT_EXPIRY_SECONDS, ApprovalRepository
from pharos.harness.artifacts import ArtifactStore, content_hash
from pharos.harness.contracts import (
    ArtifactSensitivity,
    AttemptErrorClass,
    AttemptState,
    DeliveryState,
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
from pharos.harness.model_gateway import (
    AttemptContext,
    GatewayFactory,
    GatewayHandle,
    GatewayLifecycleError,
)
from pharos.harness.repository import HarnessRunRepository, HarnessStepRepository, Scope, json_dump
from pharos.harness.state import HarnessStateService
from pharos.harness.tables import attempts, runs, steps, usage_events
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
    agent_output_contracts: dict[tuple[str, str], AgentOutputContract] = field(default_factory=dict)
    capabilities: dict[tuple[str, str], Any] = field(default_factory=dict)
    state: HarnessStateService = field(default_factory=HarnessStateService)
    usage: UsageLedger = field(default_factory=UsageLedger)
    events: EventStore = field(default_factory=EventStore)
    clock: FakeClock = field(default_factory=FakeClock)
    expanders: dict[str, StepExpander] = field(default_factory=dict)
    run_reducers: dict[str, RunReducer] = field(default_factory=dict)


def _scope_of(claimed: ClaimedStep) -> Scope:
    return Scope(scope_type=ScopeType(claimed.scope_type), scope_id=claimed.scope_id)


def _durable_error_message(error_class: AttemptErrorClass) -> str:
    """Return a fixed public diagnostic; never persist delegate exception text."""
    return {
        AttemptErrorClass.validation: "agent output does not match its role schema",
        AttemptErrorClass.configuration: "agent execution configuration is invalid",
        AttemptErrorClass.provider: "model gateway failed",
        AttemptErrorClass.budget: "agent budget was exceeded",
        AttemptErrorClass.timeout: "agent execution timed out",
        AttemptErrorClass.cancelled: "run cancellation requested",
        AttemptErrorClass.indeterminate: "model delivery outcome requires reconciliation",
        AttemptErrorClass.bug: "agent execution failed",
    }.get(error_class, "agent execution failed")


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
        self._cleanup_failed_handles: dict[str, GatewayHandle] = {}
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

    @property
    def cleanup_failed_attempt_ids(self) -> tuple[str, ...]:
        """Attempts whose bounded handle cleanup did not prove completion."""
        with self._active_handles_lock:
            return tuple(sorted(self._cleanup_failed_handles))

    @property
    def cleanup_failed_attempt_count(self) -> int:
        with self._active_handles_lock:
            return len(self._cleanup_failed_handles)

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
            if attempt_id in self._active_handles or attempt_id in self._cleanup_failed_handles:
                raise StateError(f"Attempt {attempt_id} already has a managed gateway handle")
            self._active_handles[attempt_id] = handle

    def _unregister_handle(self, attempt_id: str, handle: GatewayHandle) -> None:
        with self._active_handles_lock:
            # Identity comparison is deliberate: a late callback cannot
            # unregister a successor handle that reused the same Attempt key.
            if self._active_handles.get(attempt_id) is handle:
                del self._active_handles[attempt_id]

    def _mark_cleanup_failed(self, attempt_id: str, handle: GatewayHandle) -> None:
        """Move one exact handle out of active execution without losing it."""
        with self._active_handles_lock:
            if self._active_handles.get(attempt_id) is handle:
                del self._active_handles[attempt_id]
            existing = self._cleanup_failed_handles.get(attempt_id)
            if existing is not None and existing is not handle:
                raise StateError(f"Attempt {attempt_id} already has a different cleanup handle")
            self._cleanup_failed_handles[attempt_id] = handle

    def retry_failed_cleanup(self, attempt_id: str) -> bool:
        """Retry cleanup for a tracked handle and remove it only on success."""
        with self._active_handles_lock:
            handle = self._cleanup_failed_handles.get(attempt_id)
        if handle is None:
            return False
        try:
            handle.retry_cleanup()
        except BaseException:  # cleanup remains visible for operator recovery
            return False
        with self._active_handles_lock:
            if self._cleanup_failed_handles.get(attempt_id) is handle:
                del self._cleanup_failed_handles[attempt_id]
        return True

    def cancel_run_handles(self, *, scope: Scope, run_id: str) -> tuple[str, ...]:
        """Signal every active handle for exactly one owner-scoped Run."""
        with self._active_handles_lock:
            selected: list[tuple[str, GatewayHandle]] = []
            for registry in (self._active_handles, self._cleanup_failed_handles):
                selected.extend(
                    (attempt_id, handle)
                    for attempt_id, handle in registry.items()
                    if handle.context.run_id == run_id
                    and handle.context.scope_type == scope.scope_type.value
                    and handle.context.scope_id == scope.scope_id
                )
        signalled: list[str] = []
        for attempt_id, handle in selected:
            try:
                handle.cancel()
            except Exception:  # terminal/late handle; its runner owns cleanup
                continue
            signalled.append(attempt_id)
        return tuple(sorted(signalled))

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
            if self._run_cancel_requested(session, claimed=claimed):
                self.state.cancel_attempt_cas(
                    session,
                    scope=_scope_of(claimed),
                    run_id=claimed.run_id,
                    step_id=claimed.step_id,
                    attempt_id=claimed.attempt_id,
                    attempt_no=claimed.attempt_no,
                    lease_owner=claimed.lease_owner,
                    attempt_state=AttemptState.leased,
                    step_state=StepState.leased,
                    now_us=now_us,
                    error_class=AttemptErrorClass.cancelled.value,
                )
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
                    error_message="retryable capability failure",
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
                    error_message="capability retries exhausted",
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
            raise SnapshotIntegrityError("deterministic entry does not match the frozen step")
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
        except RetryableCapabilityError:
            self._schedule_retry_or_fail(
                claimed=claimed,
                snapshot=snapshot,
                now_us=self.executor.clock.utc_epoch_us(),
            )
            return
        except Exception:  # noqa: BLE001 -- capability outcome is typed in the DB
            with session_scope() as session:
                self.state.transition_attempt(
                    session,
                    attempt_id=claimed.attempt_id,
                    target=AttemptState.failed,
                    now_us=self.executor.clock.utc_epoch_us(),
                    error_class=AttemptErrorClass.bug.value,
                    error_message="capability executor failed",
                )
                self.state.transition_step(
                    session,
                    step_id=claimed.step_id,
                    target=StepState.failed,
                    now_us=self.executor.clock.utc_epoch_us(),
                    error_code="capability_error",
                    error_message="capability executor failed",
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
        if not all(isinstance(value, str) and value for value in (provider, model, usage_source)):
            raise SnapshotIntegrityError("agent snapshot has incomplete model route metadata")
        assert isinstance(usage_source, str)
        contract = self._agent_contract(snapshot)
        if contract is None:
            # Do not reserve usage or call the gateway when the role contract
            # is absent, keyed by the wrong role hash, or disagrees with the
            # authenticated role definition.
            with session_scope() as session:
                self._start_agent_attempt(session, claimed=claimed, now_us=now_us)
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
                if not self._start_agent_attempt(session, claimed=claimed, now_us=now_us):
                    return
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
        input = json.loads(run["input_json"])
        reservation: str | None = None
        with session_scope() as session:
            # Parent Run lock, leased->running CAS, and usage reserve share
            # one transaction.  The route values come only from this frozen
            # snapshot, never from a live config projection.
            if not self._start_agent_attempt(session, claimed=claimed, now_us=now_us):
                return
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
                provider=provider,
                model=model,
            )
        # The reservation transaction ends before opening the gateway.  Recheck
        # the persisted parent fence so a cancel committed in that gap cannot
        # even invoke ``factory.open`` (and therefore cannot create a runtime
        # handle or send anything externally).
        with session_scope() as session:
            if self._run_cancel_requested(session, claimed=claimed):
                self._release_reservation(session, reservation_id=reservation, now_us=now_us)
                self.state.finish_attempt_cas(
                    session,
                    scope=_scope_of(claimed),
                    run_id=claimed.run_id,
                    step_id=claimed.step_id,
                    attempt_id=claimed.attempt_id,
                    attempt_no=claimed.attempt_no,
                    lease_owner=claimed.lease_owner,
                    expected_attempt_state=AttemptState.running,
                    expected_step_state=StepState.running,
                    target=AttemptState.failed,
                    now_us=now_us,
                    attempt_values={"error_class": AttemptErrorClass.cancelled.value},
                    step_values={"error_code": AttemptErrorClass.cancelled.value},
                    cancel_on_request=True,
                )
                return
        handle: GatewayHandle | None = None
        call_error: BaseException | None = None
        delivery_state = DeliveryState.NOT_STARTED
        completion_invoked = False
        completion_succeeded = False
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
            with session_scope() as session:
                cancel_after_open = self._run_cancel_requested(session, claimed=claimed)
            if cancel_after_open:
                handle.cancel()
                raise GatewayLifecycleError("run cancelled before model delivery")
            completion_invoked = True
            result = handle.complete(
                {
                    "workflow": run["workflow_key"],
                    "step": claimed.definition_step_key,
                    "input": input,
                }
            )
            completion_succeeded = True
        except BaseException as error:
            # Defer classification until cleanup has completed.  In
            # particular, cleanup must not hide a typed gateway error.
            call_error = error
        finally:
            if handle is not None:
                try:
                    observed_delivery = handle.delivery_state
                    if not isinstance(observed_delivery, DeliveryState):
                        raise TypeError("gateway delivery state is not typed")
                    delivery_state = observed_delivery
                except Exception:  # noqa: BLE001 -- malformed factory seam
                    delivery_state = (
                        DeliveryState.UNKNOWN if completion_invoked else DeliveryState.NOT_STARTED
                    )
                    if call_error is None:
                        call_error = GatewayLifecycleError(
                            "gateway handle did not expose a typed delivery state"
                        )

        close_error: BaseException | None = None
        if handle is not None:
            try:
                # Reap/cleanup is part of the Attempt boundary and precedes
                # validation and Artifact publication.
                handle.close()
            except BaseException as error:
                close_error = error
                # A failed close is not proof that a subprocess/process group
                # is gone. Keep the exact handle visible and fence out a
                # successor until cleanup can be proved or reconciled.
                self._mark_cleanup_failed(claimed.attempt_id, handle)
            else:
                # Remove only after successful cleanup; identity fencing
                # prevents a late callback from unregistering a successor.
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
        if call_error is not None or close_error is not None:
            now = self.executor.clock.utc_epoch_us()
            typed_class = (
                call_error.error_class
                if isinstance(call_error, GatewayError)
                else AttemptErrorClass.bug
            )
            delivery_unknown = (
                completion_succeeded
                or close_error is not None
                or delivery_state is not DeliveryState.NOT_STARTED
                or typed_class is AttemptErrorClass.indeterminate
            )
            if delivery_unknown:
                # The request may have crossed the provider boundary.  Do not
                # release or settle the reservation: reconciliation owns it.
                self._finish_gateway_indeterminate(
                    claimed=claimed,
                    delivery_state=(
                        delivery_state
                        if delivery_state
                        in {
                            DeliveryState.UNKNOWN,
                            DeliveryState.SENT,
                            DeliveryState.ACKNOWLEDGED,
                        }
                        else DeliveryState.UNKNOWN
                    ),
                    now_us=now,
                )
            else:
                self._finish_agent_failure(
                    claimed=claimed,
                    reservation_id=reservation or "",
                    error_class=typed_class,
                    error_message=(
                        "model gateway handle cleanup failed"
                        if close_error is not None
                        else "model gateway failed before delivery"
                    ),
                    now_us=now,
                )
            if call_error is not None and not isinstance(call_error, Exception):
                # Process-control exceptions still stop the worker, but only
                # after the Attempt and usage reservation have a durable,
                # delivery-aware outcome.
                raise call_error
            return

        if delivery_state is not DeliveryState.ACKNOWLEDGED:
            # A result object alone is not a provider receipt. A malformed or
            # custom handle may return early; publishing it would fabricate
            # ACK evidence and could hide a replay/double-spend window.
            self._finish_gateway_indeterminate(
                claimed=claimed,
                delivery_state=(
                    delivery_state
                    if delivery_state
                    in {
                        DeliveryState.UNKNOWN,
                        DeliveryState.SENT,
                        DeliveryState.ACKNOWLEDGED,
                    }
                    else DeliveryState.UNKNOWN
                ),
                now_us=self.executor.clock.utc_epoch_us(),
            )
            return

        if not isinstance(result, ModelResult):
            # ACK proves delivery, but without a typed usage/result envelope
            # the accounting outcome is unresolved and must be reconcilable.
            self._finish_gateway_indeterminate(
                claimed=claimed,
                delivery_state=delivery_state,
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
            self._finish_agent_failure_with_usage(
                claimed=claimed,
                reservation_id=reservation or "",
                error_class=AttemptErrorClass.validation,
                error_message=str(error),
                actual_tokens=result.output_tokens,
                delivery_state=delivery_state,
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
            self._finish_agent_failure_with_usage(
                claimed=claimed,
                reservation_id=reservation or "",
                error_class=AttemptErrorClass.bug,
                error_message="agent finish transaction failed",
                actual_tokens=result.output_tokens,
                delivery_state=delivery_state,
                now_us=self.executor.clock.utc_epoch_us(),
            )

    def _start_agent_attempt(self, session: Session, *, claimed: ClaimedStep, now_us: int) -> bool:
        """Start exactly one leased generation under the persisted Run fence."""
        return self.state.start_attempt_cas(
            session,
            scope=_scope_of(claimed),
            run_id=claimed.run_id,
            step_id=claimed.step_id,
            attempt_id=claimed.attempt_id,
            attempt_no=claimed.attempt_no,
            lease_owner=claimed.lease_owner,
            now_us=now_us,
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

    def _agent_contract(self, snapshot: AttemptDefinitionSnapshot) -> AgentOutputContract | None:
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
            # A valid result proves provider execution even if the frozen
            # snapshot becomes unavailable before publication. Settle its
            # trusted usage and terminalise the exact active generation.
            self._finish_agent_failure_with_usage(
                claimed=claimed,
                reservation_id=reservation_id,
                delivery_state=DeliveryState.ACKNOWLEDGED,
                actual_tokens=result.output_tokens,
                error_class=AttemptErrorClass.bug,
                error_message="execution snapshot unavailable at publication",
                now_us=now_us,
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
                # The exact Attempt generation lost its terminal CAS, but its
                # completed provider result still carries real usage. Settle
                # that reservation without allowing the late result to mutate
                # the successor Step or publish an Artifact.
                with contextlib.suppress(LedgerConflict):
                    self.executor.usage.settle(
                        session,
                        reservation_id=reservation_id,
                        actual=result.output_tokens,
                        now_us=now_us,
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
                self._release_reservation(session, reservation_id=reservation_id, now_us=now_us)
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

            # This CAS is the publication fence.  It must be the first write
            # in this transaction and includes the run's cancel predicate;
            # SQLite therefore serialises a winning cancel commit against all
            # subsequent Artifact/usage/terminal writes.
            publication_fenced = self.state.publish_attempt_cas(
                session,
                scope=scope,
                run_id=claimed.run_id,
                step_id=claimed.step_id,
                attempt_id=claimed.attempt_id,
                attempt_no=claimed.attempt_no,
                lease_owner=claimed.lease_owner,
                now_us=now_us,
                delivery_state=DeliveryState.ACKNOWLEDGED.value,
                role_or_capability=role,
                model_prompt_version=contract.prompt_version,
                input_sha256=run["input_sha256"],
                output_sha256=content_hash(typed_output),
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cost_micros=result.cost_micros,
                provider_request_id=result.provider_request_id,
            )
            if not publication_fenced:
                # A cancel CAS may have won before this publication fence. In
                # that case the provider result is real and must be settled,
                # but it is never user-visible. A different terminal winner
                # owns the reservation and this replay becomes a no-op.
                with contextlib.suppress(LedgerConflict):
                    self.executor.usage.settle(
                        session,
                        reservation_id=reservation_id,
                        actual=result.output_tokens,
                        now_us=now_us,
                    )
                self.state.cancel_attempt_cas(
                    session,
                    scope=scope,
                    run_id=claimed.run_id,
                    step_id=claimed.step_id,
                    attempt_id=claimed.attempt_id,
                    attempt_no=claimed.attempt_no,
                    lease_owner=claimed.lease_owner,
                    attempt_state=AttemptState.running,
                    step_state=StepState.running,
                    now_us=now_us,
                    delivery_state=DeliveryState.ACKNOWLEDGED.value,
                    error_class=AttemptErrorClass.cancelled.value,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    cost_micros=result.cost_micros,
                    provider_request_id=result.provider_request_id,
                )
                return

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
            self._release_reservation(session, reservation_id=reservation_id, now_us=now_us)
            # The terminal CAS rechecks owner, generation, parent state and
            # cancellation.  A stale callback can settle/release only its own
            # reservation and cannot mutate a successor Attempt.
            self.state.finish_attempt_cas(
                session,
                scope=_scope_of(claimed),
                run_id=claimed.run_id,
                step_id=claimed.step_id,
                attempt_id=claimed.attempt_id,
                attempt_no=claimed.attempt_no,
                lease_owner=claimed.lease_owner,
                expected_attempt_state=AttemptState.running,
                expected_step_state=StepState.running,
                target=AttemptState.failed,
                now_us=now_us,
                attempt_values={
                    "error_class": error_class.value,
                    "error_message": _durable_error_message(error_class),
                },
                step_values={
                    "error_code": error_class.value,
                    "error_message": _durable_error_message(error_class),
                    "skip_reason": (
                        "run_cancelled" if error_class is AttemptErrorClass.cancelled else None
                    ),
                },
                cancel_on_request=True,
            )

    def _finish_gateway_indeterminate(
        self,
        *,
        claimed: ClaimedStep,
        delivery_state: DeliveryState,
        now_us: int,
    ) -> None:
        """Terminalise an unknown external outcome without spending its reserve."""
        if delivery_state in {DeliveryState.NOT_STARTED, DeliveryState.RECONCILED}:
            raise StateError("indeterminate gateway outcome requires delivery evidence")
        with session_scope() as session:
            self.state.finish_attempt_cas(
                session,
                scope=_scope_of(claimed),
                run_id=claimed.run_id,
                step_id=claimed.step_id,
                attempt_id=claimed.attempt_id,
                attempt_no=claimed.attempt_no,
                lease_owner=claimed.lease_owner,
                expected_attempt_state=AttemptState.running,
                expected_step_state=StepState.running,
                target=AttemptState.indeterminate,
                now_us=now_us,
                attempt_values={
                    "delivery_state": delivery_state.value,
                    "external_outcome": "indeterminate",
                    "error_class": AttemptErrorClass.indeterminate.value,
                    "error_message": "model delivery outcome requires reconciliation",
                },
                step_values={"error_code": "external_outcome_unknown"},
            )

    def _finish_agent_failure_with_usage(
        self,
        *,
        claimed: ClaimedStep,
        reservation_id: str,
        delivery_state: DeliveryState,
        actual_tokens: int,
        error_class: AttemptErrorClass,
        error_message: str,
        now_us: int,
    ) -> None:
        """Fail a known response and settle its trusted token usage atomically."""
        if delivery_state is not DeliveryState.ACKNOWLEDGED:
            raise StateError("trusted model usage requires an acknowledged response")
        with session_scope() as session:
            settled = False
            try:
                with session.begin_nested():
                    self.executor.usage.settle(
                        session,
                        reservation_id=reservation_id,
                        actual=actual_tokens,
                        now_us=now_us,
                    )
                settled = True
            except Exception:  # noqa: BLE001 -- unresolved reserve is safer than release
                log.error(
                    "agent failure usage settlement remains unresolved for %s",
                    claimed.attempt_id,
                )
            if not settled:
                self.state.finish_attempt_cas(
                    session,
                    scope=_scope_of(claimed),
                    run_id=claimed.run_id,
                    step_id=claimed.step_id,
                    attempt_id=claimed.attempt_id,
                    attempt_no=claimed.attempt_no,
                    lease_owner=claimed.lease_owner,
                    expected_attempt_state=AttemptState.running,
                    expected_step_state=StepState.running,
                    target=AttemptState.indeterminate,
                    now_us=now_us,
                    attempt_values={
                        "delivery_state": delivery_state.value,
                        "external_outcome": "usage_reconciliation_required",
                        "error_class": AttemptErrorClass.indeterminate.value,
                        "error_message": "model usage requires reconciliation",
                        "output_tokens": actual_tokens,
                    },
                    step_values={"error_code": "usage_reconciliation_required"},
                )
                return
            self.state.finish_attempt_cas(
                session,
                scope=_scope_of(claimed),
                run_id=claimed.run_id,
                step_id=claimed.step_id,
                attempt_id=claimed.attempt_id,
                attempt_no=claimed.attempt_no,
                lease_owner=claimed.lease_owner,
                expected_attempt_state=AttemptState.running,
                expected_step_state=StepState.running,
                target=AttemptState.failed,
                now_us=now_us,
                attempt_values={
                    "delivery_state": delivery_state.value,
                    "error_class": error_class.value,
                    "error_message": _durable_error_message(error_class),
                    "output_tokens": actual_tokens,
                },
                step_values={
                    "error_code": error_class.value,
                    "error_message": _durable_error_message(error_class),
                },
                cancel_on_request=True,
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
            session.execute(steps.select().where(steps.c.id == claimed.step_id)).mappings().first()
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

    def _run_cancel_requested(self, session: Session, *, claimed: ClaimedStep) -> bool:
        requested_at = session.execute(
            runs.select()
            .with_only_columns(runs.c.cancel_requested_at)
            .where(
                runs.c.id == claimed.run_id,
                runs.c.scope_type == claimed.scope_type,
                runs.c.scope_id == claimed.scope_id,
            )
        ).scalar_one_or_none()
        return requested_at is not None

    def _release_reservation(
        self, session: Session, *, reservation_id: str | None, now_us: int
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
            if error.error_class == AttemptErrorClass.indeterminate:
                self.state.finish_attempt_cas(
                    session,
                    scope=_scope_of(claimed),
                    run_id=claimed.run_id,
                    step_id=claimed.step_id,
                    attempt_id=claimed.attempt_id,
                    attempt_no=claimed.attempt_no,
                    lease_owner=claimed.lease_owner,
                    expected_attempt_state=AttemptState.running,
                    expected_step_state=StepState.running,
                    target=AttemptState.indeterminate,
                    now_us=now_us,
                    attempt_values={
                        "external_outcome": "indeterminate",
                        "error_class": AttemptErrorClass.indeterminate.value,
                        "error_message": "model delivery outcome requires reconciliation",
                    },
                    step_values={"error_code": "external_outcome_unknown"},
                )
                return
            self.state.finish_attempt_cas(
                session,
                scope=_scope_of(claimed),
                run_id=claimed.run_id,
                step_id=claimed.step_id,
                attempt_id=claimed.attempt_id,
                attempt_no=claimed.attempt_no,
                lease_owner=claimed.lease_owner,
                expected_attempt_state=AttemptState.running,
                expected_step_state=StepState.running,
                target=AttemptState.failed,
                now_us=now_us,
                attempt_values={
                    "error_class": error.error_class.value,
                    "error_message": "model gateway failed",
                },
                step_values={
                    "error_code": error.error_class.value,
                    "error_message": "model gateway failed",
                },
                cancel_on_request=True,
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
            self.state.reduce_run_cas(
                session,
                scope=scope,
                run_id=run["id"],
                expected_state=RunState(run["state"]),
                target=target,
                outcome=outcome.value if outcome else None,
                now_us=now_us,
            )
        else:
            self.state.reduce_run_cas(
                session,
                scope=scope,
                run_id=run["id"],
                expected_state=RunState(run["state"]),
                target=target,
                outcome=None,
                now_us=now_us,
            )

    # ---------------------------------------------------------------- control

    def _has_registered_handle(self, attempt_id: str) -> bool:
        """Check both live and cleanup-failed registries without doing I/O."""
        with self._active_handles_lock:
            return attempt_id in self._active_handles or attempt_id in self._cleanup_failed_handles

    @staticmethod
    def _attempt_has_pending_usage(session: Session, attempt_id: str) -> bool:
        reserve = usage_events.alias("cancel_reserve")
        spent = usage_events.alias("cancel_spent")
        return (
            session.execute(
                select(reserve.c.id)
                .where(
                    reserve.c.attempt_id == attempt_id,
                    reserve.c.op == "reserve",
                    ~exists(
                        select(1)
                        .select_from(spent)
                        .where(
                            spent.c.reservation_id == reserve.c.reservation_id,
                            spent.c.run_id == reserve.c.run_id,
                            spent.c.step_id == reserve.c.step_id,
                            spent.c.attempt_id == reserve.c.attempt_id,
                            spent.c.scope_type == reserve.c.scope_type,
                            spent.c.scope_id == reserve.c.scope_id,
                            spent.c.op.in_(["settle", "release"]),
                        )
                    ),
                )
                .limit(1)
            ).scalar_one_or_none()
            is not None
        )

    def apply_pending_control(self, *, now_us: int) -> int:
        """Turn persistent pause/cancel requests into states."""
        changed = 0
        # Cancel: requests win unless an external outcome is indeterminate.
        with session_scope() as session:
            requested = [
                dict(row)
                for row in (
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
            ]
        # Runtime cancellation is deliberately outside a database transaction.
        # A blocked provider/child cannot hold SQLite's writer lock.
        for requested_row in requested:
            scope = Scope(
                scope_type=ScopeType(requested_row["scope_type"]),
                scope_id=requested_row["scope_id"],
            )
            self.cancel_run_handles(scope=scope, run_id=requested_row["id"])

        with session_scope() as session:
            for requested_run in requested:
                current_run = (
                    session.execute(
                        runs.select().where(
                            runs.c.id == requested_run["id"],
                            runs.c.scope_type == requested_run["scope_type"],
                            runs.c.scope_id == requested_run["scope_id"],
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
                    .first()
                )
                if current_run is None:
                    continue
                scope = Scope(
                    scope_type=ScopeType(current_run["scope_type"]),
                    scope_id=current_run["scope_id"],
                )
                run_steps = self.step_repository.for_run(
                    session, scope=scope, run_id=current_run["id"]
                )
                # The in-memory handle registry is only an optimisation for
                # signalling. Recovery must inspect the durable current
                # generation, including Attempts created by another worker or
                # before a restart.  The owner and attempt number below are
                # all part of each terminal CAS fence.
                active_attempts = (
                    session.execute(
                        attempts.select()
                        .add_columns(
                            steps.c.state.label("current_step_state"),
                            steps.c.lease_owner.label("current_step_lease_owner"),
                        )
                        .where(
                            attempts.c.run_id == current_run["id"],
                            attempts.c.scope_type == current_run["scope_type"],
                            attempts.c.scope_id == current_run["scope_id"],
                            attempts.c.state.in_(["leased", "running"]),
                            attempts.c.attempt_no == steps.c.attempt_count,
                            steps.c.id == attempts.c.step_id,
                            steps.c.run_id == attempts.c.run_id,
                            steps.c.scope_type == attempts.c.scope_type,
                            steps.c.scope_id == attempts.c.scope_id,
                            steps.c.state.in_(["leased", "running"]),
                            steps.c.lease_owner == attempts.c.lease_owner,
                        )
                        .order_by(attempts.c.attempt_no, attempts.c.id)
                    )
                    .mappings()
                    .all()
                )
                for active_attempt in active_attempts:
                    attempt_id = active_attempt["id"]
                    # A local handle owns its callback/cleanup boundary. The
                    # cancellation signal was sent above; changing its DB
                    # state here would race delivery classification.
                    if self._has_registered_handle(attempt_id):
                        continue
                    owner = active_attempt["lease_owner"]
                    step_state = StepState(active_attempt["current_step_state"])
                    attempt_state = AttemptState(active_attempt["state"])
                    delivery = active_attempt["delivery_state"]
                    pending_usage = self._attempt_has_pending_usage(session, attempt_id)
                    # Without a local handle, a leased Attempt is cancellable
                    # only when both delivery and usage show no provider
                    # boundary. Running, any delivery evidence, or a pending
                    # reservation is deliberately indeterminate.
                    definitely_unsent = (
                        attempt_state is AttemptState.leased
                        and delivery in (None, DeliveryState.NOT_STARTED.value)
                        and not pending_usage
                        and active_attempt["runtime_session_id"] is None
                        and active_attempt["child_pid"] is None
                    )
                    if not isinstance(owner, str) or not owner:
                        definitely_unsent = False
                    if definitely_unsent:
                        self.state.cancel_attempt_cas(
                            session,
                            scope=scope,
                            run_id=current_run["id"],
                            step_id=active_attempt["step_id"],
                            attempt_id=attempt_id,
                            attempt_no=active_attempt["attempt_no"],
                            lease_owner=owner,
                            attempt_state=attempt_state,
                            step_state=step_state,
                            now_us=now_us,
                        )
                    else:
                        self.state.indeterminate_attempt_cas(
                            session,
                            scope=scope,
                            run_id=current_run["id"],
                            step_id=active_attempt["step_id"],
                            attempt_id=attempt_id,
                            attempt_no=active_attempt["attempt_no"],
                            lease_owner=owner,
                            attempt_state=attempt_state,
                            step_state=step_state,
                            delivery_state=(
                                delivery
                                if delivery
                                in {
                                    DeliveryState.SENT.value,
                                    DeliveryState.ACKNOWLEDGED.value,
                                    DeliveryState.UNKNOWN.value,
                                }
                                else DeliveryState.UNKNOWN.value
                            ),
                            now_us=now_us,
                        )
                # Refresh after the current-generation CAS decisions. Stale
                # attempts and late terminal callbacks must not keep a Run
                # from reducing, and must never be mistaken for a successor.
                run_steps = self.step_repository.for_run(
                    session, scope=scope, run_id=current_run["id"]
                )
                pending_steps = [
                    row
                    for row in run_steps
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
                # A running/leased Attempt owns its terminal delivery and
                # usage decision. Do not cancel the Run over it: the handle
                # callback will choose cancelled (definitely unsent) or
                # indeterminate (possibly delivered), then the next pass can
                # reduce the Run safely.
                if any(row["state"] in ("leased", "running") for row in run_steps):
                    continue
                if any(row["state"] == StepState.indeterminate.value for row in run_steps):
                    reduced = self.state.reduce_run_cas(
                        session,
                        scope=scope,
                        run_id=current_run["id"],
                        target=RunState.indeterminate,
                        expected_state=RunState(current_run["state"]),
                        outcome=RunOutcome.incomplete.value,
                        now_us=now_us,
                    )
                    changed += int(reduced)
                    continue
                reduced = self.state.reduce_run_cas(
                    session,
                    scope=scope,
                    run_id=current_run["id"],
                    target=RunState.cancelled,
                    expected_state=RunState(current_run["state"]),
                    outcome=RunOutcome.incomplete.value,
                    now_us=now_us,
                )
                changed += int(reduced)
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
                paused = self.state.reduce_run_cas(
                    session,
                    scope=scope,
                    run_id=run["id"],
                    expected_state=RunState(run["state"]),
                    target=RunState.paused,
                    outcome=None,
                    now_us=now_us,
                )
                changed += int(paused)
        return changed
