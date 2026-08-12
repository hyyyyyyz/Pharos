"""每日论文 orchestration — fetch, persist, read, and the run bookkeeping.

This is the layer that turns the two pure modules next door into a durable
daily digest:

* :mod:`pharos.daily.fetcher` knows how to ask arXiv what came out, and nothing
  about databases.
* :mod:`pharos.daily.reader` knows how to turn one abstract into one card, and
  nothing about databases.
* This module owns every write, decides what is worth reading, and records what
  happened in a :class:`~pharos.db.models.DailyRun`.

Three rules shape everything below.

**Fetching and reading are independent.** Papers are persisted the moment they
are fetched, with ``read_status="pending"``. If no LLM provider is configured
they simply stay that way and the run still finishes as ``done`` — an unread
paper is a true statement about the world, whereas a placeholder summary is a
lie the user cannot detect. Nothing here ever writes ``summary_zh``,
``highlights``, or ``scores`` from any source other than a validated
:class:`~pharos.daily.reader.Reading`.

**A paper appears on exactly one date.** ``exclude_ids`` is the set of every
arXiv id already stored, across all dates, so the first sweep that surfaces a
paper owns it. arXiv genuinely re-announces papers (cross-lists, revisions), and
this digest is a reading queue rather than an announcement log: seeing the same
paper again next week is noise, not news. The consequence to keep in mind is
that ``DailyPaper.date`` means "the digest day this paper surfaced on", not "the
day arXiv announced it" — ``published_at`` is the authority on the latter, and a
multi-day catch-up sweep deliberately files older papers under the sweep date.

**Sessions stay short.** An LLM call takes tens of seconds; a SQLite write takes
microseconds. Every network call in this module happens with no session open, so
one slow provider can never hold a write lock against the rest of the app.

**The sweep is global; the feed is personal.** One arXiv fetch and one LLM
reading serve every account, because a paper's Chinese summary and its key
points are facts about the paper rather than about who is reading it. What the
sweep does take from its users is the *shape of the net*: it queries the union
of every account's arXiv categories and keeps a paper if it matches any
account's keywords (:func:`load_sweep_plan`). Adding a category therefore widens
one shared query — it never starts a private crawl, which is what makes this
survivable against a service that has already answered one probe with HTTP 429.
Which papers a given reader actually *sees*, under which direction, and how
relevant they are is decided at query time by :func:`papers_for_user`, so
editing a direction re-ranks that reader's feed on the next request with nothing
re-fetched and nothing re-read.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from pharos.daily import reader, user_directions
from pharos.daily.directions import ARXIV_CATEGORIES, MAX_PAPERS_PER_DAY, direction_rank
from pharos.daily.fetcher import FetchedPaper, fetch_for_date
from pharos.daily.user_directions import Direction
from pharos.db.models import DailyPaper, DailyRun, UserDailyConfig, UserDirection
from pharos.db.session import session_scope

log = logging.getLogger(__name__)

__all__ = [
    "DailySweeper",
    "DateSummary",
    "FeedPaper",
    "PdfDownloadError",
    "SweepPlan",
    "date_summaries",
    "date_summaries_for_user",
    "download_pdf",
    "load_sweep_plan",
    "papers_for_date",
    "papers_for_user",
    "parse_date",
    "read_one",
    "run_for_date",
    "run_sweep",
    "today",
]

#: How many papers are read at once. The bound exists for the provider's sake,
#: not ours: a relay that happily answers one request at a time will start
#: shedding load at twenty. Four keeps a 60-paper day under ten minutes while
#: staying polite enough that a self-hosted endpoint on a single GPU survives.
READ_CONCURRENCY = 4

#: Per-paper ceiling handed to :func:`reader.read_paper`. Deliberately shorter
#: than a user-facing timeout: this is a batch, and one wedged request must not
#: consume a meaningful share of the whole budget.
READ_TIMEOUT_SECONDS = 90.0

#: Total wall-clock budget for the reading phase of one sweep. When it runs out
#: we stop *starting* new readings and leave the remainder ``pending``, which the
#: next sweep picks up. The alternative — an unbounded loop against a degraded
#: provider — is how a scheduled job silently stops being scheduled.
READ_BUDGET_SECONDS = 45 * 60.0

#: Truncation for anything written to ``DailyPaper.read_error``; the column is
#: rendered in the UI and a provider can return a very large error body.
_MAX_ERROR_CHARS = 2000

#: Hosts we will download a PDF from. ``pdf_url`` arrives from the arXiv feed —
#: third-party content — and is later handed to :func:`urllib.request.urlopen`.
#: Without an allowlist a poisoned or spoofed feed turns the import button into
#: a server-side request forgery against whatever the backend can reach.
_PDF_HOSTS = frozenset({"arxiv.org", "www.arxiv.org", "export.arxiv.org"})

#: A paper PDF larger than this is not something we want to buffer in memory.
_MAX_PDF_BYTES = 64 * 1024 * 1024
_PDF_CHUNK_BYTES = 256 * 1024
_PDF_TIMEOUT_SECONDS = 120.0

_USER_AGENT = "Pharos/0.1 (daily arXiv digest; +https://github.com/hyyyyyyz/Pharos)"

#: Hard ceiling on one sweep's paper count, whatever the accounts ask for.
#: ``max_per_day`` is per-user configuration bounded at
#: :data:`~pharos.daily.user_directions.MAX_PER_DAY`, and the sweep takes the
#: largest of them so nobody's cap is silently unreachable — but the sweep also
#: pays an LLM call per paper, so the number that decides that spend must be
#: bounded here rather than only in a validator somebody could later relax.
_SWEEP_HARD_CAP = 200

#: Weights for the per-reader ``recommendation``. Relevance leads because it is
#: the only component that knows who is asking; the other three are the LLM's
#: judgement of the paper itself and are identical for every reader. They sum to
#: 1.0, so the result stays on the same 0-10 scale as its inputs and can sit
#: beside them in the UI without needing a second legend.
_RECOMMENDATION_WEIGHTS: dict[str, float] = {
    "relevance": 0.40,
    "quality": 0.30,
    "popularity": 0.20,
    "recency": 0.10,
}


# --------------------------------------------------------------------------- #
# dates
# --------------------------------------------------------------------------- #


def today() -> dt.date:
    """The digest's notion of "today".

    Local, not UTC: the user reads this in the morning over coffee and expects
    the top of the list to be dated the same day their laptop clock says. arXiv
    windows submissions in UTC, which means a sweep run just after local midnight
    east of Greenwich may find nothing yet — the scheduler's hourly retry of an
    empty day is what absorbs that, rather than pretending timezones agree.
    """
    return dt.date.today()


def parse_date(value: str) -> dt.date:
    """Parse a ``YYYY-MM-DD`` path/body parameter, raising ``ValueError`` if not.

    ``dt.date.fromisoformat`` accepts the compact ``YYYYMMDD`` form on 3.11+,
    which would produce a ``date`` string that never matches a stored row. The
    length check keeps the API's one date format the only date format.
    """
    if len(value) != 10:
        raise ValueError(f"expected a YYYY-MM-DD date, got {value!r}")
    return dt.date.fromisoformat(value)


# --------------------------------------------------------------------------- #
# read-side queries (used by the API layer)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DateSummary:
    """Per-day counts for the date rail."""

    date: str
    total: int
    read: int
    pending: int
    failed: int


def date_summaries(session: Session) -> list[DateSummary]:
    """Every date in the shared table, newest first, with its read/pending split.

    Counted in SQL rather than by loading rows, and counted over the *whole*
    table — every paper any account's keywords pulled in. That makes it the
    sweep's own view of what it has stored, useful for operations and for the
    run bookkeeping, and the wrong thing to put in front of a reader:
    :func:`date_summaries_for_user` is what the date rail calls, because a count
    the day view then filters down reads as a broken digest.
    """
    stmt = (
        select(
            DailyPaper.date,
            func.count().label("total"),
            func.sum(case((DailyPaper.read_status == "done", 1), else_=0)).label("read"),
            func.sum(case((DailyPaper.read_status == "pending", 1), else_=0)).label("pending"),
            func.sum(case((DailyPaper.read_status == "error", 1), else_=0)).label("failed"),
        )
        .group_by(DailyPaper.date)
        .order_by(DailyPaper.date.desc())
    )
    return [
        DateSummary(
            date=row.date,
            total=int(row.total or 0),
            read=int(row.read or 0),
            pending=int(row.pending or 0),
            failed=int(row.failed or 0),
        )
        for row in session.execute(stmt)
    ]


def papers_for_date(session: Session, date_str: str) -> list[DailyPaper]:
    """One day's papers, unfiltered, in the shared default-rubric order.

    The *whole* table for a date, with no reader applied. Kept for operational
    use — backfills, the scheduler, a shell session asking what the sweep
    actually stored — and deliberately not used by the API any more: an endpoint
    calling this would show one account the papers another account's keywords
    pulled in. :func:`papers_for_user` is the one the feed wants.
    """
    rows = list(session.scalars(select(DailyPaper).where(DailyPaper.date == date_str)))
    rows.sort(
        key=lambda p: (
            p.score_recommendation is None,
            -(p.score_recommendation or 0.0),
            direction_rank(p.matched_domain),
            p.arxiv_id,
        )
    )
    return rows


# --------------------------------------------------------------------------- #
# per-user matching at query time
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FeedPaper:
    """One paper as it appears in one reader's feed.

    The shared :class:`~pharos.db.models.DailyPaper` row plus everything that is
    true only for this caller. ``direction`` and ``keywords`` override the row's
    ``matched_domain``/``matched_keywords``, and ``scores``/``recommendation``
    override the row's stored copies — see :func:`_score_for_reader` for why
    presenting the stored ones would be a lie.

    ``scores`` and ``recommendation`` are ``None`` for a paper nothing has read
    yet. That is the honest answer: relevance alone is computable, but a
    recommendation assembled from one known component and three missing ones
    would look exactly like a real one.
    """

    paper: DailyPaper
    direction: str | None
    keywords: tuple[str, ...]
    relevance: float
    scores: dict[str, float] | None
    recommendation: float | None
    #: Position of ``direction`` in the reader's list; the display tie-break.
    position: int


def _load_scores(paper: DailyPaper) -> dict[str, float] | None:
    """Parse a row's stored ``scores`` JSON into numbers, or ``None``.

    Degrades to ``None`` rather than raising: one malformed row must not 500 a
    whole day's listing, and ``None`` already means "not read", which every
    caller renders.
    """
    if not paper.scores:
        return None
    try:
        value = json.loads(paper.scores)
    except ValueError:
        log.warning("daily paper %s has unparseable scores JSON", paper.id)
        return None
    if not isinstance(value, dict):
        return None
    return {str(k): float(v) for k, v in value.items() if isinstance(v, (int, float))}


def _score_for_reader(
    paper: DailyPaper, relevance: float
) -> tuple[dict[str, float] | None, float | None]:
    """Rebuild ``scores`` and ``recommendation`` for the caller who is asking.

    Two numbers in a stored card are not about this reader and must not be
    presented as though they were:

    ``relevance`` was produced by the LLM against the old *global* rubric — the
    operator's hard-coded interest list. Once accounts follow different
    directions it is not merely imprecise, it answers a question this caller
    never asked. It is replaced outright with ``relevance``, which
    :func:`pharos.daily.user_directions.relevance_for` derived from terms this
    caller wrote down themselves.

    ``recommendation`` was the model's weighted total *including* that stale
    relevance, so it inherits the same problem, and it is the number the UI puts
    on the card corner. It is recomputed here from the components that really
    are user-independent (``quality``, ``popularity``, ``recency`` — properties
    of the paper, identical for everybody) plus the new relevance.

    A card missing any component falls back to the model's own recommendation
    rather than a partial weighted sum: an average over whichever keys happened
    to survive would silently change meaning per row.
    """
    scores = _load_scores(paper)
    if scores is None:
        return None, None

    updated = dict(scores)
    updated["relevance"] = relevance
    if all(key in updated for key in _RECOMMENDATION_WEIGHTS):
        total = sum(weight * updated[key] for key, weight in _RECOMMENDATION_WEIGHTS.items())
        updated["recommendation"] = round(min(10.0, max(0.0, total)), 1)
    return updated, updated.get("recommendation")


def _default_directions() -> list[Direction]:
    """The global default rubric, shaped like a user's direction list.

    Only the *sweep* needs this now — see :func:`load_sweep_plan`, which falls
    back to it when no account has contributed a single keyword, so a scheduler
    running before anyone registers still populates the table. Individual readers
    never reach it: :func:`pharos.daily.user_directions.ensure_seeded` copies
    these same defaults into real rows the first time an account touches the
    module, so a new reader has directions of their own rather than a borrowed
    view of somebody's constant.
    """
    from pharos.daily.directions import DIRECTIONS

    return [
        Direction(
            id=f"default:{name}",
            name=name,
            keywords=tuple(keywords),
            enabled=True,
            position=position,
        )
        for position, (name, keywords) in enumerate(DIRECTIONS.items())
    ]


def reader_directions(session: Session, user_id: str) -> list[Direction]:
    """The directions to match ``user_id``'s feed against, seeding on first use.

    A thin pass-through to
    :func:`pharos.daily.user_directions.load_directions`, kept as the one named
    place the daily module asks this question so the seeding side effect is
    obvious at every call site rather than buried in an import.

    Note that it *writes* on a brand-new account: the defaults are copied in so
    the digest works the first time it is opened instead of presenting an empty
    page that reads as "the sweep found nothing". An empty result afterwards is
    therefore meaningful rather than merely unconfigured — it means the reader
    deleted every direction on purpose, and ``UserDailyConfig.seeded`` is what
    stops the next request from overruling that.
    """
    return user_directions.load_directions(session, user_id=user_id, enabled_only=True)


def _feed_for(paper: DailyPaper, directions: list[Direction]) -> FeedPaper | None:
    """Match one paper for one reader, or ``None`` if it is not theirs to see."""
    name, hits = user_directions.match_for_user(directions, paper.title, paper.abstract)
    if name is None:
        return None
    matched = next((d for d in directions if d.name == name), None)
    relevance = user_directions.relevance_for(hits, matched)
    scores, recommendation = _score_for_reader(paper, relevance)
    return FeedPaper(
        paper=paper,
        direction=name,
        keywords=hits,
        relevance=relevance,
        scores=scores,
        recommendation=recommendation,
        position=matched.position if matched is not None else len(directions),
    )


def feed_entry(session: Session, paper: DailyPaper, user_id: str) -> FeedPaper:
    """One paper rendered for one reader, matched or not.

    The single-paper counterpart to :func:`papers_for_user`, for endpoints that
    address a paper by id rather than listing a day. It never returns ``None``:
    the caller named this paper explicitly, so refusing to describe it would be
    unhelpful, and describing it with the *stored* relevance — scored against
    the old global rubric — is exactly the misattribution this module avoids. A
    paper matching none of their directions comes back with no direction, no
    keywords, and a relevance of 0.0, which is what "this is not in your feed"
    honestly looks like.
    """
    directions = reader_directions(session, user_id)
    matched = _feed_for(paper, directions) if directions else None
    if matched is not None:
        return matched
    scores, recommendation = _score_for_reader(paper, 0.0)
    return FeedPaper(
        paper=paper,
        direction=None,
        keywords=(),
        relevance=0.0,
        scores=scores,
        recommendation=recommendation,
        position=len(directions),
    )


def _rank(feed: list[FeedPaper], limit: int) -> list[FeedPaper]:
    """Order one day's matches "what should I read next", then apply the cap.

    Highest recommendation first. Anything unread has no recommendation at all
    and sorts after everything that does — it is genuinely unknown, and floating
    it to the top on the strength of keyword relevance alone would rank a paper
    nobody has read above one that was read and judged mediocre. Within either
    group the reader's own direction order breaks the tie, then the arXiv id, so
    the sequence is total and stable across requests.
    """
    feed.sort(
        key=lambda f: (
            f.recommendation is None,
            -(f.recommendation or 0.0),
            f.position,
            f.paper.arxiv_id,
        )
    )
    return feed[:limit]


def _reader_context(session: Session, user_id: str) -> tuple[list[Direction], bool, int]:
    """``(directions, module enabled, max per day)`` — everything a feed needs.

    Seeding first and reading the config *after* is deliberate, not incidental
    ordering: ``ensure_seeded`` creates the config row when it is missing, so
    doing it the other way round would mean branching on a ``None`` config that
    is about to exist, and inventing a default for ``max_per_day`` that the
    column already defines. One call, one source of truth, no ordering hazard
    for a later edit to trip over.
    """
    config = user_directions.ensure_seeded(session, user_id=user_id)
    directions = user_directions.load_directions(session, user_id=user_id, enabled_only=True)
    return directions, bool(config.enabled), max(1, int(config.max_per_day or 1))


def papers_for_user(session: Session, date_str: str, user_id: str) -> list[FeedPaper]:
    """One day's papers as ``user_id`` sees them: filtered, scored, and ranked.

    Papers matching none of the caller's directions are excluded — a filter over
    a shared table, never a delete, so they stay visible to whoever's keywords
    pulled them in. The cap is the caller's own ``max_per_day``.

    A day holds at most a few dozen rows and the directions are loaded once, so
    this is one query plus a substring scan; nothing here touches the network or
    the LLM, which is what makes "edit a direction, see the feed re-rank" cost a
    page refresh.
    """
    directions, enabled, limit = _reader_context(session, user_id)
    if not enabled or not directions:
        return []
    rows = session.scalars(select(DailyPaper).where(DailyPaper.date == date_str))
    feed = [f for row in rows if (f := _feed_for(row, directions)) is not None]
    return _rank(feed, limit)


def _keyword_filter(directions: list[Direction]):
    """A SQL predicate that is true for any row some keyword could match.

    A coarse prefilter, not the matcher: it narrows the scan to rows worth
    loading, and :func:`_feed_for` still decides which direction wins. Without it
    the date rail would pull every paper the install has ever stored into Python
    just to count them, which grows without bound as the digest accumulates.

    ``autoescape`` matters — a keyword is free text, and an unescaped ``%`` in it
    would quietly turn one term into a wildcard that matches everything.

    ``py_lower`` rather than SQL's ``lower`` matters for the same reason in the
    other direction. SQLite's built-in ``lower()`` folds ASCII only, so a title
    rendered ``SCHRÖDINGER BRIDGES`` stays ``schrÖdinger`` in SQL while
    :func:`~pharos.daily.user_directions.match_for_user` lower-cases it fully in
    Python. The keyword ``schrödinger`` then matches in Python and not in SQL —
    the prefilter becomes *narrower* than the matcher, which is the one thing a
    prefilter may never be. The date rail would drop the row, and with it the
    whole date, so the day view still lists a paper the rail says does not
    exist and the user has no way to navigate to it.
    """
    haystack = func.py_lower(DailyPaper.title + " " + func.coalesce(DailyPaper.abstract, ""))
    terms = {kw for direction in directions for kw in direction.keywords if kw}
    return or_(*(haystack.contains(term, autoescape=True) for term in terms)) if terms else None


def date_summaries_for_user(session: Session, user_id: str) -> list[DateSummary]:
    """Every date with papers ``user_id`` can see, newest first, with its split.

    Counted the same way the day view builds its list, and that is the point:
    counting the whole shared table here would promise papers that
    :func:`papers_for_user` then filters away, and a date rail that says "12" and
    opens onto three papers reads as a bug in the digest rather than as the
    filter working.

    Costlier than the plain SQL ``COUNT`` it replaces, because the cap has to be
    applied to a *ranked* day to know which papers survive it, and ranking needs
    each row's scores. The keyword prefilter keeps what is loaded proportional to
    what the caller actually matches rather than to the age of the install. If
    that stops being enough, the fix is a materialised per-user match table, not
    a cheaper count that disagrees with the day view.
    """
    directions, enabled, limit = _reader_context(session, user_id)
    if not enabled or not directions:
        return []

    stmt = select(DailyPaper)
    predicate = _keyword_filter(directions)
    if predicate is not None:
        stmt = stmt.where(predicate)

    by_date: dict[str, list[FeedPaper]] = {}
    for row in session.scalars(stmt):
        matched = _feed_for(row, directions)
        if matched is not None:
            by_date.setdefault(row.date, []).append(matched)

    summaries: list[DateSummary] = []
    for date_str, feed in by_date.items():
        visible = _rank(feed, limit)
        statuses = [f.paper.read_status for f in visible]
        summaries.append(
            DateSummary(
                date=date_str,
                total=len(visible),
                read=statuses.count("done"),
                pending=statuses.count("pending"),
                failed=statuses.count("error"),
            )
        )
    summaries.sort(key=lambda s: s.date, reverse=True)
    return summaries


def run_for_date(session: Session, date_str: str) -> DailyRun | None:
    return session.scalar(select(DailyRun).where(DailyRun.date == date_str))


def latest_run(session: Session) -> DailyRun | None:
    """The most recently *started* run, which is what "last run" means to a user.

    Ordered by ``started_at`` rather than ``date`` so a backfill of an old date
    correctly reports as the latest activity.
    """
    return session.scalar(select(DailyRun).order_by(DailyRun.started_at.desc()).limit(1))


# --------------------------------------------------------------------------- #
# write-side helpers — each opens and closes its own short transaction
# --------------------------------------------------------------------------- #


def _begin_run(date_str: str) -> str:
    """Create or reset the run row for ``date_str``; returns its id.

    ``DailyRun.date`` is UNIQUE, so a second sweep of the same day must update
    the existing row rather than insert beside it. Counters reset to zero because
    they describe *this* execution; the papers themselves persist across runs, so
    nothing is lost by forgetting that a previous attempt read nine of them.
    """
    with session_scope() as session:
        run = run_for_date(session, date_str)
        if run is None:
            run = DailyRun(date=date_str)
            session.add(run)
        run.status = "running"
        run.fetched = 0
        run.read_done = 0
        run.read_failed = 0
        run.error = None
        run.started_at = dt.datetime.now(dt.UTC)
        run.finished_at = None
        session.flush()
        return run.id


def _finish_run(run_id: str, *, fetched: int, done: int, failed: int, error: str | None) -> None:
    with session_scope() as session:
        run = session.get(DailyRun, run_id)
        if run is None:  # pragma: no cover — only if the row was deleted mid-sweep
            return
        run.status = "error" if error else "done"
        run.fetched = fetched
        run.read_done = done
        run.read_failed = failed
        run.error = error[:_MAX_ERROR_CHARS] if error else None
        run.finished_at = dt.datetime.now(dt.UTC)


def _known_arxiv_ids() -> set[str]:
    """Every arXiv id already stored, on any date — see the dedup rule up top."""
    with session_scope() as session:
        return set(session.scalars(select(DailyPaper.arxiv_id)))


def _load_sweep_plan() -> SweepPlan:
    """:func:`load_sweep_plan` with its own short session, for the sweep to thread."""
    with session_scope() as session:
        return load_sweep_plan(session)


def _store_fetched(date_str: str, papers: list[FetchedPaper]) -> int:
    """Persist fetched papers as ``pending`` rows. Returns how many were new.

    The in-transaction id re-check is not redundant with ``exclude_ids``: the
    fetch runs for minutes with no session held, and a concurrent single-paper
    action could have inserted in the meantime. It is cheap insurance against
    ever showing the user the same paper twice.
    """
    if not papers:
        return 0
    with session_scope() as session:
        existing = set(session.scalars(select(DailyPaper.arxiv_id)))
        stored = 0
        for paper in papers:
            if paper.arxiv_id in existing:
                continue
            existing.add(paper.arxiv_id)
            session.add(
                DailyPaper(
                    arxiv_id=paper.arxiv_id,
                    date=date_str,
                    title=paper.title,
                    authors="; ".join(paper.authors) or None,
                    abstract=paper.abstract or None,
                    categories=",".join(paper.categories) or None,
                    # NOTE — these two columns changed meaning when directions
                    # became per-user, and nothing about their names says so.
                    # They used to be "why this paper is in the digest". They
                    # are now "how the GLOBAL DEFAULT rubric in
                    # pharos.daily.directions classifies this paper", which is
                    # a different question with a different answer: a paper kept
                    # because one account's private keyword matched stores NULL
                    # here, and a paper stored with a domain may still be
                    # invisible to every reader. Nothing user-facing reads them
                    # except as the fallback rubric for a reader who has no
                    # directions of their own (see service.reader_directions);
                    # the feed derives its own matched_domain per caller at
                    # query time. Do not reintroduce them as a filter.
                    matched_domain=paper.matched_domain,
                    matched_keywords=",".join(paper.matched_keywords) or None,
                    arxiv_url=paper.arxiv_url,
                    pdf_url=paper.pdf_url,
                    published_at=paper.published_at,
                    read_status="pending",
                )
            )
            stored += 1
        return stored


@dataclass(frozen=True)
class SweepPlan:
    """The shape of one sweep's net, pooled from every account.

    Assembled once per run rather than consulted per paper: the keyword set is
    the union across all users and is scanned against every candidate abstract,
    so rebuilding it inside the loop would turn a database read into a
    per-paper cost.
    """

    categories: tuple[str, ...]
    #: Union of every enabled direction's keywords, lower-cased. A paper is kept
    #: if any of these appears in its title or abstract.
    keywords: frozenset[str]
    max_papers: int
    #: How many accounts contributed. Zero means the defaults are in force,
    #: which is worth logging: it is the difference between "nobody configured
    #: anything" and "everybody happens to want the defaults".
    users: int

    def keep(self, text: str) -> bool:
        """Whether ``text`` (lower-cased title + abstract) interests anybody."""
        return any(keyword in text for keyword in self.keywords)


def load_sweep_plan(session: Session) -> SweepPlan:
    """Pool every account's settings into one arXiv query and one keep-rule.

    Two whole-table reads, not one per user: this decides a single shared fetch,
    and it runs once per sweep.

    Falls back to the global defaults whenever the union comes out empty —
    a fresh install, the scheduler firing before anyone has registered, or an
    install where every account has emptied its direction list. The module has
    to keep populating in those cases, because the alternative is a scheduler
    that quietly stops producing and only announces it weeks later when somebody
    finally configures a direction and finds no history behind it.

    The two halves fall back independently. An install where somebody has set
    categories but nobody has kept a keyword should still sweep *their*
    categories under the default rubric, rather than being thrown back to the
    default categories as well.
    """
    categories: list[str] = []
    seen: set[str] = set()
    caps: list[int] = []
    users = 0
    for config in session.scalars(select(UserDailyConfig)):
        users += 1
        if not config.enabled:
            # A reader who has switched the module off is not asking for a
            # wider net. Their directions still exist and come back the moment
            # they switch it on again.
            continue
        caps.append(max(1, int(config.max_per_day or 1)))
        for category in user_directions.config_categories(config):
            if category not in seen:
                seen.add(category)
                categories.append(category)

    keywords: set[str] = set()
    stmt = (
        select(UserDirection.keywords)
        .join(UserDailyConfig, UserDailyConfig.user_id == UserDirection.user_id, isouter=True)
        .where(UserDirection.enabled.is_(True))
        .where(or_(UserDailyConfig.enabled.is_(True), UserDailyConfig.user_id.is_(None)))
    )
    for raw in session.scalars(stmt):
        for line in (raw or "").splitlines():
            term = line.strip().lower()
            if term:
                keywords.add(term)

    if not categories:
        categories = list(ARXIV_CATEGORIES)
    if not keywords:
        keywords = {kw for direction in _default_directions() for kw in direction.keywords}

    max_papers = min(_SWEEP_HARD_CAP, max([MAX_PAPERS_PER_DAY, *caps]))
    return SweepPlan(
        categories=tuple(categories),
        keywords=frozenset(keywords),
        max_papers=max_papers,
        users=users,
    )


@dataclass(frozen=True)
class _ReadTask:
    """The minimum a reading needs, lifted out of the DB before the network call."""

    paper_id: str
    title: str
    abstract: str
    domain: str | None
    authors: tuple[str, ...]


def _papers_to_read(date_str: str, *, reread: bool) -> list[_ReadTask]:
    """Which papers this sweep should hand to the LLM.

    Default is every paper for the day that is not already ``done`` — pending
    *and* previously errored. That makes a sweep resumable for free: a run that
    hit its time budget, crashed, or ran while the provider was down leaves work
    behind, and the next sweep of that date finishes it instead of requiring the
    user to notice and intervene.

    ``reread`` additionally re-reads papers already read, which is what you want
    after pointing the digest at a stronger model.

    A paper with no abstract is skipped rather than sent: ``read_paper`` would
    reject it anyway, and marking it ``error`` every single sweep would turn one
    unusable arXiv record into permanent noise in the failure count.
    """
    with session_scope() as session:
        stmt = select(DailyPaper).where(DailyPaper.date == date_str)
        if not reread:
            stmt = stmt.where(DailyPaper.read_status != "done")
        rows = list(session.scalars(stmt))
        return [
            _ReadTask(
                paper_id=row.id,
                title=row.title,
                abstract=row.abstract or "",
                domain=row.matched_domain,
                authors=tuple(a.strip() for a in (row.authors or "").split(";") if a.strip()),
            )
            for row in rows
            if (row.abstract or "").strip()
        ]


def _store_reading(paper_id: str, reading: reader.Reading) -> None:
    """Persist a validated card. The only writer of the reading columns.

    ``ensure_ascii=False`` keeps the stored JSON readable as Chinese in ``sqlite3``
    and halves its size; ``score_recommendation`` is denormalised here because it
    is the list's sort key and JSON is not sortable in SQL.
    """
    with session_scope() as session:
        paper = session.get(DailyPaper, paper_id)
        if paper is None:  # pragma: no cover — deleted between read and write
            return
        paper.summary_zh = reading.summary_zh
        paper.highlights = json.dumps(reading.highlights, ensure_ascii=False)
        paper.scores = json.dumps(reading.scores, ensure_ascii=False)
        paper.score_recommendation = reading.scores.get("recommendation")
        paper.read_model = reading.model
        paper.read_at = dt.datetime.now(dt.UTC)
        paper.read_status = "done"
        paper.read_error = None


def _store_read_error(paper_id: str, message: str) -> None:
    """Record a failed reading, leaving any previous card untouched.

    A re-read that fails must not destroy the card the user already has: the
    status turns ``error`` and the message explains why, but ``summary_zh`` and
    friends keep whatever a successful earlier reading put there.
    """
    with session_scope() as session:
        paper = session.get(DailyPaper, paper_id)
        if paper is None:  # pragma: no cover
            return
        paper.read_status = "error"
        paper.read_error = message[:_MAX_ERROR_CHARS]
        paper.read_at = dt.datetime.now(dt.UTC)


def mark_imported(paper_id: str, library_paper_id: str) -> None:
    with session_scope() as session:
        paper = session.get(DailyPaper, paper_id)
        if paper is not None:
            paper.imported_paper_id = library_paper_id


# --------------------------------------------------------------------------- #
# the sweep
# --------------------------------------------------------------------------- #


async def run_sweep(date: dt.date, *, days: int = 1, reread: bool = False) -> DailyRun:
    """Fetch, store and read one day's papers. Returns the finished run row.

    Never raises for an ordinary bad day. arXiv being unreachable, the window
    being empty, and the provider refusing every request are all recorded in the
    returned :class:`DailyRun` instead — this is called from a scheduler, and an
    exception escaping here would kill the loop that is supposed to try again
    tomorrow. Only a genuine defect (a programming error in the fetch layer)
    lands the run in ``error``.

    Args:
        date: The digest date. Papers are filed under it regardless of their own
            ``published_at`` — see the module docstring.
        days: Window size ending at ``date``, used to catch up after downtime.
        reread: Also re-read papers already read, e.g. after switching models.
    """
    date_str = date.isoformat()
    run_id = await asyncio.to_thread(_begin_run, date_str)
    log.info("daily sweep %s: starting (days=%d, reread=%s)", date_str, days, reread)

    fetched = 0
    done = failed = 0
    error: str | None = None
    try:
        try:
            exclude = await asyncio.to_thread(_known_arxiv_ids)
            # Loaded once per run, off the event loop, and then held as plain
            # data for the whole fetch: the keep-rule is consulted once per
            # candidate paper and must not be a database round trip.
            plan = await asyncio.to_thread(_load_sweep_plan)
            log.info(
                "daily sweep %s: %d account(s), %d categories, %d keywords, max=%d",
                date_str,
                plan.users,
                len(plan.categories),
                len(plan.keywords),
                plan.max_papers,
            )
            papers = await asyncio.to_thread(
                fetch_for_date,
                date,
                days=days,
                max_papers=plan.max_papers,
                exclude_ids=exclude,
                categories=plan.categories,
                keep=plan.keep,
            )
            fetched = await asyncio.to_thread(_store_fetched, date_str, papers)
            log.info("daily sweep %s: stored %d new papers", date_str, fetched)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a scheduler must not die on one bad day
            log.exception("daily sweep %s: fetch failed", date_str)
            error = f"fetch failed: {exc}"

        if error is None:
            if reader.is_available():
                done, failed = await _read_all(date_str, reread=reread)
            else:
                # Not an error: the digest is designed to be useful with no API
                # key at all. The papers are there, honestly marked unread.
                log.info(
                    "daily sweep %s: no chat provider configured; papers left pending",
                    date_str,
                )
    except asyncio.CancelledError:
        # Shutdown, or an explicit cancel. The run row must not be abandoned at
        # ``running``: nothing will ever finish it, the UI would show a sweep
        # that is permanently in progress, and only *today's* date is rescued by
        # the scheduler's "previous run never finished" rule — a cancelled
        # backfill of an older date would stay stuck forever. Finalise
        # synchronously, because awaiting anything inside a cancelled task is
        # not guaranteed to come back, and this is one microsecond SQLite write.
        _finish_run(
            run_id, fetched=fetched, done=done, failed=failed, error="sweep was interrupted"
        )
        log.info("daily sweep %s: interrupted; run recorded as error", date_str)
        raise

    await asyncio.to_thread(
        _finish_run, run_id, fetched=fetched, done=done, failed=failed, error=error
    )
    log.info(
        "daily sweep %s: finished (fetched=%d, read=%d, failed=%d)",
        date_str,
        fetched,
        done,
        failed,
    )
    with session_scope() as session:
        run = session.get(DailyRun, run_id)
        if run is None:  # pragma: no cover — only if the row was deleted mid-sweep
            raise RuntimeError(f"daily run {run_id} vanished while it was running")
        return run


async def _read_all(date_str: str, *, reread: bool) -> tuple[int, int]:
    """Read every outstanding paper for a date. Returns ``(done, failed)``.

    Bounded three ways, because this is the only part of the sweep that talks to
    a provider we do not control: at most :data:`READ_CONCURRENCY` in flight, at
    most :data:`READ_TIMEOUT_SECONDS` each, and at most
    :data:`READ_BUDGET_SECONDS` in total. Papers not reached inside the budget
    keep ``read_status="pending"`` and are picked up by the next sweep.

    ``read_paper`` is blocking, so each call gets a worker thread. The semaphore
    bounds the fan-out well below the default executor's pool, so the sweep can
    never starve the rest of the app of threads.
    """
    tasks = await asyncio.to_thread(_papers_to_read, date_str, reread=reread)
    if not tasks:
        return 0, 0

    log.info("daily sweep %s: reading %d papers", date_str, len(tasks))
    semaphore = asyncio.Semaphore(READ_CONCURRENCY)
    deadline = time.monotonic() + READ_BUDGET_SECONDS
    done = 0
    failed = 0
    skipped = 0

    async def read_one_task(task: _ReadTask) -> None:
        nonlocal done, failed, skipped
        async with semaphore:
            if time.monotonic() >= deadline:
                # Leave it pending — see the budget note above.
                skipped += 1
                return
            try:
                reading = await asyncio.to_thread(
                    reader.read_paper,
                    task.title,
                    task.abstract,
                    domain=task.domain,
                    authors=task.authors,
                    timeout=READ_TIMEOUT_SECONDS,
                )
            except reader.ReaderUnavailable:
                # Configuration disappeared mid-sweep. Never attempted is not
                # failed: stay pending so a later run picks it up cleanly.
                skipped += 1
                return
            except reader.ReaderError as exc:
                await asyncio.to_thread(_store_read_error, task.paper_id, str(exc))
                failed += 1
                return
            except Exception as exc:  # noqa: BLE001 — one bad paper, not one bad sweep
                log.exception("daily sweep %s: unexpected failure reading %s", date_str, task.title)
                message = f"{type(exc).__name__}: {exc}"
                await asyncio.to_thread(_store_read_error, task.paper_id, message)
                failed += 1
                return
            await asyncio.to_thread(_store_reading, task.paper_id, reading)
            done += 1

    await asyncio.gather(*(read_one_task(task) for task in tasks))
    if skipped:
        log.warning(
            "daily sweep %s: %d papers left pending (time budget or provider unavailable)",
            date_str,
            skipped,
        )
    return done, failed


def read_one(paper_id: str) -> None:
    """Re-read a single paper, blocking. Raises so the API can distinguish cases.

    Deliberately *not* a one-element :func:`_read_all`: this is a user standing
    in front of the UI having pressed a button, so a provider failure is recorded
    on the paper (``read_status="error"``, ``read_error``) and then re-raised, and
    an unconfigured provider raises without touching the row at all. Swallowing
    either would leave the user watching a spinner resolve into no change.

    Raises:
        LookupError: No such paper, or it has no abstract to read.
        reader.ReaderUnavailable: No provider configured; nothing was attempted.
        reader.ReaderError: The provider failed; the paper now records why.
    """
    with session_scope() as session:
        paper = session.get(DailyPaper, paper_id)
        if paper is None:
            raise LookupError(paper_id)
        title = paper.title
        abstract = (paper.abstract or "").strip()
        domain = paper.matched_domain
        authors = tuple(a.strip() for a in (paper.authors or "").split(";") if a.strip())
    if not abstract:
        raise LookupError(f"{paper_id} has no abstract to read")

    try:
        reading = reader.read_paper(
            title, abstract, domain=domain, authors=authors, timeout=READ_TIMEOUT_SECONDS
        )
    except reader.ReaderUnavailable:
        raise
    except reader.ReaderError as exc:
        _store_read_error(paper_id, str(exc))
        raise
    _store_reading(paper_id, reading)


# --------------------------------------------------------------------------- #
# PDF download (for importing a daily paper into the library)
# --------------------------------------------------------------------------- #


class PdfDownloadError(RuntimeError):
    """The paper's PDF could not be retrieved."""


