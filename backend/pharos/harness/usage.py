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

from sqlalchemy import exists, insert, literal, select
from sqlalchemy.orm import Session

from pharos.harness.contracts import NotFoundError
from pharos.harness.repository import Scope, new_id
from pharos.harness.tables import (
    attempts,
    runs,
    steps,
    usage_events,
    usage_reconciliations,
)


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
    _RECONCILIABLE_DELIVERY = frozenset({"unknown", "sent", "acknowledged"})
    _RECONCILIATION_EVIDENCE = frozenset(
        {"provider_receipt", "provider_not_found", "operator_confirmed"}
    )

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
        # Replaying an already released reservation is safe even if an
        # external recovery write has since advanced the Attempt observation;
        # only a first release must prove that delivery never crossed over.
        if self._outcome(session, reservation_id) is None:
            self._ensure_release_is_safe(session, reservation)
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

    def reconcile(
        self,
        session: Session,
        *,
        reservation_id: str,
        delivery_state: str,
        now_us: int,
        scope: Scope,
        run_id: str,
        step_id: str | None = None,
        attempt_id: str | None = None,
        actual: int | None = None,
        actual_input: int | None = None,
        actual_cost_micros: int | None = None,
        delivered: bool | None = None,
        outcome: str | None = None,
        evidence_code: str = "provider_receipt",
        provider_request_id: str | None = None,
    ) -> str:
        """Close a reservation after provider-delivery reconciliation.

        ``unknown``, ``sent`` and ``acknowledged`` are all conservative
        delivery states: none may be released merely because a response is
        absent.  A trusted usage receipt closes the reservation as ``settled``
        (with ``actual`` tokens), while an explicit not-found/unsent decision
        closes it as ``released``.  The receipt and ledger terminal rows are
        written in the caller's transaction.  Replaying the exact decision is
        a no-op; a different decision for the same reservation is a conflict.
        """
        if delivery_state not in self._RECONCILIABLE_DELIVERY:
            raise ValueError("reconciliation requires unknown, sent, or acknowledged delivery")
        if evidence_code not in self._RECONCILIATION_EVIDENCE:
            raise ValueError("unknown reconciliation evidence code")
        if provider_request_id is not None and (
            type(provider_request_id) is not str
            or not 0 < len(provider_request_id) <= 256
            or "\x00" in provider_request_id
        ):
            raise ValueError("provider request id is invalid")
        if outcome is not None and outcome not in {"settled", "released"}:
            raise ValueError("reconciliation outcome must be settled or released")
        if outcome is not None:
            requested_delivered = outcome == "settled"
            if delivered is not None and delivered != requested_delivered:
                raise ValueError("delivered and outcome disagree")
            delivered = requested_delivered
        if delivered is None:
            delivered = actual is not None
        if type(delivered) is not bool:
            raise ValueError("delivered must be a boolean when supplied")
        outcome = "settled" if delivered else "released"
        if outcome == "settled" and actual is None:
            raise ValueError("settlement reconciliation requires actual usage")
        if outcome == "released" and actual not in (None, 0):
            raise ValueError("released reconciliation cannot include usage")
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
        if input_reservation is not None and delivered and actual_input is None:
            raise LedgerConflict("input usage is required to settle a multidimensional reservation")
        if delivered and actual_cost_micros is None and reservation["cost_micros"]:
            raise LedgerConflict("cost usage is required to settle a paid reservation")
        self._mark_delivery_reconciled(
            session,
            reservation=reservation,
            delivery_state=delivery_state,
        )
        resolved_input = actual_input or 0
        resolved_amount = actual or 0
        resolved_cost = actual_cost_micros or 0
        for value, label in (
            (resolved_amount, "actual usage"),
            (resolved_input, "actual input usage"),
            (resolved_cost, "actual cost"),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"{label} must be a non-negative integer")
        if not delivered:
            resolved_input = resolved_amount = resolved_cost = 0

        self._append_outcome(
            session,
            reservation=reservation,
            reservation_id=reservation_id,
            op="settle" if delivered else "release",
            amount=resolved_amount,
            cost_micros=resolved_cost,
            now_us=now_us,
        )
        if input_reservation is not None:
            self._append_outcome(
                session,
                reservation=input_reservation,
                reservation_id=reservation_id + self._INPUT_SUFFIX,
                op="settle" if delivered else "release",
                amount=resolved_input,
                cost_micros=0,
                now_us=now_us,
            )

        receipt = self._reconciliation_receipt(session, reservation_id)
        if receipt is not None:
            self._assert_reconciliation_match(
                receipt,
                delivery_state=delivery_state,
                outcome=outcome,
                amount=resolved_amount,
                input_amount=resolved_input,
                cost_micros=resolved_cost,
                evidence_code=evidence_code,
                provider_request_id=provider_request_id,
            )
            return str(receipt["id"])

        # The unique reservation key is the reconciliation CAS.  The
        # conditional insert keeps a concurrent retry from creating a second
        # decision; the subsequent read gives the winner's receipt to the
        # loser, which can then validate that the replay is identical.
        receipt_id = new_id()
        receipt_columns = [
            "id", "reservation_id", "run_id", "step_id", "attempt_id",
            "scope_type", "scope_id", "delivery_state", "outcome", "amount",
            "input_amount", "cost_micros", "evidence_code", "provider_request_id",
            "created_at",
        ]
        receipt_values = select(
            literal(receipt_id),
            literal(reservation_id),
            literal(reservation["run_id"]),
            literal(reservation["step_id"]),
            literal(reservation["attempt_id"]),
            literal(reservation["scope_type"]),
            literal(reservation["scope_id"]),
            literal(delivery_state),
            literal(outcome),
            literal(resolved_amount),
            literal(resolved_input),
            literal(resolved_cost),
            literal(evidence_code),
            literal(provider_request_id),
            literal(now_us),
        ).where(
            ~exists(
                select(1).where(
                    usage_reconciliations.c.reservation_id == reservation_id
                )
            )
        )
        result = session.execute(
            insert(usage_reconciliations).from_select(receipt_columns, receipt_values)
        )
        if result.rowcount != 1:
            receipt = self._reconciliation_receipt(session, reservation_id)
            if receipt is None:
                raise LedgerConflict("reconciliation CAS lost without a receipt")
            self._assert_reconciliation_match(
                receipt,
                delivery_state=delivery_state,
                outcome=outcome,
                amount=resolved_amount,
                input_amount=resolved_input,
                cost_micros=resolved_cost,
                evidence_code=evidence_code,
                provider_request_id=provider_request_id,
            )
            return str(receipt["id"])
        return receipt_id

    def list_pending_reconciliation(
        self,
        session: Session,
        *,
        scope: Scope,
        run_id: str | None = None,
        attempt_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List open Attempt reservations whose delivery may have occurred."""
        spent = usage_events.alias("spent_reconciliation")
        predicates = [
            usage_events.c.scope_type == scope.scope_type.value,
            usage_events.c.scope_id == scope.scope_id,
            usage_events.c.op == "reserve",
            ~usage_events.c.reservation_id.like("%" + self._INPUT_SUFFIX),
            attempts.c.run_id == usage_events.c.run_id,
            attempts.c.scope_type == usage_events.c.scope_type,
            attempts.c.scope_id == usage_events.c.scope_id,
            attempts.c.delivery_state.in_(self._RECONCILIABLE_DELIVERY),
            ~exists(
                select(1)
                .select_from(spent)
                .where(
                    spent.c.reservation_id == usage_events.c.reservation_id,
                    spent.c.op.in_(("settle", "release")),
                )
            ),
        ]
        if run_id is not None:
            predicates.append(usage_events.c.run_id == run_id)
        if attempt_id is not None:
            predicates.append(usage_events.c.attempt_id == attempt_id)
        rows = session.execute(
            select(usage_events, attempts.c.delivery_state)
            .join(attempts, attempts.c.id == usage_events.c.attempt_id)
            .where(*predicates)
            .order_by(usage_events.c.created_at, usage_events.c.id)
        ).mappings()
        return [dict(row) for row in rows]

    # A short alias is convenient for a reconciliation coordinator and keeps
    # the accounting vocabulary symmetrical with ``totals``.
    pending_reconciliation = list_pending_reconciliation

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
            elif row["op"] == "release":
                if not self._is_input_reservation(row["reservation_id"]):
                    released_count += 1
            else:
                raise LedgerConflict("usage row has an unknown operation")
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
    ) -> bool:
        if type(amount) is not int or amount < 0:
            raise ValueError("usage amount must be a non-negative integer")
        if type(cost_micros) is not int or cost_micros < 0:
            raise ValueError("usage cost must be a non-negative integer")
        existing = self._outcome(session, reservation_id)
        if existing is not None:
            if (
                existing["op"] != op
                or existing["amount"] != amount
                or existing["cost_micros"] != cost_micros
            ):
                raise LedgerConflict(f"reservation {reservation_id} already {existing['op']}(ed)")
            return False

        # Conditional INSERT is the write fence.  A concurrent settle/release
        # cannot both observe an open reservation and append an outcome: the
        # unique partial index is backed by this NOT EXISTS CAS predicate.
        outcome_values = select(
            literal(new_id()),
            literal(reservation["run_id"]),
            literal(reservation["step_id"]),
            literal(reservation["attempt_id"]),
            literal(reservation["scope_type"]),
            literal(reservation["scope_id"]),
            literal(reservation["source"]),
            literal(reservation["kind"]),
            literal(reservation_id),
            literal(op),
            literal(amount),
            literal(cost_micros),
            literal(reservation["provider"]),
            literal(reservation["model"]),
            literal(now_us),
        ).where(
            ~exists(
                select(1).where(
                    usage_events.c.reservation_id == reservation_id,
                    usage_events.c.op.in_(("settle", "release")),
                )
            )
        )
        result = session.execute(
            insert(usage_events).from_select(
                [
                    "id", "run_id", "step_id", "attempt_id", "scope_type", "scope_id",
                    "source", "kind", "reservation_id", "op", "amount", "cost_micros",
                    "provider", "model", "created_at",
                ],
                outcome_values,
            )
        )
        if result.rowcount == 1:
            return True
        existing = self._outcome(session, reservation_id)
        if existing is None:
            raise LedgerConflict("usage outcome CAS lost without a terminal row")
        if (
            existing["op"] != op
            or existing["amount"] != amount
            or existing["cost_micros"] != cost_micros
        ):
            raise LedgerConflict(f"reservation {reservation_id} already {existing['op']}(ed)")
        return False

    @staticmethod
    def _outcome(session: Session, reservation_id: str) -> dict | None:
        rows = (
            session.execute(
                select(usage_events).where(
                    usage_events.c.reservation_id == reservation_id,
                    usage_events.c.op.in_(("settle", "release")),
                )
            )
            .mappings()
            .all()
        )
        if len(rows) > 1:
            raise LedgerConflict("usage reservation has multiple outcomes")
        return dict(rows[0]) if rows else None

    @staticmethod
    def _reconciliation_receipt(session: Session, reservation_id: str) -> dict | None:
        row = session.execute(
            select(usage_reconciliations).where(
                usage_reconciliations.c.reservation_id == reservation_id
            )
        ).mappings().first()
        return dict(row) if row is not None else None

    @staticmethod
    def _assert_reconciliation_match(
        receipt: dict,
        *,
        delivery_state: str,
        outcome: str,
        amount: int,
        input_amount: int,
        cost_micros: int,
        evidence_code: str,
        provider_request_id: str | None,
    ) -> None:
        expected = {
            "delivery_state": delivery_state,
            "outcome": outcome,
            "amount": amount,
            "input_amount": input_amount,
            "cost_micros": cost_micros,
            "evidence_code": evidence_code,
            "provider_request_id": provider_request_id,
        }
        if any(receipt[field] != value for field, value in expected.items()):
            raise LedgerConflict("reconciliation decision does not match the existing receipt")

    @staticmethod
    def _mark_delivery_reconciled(
        session: Session,
        *,
        reservation: dict,
        delivery_state: str,
    ) -> None:
        """Advance the Attempt delivery observation with a narrow CAS.

        Reconciliation is the one service allowed to close the delivery
        observation.  A stale worker cannot turn a newer delivery observation
        into ``reconciled`` or close a reservation belonging to another
        Attempt generation.
        """
        attempt_id = reservation.get("attempt_id")
        if attempt_id is None:
            return
        predicates = [
            attempts.c.id == attempt_id,
            attempts.c.run_id == reservation["run_id"],
            attempts.c.step_id == reservation["step_id"],
            attempts.c.scope_type == reservation["scope_type"],
            attempts.c.scope_id == reservation["scope_id"],
        ]
        current = session.execute(
            select(attempts.c.delivery_state).where(*predicates)
        ).scalar_one_or_none()
        if current is None:
            raise LedgerConflict("reconciliation Attempt has no delivery evidence")
        if current == "reconciled":
            return
        if current != delivery_state:
            raise LedgerConflict("reconciliation delivery state is stale")
        result = session.execute(
            attempts.update()
            .where(*predicates, attempts.c.delivery_state == delivery_state)
            .values(delivery_state="reconciled")
        )
        if result.rowcount != 1:
            raise LedgerConflict("reconciliation delivery CAS lost")

    @classmethod
    def _ensure_release_is_safe(cls, session: Session, reservation: dict) -> None:
        """Never release a reservation after the provider boundary."""
        attempt_id = reservation.get("attempt_id")
        if attempt_id is None:
            return
        row = session.execute(
            select(
                attempts.c.delivery_state,
                attempts.c.runtime_session_id,
                attempts.c.child_pid,
                attempts.c.runtime_message_id,
            ).where(
                attempts.c.id == attempt_id,
                attempts.c.run_id == reservation["run_id"],
                attempts.c.step_id == reservation["step_id"],
                attempts.c.scope_type == reservation["scope_type"],
                attempts.c.scope_id == reservation["scope_id"],
            )
        ).mappings().first()
        if row is None:
            raise LedgerConflict("release Attempt is missing its owner fence")
        delivery = row["delivery_state"]
        runtime_evidence = any(
            row[field] is not None
            for field in ("runtime_session_id", "child_pid", "runtime_message_id")
        )
        if delivery in cls._RECONCILIABLE_DELIVERY or runtime_evidence:
            raise LedgerConflict("provider-delivered usage requires reconciliation")

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
