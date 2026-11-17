"""Dual-worker claim races: two independent workers, one file database.

The dispatcher's claim is a single conditional UPDATE, so of N competing
workers exactly one may own each step. The race runs with two real threads on
independent SQLAlchemy engines over one file-backed database -- never
``:memory:``, which would fake the concurrency away. The loser continues the
bounded keyset scan as if the first candidate were already claimed, and never
leaks a worker-thread exception.
"""

from __future__ import annotations

import threading
import uuid

import pytest
from pharos.db.session import _configure_sqlite, session_scope
from pharos.harness.configrev import HarnessConfigSnapshot, WorkflowRoute
from pharos.harness.contracts import ActivationState, AttemptState, ExecutionMode, StepState
from pharos.harness.dispatcher import HarnessDispatcher
from pharos.harness.execution_snapshots import SnapshotIntegrityError
from pharos.harness.fakes import FakeClock
from pharos.harness.repository import now_iso
from pharos.harness.tables import attempts, config_head, config_revisions, runs, steps
from pharos.harness.tables import events as events_table
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


def test_claim_skips_legacy_run_and_reaches_snapshot_bound_run(app, owner):
    """A pre-snapshot ready row cannot hide a valid later queue entry."""
    enable_canary(app)
    with session_scope() as session:
        current = app.config_service.current_validated(session)
        assert current is not None
        workflow = app.registry.require_workflow("harness.canary@1")
        legacy = app.runner.run_repository.create(
            session,
            scope=owner,
            workflow=workflow,
            config_revision_id=current.revision_id,
            input={"mode": "success", "note": "legacy-first", "items": ["a"]},
            idempotency_key=uuid.uuid4().hex,
            initiator="user",
            now_us=app.clock.utc_epoch_us(),
        )
        assert legacy is not None
        legacy_id = legacy["id"]
        app.runner.activate_run(session, scope=owner, run=legacy, now_us=app.clock.utc_epoch_us())
        for row in session.execute(steps.select().where(steps.c.run_id == legacy_id)).mappings():
            if row["state"] == StepState.pending.value and row["depends_on_json"] in (
                None,
                "",
                "[]",
            ):
                app.state.transition_step(
                    session,
                    step_id=row["id"],
                    target=StepState.ready,
                    now_us=app.clock.utc_epoch_us(),
                    ready_at=app.clock.utc_epoch_us(),
                )
    valid = _seed(app, owner, mode="success")
    with session_scope() as session:
        claimed = app.dispatcher.claim_due(session, now_us=app.clock.utc_epoch_us())
    assert claimed is not None and claimed.run_id == valid


def test_claim_snapshot_failure_rolls_back_lease_attempt_and_counter(app, owner, monkeypatch):
    """A failed Attempt snapshot cannot leave a partially claimed Step."""
    enable_canary(app)
    run_id = _seed(app, owner, mode="success")

    def fail_snapshot(*args, **kwargs):  # noqa: ANN002, ANN003
        raise SnapshotIntegrityError("test snapshot failure")

    monkeypatch.setattr(app.dispatcher.execution_snapshots, "write_attempt", fail_snapshot)
    with session_scope() as session:
        session.execute(runs.update().where(runs.c.id == run_id).values(priority=7))
        try:
            app.dispatcher.claim_due(session, now_us=app.clock.utc_epoch_us())
        except SnapshotIntegrityError as exc:
            assert str(exc) == "test snapshot failure"
        else:
            pytest.fail("claim unexpectedly succeeded")

    with session_scope() as session:
        assert session.execute(
            runs.select().where(runs.c.id == run_id)
        ).mappings().one()["priority"] == 7
        rows = session.execute(steps.select().where(steps.c.run_id == run_id)).mappings().all()
        assert rows and all(row["state"] != StepState.leased.value for row in rows)
        assert all(row["attempt_count"] == 0 for row in rows)
        assert session.execute(attempts.select().where(attempts.c.run_id == run_id)).first() is None


