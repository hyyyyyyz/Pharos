"""Persistent, least-scope approvals.

An approval grant is bound to a canonical action + resource + request hash +
version and always carries an expiry. A grant can be consumed exactly once,
by the same step, for the same request hash; a changed payload or a different
tool cannot borrow it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from pharos.harness.contracts import (
    ApprovalConflictError,
    ApprovalState,
    NotFoundError,
)
from pharos.harness.definitions import sha256_hex
from pharos.harness.repository import Scope, json_dump, new_id
from pharos.harness.tables import approvals

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
        if decision not in (ApprovalState.approved, ApprovalState.rejected):
            raise ApprovalConflictError("decision must be approve or reject")
        row = self.require(session, scope=scope, approval_id=approval_id)
        if row["state"] != ApprovalState.pending.value:
            raise ApprovalConflictError(f"approval is {row['state']}, not pending")
        if now_us > row["expires_at"]:
            session.execute(
                update(approvals)
                .where(approvals.c.id == approval_id)
                .values(state=ApprovalState.expired.value, resolved_at=now_us)
            )
            raise ApprovalConflictError("approval has expired")
        session.execute(
            update(approvals)
            .where(approvals.c.id == approval_id)
            .values(
                state=decision.value,
                resolver_user_id=resolver_user_id,
                resolver_reason=reason,
                resolved_at=now_us,
            )
        )
        updated = self.require(session, scope=scope, approval_id=approval_id)
        return updated

    def consume(
        self,
        session: Session,
        *,
        scope: Scope,
        approval_id: str,
        step_id: str,
        request_hash: str,
        consuming_attempt_id: str,
        now_us: int,
    ) -> None:
        """Atomically spend an approved grant exactly once."""
        result: Any = session.execute(
            update(approvals)
            .where(
                scope.where(approvals),
                approvals.c.id == approval_id,
                approvals.c.step_id == step_id,
                approvals.c.request_hash == request_hash,
                approvals.c.state == ApprovalState.approved.value,
                approvals.c.consumed_by_attempt_id.is_(None),
            )
            .values(consumed_by_attempt_id=consuming_attempt_id, resolved_at=now_us)
        )
        if result.rowcount != 1:
            raise ApprovalConflictError("grant does not match this action or was already consumed")

    def expire_outstanding(self, session: Session, *, now_us: int) -> int:
        result: Any = session.execute(
            update(approvals)
            .where(
                approvals.c.state == ApprovalState.pending.value,
                approvals.c.expires_at <= now_us,
            )
            .values(state=ApprovalState.expired.value, resolved_at=now_us)
        )
        return result.rowcount
