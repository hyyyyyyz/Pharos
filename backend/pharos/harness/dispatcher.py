"""Database-backed claim, lease, heartbeat and reaper.

The database is the execution truth. A claim is one conditional UPDATE in a
short transaction; two workers racing on the same step get at most one lease.
Network and model calls happen strictly outside any transaction, and all
synchronous SQLAlchemy work runs in a worker thread so the event loop never
blocks on SQLite.

Time is UTC epoch microseconds throughout (see the schema comments); nothing
here reads SQLite datetimes for lease math.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.orm import Session

from pharos.harness.configrev import (
    HarnessConfigSnapshot,
    emergency_stop_active,
)
from pharos.harness.contracts import (
    AttemptState,
    ConfigIntegrityError,
    NotFoundError,
    RetryClass,
    RunState,
    ScopeType,
    StepState,
)
from pharos.harness.execution_snapshots import (
    AttemptDefinitionSnapshot,
    ExecutionSnapshotStore,
    MissingExecutionSnapshotError,
    SnapshotIntegrityError,
)
from pharos.harness.repository import HarnessConfigService, HarnessRunRepository, Scope
from pharos.harness.state import HarnessStateService
from pharos.harness.tables import attempts, config_head, runs, steps, usage_events
from pharos.harness.usage import LedgerConflict, UsageLedger

log = logging.getLogger(__name__)

MICROSECONDS_PER_SECOND = 1_000_000

#: lease > busy_timeout + measured scheduling jitter, and heartbeat < 1/3 lease.
DEFAULT_LEASE_SECONDS = 60.0
DEFAULT_HEARTBEAT_SECONDS = 15.0
DEFAULT_CLAIM_BATCH = 8


@dataclass(frozen=True)
class ClaimedStep:
    step_id: str
    attempt_id: str
    run_id: str
    scope_type: str
    scope_id: str
    definition_step_key: str
    instance_key: str
    step_kind: str
    definition_json: str
    attempt_no: int
    lease_owner: str
    # These are projections of the authenticated Run binding/policy and the
    # current config safety fence, never copied from run input or accepted as
    # worker-provided metadata.
    role: str | None = None
    runtime_kind: str | None = None
    config_revision_id: str | None = None
    snapshot_sha256: str | None = None
    role_definition_sha256: str | None = None
    attempt_snapshot: AttemptDefinitionSnapshot | None = None


class HarnessDispatcher:
    """Claims due steps and recovers abandoned leases."""

    def __init__(
        self,
        *,
        worker_id: str | None = None,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
        claim_batch: int = DEFAULT_CLAIM_BATCH,
        state_service: HarnessStateService | None = None,
        config_service: HarnessConfigService | None = None,
        usage_ledger: UsageLedger | None = None,
    ) -> None:
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:12]}"
        if heartbeat_seconds >= lease_seconds / 3:
            raise ValueError("heartbeat interval must be under a third of the lease")
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.claim_batch = claim_batch
        self.state = state_service or HarnessStateService()
        self.config_service = config_service
        self.usage = usage_ledger or UsageLedger()
        self.execution_snapshots = ExecutionSnapshotStore()

    # ------------------------------------------------------------- claiming

    def _execution_fence(
        self, session: Session
    ) -> tuple[str, HarnessConfigSnapshot, str] | None:
        """Load the current config head used to fence a claim.

        The head is deliberately read and validated here, at claim time. A
        queued run is not permission to execute forever: an operator can
        disable the dispatcher or move a workflow back to its legacy writer
        after that run was created. Malformed or inconsistent config is a
        closed gate, never a reason to guess.
        """
        if emergency_stop_active() or self.config_service is None:
            return None
        current = self.config_service.current_validated(session)
        if current is None:
            return None
        head = current.revision_id
        snapshot = current.snapshot
        if not snapshot.gates.get("harness_enabled") or not snapshot.gates.get(
            "dispatcher_enabled"
        ):
            return None
        return head, snapshot, current.snapshot_sha256

    @staticmethod
    def _route_allows_claim(
        snapshot: HarnessConfigSnapshot,
        *,
        workflow_key: str,
        workflow_version: int,
        step_kind: str,
        runtime_kind: str | None = None,
    ) -> bool:
        route = next((item for item in snapshot.routes if item.workflow_key == workflow_key), None)
        if route is None or route.activation_state.value != "active":
            return False
        # A queued Run already carries its immutable workflow definition.  A
        # later active-version cutover must not strand that Run solely because
        # its version is no longer the creation-time head.  Route activation,
        # execution mode and operator gates remain the live safety fence.
        # The internal canary has no legacy writer and therefore intentionally
        # uses execution_mode=NULL. Every business workflow must explicitly be
        # in Harness mode before a queued step can cross this fence.
        if workflow_key == "harness.canary":
            if not snapshot.gates.get("canary_enabled") or route.execution_mode is not None:
                return False
        elif route.execution_mode is None or route.execution_mode.value != "harness":
            return False
        if step_kind in ("agent", "mapped_agent"):
            if not snapshot.gates.get("agent_steps_enabled"):
                return False
            # The legacy in-process fake is deliberately independent of the
            # H1.5 DSH gate. Only a trusted DSH role crosses that extra fence.
            if runtime_kind == "dsh" and not snapshot.gates.get("agent_runtime_enabled"):
                return False
        return True

    @staticmethod
    def _runtime_kind_from_executor(
        fields: dict[str, Any], run_snapshot: Any
    ) -> str | None:
        if fields.get("executor_kind") != "role":
            return None
        identity = fields.get("executor_identity")
        for binding in run_snapshot.policy_snapshot.role_bindings:
            if binding.role_identity == identity:
                return binding.role_definition.runtime_kind
        return None

    def claim_due(
        self,
        session: Session,
        *,
        now_us: int,
        limit: int | None = None,
        _after: tuple[int, str] | None = None,
    ) -> ClaimedStep | None:
        """Atomically claim one due, ready step; None when the queue is empty.

        The claim is a single conditional UPDATE guarded by
        ``state='ready'``; the matching attempt is inserted in the same short
        transaction. A step that was already claimed by another worker simply
        no longer matches the WHERE clause -- no select-then-update race.
        """
        limit = limit or self.claim_batch
        lease_expires = now_us + int(self.lease_seconds * MICROSECONDS_PER_SECOND)
        try:
            fence = self._execution_fence(session)
        except ConfigIntegrityError:
            # A malformed/tampered config is an unavailable claim fence, not a
            # reason to guess a route or create an Attempt.
            return None
        if fence is None:
            return None
        revision_id, snapshot, snapshot_sha256 = fence
        # Claims only steps whose run is active and has no pause/cancel request:
        # the fence is read in the same short transaction as the claim, so a
        # control request committed just before cannot be crossed by a stale
        # worker.
        candidates = (
            select(
                steps.c.id,
                steps.c.run_id,
                steps.c.scope_type,
                steps.c.scope_id,
                runs.c.workflow_key,
                runs.c.workflow_version,
                runs.c.definition_sha256,
                steps.c.ready_at,
                steps.c.step_kind,
                steps.c.definition_step_key,
                steps.c.instance_key,
                steps.c.definition_json,
                steps.c.depends_on_json,
                steps.c.fan_in,
                steps.c.min_success_count,
                steps.c.max_attempts,
                steps.c.timeout_seconds,
                steps.c.retry_policy_json,
            )
            .join(runs, runs.c.id == steps.c.run_id)
            .where(
                steps.c.state == StepState.ready.value,
                steps.c.ready_at <= now_us,
                runs.c.state.in_([RunState.queued.value, RunState.running.value]),
                runs.c.cancel_requested_at.is_(None),
                runs.c.pause_requested_at.is_(None),
            )
        )
        page_size = max(limit, self.claim_batch, 1)
        cursor: tuple[int, str] | None = _after
        while True:
            page_query = candidates
            if cursor is not None:
                ready_cursor, id_cursor = cursor
                page_query = page_query.where(
                    or_(
                        steps.c.ready_at > ready_cursor,
                        and_(steps.c.ready_at == ready_cursor, steps.c.id > id_cursor),
                    )
                )
            page = (
                session.execute(page_query.order_by(steps.c.ready_at, steps.c.id).limit(page_size))
                .mappings()
                .all()
            )
            if not page:
                return None
            for row in page:
                # Legacy Runs remain readable, but cannot cross the execution
                # boundary. Invalid snapshots are skipped as queue data, so
                # one corrupt row cannot repeatedly poison the queue head.
                try:
                    run_snapshot = self.execution_snapshots.read_run(
                        session,
                        scope=row["scope_type"],
                        scope_id=row["scope_id"],
                        run_id=row["run_id"],
                        require_for_execution=True,
                    )
                    assert run_snapshot is not None
                    if (
                        run_snapshot.workflow_key,
                        run_snapshot.workflow_version,
                        run_snapshot.workflow_definition_sha256,
                    ) != (
                        row["workflow_key"],
                        row["workflow_version"],
                        row["definition_sha256"],
                    ):
                        continue
                    executor_fields = self.execution_snapshots.resolve_executor_fields(
                        session, run_snapshot=run_snapshot, step=dict(row)
                    )
                except (MissingExecutionSnapshotError, SnapshotIntegrityError, NotFoundError):
                    continue
                runtime_kind = self._runtime_kind_from_executor(executor_fields, run_snapshot)
                if not self._route_allows_claim(
                    snapshot,
                    workflow_key=row["workflow_key"],
                    workflow_version=row["workflow_version"],
                    step_kind=row["step_kind"],
                    runtime_kind=runtime_kind,
                ):
                    continue
                candidate, candidate_snapshot, candidate_fields = (
                    row,
                    run_snapshot,
                    executor_fields,
                )
                result: Any = None
                # The savepoint is the claim's atomic boundary.  If snapshot
                # validation or persistence fails, a caller may catch the
                # exception and still commit its outer transaction without
                # leaving behind a leased Step or orphan Attempt.
                with session.begin_nested():
                    result = session.execute(
                        update(steps)
                        .where(
                            steps.c.id == candidate["id"],
                            steps.c.state == StepState.ready.value,
                            exists(
                                select(1)
                                .select_from(runs)
                                .where(
                                    runs.c.id == steps.c.run_id,
                                    runs.c.state.in_([
                                        RunState.queued.value,
                                        RunState.running.value,
                                    ]),
                                    runs.c.cancel_requested_at.is_(None),
                                    runs.c.pause_requested_at.is_(None),
                                )
                                .correlate_except(runs)
                            ),
                            # Re-check the head in the CAS itself. If an
                            # operator committed a new revision after the
                            # fence was read, lose cleanly.
                            exists(
                                select(1)
                                .select_from(config_head)
                                .where(
                                    config_head.c.head_key == "singleton",
                                    config_head.c.current_revision_id == revision_id,
                                )
                            ),
                        )
                        .values(
                            state=StepState.leased.value,
                            lease_owner=self.worker_id,
                            lease_expires_at=lease_expires,
                            heartbeat_at=now_us,
                            updated_at=now_us,
                        )
                    )
                    if result.rowcount != 1:
                        # Nothing was written in this savepoint. Continue the
                        # keyset scan rather than treating a CAS loser as an
                        # empty queue.
                        continue
                    claimed_row = (
                        session.execute(select(steps).where(steps.c.id == candidate["id"]))
                        .mappings()
                        .first()
                    )
                    assert claimed_row is not None
                    row = claimed_row
                    attempt_id = uuid.uuid4().hex
                    attempt_no = row["attempt_count"] + 1
                    session.execute(
                        attempts.insert().values(
                            id=attempt_id,
                            step_id=row["id"],
                            run_id=row["run_id"],
                            scope_type=row["scope_type"],
                            scope_id=row["scope_id"],
                            attempt_no=attempt_no,
                            worker_id=self.worker_id,
                            state=AttemptState.leased.value,
                            lease_owner=self.worker_id,
                            started_at=now_us,
                            heartbeat_at=now_us,
                        )
                    )
                    attempt_snapshot = self.execution_snapshots.write_attempt(
                        session,
                        scope=row["scope_type"],
                        scope_id=row["scope_id"],
                        attempt_id=attempt_id,
                        run_id=row["run_id"],
                        step_id=row["id"],
                        attempt_no=attempt_no,
                        lease_owner=self.worker_id,
                        definition_binding_sha256=candidate_snapshot.definition_binding_sha256,
                        run_policy_sha256=candidate_snapshot.policy_snapshot_sha256,
                        policy_snapshot=candidate_snapshot.policy_snapshot,
                        **candidate_fields,
                    )
                    session.execute(
                        update(steps)
                        .where(steps.c.id == row["id"])
                        .values(attempt_count=row["attempt_count"] + 1, updated_at=now_us)
                    )
                    return ClaimedStep(
                        step_id=row["id"],
                        attempt_id=attempt_id,
                        run_id=row["run_id"],
                        scope_type=row["scope_type"],
                        scope_id=row["scope_id"],
                        definition_step_key=row["definition_step_key"],
                        instance_key=row["instance_key"],
                        step_kind=row["step_kind"],
                        definition_json=row["definition_json"],
                        attempt_no=attempt_no,
                        lease_owner=self.worker_id,
                        role=(
                            attempt_snapshot.executor_identity
                            if attempt_snapshot.executor_kind == "role"
                            else None
                        ),
                        runtime_kind=attempt_snapshot.runtime_kind,
                        config_revision_id=revision_id,
                        snapshot_sha256=snapshot_sha256,
                        role_definition_sha256=attempt_snapshot.executor_role_definition_sha256,
                        attempt_snapshot=attempt_snapshot,
                    )
            if len(page) < page_size:
                return None
            cursor = page[-1]["ready_at"], page[-1]["id"]

    # ----------------------------------------------------------- retry queue

    def activate_retries(self, session: Session, *, now_us: int) -> int:
        """Promote due retries through the frozen execution fence.

        A retry is a continuation of its original Run, not a new policy
        decision.  The current head still supplies the operator safety fence;
        the frozen Run binding supplies executor identity and version.
        """
        try:
            fence = self._execution_fence(session)
        except ConfigIntegrityError:
            return 0
        if fence is None:
            return 0
        revision_id, snapshot, _ = fence
        rows = (
            session.execute(
                select(
                    steps.c.id,
                    steps.c.run_id,
                    steps.c.scope_type,
                    steps.c.scope_id,
                    steps.c.step_kind,
                    steps.c.definition_step_key,
                    steps.c.instance_key,
                    steps.c.definition_json,
                    steps.c.depends_on_json,
                    steps.c.fan_in,
                    steps.c.min_success_count,
                    steps.c.max_attempts,
                    steps.c.timeout_seconds,
                    steps.c.retry_policy_json,
                    runs.c.workflow_key,
                    runs.c.workflow_version,
                    runs.c.definition_sha256,
                )
                .join(
                    runs,
                    (runs.c.id == steps.c.run_id)
                    & (runs.c.scope_type == steps.c.scope_type)
                    & (runs.c.scope_id == steps.c.scope_id),
                )
                .where(
                    steps.c.state == StepState.retry_scheduled.value,
                    steps.c.ready_at <= now_us,
                    runs.c.state.in_([RunState.queued.value, RunState.running.value]),
                    runs.c.cancel_requested_at.is_(None),
                    runs.c.pause_requested_at.is_(None),
                )
                .order_by(steps.c.ready_at, steps.c.id)
            )
            .mappings()
            .all()
        )
        activated = 0
        for row in rows:
            try:
                run_snapshot = self.execution_snapshots.read_run(
                    session,
                    scope=row["scope_type"],
                    scope_id=row["scope_id"],
                    run_id=row["run_id"],
                    require_for_execution=True,
                )
                assert run_snapshot is not None
                if (
                    run_snapshot.workflow_key,
                    run_snapshot.workflow_version,
                    run_snapshot.workflow_definition_sha256,
                ) != (
                    row["workflow_key"],
                    row["workflow_version"],
                    row["definition_sha256"],
                ):
                    continue
                executor_fields = self.execution_snapshots.resolve_executor_fields(
                    session, run_snapshot=run_snapshot, step=dict(row)
                )
            except (MissingExecutionSnapshotError, SnapshotIntegrityError, NotFoundError):
                continue
            if not self._route_allows_claim(
                snapshot,
                workflow_key=row["workflow_key"],
                workflow_version=row["workflow_version"],
                step_kind=row["step_kind"],
                runtime_kind=self._runtime_kind_from_executor(executor_fields, run_snapshot),
            ):
                continue
            # Multiple dispatchers can observe the same due row.  The state
            # service owns the conditional UPDATE; only its winner emits the
            # step.ready event.
            if self.state.activate_retry_cas(
                session,
                step_id=row["id"],
                now_us=now_us,
                config_revision_id=revision_id,
            ):
                activated += 1
        return activated

    # ------------------------------------------------------------ heartbeat

    def heartbeat(self, session: Session, *, attempt_id: str, now_us: int) -> bool:
        """Extend the lease; only the current lease owner may."""
        return self.state.update_attempt_heartbeat_cas(
            session,
            attempt_id=attempt_id,
            lease_owner=self.worker_id,
            now_us=now_us,
            lease_expires_at=now_us + int(self.lease_seconds * MICROSECONDS_PER_SECOND),
        )

    # ---------------------------------------------------------------- reaper

    def reap_expired(self, session: Session, *, now_us: int) -> list[dict]:
        """Mark attempts whose lease expired as abandoned; report them.

        Only attempts whose step still carries the same lease owner are
        abandoned (a step that was already re-claimed by a new lease is none
        of the reaper's business -- the old attempt row still terminates, but
        the step must not be touched).
        """
        expired = (
            session.execute(
                select(
                    attempts.c.id,
                    attempts.c.step_id,
                    attempts.c.lease_owner,
                    attempts.c.attempt_no,
                    attempts.c.run_id,
                    attempts.c.scope_type,
                    attempts.c.scope_id,
                    attempts.c.state,
                    attempts.c.delivery_state,
                    attempts.c.runtime_session_id,
                    attempts.c.child_pid,
                )
                .join(steps, steps.c.id == attempts.c.step_id)
                .where(
                    attempts.c.state.in_([AttemptState.leased.value, AttemptState.running.value]),
                    attempts.c.lease_owner == steps.c.lease_owner,
                    steps.c.lease_expires_at.is_not(None),
                    steps.c.lease_expires_at <= now_us,
                )
            )
            .mappings()
            .all()
        )
        abandoned: list[dict] = []
        for row in expired:
            recovery = self._expired_recovery(session, row=dict(row), now_us=now_us)
            scope = Scope(ScopeType(row["scope_type"]), row["scope_id"])
            if self.state.abandon_expired_attempt_cas(
                session,
                scope=scope,
                run_id=row["run_id"],
                attempt_id=row["id"],
                step_id=row["step_id"],
                attempt_no=row["attempt_no"],
                lease_owner=row["lease_owner"],
                now_us=now_us,
                recovery_step_state=recovery[0],
                retry_ready_at=recovery[1],
            ):
                if recovery[2]:
                    self._release_expired_reservations(session, row=dict(row), now_us=now_us)
                abandoned.append(dict(row))
        return abandoned

    def _expired_recovery(
        self, session: Session, *, row: dict, now_us: int
    ) -> tuple[StepState, int | None, bool]:
        """Choose expiry recovery from durable evidence and frozen policy only.

        A leased Attempt with no launch/delivery evidence has not crossed an
        executor boundary. Running Attempts are always conservative: the
        spawn-to-PID attach interval cannot be proven unsent from this schema.
        """
        evidence = row.get("delivery_state") not in (None, "not_started") or any(
            row.get(field) is not None for field in ("runtime_session_id", "child_pid")
        )
        if row.get("state") != AttemptState.leased.value or evidence:
            return StepState.indeterminate, None, False
        try:
            snapshot = self.execution_snapshots.read_attempt(
                session,
                scope=row["scope_type"],
                scope_id=row["scope_id"],
                attempt_id=row["id"],
                require_for_execution=True,
            )
        except (MissingExecutionSnapshotError, SnapshotIntegrityError, NotFoundError):
            return StepState.indeterminate, None, False
        if snapshot is None:
            return StepState.indeterminate, None, False
        policy = snapshot.retry_policy
        if policy is None or row["attempt_no"] >= snapshot.max_attempts:
            return StepState.failed, None, True
        if RetryClass.connect_timeout_unsent not in policy.retry_classes:
            return StepState.failed, None, True
        backoff = float(
            policy.backoff_seconds * (float(policy.backoff_factor) ** (row["attempt_no"] - 1))
        )
        return StepState.retry_scheduled, now_us + int(backoff * MICROSECONDS_PER_SECOND), True

    def _release_expired_reservations(self, session: Session, *, row: dict, now_us: int) -> None:
        """Release only open base reservations for a proven-unsent Attempt."""
        scope = Scope(ScopeType(row["scope_type"]), row["scope_id"])
        reserves = session.execute(
            select(usage_events.c.reservation_id).where(
                usage_events.c.scope_type == scope.scope_type.value,
                usage_events.c.scope_id == scope.scope_id,
                usage_events.c.run_id == row["run_id"],
                usage_events.c.step_id == row["step_id"],
                usage_events.c.attempt_id == row["id"],
                usage_events.c.op == "reserve",
                usage_events.c.reservation_id.is_not(None),
                ~usage_events.c.reservation_id.like("%:input"),
            )
        ).scalars().all()
        for reservation_id in reserves:
            try:
                self.usage.release(
                    session,
                    reservation_id=reservation_id,
                    scope=scope,
                    run_id=row["run_id"],
                    step_id=row["step_id"],
                    attempt_id=row["id"],
                    now_us=now_us,
                )
            except LedgerConflict:
                # A prior worker may have closed the reservation; the unique
                # outcome index makes that replay a harmless no-op.
                continue

    def step_scopes(self, session: Session, *, step_id: str) -> Scope | None:
        row = session.execute(select(steps).where(steps.c.id == step_id)).mappings().first()
        if row is None:
            return None
        return Scope(
            scope_type=ScopeType(row["scope_type"]),
            scope_id=row["scope_id"],
        )

    def run_repo(self) -> HarnessRunRepository:
        return HarnessRunRepository()
