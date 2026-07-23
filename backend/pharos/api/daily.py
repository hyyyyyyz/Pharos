"""每日论文 API — the daily arXiv digest.

Two conventions run through every response model here.

*Stored shape is not wire shape.* ``authors``/``categories``/``matched_keywords``
live in SQLite as joined strings and ``highlights``/``scores`` as JSON text,
because the columns predate any client. None of that is the client's problem, so
they go out as arrays and objects.

*Unread is a first-class state, not an empty one.* ``summary_zh``, ``highlights``
and ``scores`` are ``null`` — never ``{}``, never a placeholder string — until a
model has actually read the paper. ``read_status`` says which case a client is
looking at, and ``/status`` says whether reading is even possible right now, so
the UI can explain an unread digest rather than looking broken.

*The table is shared; the feed is not.* ``daily_papers`` holds the union of what
every account's keywords asked for, because one arXiv fetch and one LLM reading
serve everybody. Every read endpoint here therefore filters that table through
the caller's own directions before rendering it: which papers they see, which
direction each falls under, how relevant it is, and where it ranks are all
derived per request from :mod:`pharos.daily.user_directions`. Two fields on the
way out are overridden rather than echoed — ``matched_domain``/
``matched_keywords`` (the shared default rubric's answer) and the ``relevance``
inside ``scores`` (computed by the LLM against that same shared rubric). Passing
either through unchanged would put a number on screen that looks personal and
is not, which is the specific failure this module is arranged to prevent.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from pharos.api.deps import current_user, get_library, get_session
from pharos.api.schemas import as_utc
from pharos.daily import reader, service
from pharos.db.models import DailyPaper, DailyRun, Paper, User
from pharos.db.session import session_scope
from pharos.services.library import LibraryService

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/daily", tags=["daily"])

#: ``YYYY-MM-DD``. Constrains the path so ``/api/daily/dates`` can never be
#: mistaken for a date, independently of route declaration order.
_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"

#: Upper bound on a backfill window. A sweep of 30 days would page through the
#: whole archive and blow past arXiv's rate guidance; catching up after downtime
#: needs single digits.
_MAX_DAYS = 7

#: Sentinel distinguishing "no such daily paper" (404) from a message meant for
#: the user (422), since ``_import_to_library`` returns both through one channel.
_ERR_NOT_FOUND = "not-found"


# --------------------------------------------------------------------------- #
# response models
# --------------------------------------------------------------------------- #


class DailyPaperOut(BaseModel):
    id: str
    arxiv_id: str
    date: str
    title: str
    authors: list[str] = []
    abstract: str | None = None
    categories: list[str] = []
    #: The CALLER's direction this paper fell under, and the caller's own
    #: keywords that fired — not the stored columns of the same name, which
    #: record the shared default rubric's answer. See the module docstring.
    matched_domain: str | None = None
    matched_keywords: list[str] = []
    arxiv_url: str | None = None
    pdf_url: str | None = None
    published_at: dt.datetime | None = None
    venue: str | None = None

    #: "pending" | "done" | "error". The client must branch on this rather than
    #: on whether ``summary_zh`` is truthy.
    read_status: str
    summary_zh: str | None = None
    #: {contribution, innovation, method, results} — null until read.
    highlights: dict[str, str] | None = None
    #: {relevance, recency, popularity, quality, recommendation} — null until
    #: read. ``recency``/``popularity``/``quality`` are the LLM's, and describe
    #: the paper. ``relevance`` is computed for THIS caller from their own
    #: keywords, and ``recommendation`` is re-weighted from the other four, so
    #: neither is the number the model emitted.
    scores: dict[str, float] | None = None
    #: Mirrors ``scores.recommendation`` — likewise the caller's, not the row's.
    score_recommendation: float | None = None
    read_model: str | None = None
    read_at: dt.datetime | None = None
    read_error: str | None = None

    imported_paper_id: str | None = None
    created_at: dt.datetime


class DailyRunOut(BaseModel):
    id: str
    date: str
    status: str
    fetched: int
    read_done: int
    read_failed: int
    error: str | None = None
    started_at: dt.datetime
    finished_at: dt.datetime | None = None


class DateSummaryOut(BaseModel):
    date: str
    total: int
    read: int
    pending: int
    failed: int


class DailyDayOut(BaseModel):
    """One day's digest. ``run`` is null for a date never swept in this install."""

    date: str
    total: int
    run: DailyRunOut | None = None
    papers: list[DailyPaperOut]


