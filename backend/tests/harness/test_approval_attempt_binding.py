"""Approval grants are consumable only by their exact running generation."""

from __future__ import annotations

from threading import Event, Thread

import pytest
from pharos.db.session import _configure_sqlite, session_scope
from pharos.harness.contracts import ApprovalConflictError, ApprovalState, RunState
from pharos.harness.repository import Scope
from pharos.harness.tables import runs, steps
from pharos.harness.workflows.canary import canary_input
from sqlalchemy import create_engine, event, update
from sqlalchemy.orm import Session
from tests.harness.conftest import enable_canary


def _active_attempt(app, owner: Scope, key: str):
    enable_canary(app, agent_steps=True)
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("agent"),
        idempotency_key=key,
        initiator="operator",
    )
    app.dispatcher.claim_batch = 1
    claimed = None
    for _ in range(20):
        with session_scope() as session:
            candidate = app.dispatcher.claim_due(session, now_us=app.clock.utc_epoch_us(), limit=1)
        if candidate is None:
            continue
        if candidate.definition_step_key == "actor_turn":
            claimed = candidate
            break
        app.runner._execute_one(  # noqa: SLF001 - establish a real current generation
            claimed=candidate, now_us=app.clock.utc_epoch_us()
        )
        app.runner.reduce_all(now_us=app.clock.utc_epoch_us())
    assert claimed is not None
    with session_scope() as session:
        started = app.state.start_attempt_cas(
            session,
            scope=owner,
            run_id=run["id"],
            step_id=claimed.step_id,
            attempt_id=claimed.attempt_id,
            attempt_no=claimed.attempt_no,
            lease_owner=claimed.lease_owner,
            now_us=app.clock.utc_epoch_us(),
        )
    assert started
    return run, claimed


def _approved(app, owner: Scope, run: dict, claimed) -> dict:  # noqa: ANN001
    with session_scope() as session:
        approval = app.approvals.request(
            session,
            scope=owner,
            run_id=run["id"],
            step_id=claimed.step_id,
            requesting_attempt_id=claimed.attempt_id,
            action="harness.test.consume",
            resource={"run_id": run["id"], "step_id": claimed.step_id},
            request={"mode": "test"},
            effect_summary={"writes": "none"},
            now_us=app.clock.utc_epoch_us(),
            expires_at_us=app.clock.utc_epoch_us() + 1_000_000,
        )
        approval = app.approvals.decide(
            session,
            scope=owner,
            approval_id=approval["id"],
            decision=ApprovalState.approved,
            resolver_user_id=owner.scope_id,
            reason="test",
            now_us=app.clock.utc_epoch_us(),
        )
    return approval


def _pending(app, owner: Scope, run: dict, claimed, *, expires_at: int) -> dict:  # noqa: ANN001
    with session_scope() as session:
        return app.approvals.request(
            session,
            scope=owner,
            run_id=run["id"],
            step_id=claimed.step_id,
            requesting_attempt_id=claimed.attempt_id,
            action="harness.test.decide",
            resource={"run_id": run["id"], "step_id": claimed.step_id},
            request={"mode": "test"},
            effect_summary={"writes": "none"},
            now_us=app.clock.utc_epoch_us(),
            expires_at_us=expires_at,
        )


def _consume_kwargs(owner: Scope, run: dict, claimed, approval: dict) -> dict:  # noqa: ANN001
    return {
        "scope": owner,
        "approval_id": approval["id"],
        "run_id": run["id"],
        "step_id": claimed.step_id,
        "attempt_id": claimed.attempt_id,
        "attempt_no": claimed.attempt_no,
        "lease_owner": claimed.lease_owner,
        "request_hash": approval["request_hash"],
        "now_us": 2,
    }


