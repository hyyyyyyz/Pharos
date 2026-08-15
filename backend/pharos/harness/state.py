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

import json
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from pharos.harness.contracts import (
    ATTEMPT_TERMINAL_STATES,
    RUN_TERMINAL_STATES,
    STEP_TERMINAL_STATES,
    AttemptState,
    RunState,
    StateError,
    StepState,
)
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
        from pharos.harness.tables import events

        session.execute(
            events.insert().values(
                run_id=run_id,
                scope_type=scope_type,
                scope_id=scope_id,
                step_id=step_id,
                attempt_id=attempt_id,
                event_type=event_type,
                payload_json=json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
                created_at=now_us,
            )
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
        self, session: Session, *, attempt_id: str, lease_owner: str, now_us: int
    ) -> bool:
        """Only the current lease owner may heartbeat an active attempt."""
        result: Any = session.execute(
            update(attempts)
            .where(
                attempts.c.id == attempt_id,
                attempts.c.lease_owner == lease_owner,
                attempts.c.state.in_([AttemptState.leased.value, AttemptState.running.value]),
            )
            .values(heartbeat_at=now_us)
        )
        return result.rowcount == 1