class DailyStatusOut(BaseModel):
    """Whether the digest can read, and what it has done lately."""

    #: False means papers are still fetched but stay ``pending``. The UI should
    #: say "未配置阅读模型" rather than implying something failed.
    llm_configured: bool
    #: The chat provider, via ``LLMProvider.redacted()`` — never the API key.
    provider: dict[str, Any] | None = None
    #: The CALLER's own research directions, in their declared priority order.
    #: Was the global list; a per-user module cannot report one list to
    #: everybody without describing somebody else's reading.
    directions: list[str]
    last_run: DailyRunOut | None = None
    #: Today's counts, or null when today has not been swept yet.
    today: DateSummaryOut | None = None
    #: The date of a sweep running right now, if any.
    sweeping: str | None = None


class RefreshRequest(BaseModel):
    """Body of a refresh. Every field optional — the common case is ``{}``."""

    model_config = ConfigDict(extra="forbid")

    #: Digest date to sweep. Defaults to today.
    date: str | None = None
    #: Window size ending at ``date``, for catching up after downtime.
    days: int | None = Field(default=None, ge=1, le=_MAX_DAYS)
    #: Also re-read papers already read — e.g. after switching to a better model.
    reread: bool = False


class ImportResult(BaseModel):
    paper_id: str


# --------------------------------------------------------------------------- #
# converters
# --------------------------------------------------------------------------- #


def _safe_filename(stem: str) -> str:
    """Build an ``orig_filename`` from a paper's id and title.

    Path separators are stripped rather than escaped: legacy arXiv ids contain a
    slash (``hep-ph/0701001``) and titles contain anything at all. The blob store
    addresses files by hash, so this string is never itself a path — but it is
    handed back as a download filename, and a value that can traverse a directory
    should not be created in the first place.
    """
    cleaned = " ".join(stem.replace("/", "-").replace("\\", "-").split())
    cleaned = "".join(ch for ch in cleaned if ch.isprintable() and ch not in '<>:"|?*')
    return (cleaned[:180].strip() or "arxiv-paper") + ".pdf"


def _split(raw: str | None, sep: str) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(sep) if part.strip()]


def _load_json_object(raw: str | None, kind: str, paper_id: str) -> dict[str, Any] | None:
    """Parse a stored JSON column, degrading to ``None`` rather than failing.

    The writer only ever stores a validated card, so this should not happen — but
    the alternative to degrading is a 500 on the whole day's list because one row
    is malformed, and ``None`` already means exactly "no card", which the client
    knows how to render.
    """
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        log.warning("daily paper %s has unparseable %s JSON", paper_id, kind)
        return None
    return value if isinstance(value, dict) else None


def owned_import_ids(session: Session, papers: list[DailyPaper], user_id: str) -> set[str]:
    """Of the library ids named by ``papers``, the subset ``user_id`` actually owns.

    ``DailyPaper.imported_paper_id`` is a single column on a *shared* row, so it
    records whoever imported the paper last — very possibly somebody else. Echoing
    it back unfiltered hands every reader another user's private ``Paper``
    primary key, which is both a membership disclosure ("someone here has this
    paper") and a real id that only stays inert for as long as every paper
    endpoint remembers to be owner-scoped. Resolved as one query over the whole
    day rather than per row, so listing a digest stays a fixed number of queries.
    """
    wanted = {p.imported_paper_id for p in papers if p.imported_paper_id}
    if not wanted:
        return set()
    return set(
        session.scalars(select(Paper.id).where(Paper.id.in_(wanted), Paper.user_id == user_id))
    )