def test_retry_activation_skips_legacy_run_without_snapshot(app, owner):
    """Legacy retry rows remain inert instead of re-entering the queue."""
    enable_canary(app)
    with session_scope() as session:
        current = app.config_service.current_validated(session)
        assert current is not None
        workflow = app.registry.require_workflow("harness.canary@1")
        legacy = app.runner.run_repository.create(
            session,
            scope=owner,
            workflow=workflow,
            config_revision_id=current.revision_id,
            input={"mode": "success", "note": "legacy-retry", "items": ["a"]},
            idempotency_key=uuid.uuid4().hex,
            initiator="user",
            now_us=app.clock.utc_epoch_us(),
        )
        created = app.runner.activate_run(
            session, scope=owner, run=legacy, now_us=app.clock.utc_epoch_us()
        )
        root = next(row for row in created if row["depends_on_json"] == "[]")
        session.execute(
            steps.update()
            .where(steps.c.id == root["id"])
            .values(state=StepState.retry_scheduled.value, ready_at=app.clock.utc_epoch_us())
        )
        assert app.dispatcher.activate_retries(session, now_us=app.clock.utc_epoch_us()) == 0
        row = session.execute(steps.select().where(steps.c.id == root["id"])).mappings().one()
        assert row["state"] == StepState.retry_scheduled.value


def test_claim_respects_cancel_and_pause_run_fence(app, owner):
    """A control request committed before claim wins over a stale candidate."""
    enable_canary(app)
    cancelled_id = _seed(app, owner)
    paused_id = _seed(app, owner)
    with session_scope() as session:
        now_us = app.clock.utc_epoch_us()
        session.execute(
            runs.update().where(runs.c.id == cancelled_id).values(cancel_requested_at=now_us)
        )
        session.execute(
            runs.update().where(runs.c.id == paused_id).values(pause_requested_at=now_us)
        )
        assert app.dispatcher.claim_due(session, now_us=now_us) is None


def test_old_run_can_claim_after_same_workflow_revision_cutover(app, owner):
    """A new authorized head does not strand runs on an older snapshot."""
    enable_canary(app)
    run_id = _seed(app, owner)
    enable_canary(app)  # Same workflow/version, new policy snapshot.
    with session_scope() as session:
        claimed = app.dispatcher.claim_due(session, now_us=app.clock.utc_epoch_us())
    assert claimed is not None and claimed.run_id == run_id


def test_frozen_v1_run_can_claim_after_active_route_moves_to_v2(app, owner):
    """A real v1->v2 head cutover does not strand an already-frozen run."""
    enable_canary(app)
    run_id = _seed(app, owner, mode="success")
    with session_scope() as session:
        head = app.config_service.current(session)
        assert head is not None
        snapshot = HarnessConfigSnapshot(
            gates={
                "harness_enabled": True,
                "dispatcher_enabled": True,
                "canary_enabled": True,
                # v2 is the DSH canary route, so exercise the complete gate
                # rather than accidentally testing a same-version revision.
                "agent_steps_enabled": True,
                "agent_runtime_enabled": True,
                "domain_publish_enabled": False,
                "fulltext_enabled": False,
                "desktop_bridge_enabled": False,
                "experiments_enabled": False,
            },
            routes=(
                WorkflowRoute(
                    workflow_key="harness.canary",
                    active_version=2,
                    activation_state=ActivationState.active,
                    execution_mode=None,
                ),
            ),
            actor="operator",
            reason="cut canary route to v2",
        )
        app.config_service.apply(
            session,
            snapshot=snapshot,
            expected_head_revision=head["current_revision_id"],
            actor="operator",
            reason="cut canary route to v2",
            now=now_iso(),
        )
    with session_scope() as session:
        claimed = app.dispatcher.claim_due(session, now_us=app.clock.utc_epoch_us())
    assert claimed is not None and claimed.run_id == run_id
    assert claimed.attempt_snapshot is not None
    assert claimed.attempt_snapshot.policy_snapshot.workflow_identity == "harness.canary@1"


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
                "agent_runtime_enabled": False,
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
                events_table.c.event_type.in_(["attempt.abandoned", "step.failed"]),
            )
            ).mappings().all()
    assert len(count) == 2


