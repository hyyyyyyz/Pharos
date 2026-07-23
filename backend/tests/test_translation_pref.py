"""Whole-document PDF translation is a per-user preference, defaulting to on.

Rebuilding a paper's layout in Chinese is slow and spends API budget, and many
readers would rather keep the original in front of them and ask about a
paragraph when they get stuck. So ``User.pdf_translation`` gates the feature.

Two things are load-bearing here and are what these tests actually defend:

*Refusing must be legible.* ``POST /papers/{id}/translate`` answers 409 — not
the 404 it uses for a paper that is not yours. The 404 is a privacy measure and
has to stay uninformative; this is the caller's own setting, which they can
already read from ``GET /auth/me``, so hiding it behind a 404 would only make
the client render "no such paper" at a user who owns the paper and needs to be
told which switch to flip.

*Default-on must survive an upgrade.* The feature predates the column. An
account created before this shipped must keep working, and the column's default
is Python-side (INSERT-time) rather than a server_default — so a row written
before the column existed reads back as SQL NULL, and NULL is falsy. The read
path has to absorb that; ``test_legacy_null_row_defaults_to_on`` is the test
that says so. See also the xfail at the bottom, which records the half of this
that cannot be fixed from the API layer.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Any

import pharos.db.session as db_session
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pharos.api import auth as auth_api
from pharos.api import jobs as jobs_api
from pharos.api.auth import pdf_translation_enabled
from pharos.api.deps import current_user
from pharos.db.models import Base, Paper, User
from pharos.db.session import _add_missing_columns, init_engine, session_scope
from sqlalchemy import create_engine, select

USER_ON = "user-translate-on"
USER_OFF = "user-translate-off"
USER_LEGACY = "user-legacy-null"

PAPER_ON = "paper-on"
PAPER_OFF = "paper-off"
PAPER_LEGACY = "paper-legacy"


# ---------------------------------------------------------------- fixtures


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """A SQLite file of our own.

    ``init_engine`` memoises into module globals and returns early once an
    engine exists, so in a full-suite run whichever test module ran first would
    own the database and this one would silently write into it. Clearing the
    globals first is what makes this module hermetic — it matters more than
    usual here because one test below deliberately writes a NULL into
    ``users.pdf_translation``, and that row must not outlive this file.
    """
    db_session._engine = None
    db_session._SessionLocal = None
    engine = init_engine(tmp_path_factory.mktemp("db") / "pharos.db")

    # Reshape users.pdf_translation into the column an *upgrade* produces, so
    # this module runs against the schema real deployments will actually have.
    # ``create_all`` on a fresh database emits NOT NULL, under which the legacy
    # state is unreachable — SQLite rejects the write outright. But an upgrade
    # never goes through create_all: it goes through ALTER TABLE ADD COLUMN,
    # which cannot add a NOT NULL column and so leaves it nullable. Testing only
    # the fresh-install schema would therefore be testing the one case in which
    # the bug cannot happen.
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE users DROP COLUMN pdf_translation")
        conn.exec_driver_sql("ALTER TABLE users ADD COLUMN pdf_translation BOOLEAN")

    with session_scope() as s:
        s.add(User(id=USER_ON, email="on@example.test", password_hash="x", pdf_translation=True))
        s.add(User(id=USER_OFF, email="off@example.test", password_hash="x", pdf_translation=False))
        # No value at all: the row an upgrade leaves behind.
        s.add(User(id=USER_LEGACY, email="legacy@example.test", password_hash="x"))
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "UPDATE users SET pdf_translation = NULL WHERE id = ?", (USER_LEGACY,)
        )
    owned = ((PAPER_ON, USER_ON), (PAPER_OFF, USER_OFF), (PAPER_LEGACY, USER_LEGACY))
    for paper_id, owner in owned:
        with session_scope() as s:
            s.add(
                Paper(
                    id=paper_id,
                    user_id=owner,
                    title="Attention Is All You Need",
                    orig_sha256=f"sha-{paper_id}",
                    orig_filename="attention.pdf",
                )
            )
    yield


class _StubJobManager:
    """Records submissions instead of spawning BabelDOC."""

    def __init__(self) -> None:
        self.submitted: list[str] = []

    def submit(self, job_id: str, *_args: Any, **_kwargs: Any) -> None:
        self.submitted.append(job_id)


class _StubBlobs:
    def path(self, sha256: str, kind: str) -> str:
        return f"/nonexistent/{sha256}/{kind}.pdf"


class _StubSettings:
    def translator_config(self) -> Any:
        return type("Cfg", (), {"type": "bing"})()


@pytest.fixture()
def client() -> Iterator[TestClient]:
    """The auth and jobs routers, with the signed-in user selectable per test.

    ``current_user`` is overridden rather than driven with a real JWT: token
    minting needs an instance secret and is thoroughly covered elsewhere, and
    what is under test here is the preference, not the credential. The override
    is still a *generator* yielding a live ORM object inside ``session_scope``,
    which is what the real dependency does — so a PATCH that mutates the user is
    committed on the way out and the round-trip test is testing a real write
    rather than an in-memory object.
    """
    app = FastAPI()
    app.include_router(auth_api.router)
    app.include_router(jobs_api.router)
    app.state.settings = _StubSettings()
    app.state.job_manager = _StubJobManager()
    app.state.blobs = _StubBlobs()
    app.state.signed_in_as = USER_ON

    def _override() -> Iterator[User]:
        with session_scope() as s:
            yield s.scalar(select(User).where(User.id == app.state.signed_in_as))

    app.dependency_overrides[current_user] = _override
    with TestClient(app) as c:
        yield c


def _sign_in(client: TestClient, user_id: str) -> None:
    client.app.state.signed_in_as = user_id  # type: ignore[attr-defined]


# ------------------------------------------------------------ enforcement


def test_translate_is_refused_when_the_preference_is_off(client: TestClient) -> None:
    """409, and the body has to name the setting as the reason.

    A user who reads this message must learn that they turned something off —
    "Paper not found" would send them looking for a problem with the paper.
    """
    _sign_in(client, USER_OFF)
    r = client.post(f"/api/papers/{PAPER_OFF}/translate")

    assert r.status_code == 409
    detail = r.json()["detail"].lower()
    assert "pdf_translation" in detail or "translation" in detail
    assert "off" in detail or "turned off" in detail
    assert client.app.state.job_manager.submitted == []  # type: ignore[attr-defined]


def test_translate_is_allowed_when_the_preference_is_on(client: TestClient) -> None:
    _sign_in(client, USER_ON)
    r = client.post(f"/api/papers/{PAPER_ON}/translate")

    assert r.status_code == 202
    assert r.json()["status"] == "queued"
    assert len(client.app.state.job_manager.submitted) == 1  # type: ignore[attr-defined]


def test_refusal_does_not_depend_on_the_paper_id(client: TestClient) -> None:
    """The 409 is checked before the paper is, and that is deliberate.

    Because the answer depends only on the caller's own account, an id that does
    not exist and an id belonging to somebody else both get the identical 409.
    That is what stops the early check from becoming an existence oracle: a user
    with the preference off learns nothing about any library, their own included.
    """
    _sign_in(client, USER_OFF)

    for paper_id in ("does-not-exist", PAPER_ON):  # PAPER_ON belongs to another user
        r = client.post(f"/api/papers/{paper_id}/translate")
        assert r.status_code == 409, paper_id


def test_ownership_404_still_wins_for_a_user_who_has_translation_on(client: TestClient) -> None:
    """The privacy 404 is untouched by any of this."""
    _sign_in(client, USER_ON)
    r = client.post(f"/api/papers/{PAPER_OFF}/translate")  # someone else's paper
    assert r.status_code == 404


# --------------------------------------------------------------- exposure


def test_me_reports_the_preference(client: TestClient) -> None:
    _sign_in(client, USER_OFF)
    assert client.get("/api/auth/me").json()["pdf_translation"] is False

    _sign_in(client, USER_ON)
    assert client.get("/api/auth/me").json()["pdf_translation"] is True


def test_preference_round_trips_through_patch_me(client: TestClient) -> None:
    """PATCH sets it, the response reflects it, and it is actually persisted."""
    _sign_in(client, USER_ON)

    r = client.patch("/api/auth/me", json={"pdf_translation": False})
    assert r.status_code == 200
    assert r.json()["pdf_translation"] is False

    # Re-read through a fresh request, so this is the stored value and not the
    # object the PATCH happened to be holding.
    assert client.get("/api/auth/me").json()["pdf_translation"] is False
    with session_scope() as s:
        assert s.scalar(select(User.pdf_translation).where(User.id == USER_ON)) is False

    # And the switch works in both directions.
    assert client.patch("/api/auth/me", json={"pdf_translation": True}).json()["pdf_translation"]
    assert client.get("/api/auth/me").json()["pdf_translation"] is True


def test_patch_me_still_rejects_an_empty_body(client: TestClient) -> None:
    """Adding a second optional field must not turn ``{}`` into a silent no-op."""
    _sign_in(client, USER_ON)
    assert client.patch("/api/auth/me", json={}).status_code == 400


def test_patch_me_rejects_an_explicit_null(client: TestClient) -> None:
    """Unlike display_name, this has no "cleared" state — the column is NOT NULL.

    Accepting null here would write exactly the legacy row the read path has to
    compensate for, i.e. the API would be manufacturing its own migration bug.
    """
    _sign_in(client, USER_ON)
    assert client.patch("/api/auth/me", json={"pdf_translation": None}).status_code == 422
    with session_scope() as s:
        assert s.scalar(select(User.pdf_translation).where(User.id == USER_ON)) is not None


def test_patching_display_name_alone_leaves_the_preference_untouched(client: TestClient) -> None:
    """``exclude_unset`` semantics: omitted means "leave alone", not "reset"."""
    _sign_in(client, USER_OFF)
    r = client.patch("/api/auth/me", json={"display_name": "Ada"})
    assert r.json()["display_name"] == "Ada"
    assert r.json()["pdf_translation"] is False


# ----------------------------------------------------------- default-on


def test_a_newly_created_user_defaults_to_on() -> None:
    """The column default, exercised through a real INSERT."""
    with session_scope() as s:
        s.add(User(id="user-fresh", email="fresh@example.test", password_hash="x"))
    with session_scope() as s:
        user = s.scalar(select(User).where(User.id == "user-fresh"))
        assert user.pdf_translation is True
        assert pdf_translation_enabled(user) is True


def test_legacy_null_row_defaults_to_on(client: TestClient) -> None:
    """A row that predates the column reads as NULL — and must still be ON.

    This is the shape ``ALTER TABLE ... ADD COLUMN`` leaves behind: the column's
    default is Python-side, applied on INSERT, so it never reaches rows that
    already existed. ``None`` is falsy, so reading the attribute directly would
    switch the feature off for precisely the established accounts that "default
    on" exists to protect — the product would look like it had deleted its main
    feature from every long-standing user on upgrade.

    The row is set up in the module fixture, on a users table reshaped to the
    post-ALTER schema — the ORM cannot produce this state on a fresh database,
    because there the column is NOT NULL. Only an upgrade can, which is the
    whole point.
    """
    with session_scope() as s:
        user = s.scalar(select(User).where(User.id == USER_LEGACY))
        assert user.pdf_translation is None, "precondition: the row is genuinely NULL"
        assert pdf_translation_enabled(user) is True

    # ...and it is on through the API, not merely in the helper.
    _sign_in(client, USER_LEGACY)
    assert client.get("/api/auth/me").json()["pdf_translation"] is True
    assert client.post(f"/api/papers/{PAPER_LEGACY}/translate").status_code == 202


def test_explicit_false_is_not_mistaken_for_unset(client: TestClient) -> None:
    """The NULL tolerance above must not swallow a real opt-out."""
    with session_scope() as s:
        user = s.scalar(select(User).where(User.id == USER_OFF))
        assert pdf_translation_enabled(user) is False


# ------------------------------------------------------- known migration gap


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Pre-existing databases cannot open. users.pdf_translation is NOT NULL with "
        "no server_default, and db/session.py::_add_missing_columns raises rather "
        "than ADD COLUMN in that case — init_engine() calls it on boot, so every "
        "database written before this column existed dies at startup. Fixing it "
        "needs models.py (server_default=text('1')) and db/session.py (emit the "
        "default in the DDL, allow NOT NULL once it has one) plus a backfill; both "
        "are outside the API layer. Delete this marker when that lands."
    ),
)
def test_additive_migration_can_add_the_column_to_an_old_database(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """An existing install must survive the upgrade with the feature still on."""
    db = tmp_path_factory.mktemp("legacy") / "old.db"
    engine = create_engine(f"sqlite:///{db}", future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        # Rewind the schema to before the column existed.
        conn.exec_driver_sql("ALTER TABLE users DROP COLUMN pdf_translation")
        conn.exec_driver_sql(
            "INSERT INTO users (id, email, password_hash, is_active, created_at, token_epoch)"
            " VALUES ('old', 'old@example.test', 'h', 1, '2025-01-01 00:00:00', 0)"
        )

    _add_missing_columns(engine)  # currently raises RuntimeError

    rows = list(sqlite3.connect(db).execute("SELECT pdf_translation FROM users WHERE id = 'old'"))
    assert rows[0][0] in (1, True), "an established account must keep translation"
