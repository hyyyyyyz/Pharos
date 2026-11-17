"""Artifact producer/runtime provenance contract tests."""

from __future__ import annotations

import json

import pytest
from pharos.db.session import session_scope
from pharos.harness.artifacts import (
    ArtifactStore,
    artifact_provenance_hash,
    capability_artifact_provenance_hash,
    content_hash,
)
from pharos.harness.contracts import ArtifactSensitivity, ProducerKind
from pharos.harness.repository import Scope
from pharos.harness.tables import (
    artifacts,
    attempt_definition_snapshots,
    attempts,
    steps,
)
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from tests.harness.conftest import enable_canary


def _claimed_agent(app, owner):
    enable_canary(app, agent_steps=True)
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input={"mode": "agent", "note": "artifact provenance"},
        idempotency_key="artifact-provenance-1",
        initiator="user",
    )
    # Advance the entirely offline fake kernel until the agent Attempt has a
    # frozen role snapshot.  No provider or network is contacted.
    claimed = None
    for _ in range(12):
        app.cycle()
        with session_scope() as session:
            row = (
                session.execute(
                    select(attempts)
                    .join(
                        attempt_definition_snapshots,
                        attempt_definition_snapshots.c.attempt_id == attempts.c.id,
                    )
                    .where(
                        attempts.c.run_id == run["id"],
                        attempt_definition_snapshots.c.provider.is_not(None),
                    )
                )
                .mappings()
                .first()
            )
            if row is not None:
                claimed = row
                break
    assert claimed is not None
    # These are the values a DSH launch reservation records. The test keeps
    # the runtime offline while exercising trusted source cross-binding.
    with session_scope() as session:
        session.execute(
            update(attempts)
            .where(attempts.c.id == claimed["id"])
            .values(
                upstream_commit="d" * 40,
                runtime_session_id=claimed["id"],
                deadline_at=1_700_000_001_000_000,
                runtime_hash="a" * 64,
                profile_hash="b" * 64,
                policy_hash="c" * 64,
                protocol_version="pharos.dsh.stdio@1",
            )
        )
        fresh = dict(
            session.execute(select(attempts).where(attempts.c.id == claimed["id"]))
            .mappings()
            .one()
        )
        source_artifact_id = session.execute(
            select(steps.c.output_artifact_id).where(steps.c.id == claimed["step_id"])
        ).scalar_one()
        source_content_json = session.execute(
            select(artifacts.c.content_json).where(artifacts.c.id == source_artifact_id)
        ).scalar_one()
        assert fresh["state"] == "succeeded"
        assert fresh["input_sha256"] is not None
        assert fresh["output_sha256"] == content_hash(json.loads(source_content_json))
        fresh["_artifact_content"] = json.loads(source_content_json)
    return run, fresh


def _claimed_capability(app, owner):
    enable_canary(app)
    run = app.create_run(
        scope=owner,
        workflow_key="harness.canary",
        input={"mode": "success", "note": "capability artifact provenance"},
        idempotency_key="capability-artifact-provenance-1",
        initiator="user",
    )
    now_us = app.clock.utc_epoch_us()
    with session_scope() as session:
        claimed = app.dispatcher.claim_due(session, now_us=now_us, limit=1)
        assert claimed is not None
        assert app.state.start_attempt_cas(
            session,
            scope=owner,
            run_id=claimed.run_id,
            step_id=claimed.step_id,
            attempt_id=claimed.attempt_id,
            attempt_no=claimed.attempt_no,
            lease_owner=claimed.lease_owner,
            now_us=now_us,
        )
    return run, {
        "id": claimed.attempt_id,
        "step_id": claimed.step_id,
        "attempt_no": claimed.attempt_no,
        "lease_owner": claimed.lease_owner,
    }


