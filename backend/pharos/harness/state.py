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
from pharos.harness.tables import attempts, config_head, runs, steps

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
        {
            AttemptState.running,
            AttemptState.failed,
            AttemptState.cancelled,
            AttemptState.abandoned,
            AttemptState.indeterminate,
        }
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

    def request_pause(self, session: Session, *, scope: Scope, run_id: str, now_us: int) -> bool:
        """Persist a pause request only for this owner's live Run.

        Control requests are themselves CASes.  In particular, a request made
        after a Run reached a terminal state must not mutate its timestamps or
        create a misleading follow-up event.
        """
        result: Any = session.execute(
            update(runs)
            .where(
                scope.where(runs),
                runs.c.id == run_id,
                runs.c.state.in_((RunState.queued.value, RunState.running.value)),
                runs.c.cancel_requested_at.is_(None),
                runs.c.pause_requested_at.is_(None),
            )
            .values(pause_requested_at=now_us)
        )
        return result.rowcount == 1

    def request_cancel(self, session: Session, *, scope: Scope, run_id: str, now_us: int) -> bool:
        """Owner-scoped, nonterminal cancel-request CAS.

        A terminal Run is immutable: cancellation is a no-op and, notably,
        cannot populate ``cancel_requested_at`` after completion.
        """
        result: Any = session.execute(
            update(runs)
            .where(
                scope.where(runs),
                runs.c.id == run_id,
                runs.c.state.not_in(tuple(state.value for state in RUN_TERMINAL_STATES)),
                runs.c.cancel_requested_at.is_(None),
            )
            .values(cancel_requested_at=now_us)
        )
        return result.rowcount == 1

    def reduce_run_cas(
        self,
        session: Session,
        *,
        scope: Scope,
        run_id: str,
        expected_state: RunState,
        target: RunState,
        outcome: str | None,
        now_us: int,
        payload: dict | None = None,
    ) -> bool:
        """Reduce one exact Run generation and emit an event only on success.

        The cancellation predicate is part of the same UPDATE as the state
        transition.  Thus a stale reducer cannot publish a terminal event,
        and a cancel/reduce race has one database winner.
        """
        _check(expected_state, target, RUN_TRANSITIONS, f"run {run_id}")
        self._validate_payload(payload)
        where = [
            scope.where(runs),
            runs.c.id == run_id,
            runs.c.state == expected_state.value,
            runs.c.state.not_in(tuple(state.value for state in RUN_TERMINAL_STATES)),
        ]
        if target in {RunState.succeeded, RunState.failed} or target not in {
            RunState.cancelled,
            RunState.indeterminate,
        }:
            where.append(runs.c.cancel_requested_at.is_(None))
        elif target is RunState.cancelled:
            where.append(runs.c.cancel_requested_at.is_not(None))
        # An indeterminate delivery/reconciliation result is allowed to win a
        # cancellation race.  Its exact Attempt fence decides whether it is
        # still valid; this Run CAS must not erase that evidence.
        if target is RunState.paused:
            where.append(runs.c.pause_requested_at.is_not(None))
        elif target not in RUN_TERMINAL_STATES:
            # A requested pause fences every new nonterminal phase. A Run that
            # has already completed its work may still publish a terminal
            # result, but a reducer cannot strand the pause request in another
            # executable or waiting state.
            where.append(runs.c.pause_requested_at.is_(None))
        values: dict[str, Any] = {
            "state": target.value,
            "outcome": outcome,
            "updated_at": now_us,
        }
        if target in RUN_TERMINAL_STATES:
            values["finished_at"] = now_us
        result: Any = session.execute(update(runs).where(*where).values(**values))
        if result.rowcount != 1:
            return False
        row = (
            session.execute(runs.select().where(scope.where(runs), runs.c.id == run_id))
            .mappings()
            .first()
        )
        if row is None:  # pragma: no cover - the CAS update just matched it
            raise StateError(f"run {run_id} disappeared after reduction")
        self._event(
            session,
            run_id=run_id,
            scope_type=scope.scope_type.value,
            scope_id=scope.scope_id,
            event_type=f"run.{target.value}",
            step_id=None,
            attempt_id=None,
            payload=payload,
            now_us=now_us,
        )
        return True

    def resume_run_cas(
        self,
        session: Session,
        *,
        scope: Scope,
        run_id: str,
        now_us: int,
    ) -> bool:
        """Resume one exact paused Run without racing cancellation."""
        _check(RunState.paused, RunState.queued, RUN_TRANSITIONS, f"run {run_id}")
        result: Any = session.execute(
            update(runs)
            .where(
                scope.where(runs),
                runs.c.id == run_id,
                runs.c.state == RunState.paused.value,
                runs.c.pause_requested_at.is_not(None),
                runs.c.cancel_requested_at.is_(None),
            )
            .values(
                state=RunState.queued.value,
                pause_requested_at=None,
                updated_at=now_us,
            )
        )
        if result.rowcount != 1:
            return False
        self._event(
            session,
            run_id=run_id,
            scope_type=scope.scope_type.value,
            scope_id=scope.scope_id,
            event_type="run.queued",
            step_id=None,
            attempt_id=None,
            payload=None,
            now_us=now_us,
        )
        return True

    def start_attempt_cas(
        self,
        session: Session,
        *,
        scope: Scope,
        run_id: str,
        step_id: str,
        attempt_id: str,
        attempt_no: int,
        lease_owner: str,
        now_us: int,
    ) -> bool:
        """Promote a leased Attempt/Step under a parent Run fence.

        The Run conditional write is intentionally first.  Callers may then
        reserve usage in this same transaction; a cancel committed first
        cannot leave a reservation or reach a model factory.
        """
        run_fence: Any = session.execute(
            update(runs)
            .where(
                scope.where(runs),
                runs.c.id == run_id,
                runs.c.state.in_((RunState.queued.value, RunState.running.value)),
                runs.c.cancel_requested_at.is_(None),
            )
            .values(updated_at=runs.c.updated_at)
        )
        if run_fence.rowcount != 1:
            return False
        attempt_result: Any = session.execute(
            update(attempts)
            .where(
                scope.where(attempts),
                attempts.c.id == attempt_id,
                attempts.c.run_id == run_id,
                attempts.c.step_id == step_id,
                attempts.c.attempt_no == attempt_no,
                attempts.c.lease_owner == lease_owner,
                attempts.c.state == AttemptState.leased.value,
                exists(
                    select(1)
                    .select_from(steps)
                    .where(
                        scope.where(steps),
                        steps.c.id == step_id,
                        steps.c.run_id == run_id,
                        steps.c.attempt_count == attempt_no,
                        steps.c.state == StepState.leased.value,
                        steps.c.lease_owner == lease_owner,
                    )
                ),
            )
            .values(state=AttemptState.running.value)
        )
        if attempt_result.rowcount != 1:
            return False
        step_result: Any = session.execute(
            update(steps)
            .where(
                scope.where(steps),
                steps.c.id == step_id,
                steps.c.run_id == run_id,
                steps.c.attempt_count == attempt_no,
                steps.c.state == StepState.leased.value,
                steps.c.lease_owner == lease_owner,
            )
            .values(state=StepState.running.value, updated_at=now_us)
        )
        if step_result.rowcount != 1:
            raise StateError(f"started Attempt {attempt_id} lost its Step fence")
        self._event(
            session,
            run_id=run_id,
            scope_type=scope.scope_type.value,
            scope_id=scope.scope_id,
            event_type="attempt.running",
            step_id=step_id,
            attempt_id=attempt_id,
            payload=None,
            now_us=now_us,
        )
        self._event(
            session,
            run_id=run_id,
            scope_type=scope.scope_type.value,
            scope_id=scope.scope_id,
            event_type="step.running",
            step_id=step_id,
            attempt_id=None,
            payload=None,
            now_us=now_us,
        )
        return True

    def finish_attempt_cas(
        self,
        session: Session,
        *,
        scope: Scope,
        run_id: str,
        step_id: str,
        attempt_id: str,
        attempt_no: int,
        lease_owner: str,
        expected_attempt_state: AttemptState,
        expected_step_state: StepState,
        target: AttemptState,
        now_us: int,
        attempt_values: dict[str, Any] | None = None,
        step_values: dict[str, Any] | None = None,
        cancel_on_request: bool = False,
    ) -> AttemptState | None:
        """Exact terminal Attempt/Step CAS, optionally honoring cancellation.

        The parent Run is fenced before either child update.  A failed path
        becomes cancelled when a persisted request already won; an
        indeterminate path deliberately remains indeterminate so provider
        reconciliation is never hidden by a control request.
        """
        if target not in {AttemptState.failed, AttemptState.indeterminate}:
            raise StateError("failure Attempt CAS requires failed or indeterminate target")
        if expected_attempt_state not in {AttemptState.leased, AttemptState.running}:
            raise StateError("terminal Attempt CAS requires an active Attempt source")
        if expected_step_state not in {StepState.leased, StepState.running}:
            raise StateError("terminal Attempt CAS requires an active Step source")
        _check(expected_attempt_state, target, ATTEMPT_TRANSITIONS, f"attempt {attempt_id}")
        run_fence: Any = session.execute(
            update(runs)
            .where(
                scope.where(runs),
                runs.c.id == run_id,
                runs.c.state.not_in(tuple(state.value for state in RUN_TERMINAL_STATES)),
            )
            .values(updated_at=runs.c.updated_at)
        )
        if run_fence.rowcount != 1:
            return None
        cancel_requested = session.execute(
            runs.select()
            .with_only_columns(runs.c.cancel_requested_at)
            .where(scope.where(runs), runs.c.id == run_id)
        ).scalar_one_or_none()
        effective = (
            AttemptState.cancelled if cancel_on_request and cancel_requested is not None else target
        )
        attempt_values = dict(attempt_values or {})
        step_values = dict(step_values or {})
        if effective is AttemptState.cancelled:
            attempt_values["error_class"] = AttemptState.cancelled.value
            step_values["error_code"] = AttemptState.cancelled.value
            step_values["skip_reason"] = "run_cancelled"
        predicates = [
            scope.where(attempts),
            attempts.c.id == attempt_id,
            attempts.c.run_id == run_id,
            attempts.c.step_id == step_id,
            attempts.c.attempt_no == attempt_no,
            attempts.c.lease_owner == lease_owner,
            attempts.c.state == expected_attempt_state.value,
            exists(
                select(1)
                .select_from(steps)
                .where(
                    scope.where(steps),
                    steps.c.id == step_id,
                    steps.c.run_id == run_id,
                    steps.c.attempt_count == attempt_no,
                    steps.c.state == expected_step_state.value,
                    steps.c.lease_owner == lease_owner,
                )
            ),
        ]
        result: Any = session.execute(
            update(attempts)
            .where(*predicates)
            .values(
                state=effective.value,
                finished_at=now_us,
                **attempt_values,
            )
        )
        if result.rowcount != 1:
            return None
        terminal_step = {
            AttemptState.cancelled: StepState.cancelled,
            AttemptState.failed: StepState.failed,
            AttemptState.indeterminate: StepState.indeterminate,
        }[effective]
        _check(expected_step_state, terminal_step, STEP_TRANSITIONS, f"step {step_id}")
        result = session.execute(
            update(steps)
            .where(
                scope.where(steps),
                steps.c.id == step_id,
                steps.c.run_id == run_id,
                steps.c.attempt_count == attempt_no,
                steps.c.state == expected_step_state.value,
                steps.c.lease_owner == lease_owner,
            )
            .values(
                state=terminal_step.value,
                updated_at=now_us,
                finished_at=now_us,
                lease_owner=None,
                lease_expires_at=None,
                **step_values,
            )
        )
        if result.rowcount != 1:
            raise StateError(f"terminal Attempt {attempt_id} lost its Step fence")
        self._event(
            session,
            run_id=run_id,
            scope_type=scope.scope_type.value,
            scope_id=scope.scope_id,
            event_type=f"attempt.{effective.value}",
            step_id=step_id,
            attempt_id=attempt_id,
            payload=None,
            now_us=now_us,
        )
        self._event(
            session,
            run_id=run_id,
            scope_type=scope.scope_type.value,
            scope_id=scope.scope_id,
            event_type=f"step.{terminal_step.value}",
            step_id=step_id,
            attempt_id=None,
            payload=None,
            now_us=now_us,
        )
        return effective

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

    def publish_attempt_cas(
        self,
        session: Session,
        *,
        scope: Scope,
        run_id: str,
        step_id: str,
        attempt_id: str,
        attempt_no: int,
        lease_owner: str,
        now_us: int,
        **values,
    ) -> bool:
        """Acquire the publication fence for one running Attempt.

        The UPDATE is intentionally the first write in the publication
        transaction.  Its run predicate makes cancellation and publication
        a SQLite writer-lock race: whichever CAS acquires the writer lock
        first wins.  Once this succeeds, the caller may insert the Artifact,
        settle usage and finish the Step before releasing that lock; a late
        cancellation therefore cannot observe a half-published success.
        """
        # An EXISTS predicate on a Run row is not itself a row lock on
        # PostgreSQL-style databases. Lock the parent with a conditional
        # no-op first, while rechecking the complete owner/generation fence,
        # so cancellation cannot commit between the Attempt CAS and Artifact
        # insertion. SQLite serialises the same write naturally.
        run_fence: Any = session.execute(
            update(runs)
            .where(
                runs.c.id == run_id,
                runs.c.scope_type == scope.scope_type.value,
                runs.c.scope_id == scope.scope_id,
                runs.c.cancel_requested_at.is_(None),
                runs.c.state.not_in(tuple(state.value for state in RUN_TERMINAL_STATES)),
                exists(
                    select(1)
                    .select_from(attempts)
                    .where(
                        attempts.c.id == attempt_id,
                        attempts.c.step_id == step_id,
                        attempts.c.run_id == run_id,
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
                                steps.c.output_artifact_id.is_(None),
                            )
                        ),
                    )
                ),
            )
            .values(updated_at=runs.c.updated_at)
        )
        if run_fence.rowcount != 1:
            return False
        result: Any = session.execute(
            update(attempts)
            .where(
                attempts.c.id == attempt_id,
                attempts.c.step_id == step_id,
                attempts.c.run_id == run_id,
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
                        steps.c.output_artifact_id.is_(None),
                    )
                ),
                exists(
                    select(1)
                    .select_from(runs)
                    .where(
                        runs.c.id == run_id,
                        runs.c.scope_type == scope.scope_type.value,
                        runs.c.scope_id == scope.scope_id,
                        runs.c.cancel_requested_at.is_(None),
                        runs.c.state.not_in(tuple(state.value for state in RUN_TERMINAL_STATES)),
                    )
                ),
            )
            .values(state=AttemptState.succeeded.value, finished_at=now_us, **values)
        )
        if result.rowcount != 1:
            return False
        self._event(
            session,
            run_id=run_id,
            scope_type=scope.scope_type.value,
            scope_id=scope.scope_id,
            event_type="attempt.succeeded",
            step_id=step_id,
            attempt_id=attempt_id,
            payload=None,
            now_us=now_us,
        )
        return True

    def cancel_attempt_cas(
        self,
        session: Session,
        *,
        scope: Scope,
        run_id: str,
        step_id: str,
        attempt_id: str,
        attempt_no: int,
        lease_owner: str,
        attempt_state: AttemptState,
        step_state: StepState,
        now_us: int,
        **attempt_values,
    ) -> bool:
        """Cancel an exact current-generation Attempt after a persisted request."""
        if attempt_state not in {AttemptState.leased, AttemptState.running} or step_state not in {
            StepState.leased,
            StepState.running,
        }:
            raise StateError("cancel Attempt CAS requires an active Attempt/Step source state")
        result: Any = session.execute(
            update(attempts)
            .where(
                attempts.c.id == attempt_id,
                attempts.c.step_id == step_id,
                attempts.c.run_id == run_id,
                attempts.c.scope_type == scope.scope_type.value,
                attempts.c.scope_id == scope.scope_id,
                attempts.c.attempt_no == attempt_no,
                attempts.c.lease_owner == lease_owner,
                attempts.c.state == attempt_state.value,
                exists(
                    select(1)
                    .select_from(steps)
                    .where(
                        steps.c.id == step_id,
                        steps.c.run_id == run_id,
                        steps.c.scope_type == scope.scope_type.value,
                        steps.c.scope_id == scope.scope_id,
                        steps.c.attempt_count == attempt_no,
                        steps.c.state == step_state.value,
                        steps.c.lease_owner == lease_owner,
                    )
                ),
                exists(
                    select(1)
                    .select_from(runs)
                    .where(
                        runs.c.id == run_id,
                        runs.c.scope_type == scope.scope_type.value,
                        runs.c.scope_id == scope.scope_id,
                        runs.c.state.not_in(tuple(state.value for state in RUN_TERMINAL_STATES)),
                        runs.c.cancel_requested_at.is_not(None),
                    )
                ),
            )
            .values(
                state=AttemptState.cancelled.value,
                finished_at=now_us,
                **attempt_values,
            )
        )
        if result.rowcount != 1:
            return False
        self._event(
            session,
            run_id=run_id,
            scope_type=scope.scope_type.value,
            scope_id=scope.scope_id,
            event_type="attempt.cancelled",
            step_id=step_id,
            attempt_id=attempt_id,
            payload=None,
            now_us=now_us,
        )
        step_result: Any = session.execute(
            update(steps)
            .where(
                steps.c.id == step_id,
                steps.c.run_id == run_id,
                steps.c.scope_type == scope.scope_type.value,
                steps.c.scope_id == scope.scope_id,
                steps.c.attempt_count == attempt_no,
                steps.c.state == step_state.value,
                steps.c.lease_owner == lease_owner,
            )
            .values(
                state=StepState.cancelled.value,
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=None,
                skip_reason="run_cancelled",
                updated_at=now_us,
                finished_at=now_us,
            )
        )
        if step_result.rowcount != 1:
            raise StateError(f"cancelled Attempt {attempt_id} lost its Step fence")
        self._event(
            session,
            run_id=run_id,
            scope_type=scope.scope_type.value,
            scope_id=scope.scope_id,
            event_type="step.cancelled",
            step_id=step_id,
            attempt_id=None,
            payload=None,
            now_us=now_us,
        )
        return True

    def indeterminate_attempt_cas(
        self,
        session: Session,
        *,
        scope: Scope,
        run_id: str,
        step_id: str,
        attempt_id: str,
        attempt_no: int,
        lease_owner: str,
        attempt_state: AttemptState,
        step_state: StepState,
        delivery_state: str | None,
        now_us: int,
    ) -> bool:
        """Conservatively fence an unowned active Attempt for reconciliation."""
        if attempt_state not in {AttemptState.leased, AttemptState.running} or step_state not in {
            StepState.leased,
            StepState.running,
        }:
            raise StateError(
                "indeterminate Attempt CAS requires an active Attempt/Step source state"
            )
        values: dict[str, Any] = {
            "state": AttemptState.indeterminate.value,
            "finished_at": now_us,
            "external_outcome": "indeterminate",
            "error_class": AttemptState.indeterminate.value,
            "error_message": "model delivery outcome requires reconciliation",
        }
        if delivery_state is not None:
            values["delivery_state"] = delivery_state
        result: Any = session.execute(
            update(attempts)
            .where(
                attempts.c.id == attempt_id,
                attempts.c.step_id == step_id,
                attempts.c.run_id == run_id,
                attempts.c.scope_type == scope.scope_type.value,
                attempts.c.scope_id == scope.scope_id,
                attempts.c.attempt_no == attempt_no,
                attempts.c.lease_owner == lease_owner,
                attempts.c.state == attempt_state.value,
                exists(
                    select(1)
                    .select_from(steps)
                    .where(
                        steps.c.id == step_id,
                        steps.c.run_id == run_id,
                        steps.c.scope_type == scope.scope_type.value,
                        steps.c.scope_id == scope.scope_id,
                        steps.c.attempt_count == attempt_no,
                        steps.c.state == step_state.value,
                        steps.c.lease_owner == lease_owner,
                    )
                ),
                exists(
                    select(1)
                    .select_from(runs)
                    .where(
                        runs.c.id == run_id,
                        runs.c.scope_type == scope.scope_type.value,
                        runs.c.scope_id == scope.scope_id,
                        runs.c.state.not_in(tuple(state.value for state in RUN_TERMINAL_STATES)),
                        runs.c.cancel_requested_at.is_not(None),
                    )
                ),
            )
            .values(**values)
        )
        if result.rowcount != 1:
            return False
        self._event(
            session,
            run_id=run_id,
            scope_type=scope.scope_type.value,
            scope_id=scope.scope_id,
            event_type="attempt.indeterminate",
            step_id=step_id,
            attempt_id=attempt_id,
            payload=None,
            now_us=now_us,
        )
        step_result: Any = session.execute(
            update(steps)
            .where(
                steps.c.id == step_id,
                steps.c.run_id == run_id,
                steps.c.scope_type == scope.scope_type.value,
                steps.c.scope_id == scope.scope_id,
                steps.c.attempt_count == attempt_no,
                steps.c.state == step_state.value,
                steps.c.lease_owner == lease_owner,
            )
            .values(
                state=StepState.indeterminate.value,
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=None,
                error_code="external_outcome_unknown",
                updated_at=now_us,
                finished_at=now_us,
            )
        )
        if step_result.rowcount != 1:
            raise StateError(f"indeterminate Attempt {attempt_id} lost its Step fence")
        self._event(
            session,
            run_id=run_id,
            scope_type=scope.scope_type.value,
            scope_id=scope.scope_id,
            event_type="step.indeterminate",
            step_id=step_id,
            attempt_id=None,
            payload=None,
            now_us=now_us,
        )
        return True

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

    def activate_retry_cas(
        self,
        session: Session,
        *,
        step_id: str,
        now_us: int,
        config_revision_id: str | None = None,
    ) -> bool:
        """Promote one due retry, emitting an event only for the CAS winner."""
        row = session.execute(select(steps).where(steps.c.id == step_id)).mappings().first()
        if row is None:
            return False
        predicates = [
            steps.c.id == step_id,
            steps.c.state == StepState.retry_scheduled.value,
            steps.c.ready_at <= now_us,
            exists(
                select(1)
                .select_from(runs)
                .where(
                    runs.c.id == steps.c.run_id,
                    runs.c.scope_type == steps.c.scope_type,
                    runs.c.scope_id == steps.c.scope_id,
                    runs.c.state.in_([RunState.queued.value, RunState.running.value]),
                    runs.c.cancel_requested_at.is_(None),
                    runs.c.pause_requested_at.is_(None),
                )
                .correlate_except(runs)
            ),
        ]
        if config_revision_id is not None:
            predicates.append(
                exists(
                    select(1)
                    .select_from(config_head)
                    .where(
                        config_head.c.head_key == "singleton",
                        config_head.c.current_revision_id == config_revision_id,
                    )
                )
            )
        result: Any = session.execute(
            update(steps)
            .where(*predicates)
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
            session.execute(select(attempts).where(attempts.c.id == attempt_id)).mappings().one()
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
