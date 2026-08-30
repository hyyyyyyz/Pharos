"""H1.5 claim-only DSH canary gates.

These tests deliberately stop at the dispatcher/runner seam: v2 may claim
only with the runtime gate, and this slice must never route it to the fake
gateway or spawn a sidecar.
"""

from __future__ import annotations

import json

import pytest
from pharos.db.session import session_scope
from pharos.harness.app import HarnessApp
from pharos.harness.configrev import HarnessConfigSnapshot, WorkflowRoute, validate_snapshot
from pharos.harness.contracts import ActivationState, PolicyDeniedError, StepState
from pharos.harness.definitions import RoleDefinition
from pharos.harness.repository import Scope, now_iso
from pharos.harness.tables import attempts, runs, steps
from pharos.harness.workflows.canary import resolve_canary_model_profile
from sqlalchemy.exc import IntegrityError
from tests.harness.conftest import enable_canary


def _activate(app: HarnessApp, *, version: int, runtime: bool) -> None:
    with session_scope() as session:
        head = app.config_service.current(session)
        assert head is not None
        snapshot = HarnessConfigSnapshot(
            gates={
                "harness_enabled": True,
                "dispatcher_enabled": True,
                "canary_enabled": True,
                "agent_steps_enabled": True,
                "agent_runtime_enabled": runtime,
                "domain_publish_enabled": False,
                "fulltext_enabled": False,
                "desktop_bridge_enabled": False,
                "experiments_enabled": False,
            },
            routes=(
                WorkflowRoute(
                    workflow_key="harness.canary",
                    active_version=version,
                    activation_state=ActivationState.active,
                ),
            ),
            actor="test-operator",
            reason="test DSH canary gate",
        )
        app.config_service.apply(
            session,
            snapshot=snapshot,
            expected_head_revision=head["current_revision_id"],
            actor="test-operator",
            reason="test DSH canary gate",
            now=now_iso(),
        )
        session.commit()


def _agent_run(app: HarnessApp, owner: Scope, key: str):
    return app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input={"mode": "agent", "note": "test"},
        idempotency_key=key,
        initiator="operator",
    )


def test_v1_fake_role_executes_without_runtime_gate(app: HarnessApp, owner: Scope) -> None:
    enable_canary(app, agent_steps=True)
    run = _agent_run(app, owner, "dsh-gate-v1")
    app.dispatcher.claim_batch = 8
    app.runner.tick(now_us=app.clock.utc_epoch_us())
    actor = next(
        row
        for row in app.steps_for(scope=owner, run_id=run["id"])
        if row["definition_step_key"] == "actor_turn"
    )
    # The broad tick reaches the actor and uses the existing fake gateway.
    assert actor["state"] == StepState.succeeded.value
    assert len(app.fake_model.calls) == 1


def test_v2_dsh_role_requires_runtime_gate_at_creation(
    app: HarnessApp, owner: Scope
) -> None:
    _activate(app, version=2, runtime=False)
    with pytest.raises(PolicyDeniedError, match="agent_runtime_enabled"):
        _agent_run(app, owner, "dsh-gate-v2-create-off")


def test_v2_dsh_role_does_not_claim_after_runtime_gate_is_cut(
    app: HarnessApp, owner: Scope
) -> None:
    _activate(app, version=2, runtime=True)
    run = _agent_run(app, owner, "dsh-gate-v2-off")
    _activate(app, version=2, runtime=False)
    app.dispatcher.claim_batch = 2
    app.runner.tick(now_us=app.clock.utc_epoch_us())
    actor = next(
        row
        for row in app.steps_for(scope=owner, run_id=run["id"])
        if row["definition_step_key"] == "actor_turn"
    )
    assert actor["state"] == StepState.ready.value
    with session_scope() as session:
        actor_attempts = session.execute(
            attempts.select().where(attempts.c.step_id == actor["id"])
        ).mappings().all()
    assert actor_attempts == []