def _acquire_capability_publication(
    app, session, *, owner, run, claimed, content, input_sha256="e" * 64
):
    assert app.state.acquire_capability_publication_cas(
        session,
        scope=owner,
        run_id=run["id"],
        step_id=claimed["step_id"],
        attempt_id=claimed["id"],
        attempt_no=claimed["attempt_no"],
        lease_owner=claimed["lease_owner"],
        input_sha256=input_sha256,
        output_sha256=content_hash(content),
        external_outcome="succeeded",
        now_us=app.clock.utc_epoch_us(),
    )
    return input_sha256


def _drop_attempt_artifact_immutability(session) -> None:  # noqa: ANN001
    """Simulate offline database corruption so read-side checks are exercised."""

    session.execute(text("DROP TRIGGER ck_harness_artifacts_provenance_immutable"))


def _create(store, session, *, owner, run, claimed, **kwargs):
    return store.create(
        session,
        scope=owner,
        run_id=run["id"],
        step_id=claimed["step_id"],
        artifact_type="agent.canary",
        schema_name="canary.actor_out",
        schema_version=1,
        content=claimed["_artifact_content"],
        producer_kind=ProducerKind.model_inference,
        sensitivity=ArtifactSensitivity.private,
        provider=None,
        model=None,
        producer_attempt_id=claimed["id"],
        input_sha256=claimed["input_sha256"],
        now_us=app_clock(session),
        **kwargs,
    )


def app_clock(session) -> int:  # noqa: ARG001
    """Use a deterministic positive timestamp without reaching system state."""
    return 1_700_000_000_000_000


def test_existing_model_provenance_hash_is_stable():
    assert (
        artifact_provenance_hash(
            artifact_type="agent.canary",
            schema_name="canary.actor_out",
            schema_version=1,
            producer_kind="model_inference",
            producer_attempt_id="attempt-1",
            run_id="run-1",
            step_id="step-1",
            scope_type="user",
            scope_id="owner-1",
            workflow_key="harness.canary",
            workflow_version=1,
            workflow_definition_sha256="1" * 64,
            executor_kind="role",
            executor_identity="canary_actor@1",
            executor_role_definition_sha256="2" * 64,
            executor_capability_definition_sha256=None,
            role_prompt_version="canary-actor-zh@1",
            model_profile_identity="canary@1",
            model_profile_sha256="3" * 64,
            usage_source="system_shared",
            upstream_commit="4" * 40,
            runtime_session_id="session-1",
            runtime_hash="5" * 64,
            profile_hash="6" * 64,
            policy_hash="7" * 64,
            protocol_version="pharos.dsh.stdio@1",
            route_key="route-1",
            route_sha256="8" * 64,
            definition_binding_sha256="9" * 64,
            run_policy_sha256="a" * 64,
            provider="fake",
            model="canary",
        )
        == "7a3778e653da73241e6768e2e168a024cdc5904ad11dc7db2d1e2b43cc214972"
    )


def test_capability_provenance_hash_is_explicitly_versioned():
    digest = capability_artifact_provenance_hash(
        artifact_type="capability.observation",
        schema_name="canary.observation",
        schema_version=1,
        producer_kind="deterministic",
        producer_attempt_id="attempt-1",
        run_id="run-1",
        step_id="step-1",
        scope_type="user",
        scope_id="owner-1",
        workflow_key="harness.canary",
        workflow_version=1,
        workflow_definition_sha256="1" * 64,
        executor_identity="canary.noop@1",
        executor_capability_definition_sha256="2" * 64,
        definition_binding_sha256="3" * 64,
        run_policy_sha256="4" * 64,
    )
    assert digest == "52d44d800db01b7d2e6c7e090121153caadea1dbefd84c406a13ae0d416ef84a"