def test_consume_for_attempt_succeeds_once_and_rejects_repeat(app, owner: Scope) -> None:
    run, claimed = _active_attempt(app, owner, "approval-attempt-success")
    approval = _approved(app, owner, run, claimed)
    kwargs = _consume_kwargs(owner, run, claimed, approval)
    with session_scope() as session:
        app.approvals.consume_for_attempt(session, **kwargs)
    with session_scope() as session, pytest.raises(ApprovalConflictError):
        app.approvals.consume_for_attempt(session, **kwargs)
    with session_scope() as session:
        consumed = app.approvals.get(session, scope=owner, approval_id=approval["id"])
    assert consumed is not None
    assert consumed["consumed_by_attempt_id"] == claimed.attempt_id


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "wrong-run"),
        ("step_id", "wrong-step"),
        ("attempt_id", "wrong-attempt"),
        ("attempt_no", 99),
        ("lease_owner", "wrong-owner"),
        ("request_hash", "0" * 64),
    ],
)
def test_consume_for_attempt_rejects_wrong_generation_identity(
    app, owner: Scope, field: str, value
) -> None:  # noqa: ANN001
    run, claimed = _active_attempt(app, owner, f"approval-attempt-wrong-{field}")
    approval = _approved(app, owner, run, claimed)
    kwargs = _consume_kwargs(owner, run, claimed, approval)
    kwargs[field] = value
    with session_scope() as session, pytest.raises(ApprovalConflictError):
        app.approvals.consume_for_attempt(session, **kwargs)
    with session_scope() as session:
        assert (
            app.approvals.get(session, scope=owner, approval_id=approval["id"])[
                "consumed_by_attempt_id"
            ]
            is None
        )


def test_consume_for_attempt_rejects_foreign_scope_cancel_terminal_and_stale_step(
    app, owner: Scope
) -> None:
    run, claimed = _active_attempt(app, owner, "approval-attempt-fences")
    approval = _approved(app, owner, run, claimed)

    foreign_scope = Scope.user("stranger000000000000000000000001")
    foreign = _consume_kwargs(foreign_scope, run, claimed, approval)
    with session_scope() as session, pytest.raises(ApprovalConflictError):
        app.approvals.consume_for_attempt(session, **foreign)

    with session_scope() as session:
        session.execute(
            update(runs)
            .where(runs.c.id == run["id"])
            .values(cancel_requested_at=app.clock.utc_epoch_us())
        )
    with session_scope() as session, pytest.raises(ApprovalConflictError):
        app.approvals.consume_for_attempt(session, **_consume_kwargs(owner, run, claimed, approval))

    # Restore cancellation for an independent terminal fence check is not
    # needed: a cancelled Run is already nonterminal-fence negative.  Approval
    # remains unconsumed, proving the failed CAS had no side effect.
    with session_scope() as session:
        session.execute(
            update(runs)
            .where(runs.c.id == run["id"])
            .values(state=RunState.succeeded.value, cancel_requested_at=None)
        )
    with session_scope() as session, pytest.raises(ApprovalConflictError):
        app.approvals.consume_for_attempt(session, **_consume_kwargs(owner, run, claimed, approval))

    # The same approved row cannot be borrowed by a successor generation.
    with session_scope() as session:
        session.execute(
            update(steps)
            .where(steps.c.id == claimed.step_id)
            .values(attempt_count=claimed.attempt_no + 1)
        )
    with session_scope() as session, pytest.raises(ApprovalConflictError):
        app.approvals.consume_for_attempt(session, **_consume_kwargs(owner, run, claimed, approval))
    with session_scope() as session:
        consumed = app.approvals.get(session, scope=owner, approval_id=approval["id"])
    assert consumed is not None and consumed["consumed_by_attempt_id"] is None


def test_consume_for_attempt_rejects_expired_grant(app, owner: Scope) -> None:
    run, claimed = _active_attempt(app, owner, "approval-attempt-expired")
    approval = _approved(app, owner, run, claimed)
    kwargs = _consume_kwargs(owner, run, claimed, approval)
    kwargs["now_us"] = approval["expires_at"]
    with session_scope() as session, pytest.raises(ApprovalConflictError):
        app.approvals.consume_for_attempt(session, **kwargs)