def test_v2_claim_is_allowed_after_gate_but_does_not_use_fake_gateway(
    app: HarnessApp, owner: Scope
) -> None:
    _activate(app, version=2, runtime=True)
    run = _agent_run(app, owner, "dsh-gate-v2-on")
    app.dispatcher.claim_batch = 2
    app.runner.tick(now_us=app.clock.utc_epoch_us())
    app.runner.tick(now_us=app.clock.utc_epoch_us())
    actor = next(
        row
        for row in app.steps_for(scope=owner, run_id=run["id"])
        if row["definition_step_key"] == "actor_turn"
    )
    assert actor["state"] == StepState.waiting_for_input.value
    assert actor["lease_owner"] is None
    assert actor["lease_expires_at"] is None
    assert app.get_run(scope=owner, run_id=run["id"])["state"] == "waiting_for_input"
    assert len(app.fake_model.calls) == 0
    with session_scope() as session:
        attempt = session.execute(
            attempts.select().where(attempts.c.step_id == actor["id"])
        ).mappings().one()
    assert attempt["state"] == "blocked"


def test_claim_rejects_tampered_role_definition(app: HarnessApp, owner: Scope) -> None:
    enable_canary(app, agent_steps=True)
    run = _agent_run(app, owner, "dsh-bad-definition")
    app.dispatcher.claim_batch = 2
    app.runner.tick(now_us=app.clock.utc_epoch_us())
    actor = next(
        row
        for row in app.steps_for(scope=owner, run_id=run["id"])
        if row["definition_step_key"] == "actor_turn"
    )
    with session_scope() as session:
        session.execute(
            steps.update()
            .where(steps.c.id == actor["id"])
            .values(definition_json=json.dumps({"kind": "agent", "role": "unknown@9"}))
        )
    with session_scope() as session:
        assert app.dispatcher.claim_due(session, now_us=app.clock.utc_epoch_us(), limit=1) is None
        assert session.execute(
            attempts.select().where(attempts.c.step_id == actor["id"])
        ).mappings().all() == []


def test_claim_rejects_definition_with_a_missing_null_field(
    app: HarnessApp, owner: Scope
) -> None:
    """Absent and explicit-null fields are not the same frozen definition."""
    enable_canary(app, agent_steps=True)
    run = _agent_run(app, owner, "dsh-missing-definition-field")
    app.dispatcher.claim_batch = 2
    app.runner.tick(now_us=app.clock.utc_epoch_us())
    actor = next(
        row
        for row in app.steps_for(scope=owner, run_id=run["id"])
        if row["definition_step_key"] == "actor_turn"
    )
    definition = json.loads(actor["definition_json"])
    assert definition.pop("approval_on_reject") is None
    with session_scope() as session:
        session.execute(
            steps.update()
            .where(steps.c.id == actor["id"])
            .values(definition_json=json.dumps(definition))
        )
    with session_scope() as session:
        assert app.dispatcher.claim_due(session, now_us=app.clock.utc_epoch_us(), limit=1) is None
        assert session.execute(
            attempts.select().where(attempts.c.step_id == actor["id"])
        ).mappings().all() == []


@pytest.mark.parametrize(
    "mutation",
    [
        {"approval_required": True},
        {"timeout_seconds": 29},
        {"depends_on": ["start"]},
        {"budget": {"wall_seconds": 1}},
        {"retry": {"max_attempts": 1}},
        {"unexpected": True},
        {"expand_items": ["not-allowed-on-agent"]},
    ],
)
def test_claim_rejects_any_security_contract_mutation(
    app: HarnessApp, owner: Scope, mutation: dict
) -> None:
    enable_canary(app, agent_steps=True)
    run = _agent_run(app, owner, "dsh-contract-mutation-" + next(iter(mutation)))
    app.dispatcher.claim_batch = 2
    app.runner.tick(now_us=app.clock.utc_epoch_us())
    actor = next(
        row
        for row in app.steps_for(scope=owner, run_id=run["id"])
        if row["definition_step_key"] == "actor_turn"
    )
    with session_scope() as session:
        definition = json.loads(actor["definition_json"])
        definition.update(mutation)
        session.execute(
            steps.update()
            .where(steps.c.id == actor["id"])
            .values(definition_json=json.dumps(definition))
        )
    with session_scope() as session:
        assert app.dispatcher.claim_due(session, now_us=app.clock.utc_epoch_us(), limit=1) is None
        assert session.execute(
            attempts.select().where(attempts.c.step_id == actor["id"])
        ).mappings().all() == []


