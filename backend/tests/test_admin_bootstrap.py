"""The first administrator can be created without an existing administrator."""

from __future__ import annotations

from collections.abc import Iterator

import pharos.db.session as db_session
import pytest
from pharos.auth.bootstrap import apply_configured_admins, promote_by_email
from pharos.cli import main as cli_main
from pharos.config import Settings
from pharos.db.models import User
from pharos.db.session import init_engine, session_scope
from sqlalchemy import select


@pytest.fixture(autouse=True)
def _db(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    if db_session._engine is not None:
        db_session._engine.dispose()
    db_session._engine = None
    db_session._SessionLocal = None
    db_session._fts5_available = None
    init_engine(tmp_path_factory.mktemp("db") / "pharos.db")
    with session_scope() as s:
        s.add(User(id="b1", email="boss@example.test", password_hash="x"))
        s.add(User(id="b2", email="other@example.test", password_hash="x"))
    yield


def _admin(email: str) -> bool:
    with session_scope() as s:
        return bool(s.scalar(select(User.is_admin).where(User.email == email)))


def test_settings_parse_and_casefold() -> None:
    cfg = Settings(admin_emails=" Boss@Example.test , ,x@y.z")
    assert cfg.admin_email_list == ["boss@example.test", "x@y.z"]


def test_configured_address_is_promoted_only_if_registered() -> None:
    assert apply_configured_admins(["boss@example.test", "nobody@example.test"]) == 1
    assert _admin("boss@example.test") and not _admin("other@example.test")
    assert apply_configured_admins(["boss@example.test"]) == 0  # idempotent


def test_registration_does_not_confer_admin_even_for_a_listed_address(monkeypatch) -> None:
    """Listing an address that has not registered yet must not make the first
    person to register it an admin — that is the squatting hole."""
    apply_configured_admins(["future@example.test"])
    with session_scope() as s:
        s.add(User(id="b3", email="future@example.test", password_hash="x"))
    assert not _admin("future@example.test")


def test_cli_promote(capsys) -> None:
    assert cli_main(["promote", "OTHER@example.test"]) == 0
    assert _admin("other@example.test")
    assert cli_main(["promote", "ghost@example.test"]) == 1
    assert cli_main(["admins"]) == 0
    assert "other@example.test" in capsys.readouterr().out
    assert promote_by_email("boss@example.test")