def test_create_derives_provenance_from_attempt_and_snapshot(app, owner):
    run, claimed = _claimed_agent(app, owner)
    store = ArtifactStore()
    with session_scope() as session:
        artifact = _create(store, session, owner=owner, run=run, claimed=claimed)
        assert artifact["producer_attempt_id"] == claimed["id"]
        assert artifact["upstream_commit"] == "d" * 40
        assert artifact["runtime_session_id"] == claimed["id"]
        assert artifact["runtime_hash"] == "a" * 64
        assert artifact["profile_hash"] == "b" * 64
        assert artifact["policy_hash"] == "c" * 64
        assert artifact["protocol_version"] == "pharos.dsh.stdio@1"
        assert artifact["route_key"]
        assert artifact["route_sha256"]
        assert artifact["definition_binding_sha256"]
        assert artifact["run_policy_sha256"]
        assert artifact["provider"]
        assert artifact["model"]
        assert artifact["provenance_sha256"]
        assert store.require(
            session, scope=owner, artifact_id=artifact["id"]
        ) == store.read(session, scope=owner, artifact_id=artifact["id"])


def test_create_derives_deterministic_provenance_from_capability_snapshot(app, owner):
    run, claimed = _claimed_capability(app, owner)
    store = ArtifactStore()
    content = {"ok": True, "key": "offline"}
    with session_scope() as session:
        input_sha256 = _acquire_capability_publication(
            app,
            session,
            owner=owner,
            run=run,
            claimed=claimed,
            content=content,
        )
        artifact = store.create(
            session,
            scope=owner,
            run_id=run["id"],
            step_id=claimed["step_id"],
            artifact_type="capability.observation",
            schema_name="canary.observation",
            schema_version=1,
            content=content,
            producer_kind=ProducerKind.deterministic,
            sensitivity=ArtifactSensitivity.private,
            producer_attempt_id=claimed["id"],
            input_sha256=input_sha256,
            now_us=app_clock(session),
        )

        assert artifact["producer_attempt_id"] == claimed["id"]
        assert artifact["workflow_key"] == "harness.canary"
        assert artifact["workflow_version"] == 1
        assert artifact["definition_binding_sha256"]
        assert artifact["run_policy_sha256"]
        assert artifact["provenance_sha256"]
        for field in (
            "role_prompt_version",
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
            assert artifact[field] is None
        assert store.read(session, scope=owner, artifact_id=artifact["id"]) == artifact


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"schema_name": "forged.observation"}, "caller schema name/version"),
        ({"schema_version": 99}, "caller schema name/version"),
        ({"producer_kind": ProducerKind.model_inference}, "caller producer_kind"),
        ({"workflow_key": "forged.workflow"}, "caller workflow_key"),
        ({"runtime_hash": "d" * 64}, "caller runtime_hash"),
        ({"route_key": "forged-route"}, "caller route_key"),
        ({"provider": "fake"}, "caller provider"),
        ({"role_prompt_version": "forged-prompt@1"}, "caller role_prompt_version"),
        ({"definition_binding_sha256": "d" * 64}, "caller definition_binding_sha256"),
        ({"run_policy_sha256": "e" * 64}, "caller run_policy_sha256"),
        ({"provenance_sha256": "f" * 64}, "caller provenance_sha256"),
    ],
)
def test_capability_artifact_cannot_be_relabelled(app, owner, kwargs, message):
    run, claimed = _claimed_capability(app, owner)
    content = {"ok": True}
    values = {
        "schema_name": "canary.observation",
        "schema_version": 1,
        "producer_kind": ProducerKind.deterministic,
        **kwargs,
    }
    with session_scope() as session:
        input_sha256 = _acquire_capability_publication(
            app,
            session,
            owner=owner,
            run=run,
            claimed=claimed,
            content=content,
        )
        with pytest.raises(ValueError, match=message):
            ArtifactStore().create(
                session,
                scope=owner,
                run_id=run["id"],
                step_id=claimed["step_id"],
                artifact_type="capability.observation",
                content=content,
                sensitivity=ArtifactSensitivity.private,
                producer_attempt_id=claimed["id"],
                input_sha256=input_sha256,
                now_us=app_clock(session),
                **values,
            )


