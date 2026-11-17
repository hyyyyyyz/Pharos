"""Conservation tests for multidimensional model usage admission."""

from __future__ import annotations

from typing import Any

import pytest
from pharos.db.session import session_scope
from pharos.harness.contracts import AttemptState, DeliveryState
from pharos.harness.fakes import ModelResult
from pharos.harness.repository import Scope
from pharos.harness.tables import attempts
from pharos.harness.usage import LedgerConflict
from pharos.harness.workflows.canary import canary_input
from sqlalchemy import select
from tests.harness.conftest import enable_canary


def _claim_actor(app: Any, owner: Scope, run_id: str) -> Any:
    app.dispatcher.claim_batch = 1
    for _ in range(20):
        with session_scope() as session:
            claimed = app.dispatcher.claim_due(
                session,
                now_us=app.clock.utc_epoch_us(),
                limit=1,
            )
        if claimed is None:
            continue
        if claimed.run_id == run_id and claimed.definition_step_key == "actor_turn":
            return claimed
        app.runner._execute_one(  # noqa: SLF001 - drive one isolated worker generation
            claimed=claimed,
            now_us=app.clock.utc_epoch_us(),
        )
        app.runner.reduce_all(now_us=app.clock.utc_epoch_us())
    raise AssertionError("actor_turn was not claimed")


def test_model_usage_status_conserves_output_input_cost_and_calls(app: Any, owner: Scope) -> None:
    enable_canary(app)
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("success"),
        idempotency_key="usage-multidimensional-ledger",
        initiator="operator",
    )
    with session_scope() as session:
        first = app.usage.reserve(
            session,
            scope=owner,
            run_id=run["id"],
            kind="model_tokens",
            source="system_shared",
            amount=100,
            input_tokens=200,
            cost_micros=300,
            now_us=app.clock.utc_epoch_us(),
        )
        second = app.usage.reserve(
            session,
            scope=owner,
            run_id=run["id"],
            kind="model_tokens",
            source="system_shared",
            amount=50,
            input_tokens=60,
            cost_micros=70,
            now_us=app.clock.utc_epoch_us(),
        )
        status = app.usage.budget_status(session, run_id=run["id"], kind="model_tokens")
        assert (status.pending, status.input_pending, status.cost_pending) == (150, 260, 370)
        assert status.consumed_calls == 2

        app.usage.settle(
            session,
            reservation_id=first,
            scope=owner,
            run_id=run["id"],
            actual=40,
            actual_input=80,
            actual_cost_micros=90,
            now_us=app.clock.utc_epoch_us(),
        )
        status = app.usage.budget_status(session, run_id=run["id"], kind="model_tokens")
        assert (status.committed, status.input_committed, status.cost_committed) == (40, 80, 90)
        assert (status.pending, status.input_pending, status.cost_pending) == (50, 60, 70)
        assert status.consumed_calls == 2

        app.usage.release(
            session,
            reservation_id=second,
            scope=owner,
            run_id=run["id"],
            now_us=app.clock.utc_epoch_us(),
        )
        status = app.usage.budget_status(session, run_id=run["id"], kind="model_tokens")
        assert (status.pending, status.input_pending, status.cost_pending) == (0, 0, 0)
        assert status.consumed_calls == 1


def test_usage_settlement_requires_matching_owner_fence(app: Any, owner: Scope) -> None:
    enable_canary(app)
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("success"),
        idempotency_key="usage-owner-fence",
        initiator="operator",
    )
    with session_scope() as session:
        with pytest.raises(LedgerConflict):
            app.usage.reserve(
                session,
                scope=Scope.user("another-owner"),
                run_id=run["id"],
                kind="model_tokens",
                source="system_shared",
                amount=1,
                cost_micros=0,
                now_us=app.clock.utc_epoch_us(),
            )
        reservation = app.usage.reserve(
            session,
            scope=owner,
            run_id=run["id"],
            kind="model_tokens",
            source="system_shared",
            amount=1,
            cost_micros=0,
            now_us=app.clock.utc_epoch_us(),
        )
        with pytest.raises(LedgerConflict):
            app.usage.settle(
                session,
                reservation_id=reservation,
                scope=Scope.user("another-owner"),
                run_id=run["id"],
                actual=1,
                now_us=app.clock.utc_epoch_us(),
            )


def test_runner_downshifts_context_to_the_atomic_remaining_budget(
    app: Any,
    owner: Scope,
) -> None:
    enable_canary(app, agent_steps=True)
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("agent"),
        idempotency_key="usage-remaining-context",
        initiator="operator",
    )
    claimed = _claim_actor(app, owner, run["id"])
    budget = claimed.attempt_snapshot.policy_snapshot.effective_budget
    previous_output = budget.output_tokens - 5
    previous_input = budget.input_tokens - 5
    with session_scope() as session:
        previous = app.usage.reserve(
            session,
            scope=owner,
            run_id=run["id"],
            kind="model_tokens",
            source="system_shared",
            amount=previous_output,
            input_tokens=previous_input,
            cost_micros=0,
            now_us=app.clock.utc_epoch_us(),
        )
        app.usage.settle(
            session,
            reservation_id=previous,
            scope=owner,
            run_id=run["id"],
            actual=previous_output,
            actual_input=previous_input,
            actual_cost_micros=0,
            now_us=app.clock.utc_epoch_us(),
        )

    opened: list[Any] = []

    class Handle:
        delivery_state = DeliveryState.ACKNOWLEDGED

        def __init__(self, context: Any) -> None:
            self.context = context

        def complete(self, payload: dict[str, Any]) -> ModelResult:
            del payload
            return ModelResult(
                output={
                    "ok": True,
                    "workflow": "harness.canary",
                    "step": "actor_turn",
                },
                input_tokens=1,
                output_tokens=1,
                cost_micros=0,
            )

        def cancel(self) -> None:
            return None

        def close(self) -> None:
            return None

        def retry_cleanup(self) -> None:
            return None

    class Factory:
        def open(self, context: Any) -> Handle:
            opened.append(context)
            return Handle(context)

    app.executor.gateway_factory = Factory()
    app.runner._execute_one(  # noqa: SLF001 - assert the exact runtime context
        claimed=claimed,
        now_us=app.clock.utc_epoch_us(),
    )

    assert len(opened) == 1
    assert opened[0].max_output_tokens == 5
    assert opened[0].max_input_tokens == 5
    with session_scope() as session:
        attempt = session.execute(
            select(attempts).where(attempts.c.id == claimed.attempt_id)
        ).mappings().one()
        totals = app.usage.totals(session, run_id=run["id"])
    assert attempt["state"] == AttemptState.succeeded.value
    assert totals["settled"] == previous_output + 1
    assert totals["settled_input"] == previous_input + 1
    assert totals["pending_reservations"] == 0
