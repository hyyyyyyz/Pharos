"""Persistent, least-scope approvals.

An approval grant is bound to a canonical action + resource + request hash +
version and always carries an expiry. A grant can be consumed exactly once,
by the same step, for the same request hash; a changed payload or a different
tool cannot borrow it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import exists, select, update
from sqlalchemy.orm import Session

from pharos.harness.contracts import (
    RUN_TERMINAL_STATES,
    ApprovalConflictError,
    ApprovalState,
    AttemptState,
    NotFoundError,
    StepState,
)
from pharos.harness.definitions import sha256_hex
from pharos.harness.repository import Scope, json_dump, new_id
from pharos.harness.tables import approvals, attempts, runs, steps

DEFAULT_EXPIRY_SECONDS = 7 * 24 * 60 * 60


class ApprovalRepository:
    def request(
        self,
        session: Session,
        *,
        scope: Scope,
        run_id: str,
        action: str,
        resource: dict,
        request: dict,
        effect_summary: dict,
        now_us: int,
        expires_at_us: int,
        step_id: str | None = None,
        requesting_attempt_id: str | None = None,
        risk: str = "write_private",
    ) -> dict:
        request_hash = sha256_hex({"action": action, "resource": resource, "request": request})
        approval_id = new_id()
        session.execute(
            approvals.insert().values(
                id=approval_id,
                run_id=run_id,
                scope_type=scope.scope_type.value,
                scope_id=scope.scope_id,
                step_id=step_id,
                requesting_attempt_id=requesting_attempt_id,
                action=action,
                resource_json=json_dump(resource),
                risk=risk,
                effect_summary_json=json_dump(effect_summary),
                request_hash=request_hash,
                state=ApprovalState.pending.value,
                request_json=json_dump(request),
                requested_at=now_us,
                expires_at=expires_at_us,
            )
        )
        row = self.get(session, scope=scope, approval_id=approval_id)
        assert row is not None
        return row

    def get(self, session: Session, *, scope: Scope, approval_id: str) -> dict | None:
        row = (
            session.execute(
                select(approvals).where(scope.where(approvals), approvals.c.id == approval_id)
            )
            .mappings()
            .first()
        )
        return dict(row) if row is not None else None

    def require(self, session: Session, *, scope: Scope, approval_id: str) -> dict:
        row = self.get(session, scope=scope, approval_id=approval_id)
        if row is None:
            raise NotFoundError("approval not found")
        return row

    def pending_for_run(self, session: Session, *, scope: Scope, run_id: str) -> list[dict]:
        rows = session.execute(
            select(approvals).where(
                scope.where(approvals),
                approvals.c.run_id == run_id,
                approvals.c.state == ApprovalState.pending.value,
            )
        ).mappings()
        return [dict(row) for row in rows]

    def approved_for_step(self, session: Session, *, scope: Scope, step_id: str) -> list[dict]:
        """Unconsumed approved grants for a step, newest first."""
        rows = session.execute(
            select(approvals)
            .where(
                scope.where(approvals),
                approvals.c.step_id == step_id,
                approvals.c.state == ApprovalState.approved.value,
                approvals.c.consumed_by_attempt_id.is_(None),
            )
            .order_by(approvals.c.requested_at.desc())
        ).mappings()
        return [dict(row) for row in rows]

    def decide(
        self,
        session: Session,
        *,
        scope: Scope,
        approval_id: str,
        decision: ApprovalState,
        resolver_user_id: str,
        reason: str,
        now_us: int,
    ) -> dict:
        """Resolve one still-live approval exactly once.

        ``expires_at`` is an exclusive boundary throughout the Harness.  The
        decision is a conditional CAS, while the separate expiry sweep uses
        the complementary ``<=`` boundary. A competing resolver, expiry
        sweep, or Run cancellation can therefore never be overwritten by a
        stale preflight read.
        """
        if decision not in (ApprovalState.approved, ApprovalState.rejected):
            raise ApprovalConflictError("decision must be approve or reject")
        terminal_values = tuple(state.value for state in RUN_TERMINAL_STATES)
        live_run = exists(
            select(1)
            .select_from(runs)
            .where(
                runs.c.id == approvals.c.run_id,
                runs.c.scope_type == approvals.c.scope_type,
                runs.c.scope_id == approvals.c.scope_id,
                runs.c.state.not_in(terminal_values),
                runs.c.cancel_requested_at.is_(None),
            )
            .correlate(approvals)
        )
        decided: Any = session.execute(
            update(approvals)
            .where(
                scope.where(approvals),
                approvals.c.id == approval_id,
                approvals.c.state == ApprovalState.pending.value,
                approvals.c.expires_at > now_us,
                live_run,
            )
            .values(
                state=decision.value,
                resolver_user_id=resolver_user_id,
                resolver_reason=reason,
                resolved_at=now_us,
            )
        )
        if decided.rowcount != 1:
            row = self.require(session, scope=scope, approval_id=approval_id)
            if row["state"] != ApprovalState.pending.value:
                raise ApprovalConflictError(f"approval is {row['state']}, not pending")
            if row["expires_at"] <= now_us:
                # The periodic expiry CAS persists ``expired``.  Raising here
                # deliberately performs no write because a caller's normal
                # transaction scope rolls back on the conflict exception.
                raise ApprovalConflictError("approval has expired")
            raise ApprovalConflictError("approval's Run no longer accepts decisions")
        updated = self.require(session, scope=scope, approval_id=approval_id)
        return updated

    def consume_for_attempt(
        self,
        session: Session,
        *,
        scope: Scope,
        approval_id: str,
        run_id: str,
        step_id: str,
        attempt_id: str,
        attempt_no: int,
        lease_owner: str,
        request_hash: str,
        now_us: int,
    ) -> None:
        """Consume a grant only for the exact current running generation.

        Approval consumption is a side effect of execution, so an approved
        grant must not be borrowable by a stale claim, another owner, a
        cancelled Run, or a successor Attempt.  Every identity and state
        check belongs to this one conditional UPDATE; callers never act on a
        preflight SELECT result.
        """
        terminal_values = tuple(state.value for state in RUN_TERMINAL_STATES)
        current_attempt = exists(
            select(1)
            .select_from(attempts)
            .where(
                attempts.c.id == attempt_id,
                attempts.c.run_id == run_id,
                attempts.c.step_id == step_id,
                attempts.c.scope_type == scope.scope_type.value,
                attempts.c.scope_id == scope.scope_id,
                attempts.c.attempt_no == attempt_no,
                attempts.c.lease_owner == lease_owner,
                attempts.c.state == AttemptState.running.value,
                exists(
                    select(1)
                    .select_from(steps)
                    .where(
                        steps.c.id == step_id,
                        steps.c.run_id == run_id,
                        steps.c.scope_type == scope.scope_type.value,
                        steps.c.scope_id == scope.scope_id,
                        steps.c.attempt_count == attempt_no,
                        steps.c.state == StepState.running.value,
                        steps.c.lease_owner == lease_owner,
                    )
                ),
            )
        )
        live_run = exists(
            select(1)
            .select_from(runs)
            .where(
                runs.c.id == run_id,
                runs.c.scope_type == scope.scope_type.value,
                runs.c.scope_id == scope.scope_id,
                runs.c.state.not_in(terminal_values),
                runs.c.cancel_requested_at.is_(None),
            )
        )
        result: Any = session.execute(
            update(approvals)
            .where(
                scope.where(approvals),
                approvals.c.id == approval_id,
                approvals.c.run_id == run_id,
                approvals.c.step_id == step_id,
                approvals.c.request_hash == request_hash,
                approvals.c.state == ApprovalState.approved.value,
                approvals.c.consumed_by_attempt_id.is_(None),
                approvals.c.expires_at > now_us,
                current_attempt,
                live_run,
            )
            .values(consumed_by_attempt_id=attempt_id, resolved_at=now_us)
        )
        if result.rowcount != 1:
            raise ApprovalConflictError(
                "grant does not match the current running Attempt or was already consumed"
            )

    def cancel_unconsumed_for_run(
        self,
        session: Session,
        *,
        scope: Scope,
        run_id: str,
        now_us: int,
    ) -> int:
        """Cancel every unspent grant after the same scoped Run requested cancel."""
        cancelled_run = exists(
            select(1)
            .select_from(runs)
            .where(
                runs.c.id == run_id,
                runs.c.scope_type == scope.scope_type.value,
                runs.c.scope_id == scope.scope_id,
                runs.c.cancel_requested_at.is_not(None),
            )
        )
        result: Any = session.execute(
            update(approvals)
            .where(
                scope.where(approvals),
                approvals.c.run_id == run_id,
                approvals.c.state.in_((ApprovalState.pending.value, ApprovalState.approved.value)),
                approvals.c.consumed_by_attempt_id.is_(None),
                cancelled_run,
            )
            .values(state=ApprovalState.cancelled.value, resolved_at=now_us)
        )
        return result.rowcount

    def expire_outstanding(self, session: Session, *, now_us: int) -> int:
        result: Any = session.execute(
            update(approvals)
            .where(
                approvals.c.state.in_((ApprovalState.pending.value, ApprovalState.approved.value)),
                approvals.c.consumed_by_attempt_id.is_(None),
                approvals.c.expires_at <= now_us,
            )
            .values(state=ApprovalState.expired.value, resolved_at=now_us)
        )
        return result.rowcount