def test_capability_attempt_rejects_invented_runtime_provenance(app, owner):
    run, claimed = _claimed_capability(app, owner)
    content = {"ok": True}
    with session_scope() as session:
        session.execute(
            update(attempts)
            .where(attempts.c.id == claimed["id"])
            .values(runtime_hash="a" * 64)
        )
        input_sha256 = _acquire_capability_publication(
            app,
            session,
            owner=owner,
            run=run,
            claimed=claimed,
            content=content,
        )
        with pytest.raises(ValueError, match="contains model/runtime provenance"):
            ArtifactStore().create(
                session,
                scope=owner,
                run_id=run["id"],
                step_id=claimed["step_id"],
                artifact_type="capability.observation",
                schema_name="canary.observation",
                schema_version=1,
                content=content,
                producer_kind=ProducerKind.deterministic,
                producer_attempt_id=claimed["id"],
                input_sha256=input_sha256,
                now_us=app_clock(session),
            )


def test_capability_artifact_read_rechecks_frozen_observation_schema(app, owner):
    run, claimed = _claimed_capability(app, owner)
    store = ArtifactStore()
    content = {"ok": True}
    with session_scope() as session:
        input_sha256 = _acquire_capability_publication(
            app,
            session,
            owner=owner,
            run=run,
            claimed=claimed,
            content=content,
        )
        artifact = store.create(
            session,
            scope=owner,
            run_id=run["id"],
            step_id=claimed["step_id"],
            artifact_type="capability.observation",
            schema_name="canary.observation",
            schema_version=1,
            content=content,
            producer_kind=ProducerKind.deterministic,
            producer_attempt_id=claimed["id"],
            input_sha256=input_sha256,
            now_us=app_clock(session),
        )
        _drop_attempt_artifact_immutability(session)
        session.execute(
            update(artifacts)
            .where(artifacts.c.id == artifact["id"])
            .values(schema_name="forged.observation")
        )

        with pytest.raises(ValueError, match="schema name/version"):
            store.read(session, scope=owner, artifact_id=artifact["id"])


def test_caller_cannot_supply_runtime_provenance_values(app, owner):
    run, claimed = _claimed_agent(app, owner)
    with session_scope() as session:
        with pytest.raises(ValueError, match="caller runtime_hash"):
            _create(
                ArtifactStore(),
                session,
                owner=owner,
                run=run,
                claimed=claimed,
                runtime_hash="d" * 64,
            )
        with pytest.raises(ValueError, match="caller provenance_sha256"):
            _create(
                ArtifactStore(),
                session,
                owner=owner,
                run=run,
                claimed=claimed,
                provenance_sha256="d" * 64,
            )
        with pytest.raises(ValueError, match="caller upstream_commit"):
            _create(
                ArtifactStore(),
                session,
                owner=owner,
                run=run,
                claimed=claimed,
                upstream_commit="e" * 40,
            )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workflow_key", "forged.workflow"),
        ("workflow_version", 99),
        ("role_prompt_version", "forged-prompt@9"),
    ],
)
def test_caller_cannot_relabel_frozen_attempt_identity(app, owner, field, value):
    run, claimed = _claimed_agent(app, owner)
    with session_scope() as session, pytest.raises(ValueError, match=f"caller {field}"):
        _create(
            ArtifactStore(),
            session,
            owner=owner,
            run=run,
            claimed=claimed,
            **{field: value},
        )


def test_caller_cannot_relabel_frozen_attempt_as_deterministic(app, owner):
    run, claimed = _claimed_agent(app, owner)
    with session_scope() as session, pytest.raises(
        ValueError, match="caller producer_kind"
    ):
        ArtifactStore().create(
            session,
            scope=owner,
            run_id=run["id"],
            step_id=claimed["step_id"],
            artifact_type="agent.canary",
            schema_name="canary.actor_out",
            schema_version=1,
            content=claimed["_artifact_content"],
            producer_kind=ProducerKind.deterministic,
            sensitivity=ArtifactSensitivity.private,
            producer_attempt_id=claimed["id"],
            input_sha256=claimed["input_sha256"],
            now_us=app_clock(session),
        )