def _validate_pdf_url(url: str, *, https_only: bool) -> urllib.parse.SplitResult:
    """Return a parsed, allowlisted PDF URL or reject it before any connection.

    This helper is used for both the first URL and every redirect target.  A
    final-response check is too late for SSRF protection: by the time
    ``response.geturl()`` can be inspected, urllib has already connected to the
    redirected host.
    """
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise PdfDownloadError("refusing to download from a malformed URL") from exc
    if parsed.scheme not in ("http", "https") or (https_only and parsed.scheme != "https"):
        raise PdfDownloadError(f"refusing to download a non-HTTP URL: {parsed.scheme or url!r}")
    if parsed.hostname is None or parsed.hostname.lower() not in _PDF_HOSTS:
        raise PdfDownloadError(f"refusing to download from unexpected host {parsed.hostname!r}")
    if parsed.username is not None or parsed.password is not None:
        raise PdfDownloadError("refusing a PDF URL containing credentials")
    expected_port = 443 if parsed.scheme == "https" else 80
    if port is not None and port != expected_port:
        raise PdfDownloadError("refusing a PDF URL using an unexpected port")
    return parsed


class _PdfRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Validate a redirect target *before* urllib opens it."""

    def __init__(self, *, https_only: bool) -> None:
        super().__init__()
        self._https_only = https_only

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> urllib.request.Request | None:
        _validate_pdf_url(newurl, https_only=self._https_only)
        return super().redirect_request(req, fp, code, msg, headers, newurl)  # type: ignore[arg-type]


def download_pdf(
    url: str,
    *,
    timeout: float = _PDF_TIMEOUT_SECONDS,
    https_only: bool = False,
) -> bytes:
    """Fetch a daily paper's PDF, refusing anything that is not one.

    ``url`` originates from the arXiv feed, so it is untrusted input on a path
    that makes an outbound request from the server. Hence the scheme and host
    allowlist, the size ceiling enforced *while* reading rather than after, and
    the ``%PDF`` magic check — the library ingestion path applies the same check
    to uploads, and an import must not be a way around it.

    Uses :mod:`urllib.request` to match :mod:`pharos.services.enrich` and
    :mod:`pharos.daily.fetcher`; ``httpx`` is not a declared dependency.
    """
    _validate_pdf_url(url, https_only=https_only)

    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    opener = urllib.request.build_opener(_PdfRedirectHandler(https_only=https_only))
    chunks: list[bytes] = []
    size = 0
    deadline = time.monotonic() + timeout
    try:
        with opener.open(request, timeout=timeout) as response:  # noqa: S310
            # The redirect handler checks every hop before connecting. Keep a
            # final defence as well, including for test/different opener
            # implementations that may return a response without invoking it.
            geturl = getattr(response, "geturl", None)
            final_url = (geturl() if callable(geturl) else None) or url
            _validate_pdf_url(final_url, https_only=https_only)
            while True:
                # read1() returns after a single socket read, so the size ceiling
                # is enforced as the body arrives rather than once it is all in
                # memory — the point of having a ceiling at all.
                chunk = response.read1(_PDF_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > _MAX_PDF_BYTES:
                    raise PdfDownloadError("PDF exceeds the maximum import size")
                chunks.append(chunk)
                if time.monotonic() >= deadline:
                    raise PdfDownloadError("PDF download timed out")
    except PdfDownloadError:
        raise
    except urllib.error.HTTPError as exc:
        raise PdfDownloadError(f"arXiv returned HTTP {exc.code} for the PDF") from exc
    except Exception as exc:  # URLError, socket timeout, TLS
        raise PdfDownloadError(f"could not download the PDF: {exc}") from exc

    data = b"".join(chunks)
    if not data[:5].startswith(b"%PDF"):
        # arXiv serves an HTML "PDF is being generated" interstitial for very
        # fresh submissions. Storing that as a paper would be silent corruption.
        raise PdfDownloadError("the downloaded file is not a PDF (arXiv may still be building it)")
    return data


# --------------------------------------------------------------------------- #
# sweep ownership
# --------------------------------------------------------------------------- #


class DailySweeper:
    """Owns the in-flight sweep, so there is never more than one.

    Both the refresh endpoint and the scheduler start sweeps, and two running at
    once would double-hit arXiv, race the global dedup rule, and multiply the
    load on the provider. Rather than queueing a second sweep behind the first —
    which leaves a request looking accepted while nothing happens for 45 minutes
    — :meth:`submit` refuses and reports what is already running, so the caller
    can say so plainly.

    Mirrors :class:`~pharos.services.translation.JobManager`: constructed inside
    the FastAPI lifespan because it holds asyncio primitives that must belong to
    the running loop.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._active_date: str | None = None

    @property
    def active_date(self) -> str | None:
        """The date currently being swept, or ``None`` when idle."""
        return self._active_date

    async def submit(self, date: dt.date, *, days: int = 1, reread: bool = False) -> bool:
        """Start a background sweep. ``False`` if one is already running.

        The run row is created *before* returning, not inside the task, so a
        client that polls the instant this responds sees ``running`` rather than
        a 404 it has to guess the meaning of.

        ``_active_date`` is claimed before the first ``await``. The event loop
        cannot switch tasks in a straight-line block, so that assignment is the
        mutual exclusion — a second request arriving mid-``submit`` sees the slot
        taken instead of starting a parallel sweep.
        """
        if self._active_date is not None:
            return False
        date_str = date.isoformat()
        self._active_date = date_str
        try:
            await asyncio.to_thread(_begin_run, date_str)
        except Exception:
            self._active_date = None
            raise
        self._task = asyncio.create_task(self._run(date, days=days, reread=reread))
        return True

    async def _run(self, date: dt.date, *, days: int, reread: bool) -> None:
        try:
            await run_sweep(date, days=days, reread=reread)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — nothing is awaiting this task's result
            log.exception("daily sweep %s crashed", date)
        finally:
            self._active_date = None

    async def aclose(self) -> None:
        """Cancel an in-flight sweep on shutdown and wait for it to unwind.

        Without this the task is destroyed mid-await at interpreter exit, which
        surfaces as a "Task was destroyed but it is pending" warning and, worse,
        can leave the run row stuck at ``running`` forever.
        """
        task = self._task
        if task is None or task.done():
            return
        task.cancel()
        # gather(return_exceptions=True) absorbs the task's own CancelledError
        # without swallowing a cancellation aimed at *us*, which a bare
        # ``except CancelledError`` here would.
        await asyncio.gather(task, return_exceptions=True)
