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

from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.orm import Session

from pharos.harness.configrev import (
    HarnessConfigSnapshot,
    emergency_stop_active,
)
from pharos.harness.contracts import (
    AttemptState,
    ConfigIntegrityError,
    RunState,
    ScopeType,
    StepState,
)
from pharos.harness.repository import HarnessConfigService, HarnessRunRepository, Scope
from pharos.harness.state import HarnessStateService
from pharos.harness.tables import attempts, config_head, runs, steps

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
    # These are resolved from the trusted registry and persisted config fence,
    # never copied from run input or accepted as worker-provided metadata.
    role: str | None = None
    runtime_kind: str | None = None
    config_revision_id: str | None = None
    snapshot_sha256: str | None = None
    role_definition_sha256: str | None = None


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
        if step_kind in ("agent", "mapped_agent"):
            if not snapshot.gates.get("agent_steps_enabled"):
                return False
            # The legacy in-process fake is deliberately independent of the
            # H1.5 DSH gate. Only a trusted DSH role crosses that extra fence.
            if runtime_kind == "dsh" and not snapshot.gates.get("agent_runtime_enabled"):
                return False
        return True

    def _trusted_step_runtime(
        self, *, workflow_key: str, workflow_version: int, row: Any
    ) -> tuple[str | None, str | None, str | None] | None:
        """Resolve role/runtime from the immutable registry, fail closed.

        Step rows contain a frozen expander payload, but that payload is still
        database data. The registry definition is the authority for the role
        and runtime route; the row is accepted only when its security-relevant
        fields agree with that definition.
        """
        try:
            registry = self.config_service.registry if self.config_service else None
            if registry is None:
                return None
            workflow = registry.workflow(f"{workflow_key}@{workflow_version}")
            if workflow is None:
                return None
            if row["definition_sha256"] != workflow.definition_hash():
                return None
            trusted_step = workflow.step(row["definition_step_key"])
            if row["step_kind"] != trusted_step.kind:
                return None
            definition = json.loads(row["definition_json"])
            if not isinstance(definition, dict) or definition.get("kind") != trusted_step.kind:
                return None
            expected = trusted_step.model_dump(mode="json")
            if set(expected) - set(definition):
                return None
            dynamic_keys = set(definition) - set(expected)
            if trusted_step.kind in ("mapped", "mapped_agent"):
                if dynamic_keys not in (set(), {"expand_items"}):
                    return None
                if "expand_items" in definition:
                    items = definition["expand_items"]
                    if (
                        not isinstance(items, list)
                        or trusted_step.max_fanout is None
                        or len(items) > trusted_step.max_fanout
                    ):
                        return None
            elif dynamic_keys:
                return None
            if any(definition.get(key) != value for key, value in expected.items()):
                return None

            # The step table duplicates selected definition fields for query
            # and state-machine efficiency. Verify those copies too; otherwise
            # a tampered row could alter dependencies, retry, or deadlines
            # after the JSON contract check above.
            if json.loads(row["depends_on_json"] or "[]") != list(trusted_step.depends_on):
                return None
            if row["fan_in"] != trusted_step.fan_in:
                return None
            if row["min_success_count"] != trusted_step.min_success_count:
                return None
            expected_attempts = trusted_step.retry.max_attempts if trusted_step.retry else 3
            if row["max_attempts"] != expected_attempts:
                return None
            if trusted_step.timeout_seconds is None:
                if row["timeout_seconds"] is not None:
                    return None
            elif (
                row["timeout_seconds"] is None
                or float(row["timeout_seconds"]) != trusted_step.timeout_seconds
            ):
                return None
            actual_retry = (
                json.loads(row["retry_policy_json"])
                if row["retry_policy_json"]
                else None
            )
            expected_retry = (
                trusted_step.retry.model_dump(mode="json") if trusted_step.retry else None
            )
            if actual_retry != expected_retry:
                return None

            if trusted_step.kind in ("agent", "mapped_agent"):
                role = trusted_step.role
                if not isinstance(role, str) or definition.get("role") != role:
                    return None
                role_definition = registry.role(role)
                if role_definition is None:
                    return None
                runtime_kind = role_definition.runtime_kind
                if runtime_kind not in ("in_process_fake", "dsh"):
                    return None
                return role, runtime_kind, role_definition.definition_hash()
            if definition.get("capability") != trusted_step.capability:
                return None
            return None, None, None
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

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
                runs.c.workflow_key,
                runs.c.workflow_version,
                runs.c.definition_sha256,
                steps.c.ready_at,
                steps.c.step_kind,
                steps.c.definition_step_key,
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
        cursor: tuple[int, str] | None = None
        resolved: tuple[Any, tuple[str | None, str | None, str | None]] | None = None
        while resolved is None:
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
                break
            for row in page:
                metadata = self._trusted_step_runtime(
                    workflow_key=row["workflow_key"],
                    workflow_version=row["workflow_version"],
                    row=row,
                )
                if metadata is None:
                    continue
                if self._route_allows_claim(
                    snapshot,
                    workflow_key=row["workflow_key"],
                    workflow_version=row["workflow_version"],
                    step_kind=row["step_kind"],
                    runtime_kind=metadata[1],
                ):
                    resolved = row, metadata
                    break
            if resolved is not None or len(page) < page_size:
                break
            cursor = page[-1]["ready_at"], page[-1]["id"]
        if resolved is None:
            return None
        candidate, (role, runtime_kind, role_definition_sha256) = resolved
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
        claimed_row = (
            session.execute(select(steps).where(steps.c.id == candidate["id"]))
            .mappings()
            .first()
        )
        assert claimed_row is not None
        row = claimed_row
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
            role=role,
            runtime_kind=runtime_kind,
            config_revision_id=revision_id,
            snapshot_sha256=snapshot_sha256,
            role_definition_sha256=role_definition_sha256,
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
        activated = 0
        for step_id in rows:
            # Multiple dispatchers can observe the same due row.  The state
            # service owns the conditional UPDATE; only its winner emits the
            # step.ready event.
            if self.state.activate_retry_cas(session, step_id=step_id, now_us=now_us):
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
            if self.state.abandon_expired_attempt_cas(
                session,
                attempt_id=row["id"],
                step_id=row["step_id"],
                lease_owner=row["lease_owner"],
                now_us=now_us,
            ):
                abandoned.append(dict(row))
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
