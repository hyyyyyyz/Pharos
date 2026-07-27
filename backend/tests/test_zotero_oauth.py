"""Zotero OAuth handshake, replay protection, and credential-at-rest tests."""

from __future__ import annotations

import urllib.parse
from collections.abc import Iterator

import pharos.db.session as db_session
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pharos.api import zotero as zotero_api
from pharos.api.deps import current_user
from pharos.config import Settings
from pharos.db.models import User, ZoteroLink, ZoteroOAuthAttempt
from pharos.db.session import init_engine, session_scope
from pharos.services import zotero as zotero_client
from pharos.services import zotero_oauth
from pharos.services.credentials import CredentialCipher, CredentialError
from sqlalchemy import select

USER_ID = "oauth-user"
API_KEY = "A" * 24


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "auth_secret": "auth-" + "a" * 48,
        "credential_secret": "cred-" + "b" * 48,
        "zotero_oauth_client_key": "consumer-key",
        "zotero_oauth_client_secret": "consumer-secret",
        "zotero_oauth_callback_url": "https://pharos.selab.top/api/zotero/oauth/callback",
        "zotero_oauth_return_url": "https://pharos.selab.top/",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    db_session._engine = None
    db_session._SessionLocal = None
    init_engine(tmp_path_factory.mktemp("zotero-oauth") / "pharos.db")
    with session_scope() as session:
        session.add(User(id=USER_ID, email="oauth@example.test", password_hash="x"))
    yield
    db_session._engine = None
    db_session._SessionLocal = None


@pytest.fixture()
def app_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    settings = _settings()
    app = FastAPI()
    app.state.settings = settings
    app.include_router(zotero_api.router)

    def _signed_in() -> Iterator[User]:
        with session_scope() as session:
            yield session.scalar(select(User).where(User.id == USER_ID))

    app.dependency_overrides[current_user] = _signed_in
    monkeypatch.setattr(zotero_api, "get_app_settings", lambda: settings)
    monkeypatch.setattr(zotero_api.syncer, "is_running", lambda _user_id: False)
    with TestClient(app, base_url="https://pharos.selab.top") as client:
        yield client


@pytest.fixture(autouse=True)
def _clean_link_and_attempts() -> Iterator[None]:
    with session_scope() as session:
        for row in session.scalars(select(ZoteroOAuthAttempt)).all():
            session.delete(row)
        for row in session.scalars(select(ZoteroLink)).all():
            session.delete(row)
    yield


def _identity(user_id: str = "12345") -> zotero_client.ZoteroIdentity:
    return zotero_client.ZoteroIdentity(
        user_id=user_id,
        username="researcher",
        library_read=True,
        files_read=False,
        matches_claim=True,
    )


def test_credential_cipher_round_trip_tamper_and_rotation() -> None:
    old = CredentialCipher("old-secret")
    stored = old.protect(API_KEY)
    assert API_KEY not in stored
    assert old.reveal(stored) == API_KEY

    with pytest.raises(CredentialError):
        old.reveal(f"{stored[:-1]}x")

    rotated = CredentialCipher("new-secret", "old-secret")
    fresh = rotated.normalize(stored)
    assert fresh != stored
    assert rotated.reveal(fresh) == API_KEY
    with pytest.raises(CredentialError):
        CredentialCipher("wrong-secret").reveal(fresh)


def test_oauth_header_is_normalized_and_signed() -> None:
    header = zotero_oauth.authorization_header(
        "POST",
        "https://photos.example.net/initiate",
        consumer_key="dpf43f3p2l4k3l03",
        consumer_secret="kd94hf93k423kf44",
        callback="http://printer.example.com/ready",
        nonce="wIjqoS",
        timestamp=137131200,
    )
    # oauth_version is optional in RFC 5849's printed example; our client signs
    # it explicitly, which produces this deterministic vector.
    assert 'oauth_signature="msrTmwtDEKqeVXeJaufuiXOpbJI%3D"' in header
    assert 'oauth_callback="http%3A%2F%2Fprinter.example.com%2Fready"' in header


