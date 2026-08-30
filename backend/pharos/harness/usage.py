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

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from pharos.harness.contracts import NotFoundError
from pharos.harness.repository import Scope, new_id
from pharos.harness.tables import attempts, runs, steps, usage_events


class LedgerConflict(NotFoundError):
    pass


@dataclass(frozen=True, slots=True)
class UsageBudgetStatus:
    """Committed and conservatively reserved usage for one Run/kind.

    ``kind=model_tokens`` predates multidimensional model accounting and has
    only one amount column.  The ledger therefore stores input as a paired
    ``reservation_id + ':input'`` row; the base row carries output in
    ``amount`` and cost in ``cost_micros``.  The fields below expose all three
    dimensions while retaining ``committed``/``pending`` as output aliases
    for callers written against the original one-dimensional API.
    """

    committed: int
    pending: int
    consumed_calls: int
    input_committed: int = 0
    input_pending: int = 0
    cost_committed: int = 0
    cost_pending: int = 0

    @property
    def output_committed(self) -> int:
        return self.committed

    @property
    def output_pending(self) -> int:
        return self.pending

    @property
    def committed_output_tokens(self) -> int:
        return self.committed

    @property
    def pending_output_tokens(self) -> int:
        return self.pending

    @property
    def committed_input(self) -> int:
        return self.input_committed

    @property
    def pending_input(self) -> int:
        return self.input_pending

    @property
    def committed_input_tokens(self) -> int:
        return self.input_committed

    @property
    def pending_input_tokens(self) -> int:
        return self.input_pending

    @property
    def committed_cost_micros(self) -> int:
        return self.cost_committed

    @property
    def pending_cost_micros(self) -> int:
        return self.cost_pending


