"""Sealed DSH runtime through the application and durable Harness database."""

from __future__ import annotations

import os
from pathlib import Path

from pharos.db.session import session_scope
from pharos.harness.app import HarnessApp
from pharos.harness.configrev import HarnessConfigSnapshot, WorkflowRoute
from pharos.harness.contracts import ActivationState, DeliveryState
from pharos.harness.dsh_gateway import (
    DshGatewayFactory,
    DshLaunch,
    DshRuntimeConfig,
)
from pharos.harness.dsh_persistence import DshPersistenceAdapter
from pharos.harness.fakes import FakeClock
from pharos.harness.model_gateway import AttemptContext
from pharos.harness.repository import Scope, now_iso
from pharos.harness.tables import artifacts, attempts
from pharos.harness.workflows.canary import CANARY_DSH_MODEL_PROFILE
from sqlalchemy import select


class _DeferredPersistence:
    """Break the assembly cycle without weakening the real DB adapter.

    ``HarnessApp`` owns the config service consumed by
    :class:`DshPersistenceAdapter`, while the app constructor needs the DSH
    factory.  The gateway sees this non-null seam during assembly; it is bound
    to the real adapter before any Run can be created or runtime opened.
    """

    def __init__(self) -> None:
        self._delegate: DshPersistenceAdapter | None = None

    def bind(self, delegate: DshPersistenceAdapter) -> None:
        if self._delegate is not None:
            raise RuntimeError("DSH persistence is already bound")
        self._delegate = delegate

    def _require(self) -> DshPersistenceAdapter:
        if self._delegate is None:
            raise RuntimeError("DSH persistence is not bound")
        return self._delegate

    def reserve_launch(self, context: AttemptContext, launch: DshLaunch) -> None:
        self._require().reserve_launch(context, launch)

    def attach_pid(self, context: AttemptContext, pid: int) -> None:
        self._require().attach_pid(context, pid)

    def observe_delivery(
        self, context: AttemptContext, state: DeliveryState
    ) -> bool | None:
        return self._require().observe_delivery(context, state)