def test_authorization_url_requests_read_only_personal_library() -> None:
    parsed = urllib.parse.urlsplit(zotero_oauth.authorization_url("temporary"))
    query = urllib.parse.parse_qs(parsed.query)
    assert parsed.scheme == "https" and parsed.netloc == "www.zotero.org"
    assert query == {
        "oauth_token": ["temporary"],
        "name": ["Pharos"],
        "library_access": ["1"],
        "notes_access": ["0"],
        "write_access": ["0"],
        "all_groups": ["none"],
    }


def test_item_query_uses_a_supported_negative_type_filter() -> None:
    parsed = urllib.parse.urlsplit(zotero_client._items_url("12345", start=0))
    query = urllib.parse.parse_qs(parsed.query)

    assert query["itemType"] == ["-attachment"]
    assert query["format"] == ["json"]
    assert query["limit"] == ["100"]
    assert query["start"] == ["0"]


def test_status_reports_when_one_click_oauth_is_available(app_client: TestClient) -> None:
    response = app_client.get("/api/zotero/status")
    assert response.status_code == 200
    assert response.json()["oauth_available"] is True


def test_unconfigured_oauth_fails_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(zotero_oauth_client_key=None)
    app = FastAPI()
    app.state.settings = settings
    app.include_router(zotero_api.router)

    def _signed_in() -> Iterator[User]:
        with session_scope() as session:
            yield session.scalar(select(User).where(User.id == USER_ID))

    app.dependency_overrides[current_user] = _signed_in
    monkeypatch.setattr(zotero_api, "get_app_settings", lambda: settings)
    with TestClient(app, base_url="https://pharos.selab.top") as client:
        assert client.get("/api/zotero/status").json()["oauth_available"] is False
        response = client.post("/api/zotero/oauth/start")
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"].lower()


