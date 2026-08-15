"""Dual-worker claim races: two independent workers, one file database.

The dispatcher's claim is a single conditional UPDATE, so of N competing
workers exactly one may own each step. The race runs with two real threads on
independent SQLAlchemy engines over one file-backed database -- never
``:memory:``, which would fake the concurrency away. The loser must either
lose the CAS race (typed LeaseConflictError) or find nothing left to claim.
"""

from __future__ import annotations

import threading
import uuid

from pharos.db.session import _configure_sqlite, session_scope
from pharos.harness.contracts import LeaseConflictError, StepState
from pharos.harness.dispatcher import HarnessDispatcher
from pharos.harness.fakes import FakeClock
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
    worker_a = HarnessDispatcher(worker_id="worker-a", state_service=app.state)
    worker_b = HarnessDispatcher(worker_id="worker-b", state_service=app.state)
    engine_a = _engine(db)
    engine_b = _engine(db)

    def claim(worker, engine, results, ready) -> None:  # noqa: ANN001
        ready.wait()
        try:
            with Session(engine) as session:
                claimed = worker.claim_due(session, now_us=clock.utc_epoch_us())
                session.commit()
                results.append(claimed)
        except LeaseConflictError:
            results.append(None)

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
    worker_a = HarnessDispatcher(worker_id="worker-a", state_service=app.state)
    worker_b = HarnessDispatcher(worker_id="worker-b", state_service=app.state)
    engine_a = _engine(db)
    engine_b = _engine(db)
    seen: set[str] = set()

    for round_no in range(30):
        run_id = _seed(app, owner)
        with session_scope() as session:
            rows = session.execute(steps.select().where(steps.c.run_id == run_id)).mappings().all()
            ready = [row for row in rows if row["state"] == "ready"]
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
