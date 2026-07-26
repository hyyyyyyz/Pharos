"""Production SPA hosting without weakening API or asset 404 semantics."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pharos.web import mount_web_app


@pytest.fixture()
def web_root(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    assets = root / "assets"
    assets.mkdir(parents=True)
    (root / "index.html").write_text(
        '<!doctype html><html><body><div id="root"></div></body></html>',
        encoding="utf-8",
    )
    (root / "favicon.ico").write_bytes(b"ico")
    (assets / "app.abc123.js").write_text("console.log('pharos')", encoding="utf-8")
    return root


@pytest.fixture()
def client(web_root: Path) -> TestClient:
    app = FastAPI()

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    mount_web_app(app, web_root)
    return TestClient(app)


def test_root_serves_fresh_index(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache, must-revalidate"
    assert '<div id="root"></div>' in response.text


def test_extensionless_navigation_uses_spa_fallback(client: TestClient) -> None:
    response = client.get("/library/paper/example")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<div id="root"></div>' in response.text


def test_hashed_assets_are_immutable(client: TestClient) -> None:
    response = client.get("/assets/app.abc123.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


@pytest.mark.parametrize("path", ["/assets/missing.js", "/missing.js"])
def test_missing_files_remain_real_404s(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 404
    assert not response.headers["content-type"].startswith("text/html")


def test_unknown_api_path_is_json_404_not_the_spa(client: TestClient) -> None:
    response = client.get("/api/not-found")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_existing_api_and_docs_still_win(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"status": "ok"}
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "swagger-ui" in docs.text


def test_root_level_public_file_is_served_without_spa_fallback(client: TestClient) -> None:
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.content == b"ico"
    assert response.headers["cache-control"] == "public, max-age=3600"


def test_invalid_web_directory_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="Vite production build"):
        mount_web_app(FastAPI(), tmp_path / "missing")
