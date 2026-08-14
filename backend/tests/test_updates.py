"""The public desktop update advertisement.

The endpoint exists so the desktop client can learn about a new build before
sign-in. These tests cover the three authorities and the failure posture:

- an operator pin wins and is served verbatim;
- without a pin the newest ``desktop-v*`` GitHub release is advertised, and
  non-desktop tags are skipped;
- a GitHub failure answers "no update advertised" rather than an error;
- malformed pins and malformed GitHub bodies degrade to the same shape.
"""

from __future__ import annotations

import json
import urllib.error

from pharos.api import updates
from pharos.config import Settings


def _settings(**overrides) -> Settings:
    base = {
        "desktop_update_repo": "hyyyyyyz/Pharos",
        "desktop_update_version_override": None,
    }
    base.update(overrides)
    return Settings(**base)


def _github_response(releases):
    class _Response:
        def read(self, limit):
            return json.dumps(releases).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    return _Response()


def test_override_wins_and_is_served_verbatim(monkeypatch):
    payload = updates._override_payload(_settings(desktop_update_version_override="1.4.0"))
    assert payload == {
        "version": "1.4.0",
        "url": "https://github.com/hyyyyyyz/Pharos/releases/tag/desktop-v1.4.0",
        "notes": None,
    }


def test_malformed_override_is_silence_not_garbage():
    for pinned in ("1.4", "v1.4.0", "latest", "1.4.0.SOURCE", ""):
        assert (
            updates._override_payload(_settings(desktop_update_version_override=pinned)) is None
        ), f"{pinned!r} must not be advertised"


def test_github_release_is_advertised(monkeypatch):
    monkeypatch.setattr(updates, "_github_cache", None)
    # GitHub lists releases newest first; the endpoint must pick the first
    # desktop-v* tag it finds, not the newest by numeric comparison.
    releases = [
        {
            "tag_name": "desktop-v1.4.0",
            "html_url": "https://example/releases/new",
            "body": "Fix the crash",
        },
        {"tag_name": "desktop-v1.3.0", "html_url": "https://example/releases/old", "body": ""},
    ]
    monkeypatch.setattr(
        updates.urllib.request,
        "urlopen",
        lambda request, timeout: _github_response(releases),
    )
    payload = updates._github_payload(_settings(), now=1000.0)
    assert payload["version"] == "1.4.0"
    assert payload["url"] == "https://example/releases/new"
    assert payload["notes"] == "Fix the crash"


def test_github_non_desktop_tags_are_skipped(monkeypatch):
    monkeypatch.setattr(updates, "_github_cache", None)
    releases = [
        {"tag_name": "v2.0.0", "html_url": "https://example/nope", "body": ""},
        {"tag_name": "desktop-v1.3.2", "html_url": "https://example/yes", "body": ""},
    ]
    monkeypatch.setattr(
        updates.urllib.request,
        "urlopen",
        lambda request, timeout: _github_response(releases),
    )
    payload = updates._github_payload(_settings(), now=2000.0)
    assert payload["version"] == "1.3.2"


def test_github_failure_is_no_update_not_an_error(monkeypatch):
    monkeypatch.setattr(updates, "_github_cache", None)

    def boom(request, timeout):
        raise urllib.error.URLError("unreachable")

    monkeypatch.setattr(updates.urllib.request, "urlopen", boom)
    payload = updates._github_payload(_settings(), now=3000.0)
    assert payload == {"version": None, "url": None, "notes": None}


def test_github_result_is_cached(monkeypatch):
    monkeypatch.setattr(updates, "_github_cache", None)
    calls = []

    def counting(request, timeout):
        calls.append(request)
        return _github_response(
            [{"tag_name": "desktop-v1.4.1", "html_url": "https://example/one", "body": ""}]
        )

    monkeypatch.setattr(updates.urllib.request, "urlopen", counting)
    first = updates._github_payload(_settings(), now=4000.0)
    second = updates._github_payload(_settings(), now=4000.0 + updates._GITHUB_CACHE_TTL - 1)
    assert first["version"] == "1.4.1"
    assert second["version"] == "1.4.1"
    assert len(calls) == 1, "the cached hour must not hit GitHub again"
    third = updates._github_payload(_settings(), now=4000.0 + updates._GITHUB_CACHE_TTL + 1)
    assert third["version"] == "1.4.1"
    assert len(calls) == 2, "an expired cache must re-fetch"


def test_malformed_github_body_degrades_to_no_update(monkeypatch):
    monkeypatch.setattr(updates, "_github_cache", None)
    monkeypatch.setattr(
        updates.urllib.request,
        "urlopen",
        lambda request, timeout: _github_response({"tag_name": "desktop-v1.4.0"}),
    )
    payload = updates._github_payload(_settings(), now=5000.0)
    assert payload == {"version": None, "url": None, "notes": None}


def test_endpoint_answers_without_a_token(monkeypatch):
    monkeypatch.setattr(updates, "_github_cache", None)
    monkeypatch.setattr(
        updates.urllib.request,
        "urlopen",
        lambda request, timeout: _github_response(
            [{"tag_name": "desktop-v1.4.2", "html_url": "https://example/two", "body": ""}]
        ),
    )
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(updates.router)
    client = TestClient(app)
    response = client.get("/updates/desktop/latest")
    assert response.status_code == 200
    assert set(response.json()) == {"version", "url", "notes"}
    assert response.json()["version"] == "1.4.2"
