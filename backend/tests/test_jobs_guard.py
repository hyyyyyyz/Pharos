"""A trashed paper must not be translatable — nor another user's paper.

Found by review, not by a user: ``POST /papers/{id}/translate`` happily returned
202 for a soft-deleted paper. Wasting an engine slot on something the user
deleted is the mild half. The sharp half is a race — a concurrent purge
``rmtree``s the blob directory while the BabelDOC subprocess is still writing
mono/dual PDFs into it, so the job crashes or leaves torn output behind.

The ownership cases were added with multi-user accounts. ``_start_job_row``
returns ``None`` for all three of "no such paper", "trashed", and "someone
else's", because the endpoint turns ``None`` into a 404 and those three must be
indistinguishable from outside: a 403 for the last one would confirm the id is
real and let a caller enumerate other people's libraries.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from pharos.api.jobs import _start_job_row
from pharos.db import session as db_session
from pharos.db.models import Paper, User
from pharos.db.session import init_engine, session_scope

OWNER = "user-owner"
OTHER = "user-other"


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """A real SQLite file — init_engine memoises, so this must run once.

    Both users are inserted for real rather than referenced by bare id: SQLite
    foreign keys are enforced here, so ``Paper.user_id`` must name a live row.
    """
    # The session module intentionally memoises one production engine.  Test
    # modules need to reset those globals before claiming their own temporary
    # database, otherwise collection order can leak users and papers across
    # modules and make the full suite fail while this file passes in isolation.
    if db_session._engine is not None:
        db_session._engine.dispose()
    db_session._engine = None
    db_session._SessionLocal = None
    db_session._fts5_available = None
    init_engine(tmp_path_factory.mktemp("db") / "pharos.db")
    with session_scope() as s:
        for uid in (OWNER, OTHER):
            s.add(User(id=uid, email=f"{uid}@example.test", password_hash="x"))
    yield


def _add(paper_id: str, *, deleted: bool, user_id: str = OWNER) -> None:
    with session_scope() as s:
        s.add(
            Paper(
                id=paper_id,
                user_id=user_id,
                title="Attention Is All You Need",
                orig_sha256=f"sha-{paper_id}",
                orig_filename="attention.pdf",
                deleted_at=datetime.now(UTC) if deleted else None,
            )
        )


def test_live_paper_can_start_a_job() -> None:
    _add("live", deleted=False)
    created = _start_job_row("live", OWNER, "bing", "zh")
    assert created is not None
    _job_id, sha, _lang = created
    assert sha == "sha-live"


def test_trashed_paper_is_treated_as_absent() -> None:
    _add("trashed", deleted=True)
    assert _start_job_row("trashed", OWNER, "bing", "zh") is None


def test_unknown_paper_is_absent() -> None:
    assert _start_job_row("nope", OWNER, "bing", "zh") is None


def test_another_users_paper_is_absent() -> None:
    """The paper is live and real — it is simply not the caller's."""
    _add("theirs", deleted=False, user_id=OTHER)
    assert _start_job_row("theirs", OWNER, "bing", "zh") is None


def test_legacy_unowned_paper_is_absent() -> None:
    """A pre-accounts row (``user_id IS NULL``) belongs to nobody until claimed.

    Failing closed matters: these rows are exactly what a ``NULL`` owner id would
    match if one ever leaked into the filter, so no authenticated caller may
    reach them by accident.
    """
    _add("legacy", deleted=False, user_id=None)  # type: ignore[arg-type]
    assert _start_job_row("legacy", OWNER, "bing", "zh") is None
