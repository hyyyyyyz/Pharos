"""Dual-worker claim races: two independent workers, one file database.

The dispatcher's claim is a single conditional UPDATE, so of N competing
workers exactly one may own each step. The race runs with two real threads on
independent SQLAlchemy engines over one file-backed database -- never
``:memory:``, which would fake the concurrency away. The loser returns
``None`` after rolling back its short claim transaction, exactly like an empty
queue, and never leaks a worker-thread exception.
"""

from __future__ import annotations

import threading
import uuid

from pharos.db.session import _configure_sqlite, session_scope
from pharos.harness.configrev import HarnessConfigSnapshot, WorkflowRoute
from pharos.harness.contracts import ActivationState, AttemptState, ExecutionMode, StepState
from pharos.harness.dispatcher import HarnessDispatcher
from pharos.harness.fakes import FakeClock
from pharos.harness.repository import now_iso
from pharos.harness.tables import events as events_table
from pharos.harness.tables import steps
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from tests.harness.conftest import enable_canary


def _engine(path):  # noqa: ANN001
    engine = create_engine(f"sqlite:///{path}", future=True)
    event.listen(engine, "connect", _configure_sqlite)
    return engine


def _seed(app, owner, *, mode="success") -> str:  # noqa: ANN001
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input={"mode": mode, "note": "race", "items": ["a"]},
        idempotency_key=uuid.uuid4().hex,
        initiator="user",
    )
    return run["id"]


def test_two_workers_never_double_claim(app, owner, db):
    """100 rounds: two threads race; a step is never claimed by both."""
    enable_canary(app)
    clock = FakeClock()
    worker_a = HarnessDispatcher(
        worker_id="worker-a", state_service=app.state, config_service=app.config_service
    )
    worker_b = HarnessDispatcher(
        worker_id="worker-b", state_service=app.state, config_service=app.config_service
    )
    engine_a = _engine(db)
    engine_b = _engine(db)

    def claim(worker, engine, results, ready) -> None:  # noqa: ANN001
        ready.wait()
        with Session(engine) as session:
            claimed = worker.claim_due(session, now_us=clock.utc_epoch_us())
            session.commit()
            results.append(claimed)

    for round_no in range(100):
        run_id = _seed(app, owner)
        with session_scope() as session:
            rows = session.execute(steps.select().where(steps.c.run_id == run_id)).mappings().all()
            ready_steps = [row for row in rows if row["state"] == "ready"]
            assert ready_steps, "seeded run has no ready step"
            keep = ready_steps[0]["id"]
            for row in rows:
                if row["id"] != keep:
                    app.state.transition_step(
                        session,
                        step_id=row["id"],
                        target=StepState.cancelled,
                        now_us=clock.utc_epoch_us(),
                        skip_reason="race test",
                    )
        results_a: list = []
        results_b: list = []
        barrier = threading.Event()
        thread_a = threading.Thread(target=claim, args=(worker_a, engine_a, results_a, barrier))
        thread_b = threading.Thread(target=claim, args=(worker_b, engine_b, results_b, barrier))
        thread_a.start()
        thread_b.start()
        barrier.set()
        thread_a.join()
        thread_b.join()
        winners = [r for r in results_a + results_b if r is not None]
        assert len(winners) == 1, f"round {round_no}: expected one winner, got {winners}"
        assert winners[0].step_id == keep, "the winner claimed the wrong step"
    engine_a.dispose()
    engine_b.dispose()


def test_many_ready_steps_distribute_without_overlap(app, owner, db):
    """Two threads claiming from a deeper queue never collide on a step."""
    enable_canary(app)
    clock = FakeClock()
    worker_a = HarnessDispatcher(
        worker_id="worker-a", state_service=app.state, config_service=app.config_service
    )
    worker_b = HarnessDispatcher(
        worker_id="worker-b", state_service=app.state, config_service=app.config_service
    )
    engine_a = _engine(db)
    engine_b = _engine(db)
    seen: set[str] = set()

    for round_no in range(30):
        _seed(app, owner)
        claimed_ids: list[str] = []

        def claim(worker, engine, barrier, collected) -> None:  # noqa: ANN001
            barrier.wait()
            with Session(engine) as session:
                claimed = worker.claim_due(session, now_us=clock.utc_epoch_us())
                session.commit()
                if claimed is not None:
                    collected.append(claimed.step_id)

        barrier = threading.Event()
        threads = [
            threading.Thread(target=claim, args=(worker_a, engine_a, barrier, claimed_ids)),
            threading.Thread(target=claim, args=(worker_b, engine_b, barrier, claimed_ids)),
        ]
        for thread in threads:
            thread.start()
        barrier.set()
        for thread in threads:
            thread.join()
        assert len(claimed_ids) == len(
            set(claimed_ids)
        ), f"round {round_no}: workers claimed the same step"
        seen.update(claimed_ids)
    engine_a.dispose()
    engine_b.dispose()


