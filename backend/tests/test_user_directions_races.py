"""Two regressions found by attacking the per-user directions feature.

Both are cases where the code was *almost* right and the gap only opens under a
condition the happy path never reaches — a second concurrent request, or a
non-ASCII capital letter. Neither is caught by a test that exercises one user
doing one thing at a time, which is why they are pinned separately here.

Scoped to this module's own user ids and paper ids, never truncating a table:
``init_engine`` memoises, so every test module shares one database and a broad
cleanup here would be another file's failure.
"""

from __future__ import annotations

import threading

import pytest
from pharos.daily.directions import DIRECTIONS
from pharos.daily.service import _feed_for, _keyword_filter, reader_directions
from pharos.daily.user_directions import ensure_seeded
from pharos.db.models import DailyPaper, User, UserDailyConfig, UserDirection
from pharos.db.session import init_engine, session_scope
from sqlalchemy import delete, select

RACER = "races-racer"
FOLDER = "races-folder"
_USERS = (RACER, FOLDER)

#: Prefix for this module's papers, so cleanup never touches another file's.
_PAPER_DATE = "1999-01-02"


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory: pytest.TempPathFactory) -> None:
    init_engine(tmp_path_factory.mktemp("db") / "pharos.db")
    with session_scope() as s:
        for uid in _USERS:
            if s.get(User, uid) is None:
                s.add(User(id=uid, email=f"{uid}@example.test", password_hash="x"))


@pytest.fixture(autouse=True)
def _clean() -> None:
    with session_scope() as s:
        s.execute(delete(UserDirection).where(UserDirection.user_id.in_(_USERS)))
        s.execute(delete(UserDailyConfig).where(UserDailyConfig.user_id.in_(_USERS)))
        s.execute(delete(DailyPaper).where(DailyPaper.date == _PAPER_DATE))


# --------------------------------------------------------------- seeding race


def test_concurrent_first_requests_seed_exactly_once() -> None:
    """Six simultaneous first requests must leave seven directions, not forty-two.

    Each request opens its own transaction and takes its own read snapshot, so
    every one of them can see ``seeded`` false *and* see no directions — the
    read-then-write form of this check hands out a full set of defaults per
    racing request. The account's very first page load is exactly when several
    requests arrive together (the feed, the date rail and the settings page all
    call ``ensure_seeded``), so this is the common case, not an exotic one.
    """
    barrier = threading.Barrier(6)
    failures: list[str] = []

    def first_request() -> None:
        barrier.wait()
        try:
            with session_scope() as s:
                ensure_seeded(s, user_id=RACER)
        except Exception as exc:  # noqa: BLE001 — any raise is the failure
            failures.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=first_request) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)

    assert failures == [], failures
    with session_scope() as s:
        directions = list(s.scalars(select(UserDirection).where(UserDirection.user_id == RACER)))
        configs = list(s.scalars(select(UserDailyConfig).where(UserDailyConfig.user_id == RACER)))
    assert len(configs) == 1
    assert [d.name for d in directions] == list(DIRECTIONS)


# ------------------------------------------------------- prefilter vs matcher


def test_prefilter_is_never_narrower_than_the_matcher() -> None:
    """The date rail's SQL prefilter must not drop a row the matcher would keep.

    ``date_summaries_for_user`` narrows in SQL and then matches in Python. SQLite's
    built-in ``lower()`` folds ASCII only — ``lower('SCHRÖDINGER')`` keeps the Ö —
    while ``str.lower()`` folds all of Unicode, so the SQL half used to be the
    narrower of the two. The row then never reaches the matcher, the date drops
    out of the rail entirely, and the day view lists a paper the rail says does
    not exist, with no way for the reader to navigate to it.
    """
    with session_scope() as s:
        ensure_seeded(s, user_id=FOLDER)
        s.execute(delete(UserDirection).where(UserDirection.user_id == FOLDER))
        s.add(
            UserDirection(
                user_id=FOLDER,
                name="Folding",
                keywords="schrödinger\npoincaré\nworld model\nσ-algebra",
                enabled=True,
                position=0,
            )
        )
        for i, title in enumerate(
            [
                "SCHRÖDINGER BRIDGES",  # upper-case non-ASCII
                "Schrödinger bridges",  # already-lower non-ASCII
                "POINCARÉ recurrence",
                "A WORLD MODEL for agents",  # pure ASCII, always worked
                "Σ-ALGEBRA foundations",  # Greek
            ]
        ):
            s.add(
                DailyPaper(
                    arxiv_id=f"1999.{i:04d}",
                    date=_PAPER_DATE,
                    title=title,
                    abstract="",
                )
            )

    with session_scope() as s:
        directions = reader_directions(s, FOLDER)
        matched = {
            p.arxiv_id
            for p in s.scalars(select(DailyPaper).where(DailyPaper.date == _PAPER_DATE))
            if _feed_for(p, directions) is not None
        }
        predicate = _keyword_filter(directions)
        assert predicate is not None
        prefiltered = {
            p.arxiv_id
            for p in s.scalars(
                select(DailyPaper).where(DailyPaper.date == _PAPER_DATE).where(predicate)
            )
        }

    assert matched, "fixture matched nothing; the test would pass vacuously"
    assert matched <= prefiltered, (
        "prefilter is narrower than the matcher — these match but the date rail "
        f"never loads them: {sorted(matched - prefiltered)}"
    )
