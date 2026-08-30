"""Artifact producer/runtime provenance contract tests."""

from __future__ import annotations

import pytest
from pharos.db.session import session_scope
from pharos.harness.artifacts import ArtifactStore
from pharos.harness.contracts import ArtifactSensitivity, ProducerKind
from pharos.harness.repository import Scope
from pharos.harness.tables import artifacts, attempt_definition_snapshots, attempts
from sqlalchemy import select, update
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
    return run, claimed


def _create(store, session, *, owner, run, claimed, **kwargs):
    return store.create(
        session,
        scope=owner,
        run_id=run["id"],
        step_id=claimed["step_id"],
        artifact_type="agent.canary",
        schema_name="canary.actor_out",
        schema_version=1,
        content={"answer": "offline"},
        producer_kind=ProducerKind.model_inference,
        sensitivity=ArtifactSensitivity.private,
        provider=None,
        model=None,
        producer_attempt_id=claimed["id"],
        now_us=app_clock(session),
        **kwargs,
    )


def app_clock(session) -> int:  # noqa: ARG001
    """Use a deterministic positive timestamp without reaching system state."""
    return 1_700_000_000_000_000


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
            content={"answer": "offline"},
            producer_kind=ProducerKind.deterministic,
            sensitivity=ArtifactSensitivity.private,
            producer_attempt_id=claimed["id"],
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
        forged = dict(artifact)
        forged["id"] = "forged-provenance-artifact"
        forged["provenance_sha256"] = "f" * 64
        session.execute(artifacts.insert().values(**forged))

        with pytest.raises(ValueError, match="provenance hash"):
            store.read(session, scope=owner, artifact_id=forged["id"])
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
        with pytest.raises(IntegrityError, match="producer snapshot"):
            session.execute(artifacts.insert().values(**forged))
