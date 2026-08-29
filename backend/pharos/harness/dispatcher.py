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

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import exists, select, update
from sqlalchemy.orm import Session

from pharos.harness.configrev import (
    HarnessConfigSnapshot,
    emergency_stop_active,
    validate_snapshot,
)
from pharos.harness.contracts import AttemptState, RunState, ScopeType, StepState
from pharos.harness.definitions import sha256_hex
from pharos.harness.repository import HarnessConfigService, HarnessRunRepository, Scope
from pharos.harness.state import HarnessStateService
from pharos.harness.tables import attempts, config_head, config_revisions, runs, steps

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
        config_service: HarnessConfigService | None = None,
    ) -> None:
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:12]}"
        if heartbeat_seconds >= lease_seconds / 3:
            raise ValueError("heartbeat interval must be under a third of the lease")
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.claim_batch = claim_batch
        self.state = state_service or HarnessStateService()
        self.config_service = config_service

    # ------------------------------------------------------------- claiming

    def _execution_fence(self, session: Session) -> tuple[str, HarnessConfigSnapshot] | None:
        """Load the current config head used to fence a claim.

        The head is deliberately read and validated here, at claim time. A
        queued run is not permission to execute forever: an operator can
        disable the dispatcher or move a workflow back to its legacy writer
        after that run was created. Malformed or inconsistent config is a
        closed gate, never a reason to guess.
        """
        if emergency_stop_active() or self.config_service is None:
            return None
        head = (
            session.execute(
                select(config_head.c.current_revision_id).where(
                    config_head.c.head_key == "singleton"
                )
            )
            .scalar_one_or_none()
        )
        if not head:
            return None
        revision = (
            session.execute(
                select(config_revisions.c.snapshot_json, config_revisions.c.snapshot_sha256).where(
                    config_revisions.c.id == head
                )
            )
            .mappings()
            .first()
        )
        if revision is None:
            return None
        raw = revision["snapshot_json"]
        try:
            if sha256_hex(json.loads(raw)) != revision["snapshot_sha256"]:
                return None
            snapshot = HarnessConfigSnapshot.model_validate(json.loads(raw))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if validate_snapshot(snapshot, self.config_service.registry):
            return None
        if not snapshot.gates.get("harness_enabled") or not snapshot.gates.get(
            "dispatcher_enabled"
        ):
            return None
        return head, snapshot

    @staticmethod
    def _route_allows_claim(
        snapshot: HarnessConfigSnapshot,
        *,
        workflow_key: str,
        workflow_version: int,
        step_kind: str,
    ) -> bool:
        route = next((item for item in snapshot.routes if item.workflow_key == workflow_key), None)
        if route is None or route.activation_state.value != "active":
            return False
        if route.active_version != workflow_version:
            return False
        # The internal canary has no legacy writer and therefore intentionally
        # uses execution_mode=NULL. Every business workflow must explicitly be
        # in Harness mode before a queued step can cross this fence.
        if workflow_key == "harness.canary":
            if not snapshot.gates.get("canary_enabled") or route.execution_mode is not None:
                return False
        elif route.execution_mode is None or route.execution_mode.value != "harness":
            return False
        return not (
            step_kind in ("agent", "mapped_agent")
            and not snapshot.gates.get("agent_steps_enabled")
        )

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
        fence = self._execution_fence(session)
        if fence is None:
            return None
        revision_id, snapshot = fence
        # Claims only steps whose run is active and has no pause/cancel request:
        # the fence is read in the same short transaction as the claim, so a
        # control request committed just before cannot be crossed by a stale
        # worker.
        candidates = (
            session.execute(
                select(
                    steps.c.id,
                    runs.c.workflow_key,
                    runs.c.workflow_version,
                    steps.c.step_kind,
                )
                .join(runs, runs.c.id == steps.c.run_id)
                .where(
                    steps.c.state == StepState.ready.value,
                    steps.c.ready_at <= now_us,
                    runs.c.state.in_([RunState.queued.value, RunState.running.value]),
                    runs.c.cancel_requested_at.is_(None),
                    runs.c.pause_requested_at.is_(None),
                )
                .order_by(steps.c.ready_at, steps.c.id)
                .limit(max(limit, self.claim_batch))
            )
            .mappings()
            .all()
        )
        candidate = next(
            (
                row
                for row in candidates
                if self._route_allows_claim(
                    snapshot,
                    workflow_key=row["workflow_key"],
                    workflow_version=row["workflow_version"],
                    step_kind=row["step_kind"],
                )
            ),
            None,
        )
        if candidate is None:
            return None
        result: Any = session.execute(
            update(steps)
            .where(
                steps.c.id == candidate["id"],
                steps.c.state == StepState.ready.value,
                # Re-check the head in the CAS itself. If an operator
                # committed a new revision after the fence was read, this
                # UPDATE loses cleanly instead of claiming under stale policy.
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
            # A competing worker or a config cutover won the CAS. This is a
            # normal queue outcome, not an application error. Roll back the
            # short transaction so the caller cannot accidentally commit any
            # speculative work before retrying.
            session.rollback()
            return None
        row = (
            session.execute(select(steps).where(steps.c.id == candidate["id"]))
            .mappings()
            .first()
        )
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
        """Promote due retries through the state service and event log."""
        rows = (
            session.execute(
                select(steps.c.id)
                .where(
                    steps.c.state == StepState.retry_scheduled.value,
                    steps.c.ready_at <= now_us,
                )
                .order_by(steps.c.ready_at, steps.c.id)
            )
            .scalars()
            .all()
        )
        for step_id in rows:
            self.state.transition_step(
                session,
                step_id=step_id,
                target=StepState.ready,
                now_us=now_us,
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=None,
                payload={"reason": "retry_due"},
            )
        return len(rows)

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
            step = (
                session.execute(select(steps).where(steps.c.id == row["step_id"]))
                .mappings()
                .first()
            )
            if step is None:
                continue
            self.state.transition_attempt(
                session,
                attempt_id=row["id"],
                target=AttemptState.abandoned,
                now_us=now_us,
                payload={"reason": "lease_expired"},
            )
            abandoned.append(dict(row))
            # The step goes back to ready only when the capability is known
            # safe to retry; the caller (recovery policy) decides, and until
            # then the step is failed-indeterminate, never silently re-run.
            if step["state"] in (StepState.leased.value, StepState.running.value):
                self.state.transition_step(
                    session,
                    step_id=row["step_id"],
                    target=StepState.indeterminate,
                    now_us=now_us,
                    lease_owner=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                    error_code="lease_expired",
                    payload={"reason": "lease_expired"},
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
