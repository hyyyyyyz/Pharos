"""Startup recovery of orphaned translation jobs, and who may trigger a sweep."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pharos.db.session as db_session
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pharos.api import daily as daily_api
from pharos.api.deps import current_user
from pharos.db.models import Paper, TranslationJob, User
from pharos.db.session import init_engine, session_scope
from pharos.services.translation import RESTART_ERROR, fail_interrupted_jobs
from sqlalchemy import select

ADMIN = "rr-admin"
USER = "rr-user"


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    if db_session._engine is not None:
        db_session._engine.dispose()
    db_session._engine = None
    db_session._SessionLocal = None
    db_session._fts5_available = None
    init_engine(tmp_path_factory.mktemp("db") / "pharos.db")
    with session_scope() as s:
        s.add(User(id=ADMIN, email="a@example.test", password_hash="x", is_admin=True))
        s.add(User(id=USER, email="u@example.test", password_hash="x"))
    with session_scope() as s:
        s.add(Paper(id="rr-p", user_id=USER, title="t", orig_sha256="cd" * 32, orig_filename="p.pdf"))
    yield


def test_interrupted_jobs_are_failed_with_a_readable_reason() -> None:
    with session_scope() as s:
        for jid, status in (("j-q", "queued"), ("j-r", "running"), ("j-d", "done"), ("j-e", "error")):
            s.add(TranslationJob(id=jid, paper_id="rr-p", status=status, stage=status))
    assert fail_interrupted_jobs() == 2
    with session_scope() as s:
        rows = {j.id: j for j in s.scalars(select(TranslationJob))}
    for jid in ("j-q", "j-r"):
        assert rows[jid].status == "error"
        assert rows[jid].error == RESTART_ERROR
        assert rows[jid].finished_at is not None
    assert rows["j-d"].status == "done"
    assert rows["j-e"].status == "error" and rows["j-e"].error is None
    assert fail_interrupted_jobs() == 0  # idempotent


def test_failed_job_no_longer_blocks_a_new_translation() -> None:
    from pharos.api.jobs import _start_job_row

    with session_scope() as s:
        s.add(TranslationJob(id="j-stuck", paper_id="rr-p", status="running"))
    with pytest.raises(Exception):
        _start_job_row("rr-p", USER, "bing", "zh", "2")  # blocked by the orphan
    fail_interrupted_jobs()
    assert _start_job_row("rr-p", USER, "bing", "zh", "2") is not None


class _Sweeper:
    def __init__(self) -> None:
        self.calls = 0
        self.active_date = "2099-01-01"

    async def submit(self, *_a: Any, **_k: Any) -> bool:
        self.calls += 1
        return False  # reports "already running" -> 409, proving the gate passed


@pytest.fixture()
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(daily_api.router)
    app.state.daily_sweeper = _Sweeper()
    app.state.signed_in_as = USER

    def _override() -> Iterator[User]:
        with session_scope() as s:
            yield s.scalar(select(User).where(User.id == app.state.signed_in_as))

    app.dependency_overrides[current_user] = _override
    return app


def test_refresh_is_forbidden_for_ordinary_users(app: FastAPI) -> None:
    with TestClient(app) as c:
        r = c.post("/api/daily/refresh", json={})
    assert r.status_code == 403
    assert app.state.daily_sweeper.calls == 0


def test_refresh_reaches_the_sweeper_for_admins(app: FastAPI) -> None:
    app.state.signed_in_as = ADMIN
    with TestClient(app) as c:
        r = c.post("/api/daily/refresh", json={})
    assert r.status_code == 409
    assert app.state.daily_sweeper.calls == 1
