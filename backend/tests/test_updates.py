"""The public desktop update advertisement and installer streaming."""

from __future__ import annotations

import json
import urllib.error

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pharos.api import updates
from pharos.config import Settings


def _settings(**overrides) -> Settings:
    base = {
        "desktop_update_repo": "hyyyyyyz/Pharos",
        "desktop_update_version_override": None,
        "desktop_update_github_token": None,
    }
    base.update(overrides)
    return Settings(**base)


def _github_response(payload):
    class _Response:
        def read(self, limit=None):
            return json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    return _Response()


def _release(tag, assets=()):
    return {
        "tag_name": tag,
        "html_url": f"https://example/releases/{tag}",
        "body": "",
        "assets": list(assets),
    }


def _asset(name, size=100, digest="", url=""):
    return {
        "name": name,
        "browser_download_url": url or f"https://example/{name}",
        "size": size,
        "digest": digest,
    }


def test_asset_map_picks_the_platform_installers():
    release = _release(
        "desktop-v1.6.0",
        assets=(
            _asset("Pharos-1.6.0-mac.zip", digest="a" * 64),
            _asset("Pharos-1.6.0.dmg"),
            _asset("Pharos-1.6.0_win-x64.zip", digest="b" * 64),
            _asset("Pharos-1.6.0_linux-x86_64.tar.xz", digest="c" * 64),
        ),
    )
    assets = updates._asset_map(release, "1.6.0")
    assert set(assets) == {"mac", "windows", "linux"}
    assert assets["mac"]["sha256"] == "a" * 64
    assert assets["mac"]["name"] == "Pharos-1.6.0-mac.zip"


def test_check_payload_carries_assets(monkeypatch):
    monkeypatch.setattr(updates, "_github_cache", None)
    monkeypatch.setattr(
        updates,
        "_fetch_releases",
        lambda settings: [_release("desktop-v1.6.0", assets=(_asset("Pharos-1.6.0-mac.zip"),))],
    )
    payload = updates._github_payload(_settings(), now=1000.0)
    assert payload["version"] == "1.6.0"
    assert payload["assets"]["mac"]["name"] == "Pharos-1.6.0-mac.zip"


def test_download_streams_the_asset_with_verification_headers(monkeypatch):
    monkeypatch.setattr(
        updates,
        "_fetch_releases",
        lambda settings: [
            _release(
                "desktop-v1.6.0",
                assets=(_asset("Pharos-1.6.0-mac.zip", size=7, digest="d" * 64),),
            )
        ],
    )
    monkeypatch.setattr(
        updates,
        "_stream_github_asset",
        lambda settings, asset: iter([b"payload"]),
    )
    app = FastAPI()
    app.include_router(updates.router)
    client = TestClient(app)
    response = client.get("/api/updates/desktop/download?platform=mac")
    assert response.status_code == 200
    assert response.content == b"payload"
    assert response.headers["X-Pharos-Asset-SHA256"] == "d" * 64
    assert response.headers["Content-Length"] == "7"


def test_download_unknown_platform_is_400():
    app = FastAPI()
    app.include_router(updates.router)
    client = TestClient(app)
    assert client.get("/api/updates/desktop/download?platform=toaster").status_code == 400


def test_download_without_a_release_is_404(monkeypatch):
    monkeypatch.setattr(updates, "_github_cache", None)
    monkeypatch.setattr(updates, "_fetch_releases", lambda settings: [])
    monkeypatch.setattr(
        updates,
        "desktop_latest",
        lambda: {"version": None, "url": None, "notes": None},
    )
    app = FastAPI()
    app.include_router(updates.router)
    client = TestClient(app)
    assert client.get("/api/updates/desktop/download?platform=mac").status_code == 404


def test_download_respects_the_pinned_version(monkeypatch):
    seen_versions = []
    original = updates._release_asset

    def spy(settings, platform, version):
        seen_versions.append(version)
        return original(settings, platform, version)

    monkeypatch.setattr(
        updates,
        "_fetch_releases",
        lambda settings: [_release("desktop-v1.6.0", assets=(_asset("Pharos-1.6.0-mac.zip"),))],
    )
    monkeypatch.setattr(updates, "_release_asset", spy)
    monkeypatch.setattr(
        updates,
        "_stream_github_asset",
        lambda settings, asset: iter([b""]),
    )
    app = FastAPI()
    app.include_router(updates.router)
    client = TestClient(app)
    client.get("/api/updates/desktop/download?platform=mac&version=1.6.0")
    assert seen_versions == ["1.6.0"], "a named version pins the download target"


def test_github_errors_surface_as_502(monkeypatch):
    def boom(settings):
        raise urllib.error.URLError("unreachable")

    monkeypatch.setattr(updates, "_fetch_releases", boom)
    app = FastAPI()
    app.include_router(updates.router)
    client = TestClient(app)
    assert client.get("/api/updates/desktop/download?platform=mac&version=1.6.0").status_code == 502