def test_claim_is_fenced_by_a_new_operator_head(app, owner):
    """A queued step cannot cross a config cutover that disables its route."""
    enable_canary(app)
    run_id = _seed(app, owner)
    with session_scope() as session:
        app.config_service.rollback(session, actor="operator", reason="close canary", now=now_iso())

    with session_scope() as session:
        assert app.dispatcher.claim_due(session, now_us=app.clock.utc_epoch_us()) is None
        rows = session.execute(steps.select().where(steps.c.run_id == run_id)).mappings().all()
    assert rows and all(row["state"] != StepState.leased.value for row in rows)
    assert any(row["state"] == StepState.ready.value for row in rows)


def test_old_run_can_claim_after_same_workflow_revision_cutover(app, owner):
    """A new authorized head does not strand runs on an older snapshot."""
    enable_canary(app)
    run_id = _seed(app, owner)
    enable_canary(app)  # Same workflow/version, new policy snapshot.
    with session_scope() as session:
        claimed = app.dispatcher.claim_due(session, now_us=app.clock.utc_epoch_us())
    assert claimed is not None and claimed.run_id == run_id


def test_claim_is_fenced_when_route_moves_to_legacy(app, owner):
    """A route moved back to its legacy writer cannot claim old work."""
    enable_canary(app)
    run_id = _seed(app, owner)
    with session_scope() as session:
        head = app.config_service.current(session)
        snapshot = HarnessConfigSnapshot(
            gates={
                "harness_enabled": True,
                "dispatcher_enabled": True,
                "canary_enabled": True,
                "agent_steps_enabled": False,
                "domain_publish_enabled": False,
                "fulltext_enabled": False,
                "desktop_bridge_enabled": False,
                "experiments_enabled": False,
            },
            routes=(
                WorkflowRoute(
                    workflow_key="harness.canary",
                    active_version=1,
                    activation_state=ActivationState.active,
                    execution_mode=ExecutionMode.legacy,
                ),
            ),
            actor="operator",
            reason="return canary to legacy route",
        )
        app.config_service.apply(
            session,
            snapshot=snapshot,
            expected_head_revision=head["current_revision_id"],
            actor="operator",
            reason="return canary to legacy route",
            now=now_iso(),
        )
    with session_scope() as session:
        assert app.dispatcher.claim_due(session, now_us=app.clock.utc_epoch_us()) is None
        rows = session.execute(steps.select().where(steps.c.run_id == run_id)).mappings().all()
    assert rows and any(row["state"] == StepState.ready.value for row in rows)


def test_reaper_uses_state_service_and_events(app, owner):
    """Lease recovery updates both rows and emits the durable transitions."""
    enable_canary(app)
    run_id = _seed(app, owner)
    with session_scope() as session:
        claimed = app.dispatcher.claim_due(session, now_us=app.clock.utc_epoch_us())
    assert claimed is not None
    with session_scope() as session:
        app.state.transition_attempt(
            session,
            attempt_id=claimed.attempt_id,
            target=AttemptState.running,
            now_us=app.clock.utc_epoch_us(),
        )
        app.state.transition_step(
            session,
            step_id=claimed.step_id,
            target=StepState.running,
            now_us=app.clock.utc_epoch_us(),
        )
    with session_scope() as session:
        abandoned = app.dispatcher.reap_expired(
            session,
            now_us=app.clock.utc_epoch_us()
            + int(app.dispatcher.lease_seconds * 1_000_000)
            + 1,
        )
    assert [item["id"] for item in abandoned] == [claimed.attempt_id]
    steps_for_run = app.steps_for(scope=owner, run_id=run_id)
    start = next(row for row in steps_for_run if row["definition_step_key"] == "start")
    assert start["state"] == StepState.indeterminate.value
    event_types = {
        event.event_type
        for event in app.replay_events(scope=owner, run_id=run_id, after_seq=0, limit=200)
    }
    assert "attempt.abandoned" in event_types
    assert "step.indeterminate" in event_types