def test_full_oauth_flow_encrypts_key_and_starts_sync(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, str] = {}
    submitted: list[str] = []

    def _request_token(_key: str, _secret: str, callback: str) -> zotero_oauth.RequestToken:
        captured["callback"] = callback
        return zotero_oauth.RequestToken("temporary-token", "temporary-secret")

    monkeypatch.setattr(zotero_oauth, "request_token", _request_token)
    monkeypatch.setattr(
        zotero_oauth,
        "access_token",
        lambda *_args, **_kwargs: zotero_oauth.AccessToken(API_KEY, "12345", "researcher"),
    )
    monkeypatch.setattr(zotero_client, "verify", lambda *_args, **_kwargs: _identity())
    monkeypatch.setattr(
        zotero_api.syncer,
        "submit",
        lambda user_id: submitted.append(user_id) or object(),
    )

    started = app_client.post("/api/zotero/oauth/start")
    assert started.status_code == 200
    assert started.json()["authorize_url"].startswith("https://www.zotero.org/oauth/authorize?")
    state = urllib.parse.parse_qs(urllib.parse.urlsplit(captured["callback"]).query)["state"][0]

    with session_scope() as session:
        attempt = session.scalar(select(ZoteroOAuthAttempt))
        assert attempt is not None
        assert attempt.request_token_hash != "temporary-token"
        assert "temporary-secret" not in attempt.request_token_secret

    callback = app_client.get(
        "/api/zotero/oauth/callback",
        params={"state": state, "oauth_token": "temporary-token", "oauth_verifier": "verified"},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert callback.headers["location"] == "https://pharos.selab.top/?zotero=connected"
    assert callback.headers["cache-control"] == "no-store"
    assert submitted == [USER_ID]

    with session_scope() as session:
        link = session.scalar(select(ZoteroLink).where(ZoteroLink.user_id == USER_ID))
        assert link is not None
        assert API_KEY not in link.api_key
        assert CredentialCipher.from_settings(_settings()).reveal(link.api_key) == API_KEY

    replay = app_client.get(
        "/api/zotero/oauth/callback",
        params={"state": state, "oauth_token": "temporary-token", "oauth_verifier": "verified"},
        follow_redirects=False,
    )
    assert replay.headers["location"].endswith("?zotero=invalid")


def test_oauth_callback_requires_the_starting_browser_cookie(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, str] = {}

    def _request_token(_key: str, _secret: str, callback: str) -> zotero_oauth.RequestToken:
        captured["callback"] = callback
        return zotero_oauth.RequestToken("cookie-token", "cookie-secret")

    monkeypatch.setattr(zotero_oauth, "request_token", _request_token)
    assert app_client.post("/api/zotero/oauth/start").status_code == 200
    state = urllib.parse.parse_qs(urllib.parse.urlsplit(captured["callback"]).query)["state"][0]

    # A copied authorization link opened in another browser has no HttpOnly flow
    # cookie and therefore cannot attach its Zotero account to this Pharos user.
    with TestClient(app_client.app, base_url="https://pharos.selab.top") as other_browser:
        callback = other_browser.get(
            "/api/zotero/oauth/callback",
            params={"state": state, "oauth_token": "cookie-token", "oauth_verifier": "x"},
            follow_redirects=False,
        )
    assert callback.headers["location"].endswith("?zotero=invalid")
    with session_scope() as session:
        assert session.scalar(select(ZoteroLink).where(ZoteroLink.user_id == USER_ID)) is None


def test_desktop_oauth_uses_a_bound_one_time_handoff(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, str] = {}
    submitted: list[str] = []

    def _request_token(_key: str, _secret: str, callback: str) -> zotero_oauth.RequestToken:
        captured["callback"] = callback
        return zotero_oauth.RequestToken("desktop-token", "desktop-token-secret")

    monkeypatch.setattr(zotero_oauth, "request_token", _request_token)
    monkeypatch.setattr(
        zotero_oauth,
        "access_token",
        lambda *_args, **_kwargs: zotero_oauth.AccessToken(API_KEY, "12345", "researcher"),
    )
    monkeypatch.setattr(zotero_client, "verify", lambda *_args, **_kwargs: _identity())
    monkeypatch.setattr(
        zotero_api.syncer,
        "submit",
        lambda user_id: submitted.append(user_id) or object(),
    )

    started = app_client.post("/api/zotero/oauth/desktop/start")
    assert started.status_code == 200
    desktop_secret = started.json()["desktop_secret"]
    callback_query = urllib.parse.parse_qs(
        urllib.parse.urlsplit(captured["callback"]).query
    )
    assert callback_query["flow"] == ["desktop"]
    assert desktop_secret not in captured["callback"]
    assert desktop_secret not in started.json()["authorize_url"]

    callback = app_client.get(
        "/api/zotero/oauth/callback",
        params={
            "flow": "desktop",
            "state": callback_query["state"][0],
            "oauth_token": "desktop-token",
            "oauth_verifier": "desktop-verifier",
        },
        follow_redirects=False,
    )
    assert callback.status_code == 303
    location = callback.headers["location"]
    parsed_location = urllib.parse.urlsplit(location)
    handoff_query = urllib.parse.parse_qs(parsed_location.query)
    assert (parsed_location.scheme, parsed_location.netloc, parsed_location.path) == (
        "pharos",
        "oauth",
        "/zotero",
    )
    code = handoff_query["code"][0]
    for secret in (
        API_KEY,
        desktop_secret,
        "desktop-token",
        "desktop-token-secret",
        "desktop-verifier",
    ):
        assert secret not in location

    with session_scope() as session:
        attempt = session.scalar(select(ZoteroOAuthAttempt))
        assert attempt is not None
        assert attempt.flow_kind == "desktop"
        assert attempt.browser_state_hash != desktop_secret
        assert attempt.handoff_code_hash != code
        assert attempt.handoff_api_key is not None
        assert API_KEY not in attempt.handoff_api_key
        assert session.scalar(select(ZoteroLink).where(ZoteroLink.user_id == USER_ID)) is None

    finished = app_client.post(
        "/api/zotero/oauth/desktop/finish",
        json={"code": code, "desktop_secret": desktop_secret},
    )
    assert finished.status_code == 200
    assert finished.json()["linked"] is True
    assert submitted == [USER_ID]

    with session_scope() as session:
        link = session.scalar(select(ZoteroLink).where(ZoteroLink.user_id == USER_ID))
        attempt = session.scalar(select(ZoteroOAuthAttempt))
        assert link is not None and attempt is not None
        assert API_KEY not in link.api_key
        assert attempt.handoff_api_key is None
        assert attempt.handoff_code_hash is None

    replay = app_client.post(
        "/api/zotero/oauth/desktop/finish",
        json={"code": code, "desktop_secret": desktop_secret},
    )
    assert replay.status_code == 400


def test_desktop_handoff_is_bound_to_the_starting_pharos_user(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, str] = {}
    other_id = "oauth-other-user"

    def _request_token(_key: str, _secret: str, callback: str) -> zotero_oauth.RequestToken:
        captured["callback"] = callback
        return zotero_oauth.RequestToken("bound-token", "bound-secret")

    monkeypatch.setattr(zotero_oauth, "request_token", _request_token)
    monkeypatch.setattr(
        zotero_oauth,
        "access_token",
        lambda *_args, **_kwargs: zotero_oauth.AccessToken(API_KEY, "12345", "researcher"),
    )
    monkeypatch.setattr(zotero_client, "verify", lambda *_args, **_kwargs: _identity())

    started = app_client.post("/api/zotero/oauth/desktop/start")
    callback_query = urllib.parse.parse_qs(
        urllib.parse.urlsplit(captured["callback"]).query
    )
    callback = app_client.get(
        "/api/zotero/oauth/callback",
        params={
            "flow": "desktop",
            "state": callback_query["state"][0],
            "oauth_token": "bound-token",
            "oauth_verifier": "verified",
        },
        follow_redirects=False,
    )
    code = urllib.parse.parse_qs(urllib.parse.urlsplit(callback.headers["location"]).query)[
        "code"
    ][0]

    with session_scope() as session:
        other = session.scalar(select(User).where(User.id == other_id))
        if other is None:
            session.add(
                User(id=other_id, email="oauth-other@example.test", password_hash="x")
            )

    def _other_user() -> Iterator[User]:
        with session_scope() as session:
            yield session.scalar(select(User).where(User.id == other_id))

    original_override = app_client.app.dependency_overrides[current_user]
    app_client.app.dependency_overrides[current_user] = _other_user
    try:
        stolen = app_client.post(
            "/api/zotero/oauth/desktop/finish",
            json={"code": code, "desktop_secret": started.json()["desktop_secret"]},
        )
    finally:
        app_client.app.dependency_overrides[current_user] = original_override
    assert stolen.status_code == 400
    with session_scope() as session:
        assert session.scalar(select(ZoteroLink).where(ZoteroLink.user_id == other_id)) is None


def test_manual_link_is_still_supported_and_encrypted(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(zotero_client, "verify", lambda *_args, **_kwargs: _identity())
    response = app_client.post(
        "/api/zotero/link", json={"zotero_user_id": "12345", "api_key": API_KEY}
    )
    assert response.status_code == 200
    with session_scope() as session:
        link = session.scalar(select(ZoteroLink).where(ZoteroLink.user_id == USER_ID))
        assert link is not None
        assert API_KEY not in link.api_key
        assert CredentialCipher.from_settings(_settings()).reveal(link.api_key) == API_KEY
