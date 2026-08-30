"""Wiring tests for the isolated typed deterministic canary route."""

from __future__ import annotations

import pytest
from pharos.db.session import session_scope
from pharos.harness.app import HarnessApp
from pharos.harness.capabilities import CapabilityContractError
from pharos.harness.configrev import HarnessConfigSnapshot, WorkflowRoute
from pharos.harness.contracts import (
    ActivationState,
    DeliverySemantics,
    DeliveryState,
    IdempotencyKind,
    RetryClass,
)
from pharos.harness.repository import Scope, now_iso
from pharos.harness.tables import artifacts, attempts
from pharos.harness.workflows.canary import (
    CANARY_TYPED_MAPPED_SCHEMA,
    build_canary_capability_registry,
    build_executors,
    canary_capabilities,
    canary_dsh_workflow,
    canary_input,
    canary_typed_capabilities,
    canary_typed_workflow,
    canary_workflow,
)
from sqlalchemy import select

_FROZEN_V1_CAPABILITY_HASHES = {
    "canary.noop@1": "b983965d1df997a6200328aa9b09e15970859340aed188611f94c5e660592293",
    "canary.flaky@1": "65af500914374391a57040976c2fedd4d468f5c488f19f7c1c8d399f68cc6bab",
    "canary.publish@1": "5ef9e45ec10a37f27dbb9a19256ae0001433d2a23b1f2a381ceff2eb20676dee",
}


def _definition(identity: str):  # noqa: ANN202
    return next(item for item in canary_typed_capabilities() if item.identity() == identity)


def _action_payload(*, mode: str = "success", step_key: str = "start") -> dict:
    return {
        "workflow_key": "harness.canary",
        "step_key": step_key,
        "input": canary_input(mode),
    }


def _enable_typed_canary(app: HarnessApp) -> None:
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
                    active_version=3,
                    activation_state=ActivationState.active,
                    execution_mode=None,
                ),
            ),
            actor="typed-canary-test",
            reason="exercise the isolated typed capability route",
        )
        app.config_service.apply(
            session,
            snapshot=snapshot,
            expected_head_revision=(head["current_revision_id"] if head else None),
            actor="typed-canary-test",
            reason="exercise the isolated typed capability route",
            now=now_iso(),
        )


def test_v1_definitions_and_existing_workflow_hashes_remain_frozen() -> None:
    assert {
        definition.identity(): definition.definition_hash() for definition in canary_capabilities()
    } == _FROZEN_V1_CAPABILITY_HASHES
    assert canary_workflow().definition_hash() == (
        "28b38f56b1acefb62aeebece55a9c8515320f59b6cdf58fa43858f43ee9bf477"
    )
    assert canary_dsh_workflow().definition_hash() == (
        "0dc6d73a651c1eca4b29398faa78f570c80b5b94673f153120936ab5a0211dbf"
    )


def test_v3_is_fully_isolated_on_v2_capabilities() -> None:
    workflow = canary_typed_workflow()
    assert workflow.identity() == "harness.canary@3"
    assert workflow.allowed_capabilities == (
        "canary.noop@2",
        "canary.flaky@2",
        "canary.publish@2",
    )
    assert {step.capability for step in workflow.steps if step.capability is not None} == set(
        workflow.allowed_capabilities
    )


def test_v2_retry_and_publication_policies_are_explicit() -> None:
    flaky = _definition("canary.flaky@2")
    assert flaky.idempotency is IdempotencyKind.stable_key
    assert flaky.delivery is DeliverySemantics.local_exactly_once
    assert flaky.retry_classes == (RetryClass.connect_timeout_unsent,)
    publish = _definition("canary.publish@2")
    assert publish.idempotency is IdempotencyKind.stable_key
    assert publish.delivery is DeliverySemantics.local_exactly_once
    assert publish.retry_classes == (), "publication must not request a blind retry"


def test_typed_registry_validates_action_observation_and_mapped_item() -> None:
    registry = build_canary_capability_registry()
    definition = _definition("canary.noop@2")
    contract = registry.require(
        identity=definition.identity(),
        definition_sha256=definition.definition_hash(),
    )
    action = contract.create_action(_action_payload(), idempotency_key=None)
    observation = contract.succeed(action, {"ok": True, "key": None})
    assert observation.payload is not None
    assert observation.payload.value() == {
        "attempt_recovered": None,
        "key": None,
        "ok": True,
        "publication_key": None,
        "published": None,
    }

    mapped = contract.create_mapped_instance(
        definition_step_key="map_items",
        instance_key="item:x",
        stable_item_key="x",
        item_schema_identity=CANARY_TYPED_MAPPED_SCHEMA,
        item={"value": "x"},
    )
    assert mapped.item.value() == {"value": "x"}

    with pytest.raises(CapabilityContractError, match="observation payload"):
        contract.succeed(action, {"ok": True, "unknown": "smuggled"})