class UsageLedger:
    _INPUT_SUFFIX = ":input"

    def budget_status(
        self,
        session: Session,
        *,
        run_id: str,
        kind: str,
        scope: Scope | None = None,
    ) -> UsageBudgetStatus:
        """Return settled usage plus open reservation maxima.

        Callers invoke this after their parent Run/Attempt write fence in the
        same transaction, so SQLite serializes concurrent admission decisions.
        Released (definitely unsent) reservations do not consume a model call;
        settled and unresolved reservations do.

        ``run_id`` is a trusted internal aggregate key when ``scope`` is omitted
        for backwards-compatible accounting/reporting callers. Owner-facing
        callers must pass ``scope`` so rows from another owner cannot be mixed.
        """

        predicates = [usage_events.c.run_id == run_id, usage_events.c.kind == kind]
        if scope is not None:
            predicates.extend(
                [
                    usage_events.c.scope_type == scope.scope_type.value,
                    usage_events.c.scope_id == scope.scope_id,
                ]
            )
        rows = (
            session.execute(
                select(usage_events).where(*predicates)
            )
            .mappings()
            .all()
        )
        # ``reserves`` is keyed by the logical (output/base) reservation id.
        # The input companion is deliberately separate at storage level but
        # must never count as a second model call.
        reserves: dict[str, dict[str, Any]] = {}
        outcomes: dict[str, tuple[str, int, int]] = {}
        for row in rows:
            reservation_id = row["reservation_id"]
            if not isinstance(reservation_id, str) or not reservation_id:
                raise LedgerConflict("usage row has no reservation identity")
            op = row["op"]
            amount = row["amount"]
            if type(amount) is not int or amount < 0:
                raise LedgerConflict("usage row has an invalid amount")
            cost = row["cost_micros"]
            if type(cost) is not int or cost < 0:
                raise LedgerConflict("usage row has an invalid cost")
            if op == "reserve":
                logical_id = self._logical_reservation_id(reservation_id)
                dimension = "input" if logical_id != reservation_id else "output"
                current = reserves.setdefault(logical_id, {})
                if dimension in current:
                    raise LedgerConflict("usage reservation is duplicated")
                current[dimension] = (amount, cost)
            elif op in {"settle", "release"}:
                logical_id = self._logical_reservation_id(reservation_id)
                dimension = "input" if logical_id != reservation_id else "output"
                key = f"{logical_id}:{dimension}"
                if key in outcomes:
                    raise LedgerConflict("usage reservation has multiple outcomes")
                outcomes[key] = (op, amount, cost)
            else:
                raise LedgerConflict("usage row has an unknown operation")
        for key in outcomes:
            logical_id = key.rsplit(":", 1)[0]
            dimension = key.rsplit(":", 1)[1]
            if logical_id not in reserves or dimension not in reserves[logical_id]:
                raise LedgerConflict("usage outcome has no reservation")
        committed = pending = input_committed = input_pending = 0
        cost_committed = cost_pending = 0
        for logical_id, dimensions in reserves.items():
            for dimension, (amount, reserved_cost) in dimensions.items():
                outcome = outcomes.get(f"{logical_id}:{dimension}")
                if outcome is None:
                    if dimension == "input":
                        input_pending += amount
                    else:
                        pending += amount
                    cost_pending += reserved_cost
                elif outcome[0] == "settle":
                    if dimension == "input":
                        input_committed += outcome[1]
                    else:
                        committed += outcome[1]
                    cost_committed += outcome[2]
        consumed_calls = sum(
            1
            for logical_id, dimensions in reserves.items()
            if any(
                f"{logical_id}:{dimension}" not in outcomes
                or outcomes[f"{logical_id}:{dimension}"][0] == "settle"
                for dimension in dimensions
            )
        )
        return UsageBudgetStatus(
            committed=committed,
            pending=pending,
            consumed_calls=consumed_calls,
            input_committed=input_committed,
            input_pending=input_pending,
            cost_committed=cost_committed,
            cost_pending=cost_pending,
        )

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
        input_tokens: int = 0,
        step_id: str | None = None,
        attempt_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> str:
        if type(amount) is not int or amount < 0:
            raise ValueError("reserved output amount must be a non-negative integer")
        if type(input_tokens) is not int or input_tokens < 0:
            raise ValueError("reserved input amount must be a non-negative integer")
        if type(cost_micros) is not int or cost_micros < 0:
            raise ValueError("reserved cost must be a non-negative integer")
        self._validate_owner(
            session,
            scope=scope,
            run_id=run_id,
            step_id=step_id,
            attempt_id=attempt_id,
        )
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
        if input_tokens:
            # Keep the input dimension in the existing schema.  The suffix is
            # private ledger framing, never exposed as a provider/model id.
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
                    reservation_id=reservation_id + self._INPUT_SUFFIX,
                    op="reserve",
                    amount=input_tokens,
                    cost_micros=0,
                    provider=provider,
                    model=model,
                    created_at=now_us,
                )
            )
        return reservation_id

    def settle(
        self,
        session: Session,
        *,
        reservation_id: str,
        actual: int,
        now_us: int,
        scope: Scope,
        run_id: str,
        step_id: str | None = None,
        attempt_id: str | None = None,
        actual_input: int | None = None,
        actual_cost_micros: int | None = None,
    ) -> None:
        """Commit a trusted result across output, input and cost dimensions.

        A paired input row is settled in the same SQL transaction as the
        output row.  If a caller has only a token count and no input receipt,
        a multidimensional reservation is intentionally not settleable: the
        correct state is pending reconciliation, never guessed zero input.
        """
        reservation = self._require_reservation(
            session,
            reservation_id,
            scope=scope,
            run_id=run_id,
            step_id=step_id,
            attempt_id=attempt_id,
        )
        input_reservation = self._paired_reservation(session, reservation_id)
        self._assert_paired_identity(reservation, input_reservation)
        if input_reservation is not None and actual_input is None:
            raise LedgerConflict("input usage is required to settle a multidimensional reservation")
        self._require_unspent(session, reservation_id)
        if input_reservation is not None:
            self._require_unspent(session, reservation_id + self._INPUT_SUFFIX)
        self._append_outcome(
            session,
            reservation=reservation,
            reservation_id=reservation_id,
            op="settle",
            amount=actual,
            cost_micros=actual_cost_micros if actual_cost_micros is not None else 0,
            now_us=now_us,
        )
        if input_reservation is not None:
            self._append_outcome(
                session,
                reservation=input_reservation,
                reservation_id=reservation_id + self._INPUT_SUFFIX,
                op="settle",
                amount=actual_input or 0,
                cost_micros=0,
                now_us=now_us,
            )

    def release(
        self,
        session: Session,
        *,
        reservation_id: str,
        now_us: int,
        scope: Scope,
        run_id: str,
        step_id: str | None = None,
        attempt_id: str | None = None,
    ) -> None:
        reservation = self._require_reservation(
            session,
            reservation_id,
            scope=scope,
            run_id=run_id,
            step_id=step_id,
            attempt_id=attempt_id,
        )
        input_reservation = self._paired_reservation(session, reservation_id)
        self._assert_paired_identity(reservation, input_reservation)
        self._require_unspent(session, reservation_id)
        if input_reservation is not None:
            self._require_unspent(session, reservation_id + self._INPUT_SUFFIX)
        self._append_outcome(
            session,
            reservation=reservation,
            reservation_id=reservation_id,
            op="release",
            amount=0,
            cost_micros=0,
            now_us=now_us,
        )
        if input_reservation is not None:
            self._append_outcome(
                session,
                reservation=input_reservation,
                reservation_id=reservation_id + self._INPUT_SUFFIX,
                op="release",
                amount=0,
                cost_micros=0,
                now_us=now_us,
            )

    def totals(
        self, session: Session, *, run_id: str, scope: Scope | None = None
    ) -> dict[str, int]:
        """Return ledger totals for one trusted Run aggregate.

        The optional scope preserves internal legacy reporting by ``run_id``;
        every owner-facing read should provide it to enforce owner isolation.
        """
        predicates = [usage_events.c.run_id == run_id]
        if scope is not None:
            predicates.extend(
                [
                    usage_events.c.scope_type == scope.scope_type.value,
                    usage_events.c.scope_id == scope.scope_id,
                ]
            )
        rows = session.execute(
            select(usage_events).where(*predicates)
        ).mappings()
        reserved_amount = settled_amount = reserved_input = settled_input = 0
        reserved_cost = settled_cost = 0
        reserved_count = settled_count = released_count = 0
        for row in rows:
            if row["op"] == "reserve":
                if self._is_input_reservation(row["reservation_id"]):
                    reserved_input += row["amount"]
                else:
                    reserved_amount += row["amount"]
                    reserved_cost += row["cost_micros"]
                    reserved_count += 1
            elif row["op"] == "settle":
                if self._is_input_reservation(row["reservation_id"]):
                    settled_input += row["amount"]
                else:
                    settled_amount += row["amount"]
                    settled_cost += row["cost_micros"]
                    settled_count += 1
            else:
                if not self._is_input_reservation(row["reservation_id"]):
                    released_count += 1
        model_status = self.budget_status(
            session, run_id=run_id, kind="model_tokens", scope=scope
        )
        return {
            "reserved": reserved_amount,
            "settled": settled_amount,
            "pending_output": model_status.pending,
            "reserved_input": reserved_input,
            "settled_input": settled_input,
            "pending_input": model_status.input_pending,
            "reserved_cost_micros": reserved_cost,
            "settled_cost_micros": settled_cost,
            "committed_input": model_status.input_committed,
            "committed_cost_micros": model_status.cost_committed,
            "pending_cost_micros": model_status.cost_pending,
            "reserved_reservations": reserved_count,
            "settled_reservations": settled_count,
            "released_reservations": released_count,
            # An unresolved reservation is intentional when delivery may have
            # crossed the provider boundary but no trusted usage receipt is
            # available yet. Reconciliation, never a guessed zero, closes it.
            "pending_reservations": (reserved_count - settled_count - released_count),
        }

    def _require_reservation(
        self,
        session: Session,
        reservation_id: str,
        *,
        scope: Scope | None = None,
        run_id: str | None = None,
        step_id: str | None = None,
        attempt_id: str | None = None,
    ) -> dict:
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
        result = dict(row)
        self._assert_identity(
            result,
            scope=scope,
            run_id=run_id,
            step_id=step_id,
            attempt_id=attempt_id,
        )
        return result

    @staticmethod
    def _assert_identity(
        row: dict,
        *,
        scope: Scope | None,
        run_id: str | None,
        step_id: str | None,
        attempt_id: str | None,
    ) -> None:
        expected = {
            "run_id": run_id,
            "step_id": step_id,
            "attempt_id": attempt_id,
        }
        if scope is not None:
            expected.update(
                scope_type=scope.scope_type.value,
                scope_id=scope.scope_id,
            )
        # Attempt-bound reservations must always be closed by the exact
        # Attempt generation context.  Omitting these fields is safe only for
        # legacy/run-level reservations that have no Attempt owner.
        if row.get("attempt_id") is not None and (
            attempt_id != row["attempt_id"] or step_id != row["step_id"]
        ):
            raise LedgerConflict("Attempt-bound usage requires its exact step and Attempt fence")
        for field, value in expected.items():
            if value is not None and row.get(field) != value:
                raise LedgerConflict(f"usage reservation {field} does not match owner fence")

    @staticmethod
    def _validate_owner(
        session: Session,
        *,
        scope: Scope,
        run_id: str,
        step_id: str | None,
        attempt_id: str | None,
    ) -> None:
        run = session.execute(
            select(runs.c.id).where(
                runs.c.id == run_id,
                scope.where(runs),
            )
        ).scalar_one_or_none()
        if run is None:
            raise LedgerConflict("usage run does not belong to owner scope")
        if step_id is not None:
            step = session.execute(
                select(steps.c.id).where(
                    steps.c.id == step_id,
                    steps.c.run_id == run_id,
                    scope.where(steps),
                )
            ).scalar_one_or_none()
            if step is None:
                raise LedgerConflict("usage step does not belong to owner scope")
        if attempt_id is not None:
            attempt = session.execute(
                select(attempts.c.id).where(
                    attempts.c.id == attempt_id,
                    attempts.c.run_id == run_id,
                    attempts.c.step_id == step_id,
                    scope.where(attempts),
                )
            ).scalar_one_or_none()
            if attempt is None:
                raise LedgerConflict("usage Attempt does not belong to owner scope")

    @classmethod
    def _is_input_reservation(cls, reservation_id: object) -> bool:
        return isinstance(reservation_id, str) and reservation_id.endswith(cls._INPUT_SUFFIX)

    @classmethod
    def _logical_reservation_id(cls, reservation_id: str) -> str:
        return (
            reservation_id[: -len(cls._INPUT_SUFFIX)]
            if cls._is_input_reservation(reservation_id)
            else reservation_id
        )

    def _paired_reservation(self, session: Session, reservation_id: str) -> dict | None:
        row = (
            session.execute(
                select(usage_events)
                .where(
                    usage_events.c.reservation_id == reservation_id + self._INPUT_SUFFIX,
                    usage_events.c.op == "reserve",
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row is not None else None

    @staticmethod
    def _assert_paired_identity(base: dict, paired: dict | None) -> None:
        if paired is None:
            return
        for field in (
            "run_id",
            "step_id",
            "attempt_id",
            "scope_type",
            "scope_id",
            "source",
            "kind",
        ):
            if paired.get(field) != base.get(field):
                raise LedgerConflict("paired usage reservation owner identity mismatch")

    def _append_outcome(
        self,
        session: Session,
        *,
        reservation: dict,
        reservation_id: str,
        op: str,
        amount: int,
        cost_micros: int,
        now_us: int,
    ) -> None:
        if type(amount) is not int or amount < 0:
            raise ValueError("usage amount must be a non-negative integer")
        if type(cost_micros) is not int or cost_micros < 0:
            raise ValueError("usage cost must be a non-negative integer")
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
                op=op,
                amount=amount,
                cost_micros=cost_micros,
                provider=reservation["provider"],
                model=reservation["model"],
                created_at=now_us,
            )
        )

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
