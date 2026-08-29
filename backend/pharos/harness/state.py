"""The one place state transitions are legal.

Run, Step and Attempt states are closed vocabularies (see contracts.py) and
this service is their only writer. API handlers, workers, capabilities and
runners never assign a state string; they ask for a transition, get a typed
:class:`StateError` when it is illegal, and every accepted transition is
appended to the event log in the same short transaction as the state change.

Pure transition tables are module-level so tests can exhaust them without a
database; the service methods apply them through a Session.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import exists, select, update
from sqlalchemy.orm import Session

from pharos.harness.contracts import (
    ATTEMPT_TERMINAL_STATES,
    RUN_TERMINAL_STATES,
    STEP_TERMINAL_STATES,
    AttemptState,
    RunState,
    ScopeType,
    StateError,
    StepState,
)
from pharos.harness.events import EventStore, encode_event_payload
from pharos.harness.repository import Scope
from pharos.harness.tables import attempts, runs, steps

# --------------------------------------------------------------------------
# The transition tables.


RUN_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.queued: frozenset(
        {
            RunState.running,
            RunState.paused,
            RunState.cancelled,
            RunState.failed,
            RunState.indeterminate,
            RunState.waiting_for_input,
        }
    ),
    RunState.running: frozenset(
        {
            RunState.paused,
            RunState.cancelled,
            RunState.succeeded,
            RunState.failed,
            RunState.indeterminate,
            RunState.waiting_for_approval,
            RunState.waiting_for_input,
        }
    ),
    RunState.waiting_for_approval: frozenset(
        {
            RunState.running,
            RunState.cancelled,
            RunState.succeeded,
            RunState.failed,
            RunState.indeterminate,
        }
    ),
    RunState.waiting_for_input: frozenset(
        {RunState.running, RunState.cancelled, RunState.failed, RunState.indeterminate}
    ),
    RunState.paused: frozenset({RunState.running, RunState.queued, RunState.cancelled}),
    RunState.succeeded: frozenset(),
    RunState.failed: frozenset(),
    RunState.cancelled: frozenset(),
    RunState.indeterminate: frozenset(),
}

STEP_TRANSITIONS: dict[StepState, frozenset[StepState]] = {
    StepState.pending: frozenset({StepState.ready, StepState.skipped, StepState.cancelled}),
    StepState.ready: frozenset({StepState.leased, StepState.cancelled, StepState.skipped}),
    StepState.leased: frozenset(
        {StepState.running, StepState.cancelled, StepState.failed, StepState.indeterminate}
    ),
    StepState.running: frozenset(
        {
            StepState.succeeded,
            StepState.failed,
            StepState.cancelled,
            StepState.indeterminate,
            StepState.waiting_for_approval,
            StepState.waiting_for_input,
            StepState.retry_scheduled,
        }
    ),
    StepState.waiting_for_approval: frozenset(
        {
            StepState.ready,
            StepState.running,
            StepState.succeeded,
            StepState.failed,
            StepState.cancelled,
            StepState.skipped,
        }
    ),
    StepState.waiting_for_input: frozenset(
        {
            StepState.ready,
            StepState.running,
            StepState.failed,
            StepState.cancelled,
            StepState.skipped,
            StepState.indeterminate,
        }
    ),
    StepState.retry_scheduled: frozenset({StepState.ready, StepState.failed, StepState.cancelled}),
    StepState.succeeded: frozenset(),
    StepState.failed: frozenset(),
    StepState.cancelled: frozenset(),
    StepState.skipped: frozenset(),
    StepState.indeterminate: frozenset(),
}

ATTEMPT_TRANSITIONS: dict[AttemptState, frozenset[AttemptState]] = {
    AttemptState.leased: frozenset(
        {AttemptState.running, AttemptState.failed, AttemptState.cancelled, AttemptState.abandoned}
    ),
    AttemptState.running: frozenset(
        {
            AttemptState.succeeded,
            AttemptState.failed,
            AttemptState.timed_out,
            AttemptState.cancelled,
            AttemptState.abandoned,
            AttemptState.blocked,
            AttemptState.indeterminate,
        }
    ),
    AttemptState.blocked: frozenset(),
    AttemptState.succeeded: frozenset(),
    AttemptState.failed: frozenset(),
    AttemptState.timed_out: frozenset(),
    AttemptState.cancelled: frozenset(),
    AttemptState.abandoned: frozenset(),
    AttemptState.indeterminate: frozenset(),
}


def _check(current: str, target: str, table: dict, subject: str) -> None:
    try:
        allowed = table[current]
    except KeyError as error:
        raise StateError(f"{subject}: unknown state {current!r}") from error
    if target not in allowed:
        raise StateError(f"{subject}: illegal transition {current} -> {target}")


class HarnessStateService:
    """Central transition authority; also appends the matching Event."""

    @staticmethod
    def _validate_payload(payload: dict | None) -> None:
        """Apply EventStore's serializer/cap before changing state."""
        encode_event_payload(payload)

    def _event(
        self,
        session: Session,
        *,
        run_id: str,
        scope_type: str,
        scope_id: str,
        event_type: str,
        step_id: str | None,
        attempt_id: str | None,
        payload: dict | None,
        now_us: int,
    ) -> None:
        EventStore().append(
            session,
            scope=Scope(scope_type=ScopeType(scope_type), scope_id=scope_id),
            run_id=run_id,
            event_type=event_type,
            payload=payload,
            step_id=step_id,
            attempt_id=attempt_id,
            now_us=now_us,
        )

    # ------------------------------------------------------------------ Run

    def transition_run(
        self,
        session: Session,
        *,
        run_id: str,
        target: RunState,
        now_us: int,
        payload: dict | None = None,
    ) -> None:
        row = session.execute(runs.select().where(runs.c.id == run_id)).mappings().first()
        if row is None:
            raise StateError(f"run {run_id} does not exist")
        current = RunState(row["state"])
        _check(current, target, RUN_TRANSITIONS, f"run {run_id}")
        self._validate_payload(payload)
        values: dict = {"state": target.value, "updated_at": now_us}
        if target == RunState.running and row["started_at"] is None:
            values["started_at"] = now_us
        if target in RUN_TERMINAL_STATES:
            values["finished_at"] = now_us
        session.execute(runs.update().where(runs.c.id == run_id).values(**values))
        self._event(
            session,
            run_id=run_id,
            scope_type=row["scope_type"],
            scope_id=row["scope_id"],
            event_type=f"run.{target.value}",
            step_id=None,
            attempt_id=None,
            payload=payload,
            now_us=now_us,
        )

    def reduce_run(
        self,
        session: Session,
        *,
        run_id: str,
        target: RunState,
        outcome: str | None,
        now_us: int,
        payload: dict | None = None,
    ) -> None:
        """Terminal reduction: state plus outcome, decided deterministically."""
        self.transition_run(session, run_id=run_id, target=target, now_us=now_us, payload=payload)
        session.execute(
            runs.update()
            .where(runs.c.id == run_id)
            .values(outcome=outcome, updated_at=now_us, finished_at=now_us)
        )

    def request_pause(self, session: Session, *, run_id: str, now_us: int) -> None:
        session.execute(runs.update().where(runs.c.id == run_id).values(pause_requested_at=now_us))

    def request_cancel(self, session: Session, *, run_id: str, now_us: int) -> None:
        session.execute(runs.update().where(runs.c.id == run_id).values(cancel_requested_at=now_us))

    # ----------------------------------------------------------------- Step

    def transition_step(
        self,
        session: Session,
        *,
        step_id: str,
        target: StepState,
        now_us: int,
        payload: dict | None = None,
        **values,
    ) -> None:
        row = session.execute(steps.select().where(steps.c.id == step_id)).mappings().first()
        if row is None:
            raise StateError(f"step {step_id} does not exist")
        current = StepState(row["state"])
        _check(current, target, STEP_TRANSITIONS, f"step {step_id}")
        self._validate_payload(payload)
        values = {"state": target.value, "updated_at": now_us, **values}
        if target in STEP_TERMINAL_STATES:
            values["finished_at"] = now_us
        session.execute(steps.update().where(steps.c.id == step_id).values(**values))
        self._event(
            session,
            run_id=row["run_id"],
            scope_type=row["scope_type"],
            scope_id=row["scope_id"],
            event_type=f"step.{target.value}",
            step_id=step_id,
            attempt_id=None,
            payload=payload,
            now_us=now_us,
        )

    # -------------------------------------------------------------- Attempt

    def transition_attempt(
        self,
        session: Session,
        *,
        attempt_id: str,
        target: AttemptState,
        now_us: int,
        payload: dict | None = None,
        **values,
    ) -> None:
        row = (
            session.execute(attempts.select().where(attempts.c.id == attempt_id)).mappings().first()
        )
        if row is None:
            raise StateError(f"attempt {attempt_id} does not exist")
        current = AttemptState(row["state"])
        _check(current, target, ATTEMPT_TRANSITIONS, f"attempt {attempt_id}")
        self._validate_payload(payload)
        values = {"state": target.value, **values}
        if target in ATTEMPT_TERMINAL_STATES:
            values["finished_at"] = now_us
        session.execute(attempts.update().where(attempts.c.id == attempt_id).values(**values))
        self._event(
            session,
            run_id=row["run_id"],
            scope_type=row["scope_type"],
            scope_id=row["scope_id"],
            event_type=f"attempt.{target.value}",
            step_id=row["step_id"],
            attempt_id=attempt_id,
            payload=payload,
            now_us=now_us,
        )

    # ---------------------------------------------------- CAS helper (lease)

    def update_attempt_heartbeat_cas(
        self,
        session: Session,
        *,
        attempt_id: str,
        lease_owner: str,
        now_us: int,
        lease_expires_at: int | None = None,
    ) -> bool:
        """Only the current owner may heartbeat and extend an active lease.

        The step predicate is part of the CAS, so a heartbeat racing a reaper
        cannot revive an expired lease after the reaper has won.  The optional
        expiry keeps this helper source-compatible for callers that only need
        to record a heartbeat; the dispatcher always supplies the extension.
        """
        result: Any = session.execute(
            update(attempts)
            .where(
                attempts.c.id == attempt_id,
                attempts.c.lease_owner == lease_owner,
                attempts.c.state.in_([AttemptState.leased.value, AttemptState.running.value]),
                exists(
                    select(1)
                    .select_from(steps)
                    .where(
                        steps.c.id == attempts.c.step_id,
                        steps.c.state.in_([StepState.leased.value, StepState.running.value]),
                        steps.c.lease_owner == lease_owner,
                        steps.c.lease_expires_at.is_not(None),
                        steps.c.lease_expires_at > now_us,
                    )
                ),
            )
            .values(heartbeat_at=now_us)
        )
        if result.rowcount != 1:
            return False
        if lease_expires_at is not None:
            session.execute(
                update(steps)
                .where(
                    steps.c.id
                    == select(attempts.c.step_id)
                    .where(attempts.c.id == attempt_id)
                    .scalar_subquery(),
                    steps.c.lease_owner == lease_owner,
                    steps.c.state.in_([StepState.leased.value, StepState.running.value]),
                )
                .values(heartbeat_at=now_us, lease_expires_at=lease_expires_at)
            )
        return True

    def activate_retry_cas(self, session: Session, *, step_id: str, now_us: int) -> bool:
        """Promote one due retry, emitting an event only for the CAS winner."""
        row = (
            session.execute(select(steps).where(steps.c.id == step_id))
            .mappings()
            .first()
        )
        if row is None:
            return False
        result: Any = session.execute(
            update(steps)
            .where(
                steps.c.id == step_id,
                steps.c.state == StepState.retry_scheduled.value,
                steps.c.ready_at <= now_us,
            )
            .values(
                state=StepState.ready.value,
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=None,
                updated_at=now_us,
            )
        )
        if result.rowcount != 1:
            return False
        self._event(
            session,
            run_id=row["run_id"],
            scope_type=row["scope_type"],
            scope_id=row["scope_id"],
            event_type="step.ready",
            step_id=step_id,
            attempt_id=None,
            payload={"reason": "retry_due"},
            now_us=now_us,
        )
        return True

    def abandon_expired_attempt_cas(
        self,
        session: Session,
        *,
        attempt_id: str,
        step_id: str,
        lease_owner: str,
        now_us: int,
    ) -> bool:
        """Abandon an attempt only while its owner/state/expiry token matches.

        The attempt CAS takes the write lock before either transition event is
        appended.  A heartbeat or a second reaper therefore loses cleanly and
        cannot write duplicate or stale abandonment events.
        """
        result: Any = session.execute(
            update(attempts)
            .where(
                attempts.c.id == attempt_id,
                attempts.c.step_id == step_id,
                attempts.c.lease_owner == lease_owner,
                attempts.c.state.in_([AttemptState.leased.value, AttemptState.running.value]),
                exists(
                    select(1)
                    .select_from(steps)
                    .where(
                        steps.c.id == step_id,
                        steps.c.state.in_([StepState.leased.value, StepState.running.value]),
                        steps.c.lease_owner == lease_owner,
                        steps.c.lease_expires_at.is_not(None),
                        steps.c.lease_expires_at <= now_us,
                    )
                ),
            )
            .values(state=AttemptState.abandoned.value, finished_at=now_us)
        )
        if result.rowcount != 1:
            return False
        attempt = (
            session.execute(select(attempts).where(attempts.c.id == attempt_id))
            .mappings()
            .one()
        )
        self._event(
            session,
            run_id=attempt["run_id"],
            scope_type=attempt["scope_type"],
            scope_id=attempt["scope_id"],
            event_type="attempt.abandoned",
            step_id=step_id,
            attempt_id=attempt_id,
            payload={"reason": "lease_expired"},
            now_us=now_us,
        )
        step_result: Any = session.execute(
            update(steps)
            .where(
                steps.c.id == step_id,
                steps.c.state.in_([StepState.leased.value, StepState.running.value]),
                steps.c.lease_owner == lease_owner,
                steps.c.lease_expires_at.is_not(None),
                steps.c.lease_expires_at <= now_us,
            )
            .values(
                state=StepState.indeterminate.value,
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=None,
                error_code="lease_expired",
                updated_at=now_us,
                finished_at=now_us,
            )
        )
        if step_result.rowcount != 1:
            # Both rows are one recovery decision.  Raising keeps the caller's
            # transaction from committing an abandoned Attempt while its Step
            # still looks executable; a healthy FK/state pair cannot reach
            # this branch once the attempt CAS has acquired SQLite's writer
            # lock.
            raise StateError(f"expired lease for attempt {attempt_id} lost its step {step_id}")
        self._event(
            session,
            run_id=attempt["run_id"],
            scope_type=attempt["scope_type"],
            scope_id=attempt["scope_id"],
            event_type="step.indeterminate",
            step_id=step_id,
            attempt_id=None,
            payload={"reason": "lease_expired"},
            now_us=now_us,
        )
        return True
