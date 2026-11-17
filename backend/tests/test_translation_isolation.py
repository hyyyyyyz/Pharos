"""Translations belong to the job (and so the user) that produced them.

The original PDF is content-addressed and shared on purpose. Translations used
to be too — ``<sha256>/mono.pdf`` — which meant that two users holding the same
upload read each other's output, wrote into the same ``work/`` directory, and a
``pages=`` run overwrote a finished whole-document translation. These tests pin
the replacement: output lives under ``<sha256>/jobs/<job_id>/`` and is served
only through a finished job of the caller's own paper row.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pharos.db.session as db_session
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pharos.api import jobs as jobs_api
from pharos.api import papers as papers_api
from pharos.api.deps import current_user
from pharos.db.models import Paper, TranslationJob, User
from pharos.db.session import init_engine, session_scope
from pharos.engines.base import TranslationResult
from pharos.services.library import LibraryService
from pharos.services.translation import JobManager, create_job
from pharos.storage.blobs import BlobStore
from sqlalchemy import select

ALICE = "iso-alice"
BOB = "iso-bob"
SHA = "ab" * 32


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    if db_session._engine is not None:
        db_session._engine.dispose()
    db_session._engine = None
    db_session._SessionLocal = None
    db_session._fts5_available = None
    init_engine(tmp_path_factory.mktemp("db") / "pharos.db")
    with session_scope() as s:
        for uid in (ALICE, BOB):
            s.add(User(id=uid, email=f"{uid}@example.test", password_hash="x"))
    yield


class _StubJobManager:
    def __init__(self) -> None:
        self.submitted: list[tuple[str, Any]] = []

    def submit(self, job_id: str, sha256: str, source_pdf: Path, target_lang: str, pages=None):
        self.submitted.append((job_id, pages))


class _Settings:
    def translator_config(self) -> Any:
        return type("Cfg", (), {"type": "bing"})()


@pytest.fixture()
def blobs(tmp_path: Path) -> BlobStore:
    return BlobStore(tmp_path / "files")


@pytest.fixture()
def app(blobs: BlobStore) -> FastAPI:
    app = FastAPI()
    app.include_router(papers_api.router)
    app.include_router(jobs_api.router)
    app.state.settings = _Settings()
    app.state.job_manager = _StubJobManager()
    app.state.blobs = blobs
    app.state.library = LibraryService(blobs)
    app.state.signed_in_as = ALICE

    def _override() -> Iterator[User]:
        with session_scope() as s:
            yield s.scalar(select(User).where(User.id == app.state.signed_in_as))

    app.dependency_overrides[current_user] = _override
    return app


@pytest.fixture()
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _paper(paper_id: str, owner: str, sha: str = SHA) -> None:
    with session_scope() as s:
        s.add(
            Paper(
                id=paper_id,
                user_id=owner,
                title="Shared Paper",
                orig_sha256=sha,
                orig_filename="p.pdf",
            )
        )


def _finish_job(blobs: BlobStore, paper_id: str, text: bytes, pages: str | None = None) -> str:
    """Create a job and finish it through the real adopt path."""
    with session_scope() as s:
        paper = s.get(Paper, paper_id)
        job = create_job(s, paper, "bing", "zh", pages=pages)
        job_id, sha = job.id, paper.orig_sha256
    produced = blobs.work_dir(sha, job_id) / "out.pdf"
    produced.write_bytes(text)
    manager = JobManager(engine=None, blobs=blobs)  # type: ignore[arg-type]
    manager._adopt_and_finish(
        job_id, sha, TranslationResult(mono_pdf=produced, dual_pdf=produced, total_seconds=1.0)
    )
    return job_id


def _as(app: FastAPI, user: str) -> None:
    app.state.signed_in_as = user


# ------------------------------------------------------------ visibility


def test_translation_is_not_visible_to_another_owner_of_the_same_pdf(
    app: FastAPI, client: TestClient, blobs: BlobStore
) -> None:
    _paper("vis-a", ALICE, "11" * 32)
    _paper("vis-b", BOB, "11" * 32)
    _finish_job(blobs, "vis-a", b"%PDF alice translation")

    assert client.get("/api/papers/vis-a/pdf/mono").content == b"%PDF alice translation"
    assert client.get("/api/papers/vis-a/pdf/dual").status_code == 200

    _as(app, BOB)
    assert client.get("/api/papers/vis-b/pdf/mono").status_code == 404
    assert client.get("/api/papers/vis-b/pdf/dual").status_code == 404
    # Another user's paper id stays an ordinary 404, never the file.
    assert client.get("/api/papers/vis-a/pdf/mono").status_code == 404


def test_original_is_still_shared_by_content(
    app: FastAPI, client: TestClient, blobs: BlobStore
) -> None:
    sha, _ = blobs.store_original(b"%PDF-1.4 same upload")
    _paper("orig-a", ALICE, sha)
    _paper("orig-b", BOB, sha)
    assert client.get("/api/papers/orig-a/pdf/original").status_code == 200
    _as(app, BOB)
    assert client.get("/api/papers/orig-b/pdf/original").status_code == 200


# ----------------------------------------------------------- work/ isolation


def test_each_job_gets_its_own_work_and_output_dir(blobs: BlobStore) -> None:
    sha = "22" * 32
    assert blobs.work_dir(sha, "aaaa") != blobs.work_dir(sha, "bbbb")
    assert blobs.job_path(sha, "aaaa", "mono") != blobs.job_path(sha, "bbbb", "mono")
    for bad in ("../x", "a/b", ""):
        with pytest.raises(ValueError):
            blobs.job_dir(sha, bad)


def test_two_users_same_pdf_write_to_different_outputs(blobs: BlobStore) -> None:
    sha = "33" * 32
    _paper("w-a", ALICE, sha)
    _paper("w-b", BOB, sha)
    ja = _finish_job(blobs, "w-a", b"A")
    jb = _finish_job(blobs, "w-b", b"B")
    assert blobs.job_path(sha, ja, "mono").read_bytes() == b"A"
    assert blobs.job_path(sha, jb, "mono").read_bytes() == b"B"


# -------------------------------------------------------- partial vs whole


def test_partial_run_does_not_replace_the_full_translation(
    client: TestClient, blobs: BlobStore
) -> None:
    _paper("part", ALICE, "44" * 32)
    _finish_job(blobs, "part", b"FULL")
    _finish_job(blobs, "part", b"PAGES 1-2", pages="1-2")
    assert client.get("/api/papers/part/pdf/mono").content == b"FULL"


def test_partial_only_is_still_served(client: TestClient, blobs: BlobStore) -> None:
    _paper("part-only", ALICE, "45" * 32)
    _finish_job(blobs, "part-only", b"PAGES 3", pages="3")
    assert client.get("/api/papers/part-only/pdf/mono").content == b"PAGES 3"


# ------------------------------------------------------------ dedup / mutex


def test_repeat_request_returns_the_active_job_and_queues_once(
    app: FastAPI, client: TestClient
) -> None:
    _paper("dedup", ALICE, "55" * 32)
    first = client.post("/api/papers/dedup/translate")
    again = client.post("/api/papers/dedup/translate")
    assert first.status_code == again.status_code == 202
    assert first.json()["id"] == again.json()["id"]
    assert len(app.state.job_manager.submitted) == 1


def test_different_pages_while_active_is_a_conflict(client: TestClient) -> None:
    _paper("mutex", ALICE, "56" * 32)
    assert client.post("/api/papers/mutex/translate").status_code == 202
    assert client.post("/api/papers/mutex/translate?pages=1-2").status_code == 409


def test_new_job_allowed_after_previous_one_finished(
    app: FastAPI, client: TestClient, blobs: BlobStore
) -> None:
    _paper("redo", ALICE, "57" * 32)
    _finish_job(blobs, "redo", b"x")
    assert client.post("/api/papers/redo/translate?pages=2").status_code == 202


def test_users_with_the_same_pdf_do_not_block_each_other(
    app: FastAPI, client: TestClient
) -> None:
    _paper("par-a", ALICE, "58" * 32)
    _paper("par-b", BOB, "58" * 32)
    ja = client.post("/api/papers/par-a/translate").json()["id"]
    _as(app, BOB)
    jb = client.post("/api/papers/par-b/translate").json()["id"]
    assert ja != jb


# ------------------------------------------------------------------ legacy


def test_legacy_job_output_is_served_to_its_owner_only(
    app: FastAPI, client: TestClient, blobs: BlobStore
) -> None:
    sha = "66" * 32
    legacy = blobs.paper_dir(sha) / "mono.pdf"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"%PDF legacy")
    _paper("leg-a", ALICE, sha)
    _paper("leg-b", BOB, sha)
    with session_scope() as s:
        s.add(
            TranslationJob(
                paper_id="leg-a",
                status="done",
                mono_path=str(legacy),
                finished_at=datetime.now(UTC),
            )
        )
    assert client.get("/api/papers/leg-a/pdf/mono").content == b"%PDF legacy"
    _as(app, BOB)
    assert client.get("/api/papers/leg-b/pdf/mono").status_code == 404


def test_recorded_path_outside_the_store_is_refused(
    client: TestClient, tmp_path: Path
) -> None:
    outside = tmp_path / "secret.pdf"
    outside.write_bytes(b"%PDF secret")
    _paper("esc", ALICE, "67" * 32)
    with session_scope() as s:
        s.add(
            TranslationJob(
                paper_id="esc", status="done", mono_path=str(outside),
                finished_at=datetime.now(UTC),
            )
        )
    assert client.get("/api/papers/esc/pdf/mono").status_code == 404


# ------------------------------------------------------------- no stored PDF


def test_metadata_only_paper_cannot_be_translated(
    client: TestClient, blobs: BlobStore
) -> None:
    _paper("zot", ALICE, "")
    before = sorted(p.name for p in blobs.files_dir.iterdir())
    r = client.post("/api/papers/zot/translate")
    assert r.status_code == 409
    assert sorted(p.name for p in blobs.files_dir.iterdir()) == before
    with session_scope() as s:
        assert s.scalar(select(TranslationJob).where(TranslationJob.paper_id == "zot")) is None


# -------------------------------------------------------------------- purge


def test_purge_removes_own_outputs_but_keeps_other_users_when_shared(
    blobs: BlobStore,
) -> None:
    sha, _ = blobs.store_original(b"%PDF-1.4 shared purge")
    _paper("pg-a", ALICE, sha)
    _paper("pg-b", BOB, sha)
    ja = _finish_job(blobs, "pg-a", b"A")
    jb = _finish_job(blobs, "pg-b", b"B")
    library = LibraryService(blobs)
    with session_scope() as s:
        library.purge(s, s.get(Paper, "pg-a"))
    assert not blobs.job_dir(sha, ja).exists()
    assert blobs.job_path(sha, jb, "mono").read_bytes() == b"B"
    assert blobs.path(sha, "original").exists()