def test_expired_leased_attempt_retries_from_frozen_policy_and_releases_reserve(app, owner):
    enable_canary(app)
    run_id = _seed(app, owner, mode="retry_then_success")
    with session_scope() as session:
        first = app.dispatcher.claim_due(session, now_us=app.clock.utc_epoch_us())
    assert first is not None
    app.runner._execute_one(claimed=first, now_us=app.clock.utc_epoch_us())  # noqa: SLF001
    app.runner.reduce_all(now_us=app.clock.utc_epoch_us())
    with session_scope() as session:
        # Other independent canary branches can also be ready.  Make this
        # test's frozen-retry target deterministically first without relying
        # on generated UUID ordering.
        session.execute(
            steps.update()
            .where(
                steps.c.run_id == run_id,
                steps.c.definition_step_key == "flaky",
                steps.c.state == StepState.ready.value,
            )
            .values(ready_at=app.clock.utc_epoch_us() - 1)
        )
        claimed = app.dispatcher.claim_due(session, now_us=app.clock.utc_epoch_us())
        assert claimed is not None and claimed.definition_step_key == "flaky"
        reservation = app.usage.reserve(
            session,
            scope=owner,
            run_id=run_id,
            step_id=claimed.step_id,
            attempt_id=claimed.attempt_id,
            kind="model_tokens",
            source="system_shared",
            amount=10,
            cost_micros=0,
            now_us=app.clock.utc_epoch_us(),
        )
        expired_at = app.clock.utc_epoch_us() + int(app.dispatcher.lease_seconds * 1_000_000) + 1
        assert app.dispatcher.reap_expired(session, now_us=expired_at)
        row = session.execute(steps.select().where(steps.c.id == claimed.step_id)).mappings().one()
        attempt = session.execute(
            attempts.select().where(attempts.c.id == claimed.attempt_id)
        ).mappings().one()
        assert row["state"] == StepState.retry_scheduled.value
        assert row["finished_at"] is None
        assert attempt["state"] == AttemptState.abandoned.value
        assert attempt["retryable"] == 1
        assert app.usage.totals(session, run_id=run_id)["released_reservations"] == 1
        assert reservation


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


def _seed_due_retry(app, owner) -> tuple[str, str]:  # noqa: ANN001
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input={"mode": "retry_then_success", "note": "retry fence", "items": ["a"]},
        idempotency_key=uuid.uuid4().hex,
        initiator="user",
    )
    app.cycle()
    with session_scope() as session:
        retry = session.execute(
            steps.select().where(
                steps.c.run_id == run["id"],
                steps.c.definition_step_key == "flaky",
            )
        ).mappings().one()
    assert retry["state"] == StepState.retry_scheduled.value
    return run["id"], retry["id"]


def test_retry_does_not_activate_or_emit_event_when_gate_is_closed(app, owner):
    """Disabling the dispatcher leaves due retries inert and event-free."""
    enable_canary(app)
    run_id, retry_id = _seed_due_retry(app, owner)
    with session_scope() as session:
        app.config_service.rollback(
            session, actor="operator", reason="close retry gate", now=now_iso()
        )
    with session_scope() as session:
        assert app.dispatcher.activate_retries(session, now_us=app.clock.utc_epoch_us()) == 0
        retry = session.execute(steps.select().where(steps.c.id == retry_id)).mappings().one()
        ready_events = session.execute(
            events_table.select().where(
                events_table.c.run_id == run_id,
                events_table.c.step_id == retry_id,
                events_table.c.event_type == "step.ready",
            )
        ).mappings().all()
    assert retry["state"] == StepState.retry_scheduled.value
    assert not any('"reason": "retry_due"' in event["payload_json"] for event in ready_events)