def test_decision_uses_exclusive_expiry_boundary(app, owner: Scope) -> None:
    run, claimed = _active_attempt(app, owner, "approval-decision-expiry")
    expires_at = app.clock.utc_epoch_us() + 10
    approval = _pending(app, owner, run, claimed, expires_at=expires_at)

    with session_scope() as session, pytest.raises(ApprovalConflictError, match="expired"):
        app.approvals.decide(
            session,
            scope=owner,
            approval_id=approval["id"],
            decision=ApprovalState.approved,
            resolver_user_id=owner.scope_id,
            reason="too late",
            now_us=expires_at,
        )
    with session_scope() as session:
        assert app.approvals.expire_outstanding(session, now_us=expires_at) == 1
        expired = app.approvals.require(session, scope=owner, approval_id=approval["id"])
    assert expired["state"] == ApprovalState.expired.value


def test_cancel_atomically_invalidates_pending_grants(app, owner: Scope) -> None:
    run, claimed = _active_attempt(app, owner, "approval-cancel-pending")
    approval = _pending(
        app,
        owner,
        run,
        claimed,
        expires_at=app.clock.utc_epoch_us() + 1_000_000,
    )

    app.cancel(scope=owner, run_id=run["id"])

    with session_scope() as session:
        cancelled = app.approvals.require(session, scope=owner, approval_id=approval["id"])
    assert cancelled["state"] == ApprovalState.cancelled.value
    assert app.pending_approvals(scope=owner, run_id=run["id"]) == []
    with session_scope() as session, pytest.raises(ApprovalConflictError, match="cancelled"):
        app.approvals.decide(
            session,
            scope=owner,
            approval_id=approval["id"],
            decision=ApprovalState.approved,
            resolver_user_id=owner.scope_id,
            reason="stale UI",
            now_us=app.clock.utc_epoch_us(),
        )


def test_cancel_invalidates_an_approved_but_unconsumed_grant(app, owner: Scope) -> None:
    run, claimed = _active_attempt(app, owner, "approval-cancel-approved")
    approval = _approved(app, owner, run, claimed)

    app.cancel(scope=owner, run_id=run["id"])

    with session_scope() as session:
        cancelled = app.approvals.require(session, scope=owner, approval_id=approval["id"])
    assert cancelled["state"] == ApprovalState.cancelled.value
    assert cancelled["consumed_by_attempt_id"] is None


def test_two_resolvers_cannot_overwrite_one_decision(app, owner: Scope, db) -> None:  # noqa: ANN001
    run, claimed = _active_attempt(app, owner, "approval-concurrent-decision")
    approval = _pending(
        app,
        owner,
        run,
        claimed,
        expires_at=app.clock.utc_epoch_us() + 1_000_000,
    )
    engines = [create_engine(f"sqlite:///{db}", future=True) for _ in range(2)]
    for engine in engines:
        event.listen(engine, "connect", _configure_sqlite)
    start = Event()
    outcomes: list[str] = []
    failures: list[BaseException] = []

    def decide(engine, decision: ApprovalState) -> None:  # noqa: ANN001
        start.wait()
        try:
            with Session(engine) as session:
                try:
                    app.approvals.decide(
                        session,
                        scope=owner,
                        approval_id=approval["id"],
                        decision=decision,
                        resolver_user_id=owner.scope_id,
                        reason=decision.value,
                        now_us=app.clock.utc_epoch_us(),
                    )
                    session.commit()
                    outcomes.append(decision.value)
                except ApprovalConflictError:
                    session.rollback()
                    outcomes.append("conflict")
        except BaseException as error:  # pragma: no cover - asserted below
            failures.append(error)

    workers = [
        Thread(target=decide, args=(engines[0], ApprovalState.approved)),
        Thread(target=decide, args=(engines[1], ApprovalState.rejected)),
    ]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()
    for engine in engines:
        engine.dispose()

    assert failures == []
    assert outcomes.count("conflict") == 1
    assert len(set(outcomes) & {ApprovalState.approved.value, ApprovalState.rejected.value}) == 1
    with session_scope() as session:
        resolved = app.approvals.require(session, scope=owner, approval_id=approval["id"])
    assert resolved["state"] in (ApprovalState.approved.value, ApprovalState.rejected.value)
