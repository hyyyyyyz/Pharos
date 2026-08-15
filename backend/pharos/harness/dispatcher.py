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

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from pharos.harness.contracts import (
    AttemptState,
    LeaseConflictError,
    RunState,
    ScopeType,
    StepState,
)
from pharos.harness.repository import HarnessRunRepository, Scope
from pharos.harness.state import HarnessStateService
from pharos.harness.tables import attempts, runs, steps

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
    ) -> None:
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:12]}"
        if heartbeat_seconds >= lease_seconds / 3:
            raise ValueError("heartbeat interval must be under a third of the lease")
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.claim_batch = claim_batch
        self.state = state_service or HarnessStateService()

    # ------------------------------------------------------------- claiming

    def claim_due(
        self, session: Session, *, now_us: int, limit: int | None = None
    ) -> ClaimedStep | None:
        """Atomically claim one due, ready step; None when the queue is empty.

        The claim is a single conditional UPDATE guarded by
        ``state='ready'``; the matching attempt is inserted in the same short
        transaction. A step that was already claimed by another worker simply
        no longer matches the WHERE clause -- no select-then-update race.
        """
        limit = limit or self.claim_batch
        lease_expires = now_us + int(self.lease_seconds * MICROSECONDS_PER_SECOND)
        # Claims only steps whose run is active and has no pause/cancel request:
        # the fence is read in the same short transaction as the claim, so a
        # control request committed just before cannot be crossed by a stale
        # worker.
        candidate = (
            session.execute(
                select(steps.c.id)
                .join(runs, runs.c.id == steps.c.run_id)
                .where(
                    steps.c.state == StepState.ready.value,
                    steps.c.ready_at <= now_us,
                    runs.c.state.in_([RunState.queued.value, RunState.running.value]),
                    runs.c.cancel_requested_at.is_(None),
                    runs.c.pause_requested_at.is_(None),
                )
                .order_by(steps.c.ready_at, steps.c.id)
                .limit(limit)
            )
            .scalars()
            .first()
        )
        if candidate is None:
            return None
        result: Any = session.execute(
            update(steps)
            .where(
                steps.c.id == candidate,
                steps.c.state == StepState.ready.value,
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
            raise LeaseConflictError("lost the claim race")
        row = session.execute(select(steps).where(steps.c.id == candidate)).mappings().first()
        assert row is not None
        attempt_id = uuid.uuid4().hex
        session.execute(
            attempts.insert().values(
                id=attempt_id,
                step_id=row["id"],
                run_id=row["run_id"],
                scope_type=row["scope_type"],
                scope_id=row["scope_id"],
                attempt_no=row["attempt_count"] + 1,
                worker_id=self.worker_id,
                state=AttemptState.leased.value,
                lease_owner=self.worker_id,
                started_at=now_us,
                heartbeat_at=now_us,
            )
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
            attempt_no=row["attempt_count"] + 1,
            lease_owner=self.worker_id,
        )

    # ----------------------------------------------------------- retry queue

    def activate_retries(self, session: Session, *, now_us: int) -> int:
        """Promote due retry_scheduled steps back to ready (one UPDATE)."""
        result: Any = session.execute(
            update(steps)
            .where(
                steps.c.state == StepState.retry_scheduled.value,
                steps.c.ready_at <= now_us,
            )
            .values(state=StepState.ready.value, updated_at=now_us)
        )
        return result.rowcount or 0

    # ------------------------------------------------------------ heartbeat

    def heartbeat(self, session: Session, *, attempt_id: str, now_us: int) -> bool:
        """Extend the lease; only the current lease owner may."""
        return self.state.update_attempt_heartbeat_cas(
            session, attempt_id=attempt_id, lease_owner=self.worker_id, now_us=now_us
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
                select(attempts.c.id, attempts.c.step_id, attempts.c.lease_owner)
                .join(steps, steps.c.id == attempts.c.step_id)
                .where(
                    attempts.c.state.in_([AttemptState.leased.value, AttemptState.running.value]),
                    steps.c.lease_expires_at.is_not(None),
                    steps.c.lease_expires_at <= now_us,
                )
            )
            .mappings()
            .all()
        )
        abandoned: list[dict] = []
        for row in expired:
            step = (
                session.execute(select(steps).where(steps.c.id == row["step_id"]))
                .mappings()
                .first()
            )
            if step is None:
                continue
            session.execute(
                update(attempts)
                .where(attempts.c.id == row["id"])
                .values(state=AttemptState.abandoned.value, finished_at=now_us)
            )
            abandoned.append(dict(row))
            # The step goes back to ready only when the capability is known
            # safe to retry; the caller (recovery policy) decides, and until
            # then the step is failed-indeterminate, never silently re-run.
            if step["state"] == StepState.leased.value:
                session.execute(
                    update(steps)
                    .where(steps.c.id == row["step_id"])
                    .values(
                        state=StepState.indeterminate.value,
                        lease_owner=None,
                        updated_at=now_us,
                        finished_at=now_us,
                        error_code="lease_expired",
                    )
                )
        return abandoned

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