def test_run_snapshot_freezes_the_definition_hash(app: HarnessApp, owner: Scope) -> None:
    enable_canary(app, agent_steps=True)
    run = _agent_run(app, owner, "dsh-run-hash-mismatch")
    with pytest.raises(
        IntegrityError, match="identity or policy is frozen"
    ), session_scope() as session:
        session.execute(
            runs.update()
            .where(runs.c.id == run["id"])
            .values(definition_sha256="0" * 64)
        )


def test_canary_hooks_are_version_identity_keyed(app: HarnessApp) -> None:
    assert set(app.executor.expanders) == {
        "harness.canary@1",
        "harness.canary@2",
        "harness.canary@3",
    }
    assert set(app.executor.run_reducers) == {
        "harness.canary@1",
        "harness.canary@2",
        "harness.canary@3",
    }
    assert app.registry.require_workflow("harness.canary@2").step("actor_turn").role == (
        "canary_dsh_actor@1"
    )


def test_runtime_kind_is_required_and_profile_resolution_is_trusted() -> None:
    with pytest.raises(ValueError):
        RoleDefinition(
            role_key="missing-runtime",
            version=1,
            prompt_template_version="p@1",
            input_schema="in@1",
            output_schema="out@1",
            model_profile="pharos-fake-canary@1",
        )
    assert resolve_canary_model_profile("pharos-fake-canary@1") == (
        "pharos-fake",
        "pharos-fake-canary",
    )


def test_v2_rejects_non_agent_input_before_run_is_created(app: HarnessApp, owner: Scope) -> None:
    _activate(app, version=2, runtime=True)
    with pytest.raises(ValueError, match="only accepts agent mode"):
        app.create_run(
            scope=owner,
            workflow_key="harness.canary",
            input={"mode": "success", "note": "test"},
            idempotency_key="dsh-non-agent",
            initiator="operator",
        )


def test_null_mode_still_requires_a_known_active_version(app: HarnessApp) -> None:
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
                active_version=999,
                activation_state=ActivationState.active,
            ),
        ),
    )
    assert any(
        "unknown version 999" in error
        for error in validate_snapshot(snapshot, app.registry)
    )


def test_claim_keyset_scans_past_eight_bad_ready_rows(
    app: HarnessApp, owner: Scope
) -> None:
    """Gate-off/tampered rows cannot starve a later valid row."""
    enable_canary(app, agent_steps=True)
    runs = [_agent_run(app, owner, f"keyset-{index}") for index in range(9)]
    actor_ids = []
    with session_scope() as session:
        for run in runs:
            actor = next(
                row
                for row in app.runner.step_repository.for_run(
                    session, scope=owner, run_id=run["id"]
                )
                if row["definition_step_key"] == "actor_turn"
            )
            actor_ids.append(actor["id"])
            # This fixture only tests queue scanning. Move the actor to ready
            # without executing the preceding deterministic spine.
            app.state.transition_step(
                session,
                step_id=actor["id"],
                target=StepState.ready,
                now_us=app.clock.utc_epoch_us(),
                ready_at=app.clock.utc_epoch_us(),
            )
        # Keep the queue focused on these nine actor candidates; roots are
        # ready immediately after run activation and must not win the claim.
        session.execute(
            steps.update()
            .where(steps.c.run_id.in_([run["id"] for run in runs]))
            .where(steps.c.id.not_in(actor_ids))
            .values(state=StepState.skipped.value, ready_at=None)
        )
        ordered = sorted(actor_ids)
        session.execute(
            steps.update()
            .where(steps.c.id.in_(ordered[:8]))
            .values(definition_json=json.dumps({"kind": "agent", "role": "unknown@9"}))
        )
        claimed = app.dispatcher.claim_due(session, now_us=app.clock.utc_epoch_us(), limit=1)
        assert claimed is not None
        assert claimed.step_id == ordered[8]
        assert claimed.role == "canary_actor@1"
        assert claimed.role_definition_sha256 == app.registry.require_role(
            "canary_actor@1"
        ).definition_hash()
