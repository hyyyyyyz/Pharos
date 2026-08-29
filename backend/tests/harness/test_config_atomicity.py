"""Configuration integrity and run-creation cutover fences."""

from __future__ import annotations

import hashlib
import json
import threading

import pytest
from pharos.db.session import _configure_sqlite, session_scope
from pharos.harness.contracts import ConfigIntegrityError, StaleConfigError
from pharos.harness.repository import now_iso
from pharos.harness.tables import config_revisions, runs
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from tests.harness.conftest import enable_canary


def _file_engine(path):  # noqa: ANN001
    engine = create_engine(f"sqlite:///{path}", future=True)
    event.listen(engine, "connect", _configure_sqlite)
    return engine


def test_current_rejects_corrupt_persisted_snapshot_hash(app):
    enable_canary(app)
    with session_scope() as session:
        revision_id = app.config_service.current(session)["current_revision_id"]
        session.execute(
            config_revisions.update()
            .where(config_revisions.c.id == revision_id)
            .values(snapshot_sha256="0" * 64)
        )

    with pytest.raises(ConfigIntegrityError, match="hash mismatch"):
        app.current_snapshot()


def test_current_rejects_illegal_persisted_snapshot(app):
    enable_canary(app)
    with session_scope() as session:
        revision_id = app.config_service.current(session)["current_revision_id"]
        row = session.execute(
            select(config_revisions).where(config_revisions.c.id == revision_id)
        ).mappings().one()
        payload = json.loads(row["snapshot_json"])
        payload["gates"]["experiments_enabled"] = True
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        session.execute(
            config_revisions.update()
            .where(config_revisions.c.id == revision_id)
            .values(snapshot_json=raw, snapshot_sha256=hashlib.sha256(raw.encode()).hexdigest())
        )

    with pytest.raises(ConfigIntegrityError, match="Decision 9"):
        app.current_snapshot()


def test_current_accepts_hashed_legacy_json_serialization(app):
    """A valid immutable revision survives newer canonical formatting rules."""
    enable_canary(app)
    with session_scope() as session:
        revision_id = app.config_service.current(session)["current_revision_id"]
        row = session.execute(
            select(config_revisions).where(config_revisions.c.id == revision_id)
        ).mappings().one()
        raw = json.dumps(json.loads(row["snapshot_json"]), ensure_ascii=False, indent=2)
        session.execute(
            config_revisions.update()
            .where(config_revisions.c.id == revision_id)
            .values(snapshot_json=raw, snapshot_sha256=hashlib.sha256(raw.encode()).hexdigest())
        )

    assert app.current_snapshot().gates["canary_enabled"] is True


def test_concurrent_file_db_apply_has_one_winner(app, db):
    enable_canary(app)
    with session_scope() as session:
        expected = app.config_service.current(session)["current_revision_id"]
    engines = [_file_engine(db), _file_engine(db)]
    barrier = threading.Barrier(2)
    results: list[tuple[str, str]] = []

    def apply_one(index: int) -> None:
        session = Session(engines[index])
        try:
            snapshot = app.current_snapshot().model_copy(update={"actor": f"operator-{index}"})
            barrier.wait()
            revision_id = app.config_service.apply(
                session,
                snapshot=snapshot,
                expected_head_revision=expected,
                actor=f"operator-{index}",
                reason="concurrent apply",
                now=now_iso(),
            )
            session.commit()
            results.append(("ok", revision_id))
        except StaleConfigError:
            session.rollback()
            results.append(("stale", ""))
        finally:
            session.close()

    threads = [threading.Thread(target=apply_one, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for engine in engines:
        engine.dispose()

    assert sorted(result[0] for result in results) == ["ok", "stale"]


def test_stale_apply_does_not_leave_an_orphan_when_caught(app):
    enable_canary(app)
    with session_scope() as session:
        current = app.config_service.current_validated(session)
        assert current is not None
        before = session.execute(select(config_revisions.c.id)).all()
        with pytest.raises(StaleConfigError):
            app.config_service.apply(
                session,
                snapshot=current.snapshot,
                expected_head_revision="stale-revision",
                actor="operator",
                reason="stale candidate",
                now=now_iso(),
            )
        # Deliberately keep using and commit the outer transaction: apply()
        # itself owns cleanup of every candidate write.
        session.commit()

    with session_scope() as session:
        assert session.execute(select(config_revisions.c.id)).all() == before


def test_create_run_fails_closed_when_head_cuts_over_before_fence(app, owner, monkeypatch):
    enable_canary(app)
    original_fence = app.config_service.fence_current

    def cut_over_then_fence(session, *, revision_id: str) -> None:
        with session_scope() as operator_session:
            app.config_service.rollback(
                operator_session,
                actor="operator",
                reason="cut over during create",
                now=now_iso(),
            )
        original_fence(session, revision_id=revision_id)

    monkeypatch.setattr(app.config_service, "fence_current", cut_over_then_fence)
    with pytest.raises(StaleConfigError, match="head changed"):
        app.create_run(
            scope=owner,
            workflow_key="harness.canary",
            input={"mode": "success", "note": "fence", "items": ["a"]},
            idempotency_key="cutover-fence",
            initiator="user",
        )

    with session_scope() as session:
        assert session.execute(select(runs)).mappings().all() == []