def test_provenance_columns_are_immutable_once_bound(app, owner):
    run, claimed = _claimed_agent(app, owner)
    with session_scope() as session:
        artifact = _create(ArtifactStore(), session, owner=owner, run=run, claimed=claimed)
        with pytest.raises(IntegrityError, match="immutable"):
            session.execute(
                update(artifacts)
                .where(artifacts.c.id == artifact["id"])
                .values(route_key="forged-route")
            )


def test_unbound_legacy_artifacts_remain_compatible(app, owner):
    run, _ = _claimed_agent(app, owner)
    with session_scope() as session:
        artifact = ArtifactStore().create(
            session,
            scope=owner,
            run_id=run["id"],
            artifact_type="deterministic.note",
            schema_name="note",
            schema_version=1,
            content={"answer": "legacy"},
            producer_kind=ProducerKind.deterministic,
            sensitivity=ArtifactSensitivity.private,
            now_us=app_clock(session),
        )
        assert artifact["producer_attempt_id"] is None
        assert ArtifactStore().read(session, scope=owner, artifact_id=artifact["id"])[
            "content_json"
        ]


def test_provenance_cannot_cross_owner_scope(app, owner):
    run, claimed = _claimed_agent(app, owner)
    other = Scope.user("different-owner")
    with session_scope() as session, pytest.raises(ValueError, match="scope mismatch"):
        _create(
            ArtifactStore(),
            session,
            owner=other,
            run=run,
            claimed=claimed,
        )


def test_reads_fail_closed_when_stored_provenance_hash_is_forged(app, owner):
    run, claimed = _claimed_agent(app, owner)
    store = ArtifactStore()
    with session_scope() as session:
        artifact = _create(store, session, owner=owner, run=run, claimed=claimed)
        _drop_attempt_artifact_immutability(session)
        session.execute(
            update(artifacts)
            .where(artifacts.c.id == artifact["id"])
            .values(provenance_sha256="f" * 64)
        )

        with pytest.raises(ValueError, match="provenance hash"):
            store.read(session, scope=owner, artifact_id=artifact["id"])
        with pytest.raises(ValueError, match="provenance hash"):
            store.for_run(session, scope=owner, run_id=run["id"])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workflow_key", "forged.workflow"),
        ("role_prompt_version", "forged-prompt@9"),
        ("schema_name", "forged.schema"),
        ("schema_version", 99),
        ("producer_kind", ProducerKind.deterministic.value),
    ],
)
def test_reads_fail_closed_when_frozen_identity_columns_are_tampered(
    app, owner, field, value
):
    run, claimed = _claimed_agent(app, owner)
    store = ArtifactStore()
    with session_scope() as session:
        artifact = _create(store, session, owner=owner, run=run, claimed=claimed)
        _drop_attempt_artifact_immutability(session)
        session.execute(
            update(artifacts).where(artifacts.c.id == artifact["id"]).values(**{field: value})
        )
        with pytest.raises(ValueError):
            store.require(session, scope=owner, artifact_id=artifact["id"])


def test_database_rejects_provenance_that_disagrees_with_attempt_snapshot(app, owner):
    run, claimed = _claimed_agent(app, owner)
    with session_scope() as session:
        artifact = _create(ArtifactStore(), session, owner=owner, run=run, claimed=claimed)
        forged = dict(artifact)
        forged["id"] = "forged-producer-snapshot"
        forged["run_policy_sha256"] = "f" * 64
        forged["provenance_sha256"] = "e" * 64
        session.execute(text("DROP INDEX ux_harness_artifacts_producer_attempt"))
        with pytest.raises(IntegrityError, match="producer snapshot"):
            session.execute(artifacts.insert().values(**forged))