def daily_paper_out(
    paper: DailyPaper,
    *,
    owned_imports: set[str] | None = None,
    feed: service.FeedPaper | None = None,
) -> DailyPaperOut:
    """Render one daily paper.

    ``owned_imports`` is the caller's own library ids (see
    :func:`owned_import_ids`); ``imported_paper_id`` is emitted only when it names
    one of them. Defaulting to ``None`` means *nobody's* import is echoed, so a
    caller that forgets to pass it leaks nothing — the badge is merely wrong,
    which is the right way round for this to fail.

    ``feed`` carries the caller's own match (see
    :func:`pharos.daily.service.papers_for_user`) and, when present, replaces the
    row's ``matched_domain``, ``matched_keywords``, ``scores`` and
    ``score_recommendation`` with the values computed for *them*. When it is
    absent the stored ones are emitted as-is — the shared default rubric's
    answer, which is the only honest thing to say about a paper being rendered
    outside any particular reader's feed.
    """
    highlights = _load_json_object(paper.highlights, "highlights", paper.id)
    scores = _load_json_object(paper.scores, "scores", paper.id)
    imported_id = paper.imported_paper_id
    if imported_id is not None and imported_id not in (owned_imports or set()):
        imported_id = None

    matched_domain = paper.matched_domain
    matched_keywords = _split(paper.matched_keywords, ",")
    out_scores = (
        {str(k): float(v) for k, v in scores.items() if isinstance(v, (int, float))}
        if scores
        else None
    )
    recommendation = paper.score_recommendation
    if feed is not None:
        matched_domain = feed.direction
        matched_keywords = list(feed.keywords)
        out_scores = feed.scores
        recommendation = feed.recommendation

    return DailyPaperOut(
        id=paper.id,
        arxiv_id=paper.arxiv_id,
        date=paper.date,
        title=paper.title,
        authors=_split(paper.authors, ";"),
        abstract=paper.abstract,
        categories=_split(paper.categories, ","),
        matched_domain=matched_domain,
        matched_keywords=matched_keywords,
        arxiv_url=paper.arxiv_url,
        pdf_url=paper.pdf_url,
        published_at=as_utc(paper.published_at),
        venue=paper.venue,
        read_status=paper.read_status,
        summary_zh=paper.summary_zh,
        highlights={str(k): str(v) for k, v in highlights.items()} if highlights else None,
        scores=out_scores,
        score_recommendation=recommendation,
        read_model=paper.read_model,
        read_at=as_utc(paper.read_at),
        read_error=paper.read_error,
        imported_paper_id=imported_id,
        created_at=as_utc(paper.created_at),
    )


def daily_run_out(run: DailyRun) -> DailyRunOut:
    return DailyRunOut(
        id=run.id,
        date=run.date,
        status=run.status,
        fetched=run.fetched,
        read_done=run.read_done,
        read_failed=run.read_failed,
        error=run.error,
        started_at=as_utc(run.started_at),
        finished_at=as_utc(run.finished_at),
    )


# --------------------------------------------------------------------------- #
# reads
# --------------------------------------------------------------------------- #