def _activate_v2(app: HarnessApp) -> None:
    with session_scope() as session:
        head = app.config_service.current(session)
        assert head is not None
        snapshot = HarnessConfigSnapshot(
            gates={
                "harness_enabled": True,
                "dispatcher_enabled": True,
                "canary_enabled": True,
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
            actor="test-operator",
            reason="exercise the durable DSH canary",
        )
        app.config_service.apply(
            session,
            snapshot=snapshot,
            expected_head_revision=head["current_revision_id"],
            actor="test-operator",
            reason="exercise the durable DSH canary",
            now=now_iso(),
        )


def test_sealed_runtime_runs_real_loader_through_database_canary(
    db: Path,
    clock: FakeClock,
    owner: Scope,
    tmp_path: Path,
) -> None:
    """CI-only closure: provisioned Loader -> strict wire -> durable DB path."""

    del db
    names = (
        "PHAROS_TEST_DSH_MANIFEST",
        "PHAROS_TEST_DSH_NODE",
        "PHAROS_TEST_DSH_CLI",
        "PHAROS_TEST_DSH_RUNTIME_SHA256",
        "PHAROS_TEST_DSH_PROFILE_SHA256",
        "PHAROS_TEST_DSH_POLICY_SHA256",
        "PHAROS_TEST_DSH_UPSTREAM_COMMIT",
    )
    values = {name: os.environ.get(name) for name in names}
    if not all(values.values()):
        import pytest

        pytest.skip("a provisioned DSH runtime and external pins were not supplied")
    manifest = Path(str(values["PHAROS_TEST_DSH_MANIFEST"])).resolve()
    runtime_root = manifest.parent
    template = runtime_root / "template"
    patch = template / "pharos-safe.cordis.patch.yml"
    profile_hash = CANARY_DSH_MODEL_PROFILE.definition_hash()
    route = CANARY_DSH_MODEL_PROFILE.resolve_route("dsh")
    deferred = _DeferredPersistence()
    gateway = DshGatewayFactory(
        DshRuntimeConfig(
            argv=(
                str(Path(str(values["PHAROS_TEST_DSH_NODE"])).resolve()),
                str(Path(str(values["PHAROS_TEST_DSH_CLI"])).resolve()),
                "--profile",
                "sdk",
                "--patch",
                str(patch),
            ),
            cwd=str(tmp_path),
            patch=str(patch),
            prepared_dsh_home=str(template),
            runtime_manifest=str(manifest),
            upstream_commit=str(values["PHAROS_TEST_DSH_UPSTREAM_COMMIT"]),
            runtime_hash=str(values["PHAROS_TEST_DSH_RUNTIME_SHA256"]),
            profile_hash=str(values["PHAROS_TEST_DSH_PROFILE_SHA256"]),
            policy_hash=str(values["PHAROS_TEST_DSH_POLICY_SHA256"]),
            expected_model_profile_sha256=profile_hash,
            expected_model_route_sha256=CANARY_DSH_MODEL_PROFILE.route_hash(
                route.route_key
            ),
            env={"PATH": str(Path(str(values["PHAROS_TEST_DSH_NODE"])).parent)},
            initialize_timeout_seconds=20.0,
            prompt_timeout_seconds=30.0,
            idle_timeout_seconds=30.0,
            shutdown_timeout_seconds=10.0,
            reap_timeout_seconds=10.0,
        ),
        persistence=deferred,
        clock_us=clock.utc_epoch_us,
    )
    app = HarnessApp(clock=clock, dsh_gateway_factory=gateway)
    app.ensure_bootstrapped()
    deferred.bind(DshPersistenceAdapter(config_service=app.config_service, clock=clock))
    _activate_v2(app)
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input={"mode": "agent", "note": "sealed runtime integration"},
        idempotency_key="sealed-dsh-runtime-canary",
        initiator="operator",
    )
    app.dispatcher.claim_batch = 8
    for _ in range(4):
        app.cycle()
        run = app.get_run(scope=owner, run_id=run["id"])
        if run["state"] == "succeeded":
            break
    assert run["state"] == "succeeded"
    assert gateway.open_count == 1
    actor = next(
        step
        for step in app.steps_for(scope=owner, run_id=run["id"])
        if step["definition_step_key"] == "actor_turn"
    )
    with session_scope() as session:
        attempt = (
            session.execute(select(attempts).where(attempts.c.step_id == actor["id"]))
            .mappings()
            .one()
        )
        snapshot = app.execution_snapshots.read_attempt(
            session,
            scope=owner,
            attempt_id=attempt["id"],
            require_for_execution=True,
        )
        assert snapshot is not None
        artifact = (
            session.execute(
                select(artifacts).where(artifacts.c.id == actor["output_artifact_id"])
            )
            .mappings()
            .one()
        )
        # ``require`` independently re-authenticates the producer Attempt,
        # frozen snapshot and provenance digest at the public read boundary.
        app.artifacts.require(
            session,
            scope=owner,
            artifact_id=artifact["id"],
        )
        totals = app.usage.totals(session, run_id=run["id"])
    assert attempt["state"] == "succeeded"
    assert attempt["delivery_state"] == "acknowledged"
    assert attempt["child_pid"] is None
    assert attempt["input_tokens"] == 8
    assert attempt["output_tokens"] == 7
    assert isinstance(attempt["runtime_message_id"], str)
    assert 0 < len(attempt["runtime_message_id"]) <= 256
    assert artifact["producer_attempt_id"] == attempt["id"]
    assert artifact["runtime_session_id"] == attempt["runtime_session_id"]
    assert artifact["upstream_commit"] == attempt["upstream_commit"]
    assert artifact["runtime_hash"] == attempt["runtime_hash"]
    assert artifact["profile_hash"] == attempt["profile_hash"]
    assert artifact["policy_hash"] == attempt["policy_hash"]
    assert artifact["protocol_version"] == attempt["protocol_version"]
    assert artifact["route_key"] == snapshot.model_route_key
    assert artifact["route_sha256"] == snapshot.model_route_sha256
    assert artifact["definition_binding_sha256"] == snapshot.definition_binding_sha256
    assert artifact["run_policy_sha256"] == snapshot.run_policy_sha256
    assert artifact["provenance_sha256"] is not None
    assert totals["settled"] == 7
    assert totals["pending_reservations"] == 0
    assert list(tmp_path.glob("dsh-attempt-*")) == []

    # A replayed dispatcher cycle cannot launch a second runtime or publish a
    # second Artifact after the exact Attempt publication CAS has committed.
    app.cycle()
    assert gateway.open_count == 1
    with session_scope() as session:
        assert len(
            session.execute(select(artifacts.c.id).where(artifacts.c.run_id == run["id"])).all()
        ) == 1
