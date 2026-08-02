"""``GET /api/auth/status``: what a sign-in screen may ask before it has a token."""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("PHAROS_DATA_DIR", tempfile.mkdtemp(prefix="pharos-authstatus-"))
os.environ.setdefault("PHAROS_AUTH_SECRET", "test-secret-test-secret-test-secret")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from pharos.api import auth  # noqa: E402
from pharos.api.deps import get_settings  # noqa: E402
from pharos.config import Settings  # noqa: E402


def _client(*, allow_registration: bool) -> TestClient:
    app = FastAPI()
    app.include_router(auth.router)
    app.dependency_overrides[get_settings] = lambda: Settings(
        auth_secret="test-secret-test-secret-test-secret",
        allow_registration=allow_registration,
    )
    return TestClient(app)


def test_reports_registration_open() -> None:
    with _client(allow_registration=True) as client:
        response = client.get("/api/auth/status")
    assert response.status_code == 200
    assert response.json() == {"allow_registration": True}


def test_reports_registration_closed() -> None:
    with _client(allow_registration=False) as client:
        response = client.get("/api/auth/status")
    assert response.status_code == 200
    assert response.json() == {"allow_registration": False}


def test_needs_no_token() -> None:
    """The whole point: it is read before anyone has signed in."""
    with _client(allow_registration=True) as client:
        response = client.get("/api/auth/status")
    assert response.status_code == 200


def test_says_nothing_but_the_one_flag() -> None:
    """Anything readable without a token is public, so the surface stays minimal.

    A regression that started returning, say, a user count or the instance's
    configuration would be a disclosure rather than a feature.
    """
    with _client(allow_registration=True) as client:
        body = client.get("/api/auth/status").json()
    assert set(body) == {"allow_registration"}
