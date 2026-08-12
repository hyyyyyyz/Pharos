"""Direct arXiv import: normalization, SSRF guards, and owner-scoped ingest."""

from __future__ import annotations

import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pharos.api import papers as papers_api
from pharos.api.deps import current_user, get_library, get_session
from pharos.daily import service as daily_service
from pharos.db import session as db_session
from pharos.db.models import Paper, User
from pharos.db.session import init_engine, session_scope
from pharos.services import arxiv_import
from pharos.services.library import LibraryService
from pharos.storage.blobs import BlobStore
from sqlalchemy import delete

OWNER = "arxiv-import-owner"
OTHER = "arxiv-import-other"


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    if db_session._engine is not None:
        db_session._engine.dispose()
    db_session._engine = None
    db_session._SessionLocal = None
    db_session._fts5_available = None
    root = tmp_path_factory.mktemp("arxiv-import")
    init_engine(root / "pharos.db")
    with session_scope() as session:
        for user_id in (OWNER, OTHER):
            session.add(
                User(id=user_id, email=f"{user_id}@example.test", password_hash="x")
            )
    yield root


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    yield
    with session_scope() as session:
        session.execute(delete(Paper).where(Paper.user_id.in_((OWNER, OTHER))))


def test_normalize_modern_and_legacy_inputs() -> None:
    assert arxiv_import.normalize_input("1706.03762v7").arxiv_id == "1706.03762"
    assert arxiv_import.normalize_input("arXiv:1706.03762v7").pdf_url == (
        "https://arxiv.org/pdf/1706.03762"
    )
    assert arxiv_import.normalize_input("http://arxiv.org/abs/math.gt/0309136v1").arxiv_id == (
        "math.GT/0309136"
    )
    assert arxiv_import.normalize_input("https://www.arxiv.org/pdf/1706.03762.pdf").abs_url == (
        "https://arxiv.org/abs/1706.03762"
    )


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/abs/1706.03762",
        "file:///etc/passwd",
        "https://arxiv.org/abs/1706.03762?next=https://example.com",
        "https://arxiv.org.evil.example/abs/1706.03762",
        "https://arxiv.org:8443/abs/1706.03762",
        "https://arxiv.org:not-a-port/abs/1706.03762",
        "not-an-id",
    ],
)
def test_normalize_rejects_non_arxiv_or_ambiguous_urls(value: str) -> None:
    with pytest.raises(arxiv_import.ArxivInputError):
        arxiv_import.normalize_input(value)


def test_import_is_owner_scoped_and_uses_canonical_https_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested: list[str] = []
    pdf = b"%PDF-1.7\nminimal test bytes"

    def fake_download(url: str, *, https_only: bool = False, **_: Any) -> bytes:
        requested.append(url)
        assert https_only is True
        return pdf

    monkeypatch.setattr("pharos.services.arxiv_import.daily_service.download_pdf", fake_download)
    monkeypatch.setattr("pharos.services.arxiv_import.enrich.enrich_by_arxiv", lambda *a, **k: None)

    blobs = BlobStore(tmp_path / "files")
    library = LibraryService(blobs)
    with session_scope() as session:
        paper = arxiv_import.import_paper(
            library,
            session,
            user_id=OWNER,
            value="https://arxiv.org/abs/1706.03762v7",
        )
        assert paper.user_id == OWNER
        assert paper.source == "arxiv"
        assert paper.arxiv_id == "1706.03762"
        assert paper.orig_filename == "1706.03762.pdf"
    assert requested == ["https://arxiv.org/pdf/1706.03762"]

    # The same bytes imported by another user create a separate owner row.
    with session_scope() as session:
        second = arxiv_import.import_paper(
            library, session, user_id=OTHER, value="1706.03762"
        )
        assert second.id != paper.id
        assert second.user_id == OTHER


def test_endpoint_requires_auth_and_returns_paper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pdf = b"%PDF-1.7\nendpoint bytes"
    monkeypatch.setattr(
        "pharos.services.arxiv_import.daily_service.download_pdf",
        lambda url, **kwargs: pdf,
    )
    monkeypatch.setattr("pharos.services.arxiv_import.enrich.enrich_by_arxiv", lambda *a, **k: None)

    blobs = BlobStore(tmp_path / "files")
    library = LibraryService(blobs)
    app = FastAPI()
    app.include_router(papers_api.router)
    app.dependency_overrides[current_user] = lambda: User(
        id=OWNER, email="owner@example.test", password_hash="x"
    )
    app.dependency_overrides[get_library] = lambda: library

    def session_override() -> Iterator[Any]:
        with session_scope() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/papers/import/arxiv", json={"input": "1706.03762"}
            ).status_code
            == 201
        )
        body = client.post("/api/papers/import/arxiv", json={"input": "1706.03762"})
        assert body.status_code == 201
        assert body.json()["source"] == "arxiv"
        assert body.json()["orig_filename"] == "1706.03762.pdf"


def test_endpoint_rejects_ssrf_input(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    called = False

    def never(*args: Any, **kwargs: Any) -> bytes:
        nonlocal called
        called = True
        raise AssertionError("network must not be reached for malformed input")

    monkeypatch.setattr("pharos.services.arxiv_import.daily_service.download_pdf", never)
    blobs = BlobStore(tmp_path / "files")
    app = FastAPI()
    app.include_router(papers_api.router)
    app.dependency_overrides[current_user] = lambda: User(
        id=OWNER, email="owner@example.test", password_hash="x"
    )
    app.dependency_overrides[get_library] = lambda: LibraryService(blobs)

    def session_override() -> Iterator[Any]:
        with session_scope() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    with TestClient(app) as client:
        response = client.post(
            "/api/papers/import/arxiv", json={"input": "https://example.com/abs/1706.03762"}
        )
    assert response.status_code == 400
    assert called is False


def test_pdf_redirect_handler_rejects_target_before_creating_request() -> None:
    """A final ``geturl`` check is too late: the redirected host was contacted."""
    handler = daily_service._PdfRedirectHandler(https_only=True)
    request = urllib.request.Request("https://arxiv.org/pdf/1706.03762")

    with pytest.raises(daily_service.PdfDownloadError, match="unexpected host"):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://127.0.0.1/internal",
        )


def test_pdf_redirect_handler_accepts_allowlisted_https_target() -> None:
    handler = daily_service._PdfRedirectHandler(https_only=True)
    request = urllib.request.Request("https://arxiv.org/pdf/1706.03762")

    redirected = handler.redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://export.arxiv.org/pdf/1706.03762",
    )

    assert redirected is not None
    assert redirected.full_url == "https://export.arxiv.org/pdf/1706.03762"