@router.get("/status", response_model=DailyStatusOut)
def get_status(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> DailyStatusOut:
    """Can the digest read, and what has it done lately?

    The first thing the UI asks, because it decides whether an all-``pending``
    day means "not configured" or "still working".

    Requires an account even though the sweep itself is shared. This response
    mixes *instance* detail — which LLM provider is configured, what the sweeper
    has been doing — with the caller's own directions and their own view of
    today's counts. The former is deployment detail rather than public arXiv
    data, and there is no reason an unauthenticated caller should be able to
    fingerprint it; the latter has an owner by definition.
    """
    settings = request.app.state.settings
    provider = settings.provider_for("chat")
    sweeper: service.DailySweeper | None = getattr(request.app.state, "daily_sweeper", None)

    today_str = service.today().isoformat()
    # The caller's counts, not the table's: this drives the same badge the date
    # rail does, and the two disagreeing would be worse than either being wrong.
    today_feed = service.papers_for_user(session, today_str, user.id)
    statuses = [f.paper.read_status for f in today_feed]
    today_summary = (
        DateSummaryOut(
            date=today_str,
            total=len(today_feed),
            read=statuses.count("done"),
            pending=statuses.count("pending"),
            failed=statuses.count("error"),
        )
        if today_feed
        else None
    )
    last = service.latest_run(session)

    return DailyStatusOut(
        llm_configured=provider is not None,
        # redacted() is the only representation of a provider that may leave the
        # process: it omits api_key entirely rather than masking it.
        provider=provider.redacted() if provider is not None else None,
        directions=[d.name for d in service.reader_directions(session, user.id)],
        last_run=daily_run_out(last) if last is not None else None,
        today=today_summary,
        sweeping=sweeper.active_date if sweeper is not None else None,
    )


@router.get("/dates", response_model=list[DateSummaryOut])
def list_dates(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> list[DateSummaryOut]:
    """Every day with papers the CALLER can see, newest first.

    Counted through the caller's directions rather than over the shared table.
    Counting the table would be cheaper and wrong in a way that shows: the rail
    would advertise a day with twelve papers, the day view would filter nine of
    them away, and the digest would look broken rather than filtered. A date
    where nothing matches the caller does not appear at all.
    """
    return [
        DateSummaryOut(**vars(summary))
        for summary in service.date_summaries_for_user(session, user.id)
    ]


@router.get("/{date}", response_model=DailyDayOut)
def get_day(
    date: str = Path(pattern=_DATE_PATTERN, description="YYYY-MM-DD"),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> DailyDayOut:
    """One day's digest as the CALLER sees it, best-first.

    A day with no papers is 200 with an empty list, not 404: "nothing was
    announced that matched" and "that date does not exist" are the same thing
    here, and the client renders both the same way.

    ``daily_papers`` is shared — arXiv announces to the world and the sweep
    stores the union of what every account asked for — so three things are
    resolved against this caller before anything is returned. Which papers
    appear, and under which direction, comes from their own keywords; how
    relevant each is and where it ranks is recomputed from those keywords rather
    than taken from the stored card, whose ``relevance`` was scored against the
    old global rubric; and ``imported_paper_id`` is checked against their own
    library rather than echoed off the shared row. The day is capped at their
    own ``max_per_day``.
    """
    try:
        date_str = service.parse_date(date).isoformat()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    feed = service.papers_for_user(session, date_str, user.id)
    run = service.run_for_date(session, date_str)
    owned = owned_import_ids(session, [f.paper for f in feed], user.id)
    return DailyDayOut(
        date=date_str,
        total=len(feed),
        run=daily_run_out(run) if run is not None else None,
        papers=[daily_paper_out(f.paper, owned_imports=owned, feed=f) for f in feed],
    )


# --------------------------------------------------------------------------- #
# writes
# --------------------------------------------------------------------------- #


def _run_snapshot(date_str: str) -> DailyRunOut | None:
    with session_scope() as session:
        run = service.run_for_date(session, date_str)
        return daily_run_out(run) if run is not None else None


@router.post("/refresh", response_model=DailyRunOut, status_code=202)
async def refresh(
    request: Request,
    body: RefreshRequest | None = None,
    user: User = Depends(current_user),
) -> DailyRunOut:
    """Start a sweep in the background and return its run row immediately.

    Requires an account. A sweep is not a read: it pages arXiv on this
    instance's behalf and then makes one LLM call per paper against the
    operator's API key. Unauthenticated, it is a free way for anyone who can
    reach the port to spend the operator's money and get the instance
    rate-limited by arXiv, and — because it writes the shared ``DailyPaper``
    rows every account reads — to churn what every user sees.

    A sweep fetches from arXiv and then makes one LLM call per paper, so it runs
    for minutes — far too long to hold an HTTP request open. This follows the
    translation-job pattern already in the codebase: the row is created
    synchronously so there is something to poll, the work happens in a background
    task, and the client watches ``status`` on ``GET /api/daily/{date}``.

    409 when a sweep is already running: only one may run at a time (see
    :class:`~pharos.daily.service.DailySweeper`), and reporting that is more
    useful than silently queueing work the caller cannot see.
    """
    body = body or RefreshRequest()
    sweeper: service.DailySweeper = request.app.state.daily_sweeper

    try:
        date = service.parse_date(body.date) if body.date else service.today()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    started = await sweeper.submit(date, days=body.days or 1, reread=body.reread)
    if not started:
        active = sweeper.active_date or "another date"
        raise HTTPException(status_code=409, detail=f"a sweep for {active} is already running")

    # submit() creates the run row before returning, so this always finds it.
    snapshot = await asyncio.to_thread(_run_snapshot, date.isoformat())
    if snapshot is None:  # pragma: no cover — defensive
        raise HTTPException(status_code=500, detail="the sweep did not start")
    return snapshot


def _paper_snapshot(paper_id: str, user_id: str) -> DailyPaperOut | None:
    """One paper as ``user_id`` sees it, in its own short session.

    Rendered through ``feed_entry`` for the same reason the day view is: the row
    that comes back carries a freshly written card, and the ``relevance`` inside
    it was scored against the shared default rubric rather than this caller's
    directions.
    """
    with session_scope() as session:
        paper = session.get(DailyPaper, paper_id)
        if paper is None:
            return None
        owned = owned_import_ids(session, [paper], user_id)
        feed = service.feed_entry(session, paper, user_id)
        return daily_paper_out(paper, owned_imports=owned, feed=feed)


@router.post("/papers/{paper_id}/read", response_model=DailyPaperOut)
async def read_paper_now(paper_id: str, user: User = Depends(current_user)) -> DailyPaperOut:
    """Read (or re-read) one paper, waiting for the result.

    Requires an account for the same reason as ``/refresh``: this spends an LLM
    call on the operator's key and overwrites the summary, highlights and scores
    on a row that every user of this instance reads.

    Unlike a sweep this blocks, because it is a single call the user is watching
    and the answer *is* the response. A provider failure comes back as 200 with
    ``read_status="error"`` and ``read_error`` filled in — the failure is part of
    the paper's state, and returning the updated row lets the UI show why without
    a second request. An unconfigured provider is a 503 instead: nothing was
    attempted, nothing was written, and the fix is configuration rather than
    retrying.
    """
    try:
        await asyncio.to_thread(service.read_one, paper_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except reader.ReaderUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except reader.ReaderError:
        # Already recorded on the paper by ``read_one``; fall through and return
        # the row so the client can render the error next to the paper.
        log.info("daily paper %s failed to read", paper_id)

    snapshot = await asyncio.to_thread(_paper_snapshot, paper_id, user.id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    return snapshot


def _import_to_library(
    library: LibraryService, daily_paper_id: str, user_id: str
) -> tuple[str | None, str | None]:
    """Download and ingest one daily paper into ``user_id``'s library.

    Runs in a worker thread: the download is blocking and the ingestion path is
    the synchronous one the upload endpoint uses. Using ``add_upload`` rather
    than a bespoke insert is the point — content addressing, metadata extraction
    and registry enrichment all come along, and importing a paper the user had
    already uploaded returns the existing library entry instead of duplicating
    the PDF.

    The daily feed itself is shared: arXiv announces the same papers to everyone,
    so ``DailyPaper`` is looked up without an owner. Only the *library row* this
    creates is private, which is why ``user_id`` is threaded into ``add_upload``
    and into every ``Paper`` lookup below.
    """
    with session_scope() as session:
        paper = session.get(DailyPaper, daily_paper_id)
        if paper is None:
            return None, _ERR_NOT_FOUND
        if paper.imported_paper_id is not None:
            # ``DailyPaper.imported_paper_id`` is a single shared column on a
            # shared row, so it may well name *another* user's library entry —
            # whoever imported this paper first. Scoping the lookup to the caller
            # is what stops the fast path from handing back an id they do not
            # own; a miss falls through to a fresh import for them.
            existing = session.scalar(
                select(Paper).where(Paper.id == paper.imported_paper_id, Paper.user_id == user_id)
            )
            if existing is not None:
                # Already imported, so this is idempotent — except when the user
                # has since trashed it. Asking to import it again is as clear a
                # statement that they want it back as re-uploading the file
                # would be, and ``add_upload`` restores it in exactly that case;
                # restoring here says the same thing without re-downloading.
                existing.deleted_at = None
                return existing.id, None
            # The library row was purged. Fall through and import it afresh
            # rather than returning a dangling id.
        pdf_url = paper.pdf_url
        arxiv_id = paper.arxiv_id
        title = paper.title
    if not pdf_url:
        return None, "this paper has no PDF URL"

    data = service.download_pdf(pdf_url)

    with session_scope() as session:
        # Whether this paper is already in the library decides what we may
        # overwrite. ``add_upload`` is content-addressed and returns the existing
        # row for a byte-identical file, so importing something the user had
        # already uploaded must not rewrite their record's provenance: it did
        # enter the library as an upload, and ``source`` documents exactly that.
        # Scoped to the caller: the question is "did *this* user already have
        # this file", not "does the blob exist". Another user having uploaded the
        # same PDF must not suppress the ``source = "arxiv"`` stamp on the row
        # being created here, which is genuinely a fresh arXiv import.
        sha256 = hashlib.sha256(data).hexdigest()
        pre_existing = session.scalar(
            select(Paper.id).where(Paper.orig_sha256 == sha256, Paper.user_id == user_id)
        )

        # arXiv's PDF URL has no filename component, so name the file after the
        # paper: this becomes ``orig_filename`` and the fallback title if the
        # PDF carries no usable metadata of its own.
        filename = _safe_filename(f"{arxiv_id} {title}")
        imported = library.add_upload(session, user_id=user_id, filename=filename, data=data)
        if pre_existing is None:
            imported.source = "arxiv"
        if not imported.arxiv_id:
            # Useful even on an existing row: it is new information, and a later
            # metadata refresh can use it to reach the registry.
            imported.arxiv_id = arxiv_id
        session.flush()
        imported_id = imported.id

        daily = session.get(DailyPaper, daily_paper_id)
        if daily is not None:
            # NOTE: this single column on a *shared* row records only the most
            # recent importer's library id, so a second user importing the same
            # paper overwrites the first user's marker. The *disclosure* half of
            # that is now closed — every read path filters this id through
            # ``owned_import_ids``, so a user only ever sees it when it names a
            # row they own. What remains is a correctness limit, not a leak: the
            # earlier importer's "already in my library" badge goes dark once
            # somebody else imports the same paper, because one column cannot
            # hold per-user state. It wants a (user_id, daily_paper_id) join
            # table; until then the badge fails off rather than wrong.
            daily.imported_paper_id = imported_id
    return imported_id, None


@router.post("/papers/{paper_id}/import", response_model=ImportResult, status_code=201)
async def import_paper(
    paper_id: str,
    library: LibraryService = Depends(get_library),
    user: User = Depends(current_user),
) -> ImportResult:
    """Pull a daily paper's PDF into the caller's library so it can be translated.

    The only endpoint in this module that needs an account: the arXiv feed is the
    same for everybody, but the ``Paper`` row this creates is private and must be
    attributed to whoever asked for it.
    """
    try:
        imported_id, error = await asyncio.to_thread(_import_to_library, library, paper_id, user.id)
    except service.PdfDownloadError as exc:
        # 502: we are the client here, and the upstream is what failed.
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if error == _ERR_NOT_FOUND:
        raise HTTPException(status_code=404, detail="Paper not found")
    if error is not None:
        raise HTTPException(status_code=422, detail=error)
    if imported_id is None:  # pragma: no cover — defensive
        raise HTTPException(status_code=500, detail="Import failed")
    return ImportResult(paper_id=imported_id)