def test_heartbeat_renews_step_lease_and_reaper_does_not_kill_it(app, owner):
    enable_canary(app)
    worker = HarnessDispatcher(
        worker_id="worker-heartbeat",
        lease_seconds=10,
        heartbeat_seconds=2,
        state_service=app.state,
        config_service=app.config_service,
    )
    run_id = _seed(app, owner)
    with session_scope() as session:
        claimed = worker.claim_due(session, now_us=app.clock.utc_epoch_us())
    assert claimed is not None
    original_expiry = app.clock.utc_epoch_us() + 10 * 1_000_000
    renewed_at = app.clock.utc_epoch_us() + 5 * 1_000_000
    with session_scope() as session:
        assert worker.heartbeat(session, attempt_id=claimed.attempt_id, now_us=renewed_at)
    with session_scope() as session:
        assert worker.reap_expired(session, now_us=original_expiry) == []
    step = next(
        row
        for row in app.steps_for(scope=owner, run_id=run_id)
        if row["id"] == claimed.step_id
    )
    assert step["lease_expires_at"] == renewed_at + 10 * 1_000_000


def test_second_reaper_loses_without_duplicate_events(app, owner):
    enable_canary(app)
    run_id = _seed(app, owner)
    with session_scope() as session:
        claimed = app.dispatcher.claim_due(session, now_us=app.clock.utc_epoch_us())
    assert claimed is not None
    expired_at = app.clock.utc_epoch_us() + int(app.dispatcher.lease_seconds * 1_000_000) + 1
    with session_scope() as session:
        assert app.dispatcher.reap_expired(session, now_us=expired_at)
    with session_scope() as session:
        assert app.dispatcher.reap_expired(session, now_us=expired_at) == []
        count = session.execute(
            events_table.select().where(
                events_table.c.run_id == run_id,
                events_table.c.event_type.in_(["attempt.abandoned", "step.indeterminate"]),
            )
            ).mappings().all()
    assert len(count) == 2


def test_due_retry_uses_state_service_and_events(app, owner):
    """Retry activation is observable and atomic with its state transition."""
    enable_canary(app)
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input={"mode": "retry_then_success", "note": "retry event", "items": ["a"]},
        idempotency_key=uuid.uuid4().hex,
        initiator="user",
    )
    # The first flaky attempt schedules an immediate retry. Stop before the
    # next cycle so this test exercises activation itself.
    app.cycle()
    with session_scope() as session:
        retry = session.execute(
            steps.select().where(
                steps.c.run_id == run["id"],
                steps.c.definition_step_key == "flaky",
            )
        ).mappings().first()
    assert retry is not None and retry["state"] == StepState.retry_scheduled.value

    with session_scope() as session:
        assert app.dispatcher.activate_retries(session, now_us=app.clock.utc_epoch_us()) == 1
    with session_scope() as session:
        assert app.dispatcher.activate_retries(session, now_us=app.clock.utc_epoch_us()) == 0
    retry = next(
        row
        for row in app.steps_for(scope=owner, run_id=run["id"])
        if row["definition_step_key"] == "flaky"
    )
    assert retry["state"] == StepState.ready.value
    event_types = {
        event.event_type
        for event in app.replay_events(scope=owner, run_id=run["id"], after_seq=0, limit=200)
    }
    assert "step.ready" in event_types
    with session_scope() as session:
        ready_events = session.execute(
            events_table.select().where(
                events_table.c.run_id == run["id"],
                events_table.c.step_id == retry["id"],
                events_table.c.event_type == "step.ready",
            )
        ).mappings().all()
    assert sum('"reason": "retry_due"' in event["payload_json"] for event in ready_events) == 1
