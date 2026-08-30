"""Append-only usage accounting: reserve / settle / release.

Every reservation is a ledger row; settling or releasing updates nothing --
it appends a second row bound to the same reservation id. Conservation is
reconstructable: the sum of settle rows is the committed usage, and every
settle/release refers to exactly one reserve. A reservation can be settled or
released exactly once, enforced by scanning the reservation's ops in the same
short transaction as the append (SQLite serialises writers; a concurrent
settle of the same reservation loses the race and raises).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from pharos.harness.contracts import NotFoundError
from pharos.harness.repository import Scope, new_id
from pharos.harness.tables import usage_events


class LedgerConflict(NotFoundError):
    pass


class UsageLedger:
    def reserve(
        self,
        session: Session,
        *,
        scope: Scope,
        run_id: str,
        kind: str,
        source: str,
        amount: int,
        cost_micros: int,
        now_us: int,
        step_id: str | None = None,
        attempt_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> str:
        reservation_id = new_id()
        session.execute(
            usage_events.insert().values(
                id=new_id(),
                run_id=run_id,
                step_id=step_id,
                attempt_id=attempt_id,
                scope_type=scope.scope_type.value,
                scope_id=scope.scope_id,
                source=source,
                kind=kind,
                reservation_id=reservation_id,
                op="reserve",
                amount=amount,
                cost_micros=cost_micros,
                provider=provider,
                model=model,
                created_at=now_us,
            )
        )
        return reservation_id

    def settle(self, session: Session, *, reservation_id: str, actual: int, now_us: int) -> None:
        reservation = self._require_reservation(session, reservation_id)
        self._require_unspent(session, reservation_id)
        session.execute(
            usage_events.insert().values(
                id=new_id(),
                run_id=reservation["run_id"],
                step_id=reservation["step_id"],
                attempt_id=reservation["attempt_id"],
                scope_type=reservation["scope_type"],
                scope_id=reservation["scope_id"],
                source=reservation["source"],
                kind=reservation["kind"],
                reservation_id=reservation_id,
                op="settle",
                amount=actual,
                created_at=now_us,
            )
        )

    def release(self, session: Session, *, reservation_id: str, now_us: int) -> None:
        reservation = self._require_reservation(session, reservation_id)
        self._require_unspent(session, reservation_id)
        session.execute(
            usage_events.insert().values(
                id=new_id(),
                run_id=reservation["run_id"],
                step_id=reservation["step_id"],
                attempt_id=reservation["attempt_id"],
                scope_type=reservation["scope_type"],
                scope_id=reservation["scope_id"],
                source=reservation["source"],
                kind=reservation["kind"],
                reservation_id=reservation_id,
                op="release",
                amount=0,
                created_at=now_us,
            )
        )

    def totals(self, session: Session, *, run_id: str) -> dict[str, int]:
        rows = session.execute(
            select(usage_events).where(usage_events.c.run_id == run_id)
        ).mappings()
        reserved_amount = settled_amount = 0
        reserved_count = settled_count = released_count = 0
        for row in rows:
            if row["op"] == "reserve":
                reserved_amount += row["amount"]
                reserved_count += 1
            elif row["op"] == "settle":
                settled_amount += row["amount"]
                settled_count += 1
            else:
                released_count += 1
        return {
            "reserved": reserved_amount,
            "settled": settled_amount,
            "reserved_reservations": reserved_count,
            "settled_reservations": settled_count,
            "released_reservations": released_count,
            # An unresolved reservation is intentional when delivery may have
            # crossed the provider boundary but no trusted usage receipt is
            # available yet. Reconciliation, never a guessed zero, closes it.
            "pending_reservations": (reserved_count - settled_count - released_count),
        }

    def _require_reservation(self, session: Session, reservation_id: str) -> dict:
        row = (
            session.execute(
                select(usage_events).where(
                    usage_events.c.reservation_id == reservation_id,
                    usage_events.c.op == "reserve",
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise LedgerConflict(f"unknown reservation {reservation_id}")
        return dict(row)

    def _require_unspent(self, session: Session, reservation_id: str) -> None:
        spent = (
            session.execute(
                select(usage_events.c.op).where(
                    usage_events.c.reservation_id == reservation_id,
                    usage_events.c.op.in_(["settle", "release"]),
                )
            )
            .scalars()
            .all()
        )
        if spent:
            raise LedgerConflict(f"reservation {reservation_id} already {spent[0]}(ed)")