def test_typed_flaky_executor_returns_a_bound_definitely_unsent_error() -> None:
    definition = _definition("canary.flaky@2")
    contract = build_canary_capability_registry().require(
        identity=definition.identity(),
        definition_sha256=definition.definition_hash(),
    )
    action = contract.create_action(
        _action_payload(mode="retry_then_success", step_key="flaky"),
        idempotency_key="typed-flaky-key",
    )
    executor = build_executors()[(definition.identity(), definition.definition_hash())]
    first = executor.execute(action.model_dump(mode="json"))
    checked = contract.validate_observation(action, first)
    assert checked.status == "failed"
    assert checked.error is not None
    assert checked.error.delivery_state is DeliveryState.NOT_STARTED
    assert checked.error.retry_class is RetryClass.connect_timeout_unsent
    assert executor.execute(action.model_dump(mode="json")) == {
        "ok": True,
        "attempt_recovered": True,
    }


def test_app_registers_v3_without_activating_it_by_default(app: HarnessApp) -> None:
    assert app.registry.workflow("harness.canary@3") == canary_typed_workflow()
    assert app.executor.capability_contracts is not None
    assert app.current_snapshot().routes[0].active_version is None


def test_operator_can_explicitly_select_the_typed_route(app: HarnessApp, owner: Scope) -> None:
    _enable_typed_canary(app)
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("success"),
        idempotency_key="typed-canary-success",
        initiator="user",
    )
    assert run["workflow_version"] == 3
    for _ in range(50):
        app.cycle()
        run = app.get_run(scope=owner, run_id=run["id"])
        if run["state"] in {"succeeded", "failed", "indeterminate"}:
            break
    assert run["state"] == "succeeded"
    step_rows = app.steps_for(scope=owner, run_id=run["id"])
    assert step_rows
    assert all(step["output_artifact_id"] for step in step_rows)
    with session_scope() as session:
        artifact_rows = (
            session.execute(select(artifacts).where(artifacts.c.run_id == run["id"]))
            .mappings()
            .all()
        )
        attempt_rows = {
            row["id"]: row
            for row in session.execute(
                select(attempts).where(attempts.c.run_id == run["id"])
            )
            .mappings()
            .all()
        }
    assert len(artifact_rows) == len(step_rows)
    assert len({row["producer_attempt_id"] for row in artifact_rows}) == len(artifact_rows)
    for artifact in artifact_rows:
        attempt = attempt_rows[artifact["producer_attempt_id"]]
        assert attempt["state"] == "succeeded"
        assert attempt["input_sha256"] == artifact["input_sha256"]
        assert attempt["output_sha256"] == artifact["content_sha256"]
        for runtime_field in (
            "provider",
            "model",
            "upstream_commit",
            "runtime_session_id",
            "runtime_hash",
            "profile_hash",
            "policy_hash",
            "protocol_version",
            "route_key",
            "route_sha256",
        ):
            assert artifact[runtime_field] is None

    # A replayed worker/cycle cannot manufacture a second Artifact for an
    # already terminal Attempt.
    for _ in range(5):
        app.cycle()
    with session_scope() as session:
        replayed_ids = set(
            session.execute(
                select(artifacts.c.id).where(artifacts.c.run_id == run["id"])
            ).scalars()
        )
    assert replayed_ids == {row["id"] for row in artifact_rows}


def test_typed_route_retries_only_the_declared_unsent_failure(
    app: HarnessApp, owner: Scope
) -> None:
    _enable_typed_canary(app)
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input=canary_input("retry_then_success"),
        idempotency_key="typed-canary-retry",
        initiator="user",
    )
    for _ in range(80):
        app.cycle()
        run = app.get_run(scope=owner, run_id=run["id"])
        if run["state"] in {"succeeded", "failed", "indeterminate"}:
            break
    assert run["state"] == "succeeded"
    flaky = next(
        step
        for step in app.steps_for(scope=owner, run_id=run["id"])
        if step["definition_step_key"] == "flaky"
    )
    assert flaky["attempt_count"] == 2
    assert flaky["output_artifact_id"] is not None
    with session_scope() as session:
        flaky_attempts = (
            session.execute(
                select(attempts)
                .where(attempts.c.step_id == flaky["id"])
                .order_by(attempts.c.attempt_no)
            )
            .mappings()
            .all()
        )
        flaky_artifacts = (
            session.execute(
                select(artifacts).where(artifacts.c.step_id == flaky["id"])
            )
            .mappings()
            .all()
        )
    assert [row["state"] for row in flaky_attempts] == ["failed", "succeeded"]
    assert flaky_attempts[0]["delivery_state"] == DeliveryState.NOT_STARTED.value
    assert flaky_attempts[0]["error_code"] == "canary_transient_unsent"
    assert flaky_attempts[0]["input_sha256"] == flaky_attempts[1]["input_sha256"]
    assert len(flaky_artifacts) == 1
    assert flaky_artifacts[0]["producer_attempt_id"] == flaky_attempts[1]["id"]
