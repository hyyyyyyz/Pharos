"""Administrator console: access control, lockout guards, and secret hygiene.

The three properties worth protecting here, in order of how much damage their
absence does:

1. An ordinary account cannot reach the console or promote itself.
2. An administrator cannot remove the last administrator — including by
   demoting or deactivating themselves — because recovering from that needs
   direct database access.
3. No response ever carries an API key or a password hash.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from pharos.db.models import Paper, User
from pharos.db.session import session_scope
from pharos.main import create_app


#: ``init_engine`` memoises the engine for the whole process, so every test in
#: this module shares one database and one app. Accounts are therefore given
#: unique emails per test rather than relying on a fresh database each time.
@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    import os

    os.environ["PHAROS_DATA_DIR"] = str(tmp_path_factory.mktemp("admin-data"))
    os.environ["PHAROS_DAILY_ENABLED"] = "0"
    os.environ.setdefault("PHAROS_AUTH_SECRET", "x" * 48)
    from pharos import config

    config.get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    config.get_settings.cache_clear()


def _account(client: TestClient, email: str, *, admin: bool = False) -> dict:
    """Register a fresh account.

    The email carries a random suffix because the whole module shares one
    database (``init_engine`` memoises), so a literal address would collide
    with the same test's earlier run or with a sibling test.
    """
    local, _, domain = email.partition("@")
    unique = f"{local}-{uuid.uuid4().hex[:8]}@{domain}"
    response = client.post(
        "/api/auth/register", json={"email": unique, "password": "a-long-password-1234"}
    )
    assert response.status_code == 201, f"注册失败 {response.status_code}: {response.text[:200]}"
    body = response.json()
    if admin:
        with session_scope() as session:
            session.get(User, body["user"]["id"]).is_admin = True
    return {"id": body["user"]["id"], "headers": {"Authorization": f"Bearer {body['token']}"}}


def _email_of(user_id: str) -> str:
    """The stored (casefolded) address, which is what deletion asks to confirm."""
    with session_scope() as session:
        return session.get(User, user_id).email


ADMIN_PATHS = ["/api/admin/stats", "/api/admin/users", "/api/admin/providers"]


@pytest.mark.parametrize("path", ADMIN_PATHS)
def test_console_rejects_anonymous_callers(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", ADMIN_PATHS)
def test_console_rejects_ordinary_accounts(client: TestClient, path: str) -> None:
    """403 rather than 404.

    The opposite of the paper endpoints, deliberately: a paper id must look
    nonexistent to a stranger because its existence leaks library membership,
    whereas the console's existence is public knowledge and the caller is
    already authenticated.
    """
    user = _account(client, "user@test.io")
    assert client.get(path, headers=user["headers"]).status_code == 403


@pytest.mark.parametrize("path", ADMIN_PATHS)
def test_console_admits_administrators(client: TestClient, path: str) -> None:
    admin = _account(client, "admin@test.io", admin=True)
    assert client.get(path, headers=admin["headers"]).status_code == 200


def test_ordinary_account_cannot_promote_itself(client: TestClient) -> None:
    """The whole point of the role: it is not self-service."""
    user = _account(client, "climber@test.io")
    response = client.patch(
        f"/api/admin/users/{user['id']}", headers=user["headers"], json={"is_admin": True}
    )
    assert response.status_code == 403
    # And the attempt left no trace on the row.
    with session_scope() as session:
        assert session.get(User, user["id"]).is_admin is False


def test_admin_cannot_demote_themselves(client: TestClient) -> None:
    admin = _account(client, "admin@test.io", admin=True)
    response = client.patch(
        f"/api/admin/users/{admin['id']}", headers=admin["headers"], json={"is_admin": False}
    )
    assert response.status_code == 409
    with session_scope() as session:
        assert session.get(User, admin["id"]).is_admin is True


def test_admin_cannot_deactivate_themselves(client: TestClient) -> None:
    admin = _account(client, "admin@test.io", admin=True)
    response = client.patch(
        f"/api/admin/users/{admin['id']}", headers=admin["headers"], json={"is_active": False}
    )
    assert response.status_code == 409


def test_the_last_administrator_cannot_be_removed_by_another(client: TestClient) -> None:
    """Two admins: demoting one is fine, demoting the survivor is not."""
    first = _account(client, "first@test.io", admin=True)
    second = _account(client, "second@test.io", admin=True)

    assert (
        client.patch(
            f"/api/admin/users/{second['id']}",
            headers=first["headers"],
            json={"is_admin": False},
        ).status_code
        == 200
    )
    # `first` is now alone and may not demote themselves either.
    assert (
        client.patch(
            f"/api/admin/users/{first['id']}",
            headers=first["headers"],
            json={"is_admin": False},
        ).status_code
        == 409
    )


def test_deactivation_revokes_outstanding_tokens_immediately(client: TestClient) -> None:
    """A suspended account must lose access now, not when its token expires."""
    admin = _account(client, "admin@test.io", admin=True)
    user = _account(client, "user@test.io")
    assert client.get("/api/papers", headers=user["headers"]).status_code == 200

    client.patch(
        f"/api/admin/users/{user['id']}", headers=admin["headers"], json={"is_active": False}
    )
    assert client.get("/api/papers", headers=user["headers"]).status_code == 401


def test_user_listing_never_exposes_password_hashes(client: TestClient) -> None:
    admin = _account(client, "admin@test.io", admin=True)
    _account(client, "user@test.io")
    body = client.get("/api/admin/users", headers=admin["headers"]).text
    assert "password" not in body.lower()
    assert "argon2" not in body.lower()


def test_provider_view_never_exposes_a_key(client: TestClient) -> None:
    """A configured key is reported as present, but only by its last four chars.

    The app caches its Settings at startup, so the key is injected into a fresh
    Settings and rendered through the same response model the endpoint uses —
    which is where the redaction actually lives.
    """
    from pharos.api.admin import list_providers
    from pharos.config import Settings

    secret = "sk-thisisatotallysecretkey-9x8Q"
    settings = Settings(_env_file=None, deepseek_api_key=secret, chat_provider="deepseek")
    rendered = list_providers(_admin=None, settings=settings)  # type: ignore[arg-type]

    body = rendered.model_dump_json()
    assert secret not in body
    deepseek = next(p for p in rendered.providers if p.name == "deepseek")
    assert deepseek.configured is True
    assert deepseek.key_hint == secret[-4:]

    # And the live endpoint stays clean too.
    admin = _account(client, "admin@test.io", admin=True)
    response = client.get("/api/admin/providers", headers=admin["headers"])
    assert response.status_code == 200
    assert "sk-" not in response.text


def test_user_search_filters_by_email(client: TestClient) -> None:
    """Searching narrows to the matching account and excludes the others."""
    admin = _account(client, "admin@test.io", admin=True)
    # A per-run token, so repeated runs against the module's shared database
    # cannot accumulate matches for the same needle.
    needle = f"botanist{uuid.uuid4().hex[:6]}"
    target = _account(client, f"{needle}@lab.test")
    _account(client, "student@school.test")

    page = client.get(f"/api/admin/users?q={needle}", headers=admin["headers"]).json()
    assert page["total"] == 1
    assert page["users"][0]["id"] == target["id"]


def test_stats_counts_accounts(client: TestClient) -> None:
    """Asserted as deltas: the module shares one database, so absolute totals
    depend on which tests ran first."""
    admin = _account(client, "admin@test.io", admin=True)
    before = client.get("/api/admin/stats", headers=admin["headers"]).json()

    _account(client, "user@test.io")
    _account(client, "second-admin@test.io", admin=True)
    after = client.get("/api/admin/stats", headers=admin["headers"]).json()

    assert after["users"] == before["users"] + 2
    assert after["admins"] == before["admins"] + 1


def test_empty_patch_is_rejected(client: TestClient) -> None:
    admin = _account(client, "admin@test.io", admin=True)
    user = _account(client, "user@test.io")
    assert (
        client.patch(
            f"/api/admin/users/{user['id']}", headers=admin["headers"], json={}
        ).status_code
        == 400
    )


def test_unknown_user_is_404(client: TestClient) -> None:
    admin = _account(client, "admin@test.io", admin=True)
    assert (
        client.patch(
            f"/api/admin/users/deadbeef", headers=admin["headers"], json={"is_active": True}
        ).status_code
        == 404
    )


# --------------------------------------------------------------------------- #
# deletion
# --------------------------------------------------------------------------- #


def _pdf_bytes(text: str) -> bytes:
    """One-page PDF bytes.

    Note these are NOT reproducible across calls — PyMuPDF stamps a creation
    time — so a test that needs two users to share one blob must upload the
    *same* bytes object rather than regenerating it.
    """
    import pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), text, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def _upload(client: TestClient, headers: dict, source: str | bytes) -> dict:
    data = _pdf_bytes(source) if isinstance(source, str) else source
    response = client.post(
        "/api/papers", headers=headers, files={"file": ("p.pdf", data, "application/pdf")}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _blob_dir(sha256: str):
    from pharos.config import get_settings

    return get_settings().files_dir / sha256


def test_deleting_a_user_removes_their_papers_and_blobs(client: TestClient) -> None:
    admin = _account(client, "admin@test.io", admin=True)
    victim = _account(client, "victim@test.io")
    paper = _upload(client, victim["headers"], f"only-{uuid.uuid4().hex}")

    with session_scope() as session:
        sha = session.get(Paper, paper["id"]).orig_sha256
    assert _blob_dir(sha).is_dir()

    response = client.request(
        "DELETE",
        f"/api/admin/users/{victim['id']}",
        headers=admin["headers"],
        params={"confirm_email": _email_of(victim["id"])},
    )
    assert response.status_code == 204

    with session_scope() as session:
        assert session.get(User, victim["id"]) is None
        assert session.get(Paper, paper["id"]) is None
    # An unshared blob goes with the row rather than becoming an orphan.
    assert not _blob_dir(sha).exists()


def test_deleting_a_user_keeps_a_blob_another_user_still_references(
    client: TestClient,
) -> None:
    """The case a naive cascade-plus-rmtree would get wrong.

    Two researchers upload the same paper: two rows, one content-addressed blob.
    Deleting one account must not destroy the file the other still reads.
    """
    admin = _account(client, "admin@test.io", admin=True)
    leaving = _account(client, "leaving@test.io")
    staying = _account(client, "staying@test.io")

    # One byte string, uploaded twice — this is what produces one blob and two
    # rows, and it is the situation a naive delete would corrupt.
    shared = _pdf_bytes(f"shared-{uuid.uuid4().hex}")
    left = _upload(client, leaving["headers"], shared)
    kept = _upload(client, staying["headers"], shared)

    with session_scope() as session:
        sha = session.get(Paper, left["id"]).orig_sha256
        assert session.get(Paper, kept["id"]).orig_sha256 == sha  # one blob, two rows
    assert left["id"] != kept["id"]

    response = client.request(
        "DELETE",
        f"/api/admin/users/{leaving['id']}",
        headers=admin["headers"],
        params={"confirm_email": _email_of(leaving["id"])},
    )
    assert response.status_code == 204

    with session_scope() as session:
        assert session.get(Paper, left["id"]) is None
        assert session.get(Paper, kept["id"]) is not None
    assert _blob_dir(sha).is_dir(), "still-referenced PDF was destroyed"
    # And the surviving user can actually still read it.
    assert (
        client.get(f"/api/papers/{kept['id']}/pdf/original", headers=staying["headers"]).status_code
        == 200
    )


def test_delete_requires_the_matching_confirmation_email(client: TestClient) -> None:
    admin = _account(client, "admin@test.io", admin=True)
    user = _account(client, "safe@test.io")
    response = client.request(
        "DELETE",
        f"/api/admin/users/{user['id']}",
        headers=admin["headers"],
        params={"confirm_email": "someone-else@test.io"},
    )
    assert response.status_code == 400
    with session_scope() as session:
        assert session.get(User, user["id"]) is not None


def test_admin_cannot_delete_themselves(client: TestClient) -> None:
    admin = _account(client, "admin@test.io", admin=True)
    response = client.request(
        "DELETE",
        f"/api/admin/users/{admin['id']}",
        headers=admin["headers"],
        params={"confirm_email": _email_of(admin["id"])},
    )
    assert response.status_code == 409
    with session_scope() as session:
        assert session.get(User, admin["id"]) is not None


def test_ordinary_account_cannot_delete_anyone(client: TestClient) -> None:
    user = _account(client, "user@test.io")
    victim = _account(client, "victim@test.io")
    response = client.request(
        "DELETE",
        f"/api/admin/users/{victim['id']}",
        headers=user["headers"],
        params={"confirm_email": _email_of(victim["id"])},
    )
    assert response.status_code == 403
    with session_scope() as session:
        assert session.get(User, victim["id"]) is not None