def test_retry_does_not_activate_or_emit_event_on_legacy_route(app, owner):
    """Moving the route to its legacy writer also leaves due retries inert."""
    enable_canary(app)
    run_id, retry_id = _seed_due_retry(app, owner)
    with session_scope() as session:
        head = app.config_service.current(session)
        assert head is not None
        snapshot = HarnessConfigSnapshot(
            gates={
                "harness_enabled": True,
                "dispatcher_enabled": True,
                "canary_enabled": True,
                "agent_steps_enabled": False,
                "agent_runtime_enabled": False,
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
            reason="return canary retry route to legacy",
        )
        app.config_service.apply(
            session,
            snapshot=snapshot,
            expected_head_revision=head["current_revision_id"],
            actor="operator",
            reason="return canary retry route to legacy",
            now=now_iso(),
        )
    with session_scope() as session:
        assert app.dispatcher.activate_retries(session, now_us=app.clock.utc_epoch_us()) == 0
        retry = session.execute(steps.select().where(steps.c.id == retry_id)).mappings().one()
        ready_events = session.execute(
            events_table.select().where(
                events_table.c.run_id == run_id,
                events_table.c.step_id == retry_id,
                events_table.c.event_type == "step.ready",
            )
        ).mappings().all()
    assert retry["state"] == StepState.retry_scheduled.value
    assert not any('"reason": "retry_due"' in event["payload_json"] for event in ready_events)


def test_retry_cas_rechecks_run_controls_after_candidate_read(app, owner, monkeypatch):
    """A control change after the initial read still loses the retry CAS."""
    enable_canary(app)
    run_id, retry_id = _seed_due_retry(app, owner)

    with session_scope() as session:
        original_execute = session.execute
        injected = False
        now_us = app.clock.utc_epoch_us()

        def inject_cancel(statement, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            nonlocal injected
            result = original_execute(statement, *args, **kwargs)
            if not injected:
                injected = True
                original_execute(
                    runs.update().where(runs.c.id == run_id).values(cancel_requested_at=now_us)
                )
            return result

        monkeypatch.setattr(session, "execute", inject_cancel)
        assert app.state.activate_retry_cas(session, step_id=retry_id, now_us=now_us) is False
        original_execute(
            runs.update().where(runs.c.id == run_id).values(cancel_requested_at=None)
        )

    # Repeat the same post-read injection for pause and the configuration head
    # predicate. Each mutation is restored in the same transaction so this
    # test cannot alter the fixture's active fence for later assertions.
    with session_scope() as session:
        original_execute = session.execute
        injected = False

        def inject_pause(statement, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            nonlocal injected
            result = original_execute(statement, *args, **kwargs)
            if not injected:
                injected = True
                original_execute(
                    runs.update().where(runs.c.id == run_id).values(pause_requested_at=now_us)
                )
            return result

        monkeypatch.setattr(session, "execute", inject_pause)
        assert app.state.activate_retry_cas(session, step_id=retry_id, now_us=now_us) is False
        original_execute(
            runs.update().where(runs.c.id == run_id).values(pause_requested_at=None)
        )

    with session_scope() as session:
        current = app.config_service.current(session)
        assert current is not None
        alternate_revision_id = session.execute(
            config_revisions.select()
            .where(config_revisions.c.id != current["current_revision_id"])
            .limit(1)
        ).scalar_one()
        original_execute = session.execute
        injected = False

        def inject_head(statement, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            nonlocal injected
            result = original_execute(statement, *args, **kwargs)
            if not injected:
                injected = True
                original_execute(
                    config_head.update()
                    .where(config_head.c.head_key == "singleton")
                    .values(current_revision_id=alternate_revision_id)
                )
            return result

        monkeypatch.setattr(session, "execute", inject_head)
        assert (
            app.state.activate_retry_cas(
                session,
                step_id=retry_id,
                now_us=now_us,
                config_revision_id=current["current_revision_id"],
            )
            is False
        )
        original_execute(
            config_head.update()
            .where(config_head.c.head_key == "singleton")
            .values(current_revision_id=current["current_revision_id"])
        )

    with session_scope() as session:
        retry = session.execute(steps.select().where(steps.c.id == retry_id)).mappings().one()
    assert retry["state"] == StepState.retry_scheduled.value
