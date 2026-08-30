"""Owner/lease fences for the H1.5 Attempt runtime slots."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pharos.db.session as dbs
import pytest
from pharos.db.session import session_scope
from pharos.harness.contracts import AttemptState
from pharos.harness.repository import (
    AttemptRuntimeError,
    AttemptRuntimeLaunch,
    HarnessAttemptRepository,
    Scope,
)
from pharos.harness.tables import attempts, config_head, runs, steps
from pharos.harness.usage import UsageLedger

HASHES = {
    "runtime_hash": "a" * 64,
    "profile_hash": "b" * 64,
    "policy_hash": "c" * 64,
}


def _seed_attempt(
    session,
    *,
    scope_id: str = "owner",
    state: str = "running",
    suffix: str = "1",
) -> tuple[Scope, str]:
    scope = Scope.user(scope_id)
    revision = session.execute(config_head.select()).mappings().one()["current_revision_id"]
    run_id = f"run-{suffix}"
    step_id = f"step-{suffix}"
    attempt_id = f"attempt-{suffix}"
    session.execute(
        runs.insert().values(
            id=run_id,
            scope_type="user",
            scope_id=scope_id,
            user_id=None,
            workflow_key="test",
            workflow_version=1,
            definition_sha256="definition",
            config_revision_id=revision,
            state="running",
            input_json="{}",
            input_sha256="input",
            budget_json=None,
            initiator="user",
            idempotency_key=f"test-{suffix}",
            created_at=1,
            updated_at=1,
        )
    )
    session.execute(
        steps.insert().values(
            id=step_id,
            run_id=run_id,
            scope_type="user",
            scope_id=scope_id,
            definition_step_key="step",
            instance_key="__singleton__",
            step_kind="agent",
            definition_json="{}",
            state=state if state in {"running", "leased"} else "succeeded",
            depends_on_json="[]",
            input_artifact_ids_json="[]",
            attempt_count=1,
            max_attempts=3,
            lease_owner="worker-a",
            lease_expires_at=10_000,
            heartbeat_at=1,
            created_at=1,
            updated_at=1,
        )
    )
    session.execute(
        attempts.insert().values(
            id=attempt_id,
            step_id=step_id,
            run_id=run_id,
            scope_type="user",
            scope_id=scope_id,
            attempt_no=1,
            worker_id="worker-a",
            state=state,
            lease_owner="worker-a",
            started_at=1,
            heartbeat_at=1,
        )
    )
    return scope, attempt_id


def _bind(repo, session, scope, *, attempt_id="attempt-1", **overrides):
    values = {
        "scope": scope,
        "run_id": overrides.pop("run_id", "run-1"),
        "attempt_id": attempt_id,
        "step_id": overrides.pop("step_id", "step-1"),
        "attempt_no": overrides.pop("attempt_no", 1),
        "lease_owner": "worker-a",
        "expected_state": AttemptState.running,
        "now_us": 2,
        **overrides,
    }
    launch = AttemptRuntimeLaunch(
        runtime_session_id=values.pop("runtime_session_id", "session-1"),
        deadline_at=values.pop("deadline_at", 9_000),
        upstream_commit=values.pop("upstream_commit", "d" * 40),
        runtime_hash=values.pop("runtime_hash", HASHES["runtime_hash"]),
        profile_hash=values.pop("profile_hash", HASHES["profile_hash"]),
        policy_hash=values.pop("policy_hash", HASHES["policy_hash"]),
        protocol_version=values.pop("protocol_version", "pharos.dsh.stdio@1"),
    )
    return repo.reserve_runtime_launch(session, launch=launch, **values)


def _attach(repo, session, scope, *, attempt_id="attempt-1", **overrides):
    values = {
        "scope": scope,
        "run_id": overrides.pop("run_id", "run-1"),
        "attempt_id": attempt_id,
        "step_id": overrides.pop("step_id", "step-1"),
        "attempt_no": overrides.pop("attempt_no", 1),
        "lease_owner": overrides.pop("lease_owner", "worker-a"),
        "expected_state": overrides.pop("expected_state", AttemptState.running),
        "now_us": overrides.pop("now_us", 2),
        "child_pid": overrides.pop("child_pid", 1234),
        **overrides,
    }
    return repo.attach_child_process(session, **values)


def _transition(repo, session, scope, attempt_id, delivery_state, **overrides):
    values = {
        "scope": scope,
        "run_id": overrides.pop("run_id", "run-1"),
        "attempt_id": attempt_id,
        "step_id": overrides.pop("step_id", "step-1"),
        "attempt_no": overrides.pop("attempt_no", 1),
        "lease_owner": overrides.pop("lease_owner", "worker-a"),
        "expected_state": overrides.pop("expected_state", AttemptState.running),
        "now_us": overrides.pop("now_us", 2),
        "delivery_state": delivery_state,
        **overrides,
    }
    return repo.transition_delivery(session, **values)


def _record_reaped(repo, session, scope, *, attempt_id="attempt-1", **overrides):
    values = {
        "scope": scope,
        "run_id": overrides.pop("run_id", "run-1"),
        "attempt_id": attempt_id,
        "step_id": overrides.pop("step_id", "step-1"),
        "attempt_no": overrides.pop("attempt_no", 1),
        "runtime_session_id": overrides.pop("runtime_session_id", "session-1"),
        "child_pid": overrides.pop("child_pid", 1234),
        **overrides,
    }
    return repo.record_child_reaped(session, **values)


def test_bind_is_complete_immutable_and_delivery_is_monotonic(app):
    repo = HarnessAttemptRepository()
    with session_scope() as session:
        scope, attempt_id = _seed_attempt(session)
        row = _bind(repo, session, scope, attempt_id=attempt_id)
        assert row is not None and row["delivery_state"] == "not_started"
        assert _attach(repo, session, scope, attempt_id=attempt_id)
        assert _transition(repo, session, scope, attempt_id, "sent")
        assert _transition(repo, session, scope, attempt_id, "acknowledged")
        with pytest.raises(AttemptRuntimeError, match="illegal delivery"):
            _transition(repo, session, scope, attempt_id, "unknown")
        with pytest.raises(AttemptRuntimeError):
            _bind(repo, session, scope, attempt_id=attempt_id)
        with pytest.raises(AttemptRuntimeError):
            _transition(repo, session, scope, attempt_id, "sent")


def test_partial_write_cannot_be_promoted_by_the_live_worker(app):
    repo = HarnessAttemptRepository()
    with session_scope() as session:
        scope, attempt_id = _seed_attempt(session)
        _bind(repo, session, scope, attempt_id=attempt_id)
        _attach(repo, session, scope, attempt_id=attempt_id)
        with pytest.raises(AttemptRuntimeError, match="illegal delivery"):
            _transition(repo, session, scope, attempt_id, "acknowledged")
        assert _transition(repo, session, scope, attempt_id, "unknown")
        with pytest.raises(AttemptRuntimeError, match="illegal delivery"):
            _transition(repo, session, scope, attempt_id, "sent")


def test_delivery_observer_allows_complete_frame_without_partial_write(app):
    repo = HarnessAttemptRepository()
    with session_scope() as session:
        scope, attempt_id = _seed_attempt(session)
        _bind(repo, session, scope, attempt_id=attempt_id)
        _attach(repo, session, scope, attempt_id=attempt_id)
        assert _transition(repo, session, scope, attempt_id, "sent")
        assert _transition(repo, session, scope, attempt_id, "acknowledged")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("runtime_session_id", ""),
        ("runtime_session_id", "session with whitespace"),
        ("runtime_hash", ""),
        ("runtime_hash", "A" * 64),
        ("profile_hash", " "),
        ("policy_hash", ""),
        ("upstream_commit", "not-a-commit"),
        ("protocol_version", "pharos.dsh.stdio@2"),
    ],
)
def test_invalid_provenance_is_rejected(app, field, value):
    repo = HarnessAttemptRepository()
    with session_scope() as session:
        scope, attempt_id = _seed_attempt(session)
        with pytest.raises(AttemptRuntimeError):
            _bind(repo, session, scope, attempt_id=attempt_id, **{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [("deadline_at", 0), ("deadline_at", -1)],
)
def test_invalid_deadline_is_rejected(app, field, value):
    repo = HarnessAttemptRepository()
    with session_scope() as session:
        scope, attempt_id = _seed_attempt(session)
        with pytest.raises(AttemptRuntimeError):
            _bind(repo, session, scope, attempt_id=attempt_id, **{field: value})


@pytest.mark.parametrize("pid", [0, -1])
def test_invalid_child_pid_is_rejected(app, pid):
    repo = HarnessAttemptRepository()
    with session_scope() as session:
        scope, attempt_id = _seed_attempt(session)
        _bind(repo, session, scope, attempt_id=attempt_id)
        with pytest.raises(AttemptRuntimeError):
            _attach(repo, session, scope, attempt_id=attempt_id, child_pid=pid)


def test_mutation_requires_expected_state_and_live_deadline(app):
    repo = HarnessAttemptRepository()
    with session_scope() as session:
        scope, attempt_id = _seed_attempt(session)
        with pytest.raises(AttemptRuntimeError):
            _bind(repo, session, scope, attempt_id=attempt_id, expected_state=None)
        with pytest.raises(AttemptRuntimeError):
            _bind(repo, session, scope, attempt_id=attempt_id, now_us=None)
        with pytest.raises(AttemptRuntimeError):
            _bind(repo, session, scope, attempt_id=attempt_id, deadline_at=1)


def test_leased_is_an_explicitly_supported_active_generation(app):
    repo = HarnessAttemptRepository()
    with session_scope() as session:
        scope, attempt_id = _seed_attempt(session, state="leased")
        assert _bind(
            repo, session, scope, attempt_id=attempt_id, expected_state=AttemptState.leased
        )
        assert _attach(
            repo, session, scope, attempt_id=attempt_id, expected_state=AttemptState.leased
        )
        assert _transition(
            repo, session, scope, attempt_id, "unknown", expected_state=AttemptState.leased
        )
        assert [row["id"] for row in repo.list_active_runtime(session, scope=scope, now_us=2)] == [
            attempt_id
        ]


@pytest.mark.parametrize(
    ("field", "value"),
    [("run_id", "other-run"), ("step_id", "other-step"), ("attempt_no", 2)],
)
def test_mutation_requires_exact_attempt_generation(app, field, value):
    repo = HarnessAttemptRepository()
    with session_scope() as session:
        scope, attempt_id = _seed_attempt(session)
        assert _bind(repo, session, scope, attempt_id=attempt_id, **{field: value}) is None


def test_duplicate_session_and_pid_rejection_does_not_poison_outer_transaction(app):
    repo = HarnessAttemptRepository()
    with session_scope() as session:
        scope, first_id = _seed_attempt(session)
        _bind(repo, session, scope, attempt_id=first_id)
        _attach(repo, session, scope, attempt_id=first_id)
        _, second_id = _seed_attempt(session, suffix="2")
        with pytest.raises(AttemptRuntimeError, match="runtime_session_id"):
            _bind(
                repo,
                session,
                scope,
                attempt_id=second_id,
                run_id="run-2",
                step_id="step-2",
            )
        # The caller still owns a usable transaction after the rejected
        # preflight; no repository rollback/commit is allowed here.
        assert repo.list_active_runtime(session, scope=scope, attempt_id=first_id, now_us=2)
        _bind(
            repo,
            session,
            scope,
            attempt_id=second_id,
            run_id="run-2",
            step_id="step-2",
            runtime_session_id="session-2",
        )
        with pytest.raises(AttemptRuntimeError, match="child_pid"):
            _attach(
                repo,
                session,
                scope,
                attempt_id=second_id,
                run_id="run-2",
                step_id="step-2",
            )
        assert repo.list_active_runtime(session, scope=scope, attempt_id=first_id, now_us=2)


def test_attach_integrity_conflict_uses_savepoint(monkeypatch, app):
    repo = HarnessAttemptRepository()
    with session_scope() as session:
        scope, first_id = _seed_attempt(session)
        _bind(repo, session, scope, attempt_id=first_id)
        _attach(repo, session, scope, attempt_id=first_id, child_pid=9999)
        _, second_id = _seed_attempt(session, suffix="2")
        _bind(
            repo,
            session,
            scope,
            attempt_id=second_id,
            run_id="run-2",
            step_id="step-2",
            runtime_session_id="session-2",
        )

        real_execute = session.execute
        injected = False

        def execute_with_race(statement, *args, **kwargs):
            nonlocal injected
            if not injected and getattr(statement, "is_update", False):
                injected = True
                # Simulate another writer winning the child-PID uniqueness
                # race after this repository's advisory preflight.
                real_execute(
                    attempts.update().where(attempts.c.id == first_id).values(child_pid=4321)
                )
            return real_execute(statement, *args, **kwargs)

        monkeypatch.setattr(session, "execute", execute_with_race)
        with pytest.raises(AttemptRuntimeError, match="runtime identity conflict"):
            _attach(
                repo,
                session,
                scope,
                attempt_id=second_id,
                run_id="run-2",
                step_id="step-2",
                child_pid=4321,
            )
        monkeypatch.setattr(session, "execute", real_execute)
        # The savepoint rollback leaves the outer transaction usable. The
        # injected duplicate is rolled back, so a later attach can use that PID.
        assert repo.list_active_runtime(session, scope=scope, attempt_id=first_id, now_us=2)
        assert _attach(
            repo,
            session,
            scope,
            attempt_id=second_id,
            run_id="run-2",
            step_id="step-2",
            child_pid=4321,
        )


def test_file_backed_sessions_race_for_one_child_pid(app):
    repo = HarnessAttemptRepository()
    with session_scope() as session:
        scope, first_id = _seed_attempt(session)
        _, second_id = _seed_attempt(session, suffix="2")
        _bind(repo, session, scope, attempt_id=first_id)
        _bind(
            repo,
            session,
            scope,
            attempt_id=second_id,
            run_id="run-2",
            step_id="step-2",
            runtime_session_id="session-2",
        )

    barrier = threading.Barrier(2)

    def attach(attempt_id: str, run_id: str, step_id: str) -> tuple[str, bool | str]:
        assert dbs._SessionLocal is not None
        session = dbs._SessionLocal()
        try:
            barrier.wait(timeout=5)
            result = _attach(
                repo,
                session,
                scope,
                attempt_id=attempt_id,
                run_id=run_id,
                step_id=step_id,
                child_pid=4567,
            )
            session.commit()
            return "ok", result
        except AttemptRuntimeError as exc:
            session.rollback()
            return "error", str(exc)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                lambda args: attach(*args),
                (
                    (first_id, "run-1", "step-1"),
                    (second_id, "run-2", "step-2"),
                ),
            )
        )
    assert sorted(outcome[0] for outcome in outcomes) == ["error", "ok"]
    assert any("child_pid" in outcome[1] for outcome in outcomes if outcome[0] == "error")


def test_attempt_step_and_expiry_fences_reject_late_delivery(app):
    repo = HarnessAttemptRepository()
    with session_scope() as session:
        scope, attempt_id = _seed_attempt(session)
        _bind(repo, session, scope, attempt_id=attempt_id)
        _attach(repo, session, scope, attempt_id=attempt_id)
        session.execute(
            attempts.update().where(attempts.c.id == attempt_id).values(lease_owner="worker-b")
        )
        assert not _transition(repo, session, scope, attempt_id, "sent")
        session.execute(
            attempts.update().where(attempts.c.id == attempt_id).values(lease_owner="worker-a")
        )
        session.execute(steps.update().where(steps.c.id == "step-1").values(lease_owner="worker-b"))
        assert not _transition(repo, session, scope, attempt_id, "sent")
        session.execute(steps.update().where(steps.c.id == "step-1").values(lease_owner="worker-a"))
        session.execute(steps.update().where(steps.c.id == "step-1").values(lease_expires_at=1))
        assert not _transition(repo, session, scope, attempt_id, "sent")


def test_generation_rollover_and_step_state_mismatch_hide_old_launch(app):
    repo = HarnessAttemptRepository()
    with session_scope() as session:
        scope, attempt_id = _seed_attempt(session)
        _bind(repo, session, scope, attempt_id=attempt_id)
        assert repo.list_launch_recovery_candidates(session, scope=scope, now_us=2)

        # A same-worker reclaim with a new attempt generation must not attach
        # to the old reservation, even when the owner string is unchanged.
        session.execute(attempts.update().where(attempts.c.id == attempt_id).values(attempt_no=2))
        assert not _attach(repo, session, scope, attempt_id=attempt_id, attempt_no=1)
        rows = repo.list_launch_recovery_candidates(session, scope=scope, now_us=2)
        assert len(rows) == 1
        assert rows[0]["current_generation"] is False
        assert rows[0]["live_lease"] is False

        # The row remains auditable when the correlated Step no longer agrees
        # with the Attempt state, while the live-lease diagnostic is false.
        session.execute(attempts.update().where(attempts.c.id == attempt_id).values(attempt_no=1))
        session.execute(steps.update().where(steps.c.id == "step-1").values(state="succeeded"))
        rows = repo.list_launch_recovery_candidates(session, scope=scope, now_us=2)
        assert len(rows) == 1
        assert rows[0]["current_generation"] is True
        assert rows[0]["live_lease"] is False


def test_launch_recovery_scans_attached_expired_and_stale_rows(app):
    repo = HarnessAttemptRepository()
    with session_scope() as session:
        scope, attempt_id = _seed_attempt(session)
        _bind(repo, session, scope, attempt_id=attempt_id)
        rows = repo.list_launch_recovery_candidates(session, scope=scope, now_us=2)
        assert [row["id"] for row in rows] == [attempt_id]
        assert rows[0]["child_pid"] is None
        assert rows[0]["current_generation"] is True
        assert rows[0]["live_lease"] is True
        assert _attach(repo, session, scope, attempt_id=attempt_id)
        rows = repo.list_launch_recovery_candidates(session, scope=scope, now_us=2)
        assert rows[0]["child_pid"] == 1234
        session.execute(steps.update().where(steps.c.id == "step-1").values(lease_expires_at=2))
        rows = repo.list_launch_recovery_candidates(session, scope=scope, now_us=2)
        assert len(rows) == 1
        assert rows[0]["current_generation"] is True
        assert rows[0]["live_lease"] is False
        session.execute(
            attempts.update()
            .where(attempts.c.id == attempt_id)
            .values(state=AttemptState.indeterminate.value)
        )
        session.execute(steps.update().where(steps.c.id == "step-1").values(state="indeterminate"))
        rows = repo.list_launch_recovery_candidates(session, scope=scope, now_us=2)
        assert [row["id"] for row in rows] == [attempt_id]
        assert rows[0]["child_pid"] == 1234


@pytest.mark.parametrize(
    "terminal_state",
    [AttemptState.succeeded, AttemptState.failed, AttemptState.indeterminate],
)
def test_proven_reap_clears_pid_and_normal_terminal_is_not_recovered(app, terminal_state):
    repo = HarnessAttemptRepository()
    with session_scope() as session:
        scope, attempt_id = _seed_attempt(session)
        _bind(repo, session, scope, attempt_id=attempt_id)
        _attach(repo, session, scope, attempt_id=attempt_id)

        # A PID alone is not a durable process identity.  Cleanup records its
        # proof against the immutable runtime session before restart scans may
        # stop treating the child as unresolved.  An active worker must first
        # commit its terminal Attempt state, otherwise clearing the PID would
        # hide a still-running child and release the PID uniqueness fence.
        assert not _record_reaped(repo, session, scope, attempt_id=attempt_id)
        assert not _record_reaped(
            repo,
            session,
            Scope.user("stranger"),
            attempt_id=attempt_id,
        )
        assert not _record_reaped(
            repo,
            session,
            scope,
            attempt_id=attempt_id,
            runtime_session_id="wrong-session",
        )
        assert not _record_reaped(
            repo,
            session,
            scope,
            attempt_id=attempt_id,
            child_pid=4321,
        )
        session.execute(
            attempts.update().where(attempts.c.id == attempt_id).values(state=terminal_state.value)
        )
        session.execute(
            steps.update().where(steps.c.id == "step-1").values(state=terminal_state.value)
        )
        assert _record_reaped(repo, session, scope, attempt_id=attempt_id)
        row = session.execute(attempts.select().where(attempts.c.id == attempt_id)).mappings().one()
        assert row["child_pid"] is None
        assert repo.list_launch_recovery_candidates(session, scope=scope, now_us=2) == []
        assert not _record_reaped(repo, session, scope, attempt_id=attempt_id)


def test_recovery_surfaces_partial_launch_with_attached_child(app):
    repo = HarnessAttemptRepository()
    with session_scope() as session:
        scope, attempt_id = _seed_attempt(session)
        # Model the narrow crash window after spawn/PID persistence but before
        # the complete provenance/delivery transaction.  Nullable legacy or
        # interrupted rows are malformed, not safe to hide from recovery.
        session.execute(
            attempts.update()
            .where(attempts.c.id == attempt_id)
            .values(
                state=AttemptState.indeterminate.value,
                runtime_session_id="partial-session",
                child_pid=4321,
            )
        )
        session.execute(steps.update().where(steps.c.id == "step-1").values(state="indeterminate"))
        rows = repo.list_launch_recovery_candidates(session, scope=scope, now_us=2)
        assert [row["id"] for row in rows] == [attempt_id]
        assert rows[0]["child_pid"] == 4321
        assert rows[0]["runtime_hash"] is None
        assert rows[0]["live_lease"] is False
        assert _record_reaped(
            repo,
            session,
            scope,
            attempt_id=attempt_id,
            runtime_session_id="partial-session",
            child_pid=4321,
        )
        assert repo.list_launch_recovery_candidates(session, scope=scope, now_us=2) == []


@pytest.mark.parametrize(
    ("attempt_state", "step_state"),
    [
        (AttemptState.abandoned, "retry_scheduled"),
        (AttemptState.blocked, "waiting_for_input"),
        (AttemptState.timed_out, "running"),
    ],
)
def test_reap_proof_is_bound_to_terminal_attempt_not_current_step_state(
    app, attempt_state, step_state
):
    repo = HarnessAttemptRepository()
    with session_scope() as session:
        scope, attempt_id = _seed_attempt(session)
        _bind(repo, session, scope, attempt_id=attempt_id)
        _attach(repo, session, scope, attempt_id=attempt_id)
        session.execute(
            attempts.update().where(attempts.c.id == attempt_id).values(state=attempt_state.value)
        )
        session.execute(
            steps.update()
            .where(steps.c.id == "step-1")
            .values(
                state=step_state,
                attempt_count=2,
                lease_owner="worker-b" if step_state == "running" else None,
            )
        )
        assert _record_reaped(repo, session, scope, attempt_id=attempt_id)
        row = session.execute(attempts.select().where(attempts.c.id == attempt_id)).mappings().one()
        assert row["child_pid"] is None


@pytest.mark.parametrize("delivery_state", ["unknown", "sent", "acknowledged"])
@pytest.mark.parametrize(
    "terminal_state",
    [AttemptState.indeterminate, AttemptState.abandoned, AttemptState.failed],
)
def test_pending_delivery_reconciliation_is_terminal_and_scoped(
    app, delivery_state, terminal_state
):
    repo = HarnessAttemptRepository()
    with session_scope() as session:
        scope, attempt_id = _seed_attempt(session)
        _bind(repo, session, scope, attempt_id=attempt_id)
        _attach(repo, session, scope, attempt_id=attempt_id)
        UsageLedger().reserve(
            session,
            scope=scope,
            run_id="run-1",
            step_id="step-1",
            attempt_id=attempt_id,
            kind="model_tokens",
            source="system_shared",
            amount=10,
            cost_micros=0,
            now_us=2,
        )
        session.execute(
            attempts.update()
            .where(attempts.c.id == attempt_id)
            .values(
                state=terminal_state.value,
                delivery_state=delivery_state,
            )
        )
        assert [row["id"] for row in repo.list_reconciliation_candidates(session, scope=scope)] == [
            attempt_id
        ]
        assert repo.list_reconciliation_candidates(session, scope=scope)[0]["id"] == attempt_id
        assert repo.list_reconciliation_candidates(session, scope=Scope.user("stranger")) == []
        _seed_attempt(session, suffix="2")
        _seed_attempt(session, scope_id="other", suffix="3")

        # A retried old generation still owns real provider usage and remains
        # reconcilable; parent Step/Run owner lineage is the safety fence.
        session.execute(attempts.update().where(attempts.c.id == attempt_id).values(attempt_no=2))
        assert [row["id"] for row in repo.list_reconciliation_candidates(session, scope=scope)] == [
            attempt_id
        ]
        session.execute(
            attempts.update()
            .where(attempts.c.id == attempt_id)
            .values(attempt_no=1, run_id="run-2")
        )
        assert repo.list_reconciliation_candidates(session, scope=scope) == []
        session.execute(
            attempts.update()
            .where(attempts.c.id == attempt_id)
            .values(run_id="run-3", scope_id="other")
        )
        assert repo.list_reconciliation_candidates(session, scope=Scope.user("other")) == []


@pytest.mark.parametrize("delivery_state", [None, "unknown", "sent", "acknowledged"])
def test_pending_usage_is_discoverable_without_runtime_launch_metadata(app, delivery_state):
    repo = HarnessAttemptRepository()
    with session_scope() as session:
        scope, attempt_id = _seed_attempt(session)
        reservation_id = UsageLedger().reserve(
            session,
            scope=scope,
            run_id="run-1",
            step_id="step-1",
            attempt_id=attempt_id,
            kind="model_tokens",
            source="system_shared",
            amount=10,
            cost_micros=0,
            now_us=2,
        )
        session.execute(
            attempts.update()
            .where(attempts.c.id == attempt_id)
            .values(
                state=AttemptState.indeterminate.value,
                delivery_state=delivery_state,
            )
        )
        session.execute(steps.update().where(steps.c.id == "step-1").values(state="indeterminate"))
        assert [row["id"] for row in repo.list_reconciliation_candidates(session, scope=scope)] == [
            attempt_id
        ]

        # Once the exact reservation is spent, no delivery/provenance shape
        # may keep this Attempt in the reconciliation queue.
        UsageLedger().release(
            session,
            reservation_id=reservation_id,
            scope=scope,
            run_id="run-1",
            step_id="step-1",
            attempt_id=attempt_id,
            now_us=3,
        )
        assert repo.list_reconciliation_candidates(session, scope=scope) == []


def test_scope_owner_lease_and_terminal_late_updates_are_fenced(app):
    repo = HarnessAttemptRepository()
    with session_scope() as session:
        scope, attempt_id = _seed_attempt(session)
        stranger = Scope.user("stranger")
        assert _bind(repo, session, stranger, attempt_id=attempt_id) is None
        assert _bind(repo, session, scope, attempt_id=attempt_id, lease_owner="wrong") is None
        with pytest.raises(AttemptRuntimeError, match="deadline_at"):
            _bind(repo, session, scope, attempt_id=attempt_id, now_us=10_001)
        assert _bind(repo, session, scope, attempt_id=attempt_id) is not None
        assert _attach(repo, session, scope, attempt_id=attempt_id)
        session.execute(
            attempts.update().where(attempts.c.id == attempt_id).values(state="succeeded")
        )
        assert not _transition(repo, session, scope, attempt_id, "sent")


def test_active_and_restart_queries_are_scope_filtered(app):
    repo = HarnessAttemptRepository()
    with session_scope() as session:
        scope, attempt_id = _seed_attempt(session)
        _bind(repo, session, scope, attempt_id=attempt_id)
        _attach(repo, session, scope, attempt_id=attempt_id)
        assert [row["id"] for row in repo.list_active_runtime(session, scope=scope, now_us=2)] == [
            attempt_id
        ]
        assert repo.list_reconciliation_candidates(session, scope=scope) == []
        assert repo.list_active_runtime(session, scope=Scope.user("stranger"), now_us=2) == []
        assert (
            repo.list_active_runtime(session, scope=scope, attempt_id=attempt_id, now_us=2)[0]["id"]
            == attempt_id
        )
