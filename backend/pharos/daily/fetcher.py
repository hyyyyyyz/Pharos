"""arXiv sweep for the daily papers module.

This is the "fetch" half of the daily digest: it asks the public arXiv API for
recent submissions in the configured categories, keeps the ones matching a
research direction, and hands them back as plain dataclasses. It does no
database work and no LLM work — the caller decides what to persist and what to
read, so this module stays trivially testable and can be exercised offline by
monkeypatching :func:`_http_get`.

Three design points worth stating outright:

*The sweep is one shared net, widened by its users.* ``categories`` and ``keep``
are parameters rather than module constants because the caller
(:mod:`pharos.daily.service`) assembles them from the union of every account's
settings: one fetch, one set of pages, serving everybody. Left at their defaults
they reproduce the original hard-coded behaviour exactly, which is what a fresh
install with no accounts yet gets.

*Dedup lives in the database.* The source project tracked previously-seen ids in
a ``data/seen.json`` sentinel file. Here the caller passes ``exclude_ids`` from
the ``daily_papers`` table instead, so there is exactly one source of truth and
no state file to drift, corrupt, or lose on a redeploy.

*Failure is not exceptional.* This runs unattended on a schedule against a free
public service. arXiv being slow, rate-limiting us, or announcing nothing at all
(weekends, holidays, announcement gaps) are all ordinary Tuesdays. Every such
path returns an empty list rather than raising, because a scheduler that dies on
an empty weekend sweep is worse than a digest that says "no papers today".

We use :mod:`urllib.request` rather than ``httpx`` to match
:mod:`pharos.services.enrich`: httpx is only a transitive FastAPI dependency,
not a declared one.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass

from pharos.daily.directions import (
    ARXIV_CATEGORIES,
    MAX_PAPERS_PER_DAY,
    direction_rank,
    match_directions,
)

log = logging.getLogger(__name__)

__all__ = ["FetchedPaper", "fetch_for_date"]

# HTTPS, not the plain-HTTP spelling arXiv's own docs still show. Everything
# this feed carries is stored and rendered — titles, abstracts, and the pdf_url
# the import button later fetches — so on a hostile network a cleartext feed is
# an injection point into both the database and the reading model's prompt.
_ARXIV_API = "https://export.arxiv.org/api/query"

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}

# arXiv asks API clients to identify themselves so they can contact the
# maintainer of a misbehaving client instead of blanket-blocking the IP. This
# hits a free public service on a timer, so being identifiable is the rent.
_USER_AGENT = "Pharos/0.1 (daily arXiv digest; +https://github.com/hyyyyyyz/Pharos)"

# arXiv's own API guidance is one request per three seconds. We are a background
# job with no user waiting on us, so there is no reason to push it.
_PAGE_DELAY_SECONDS = 3.0

# Results per request. arXiv permits far more, but smaller pages fail faster and
# retry cheaper, and the date-windowed query rarely needs many pages.
_PAGE_SIZE = 50

# Hard ceiling on pages so a malformed window can never turn into an unbounded
# crawl of the entire archive.
_MAX_PAGES = 10

_REQUEST_TIMEOUT_SECONDS = 60.0
_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY_SECONDS = 5.0

# A metadata page is a few hundred KB; anything vastly larger is not a response
# we understand, so refuse to buffer it rather than exhausting memory.
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024

# Status codes worth a second attempt: the server admitting it is temporarily
# broken. Every other 4xx means *we* are wrong (bad query, malformed range) and
# repeating an identical bad request is just abuse.
_RETRYABLE_STATUS = frozenset({500, 502, 503, 504})

# Stop paging once this many consecutive entries fall before the window. The
# feed is sorted newest-first, so a long older-than-window run means we have
# read past the range and the remaining pages are all misses.
_OLDER_STREAK_LIMIT = 100

# Trailing version marker on an arXiv id: "2607.08448v1" -> "2607.08448". Anchor
# it to the end rather than splitting on "v", which would maul legacy ids.
_VERSION_SUFFIX_RE = re.compile(r"v\d+$")

# Shape of a real arXiv identifier, modern ("2607.08448") or legacy
# ("hep-ph/0701001"). Used as the decoy guard described in _parse_entry.
_ARXIV_ID_RE = re.compile(r"\A(?:\d{4}\.\d{4,5}|[a-z][a-z.-]{1,20}/\d{7})\Z", re.IGNORECASE)

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class FetchedPaper:
    """One arXiv paper the sweep decided to keep.

    Frozen because the sweep hands these to the persistence layer and nothing
    downstream has any business editing a record of what arXiv said.

    ``matched_domain`` and ``matched_keywords`` are filled from the *global
    default* rubric in :mod:`pharos.daily.directions`, which is no longer the
    same question as "why was this kept". A paper is kept when it matches any
    account's own keywords, so a kept paper may well carry
    ``matched_domain=None`` — see :func:`fetch_for_date`.
    """

    arxiv_id: str
    title: str
    authors: tuple[str, ...]
    abstract: str
    categories: tuple[str, ...]
    matched_domain: str | None
    matched_keywords: tuple[str, ...]
    arxiv_url: str
    pdf_url: str
    published_at: dt.datetime | None


# --------------------------------------------------------------------------- #
# HTTP layer — deliberately one small function so tests can replace it
# --------------------------------------------------------------------------- #


def _pause(seconds: float) -> None:
    """Sleep between requests. Separate function so tests can neutralise it."""
    time.sleep(seconds)


def _http_get(url: str, *, timeout: float = _REQUEST_TIMEOUT_SECONDS) -> bytes | None:
    """GET ``url`` with bounded exponential backoff, or ``None`` if it fails.

    Retries transport errors (DNS, TLS, socket timeouts) and the 5xx family,
    because those are the server or the network having a bad minute. A 4xx is
    never retried: the request itself is wrong, so an identical retry cannot
    succeed and only adds load to a service doing us a favour. Attempts are
    capped low — if arXiv is down, tomorrow's sweep can have it.

    Returns ``None`` rather than raising so the caller can degrade to a partial
    or empty sweep instead of taking down the scheduler.
    """
    for attempt in range(_MAX_ATTEMPTS):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                return response.read(_MAX_RESPONSE_BYTES)
        except urllib.error.HTTPError as exc:
            if exc.code not in _RETRYABLE_STATUS:
                log.warning("arxiv refused request (HTTP %s), not retrying", exc.code)
                return None
            reason: object = exc.code
        except Exception as exc:  # URLError, socket.timeout, TLS, malformed URL
            reason = exc

        if attempt == _MAX_ATTEMPTS - 1:
            log.warning("arxiv request failed after %s attempts: %s", _MAX_ATTEMPTS, reason)
            return None
        delay = _RETRY_BASE_DELAY_SECONDS * (2**attempt)
        log.info("arxiv request failed (%s); retrying in %.0fs", reason, delay)
        _pause(delay)
    return None


def _build_query_url(
    *,
    start: int,
    batch: int,
    date_from: dt.date,
    date_to: dt.date,
    categories: Sequence[str],
) -> str:
    """Build a category-OR'd, date-windowed, newest-first arXiv query URL.

    The server-side ``submittedDate`` clause is what makes backfill viable:
    without it we would have to sort the whole archive descending and page down
    until we reached the target date, which for anything but "yesterday" means
    dozens of wasted requests.

    Note the ``+`` separators are arXiv's own convention for this endpoint and
    must not be percent-encoded, so the query string is assembled by hand.
    """
    category_clause = "+OR+".join(f"cat:{category}" for category in categories)
    # arXiv wants YYYYMMDDHHMM and treats the range as inclusive on both ends.
    window_from = date_from.strftime("%Y%m%d") + "0000"
    window_to = date_to.strftime("%Y%m%d") + "2359"
    search = f"({category_clause})+AND+submittedDate:[{window_from}+TO+{window_to}]"
    return (
        f"{_ARXIV_API}?search_query={search}"
        f"&start={start}"
        f"&max_results={batch}"
        f"&sortBy=submittedDate"
        f"&sortOrder=descending"
    )


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #


def _text(node: ET.Element | None) -> str:
    """Whitespace-collapsed text of ``node``, or ``""``.

    arXiv hard-wraps titles and abstracts across source lines, so raw text is
    full of newlines that would otherwise be stored and rendered verbatim.
    """
    if node is None or node.text is None:
        return ""
    return _WHITESPACE_RE.sub(" ", node.text).strip()


def _strip_version(arxiv_id: str) -> str:
    """Drop the trailing version marker — the dedup key is the paper, not v3."""
    return _VERSION_SUFFIX_RE.sub("", arxiv_id)


def _id_from_url(url: str) -> str:
    """Extract the bare arXiv id from an abs URL.

    Splits on ``/abs/`` rather than taking the last path segment: legacy ids
    embed a slash (``hep-ph/0701001``) and naive splitting silently truncates
    them to the number, which would then collide across archives.
    """
    _, _, tail = url.partition("/abs/")
    return _strip_version((tail or url.rsplit("/", 1)[-1]).strip())


def _parse_entry(entry: ET.Element) -> FetchedPaper | None:
    """Parse one ``<entry>``, or ``None`` if it is not a usable paper record.

    Returns a :class:`FetchedPaper` with the match fields still empty; the
    caller fills them in once it has decided the paper is in-window and worth
    classifying, so we never pay for keyword matching on entries we discard.

    This is also where the decoy is caught. arXiv answers certain malformed
    queries with HTTP 200 and a perfectly well-formed feed containing a single
    entry whose id points at its error documentation
    (``http://arxiv.org/api/errors#...``) — so a naive client happily stores a
    "paper" titled "Error". We reject anything whose id is not shaped like a
    real arXiv identifier, which catches that case and any future variant of it
    without hardcoding the current error URL.
    """
    id_url = _text(entry.find("atom:id", _NS))
    if not id_url:
        return None
    if "/api/errors" in id_url:
        log.warning("arxiv returned an error entry instead of results: %s", id_url)
        return None
    arxiv_id = _id_from_url(id_url)
    if not _ARXIV_ID_RE.match(arxiv_id):
        log.warning("dropping arxiv entry with implausible id %r", arxiv_id)
        return None

    title = _text(entry.find("atom:title", _NS))
    abstract = _text(entry.find("atom:summary", _NS))
    if not title:
        return None

    authors = tuple(
        name
        for author in entry.findall("atom:author", _NS)
        if (name := _text(author.find("atom:name", _NS)))
    )
    categories = tuple(
        term
        for category in entry.findall("atom:category", _NS)
        if (term := (category.get("term") or "").strip())
    )

    abs_url = ""
    pdf_url = ""
    for link in entry.findall("atom:link", _NS):
        if link.get("title") == "pdf":
            pdf_url = link.get("href", "")
        elif link.get("rel") == "alternate":
            abs_url = link.get("href", "")

    return FetchedPaper(
        arxiv_id=arxiv_id,
        title=title,
        authors=authors,
        abstract=abstract,
        categories=categories,
        matched_domain=None,
        matched_keywords=(),
        arxiv_url=abs_url or f"https://arxiv.org/abs/{arxiv_id}",
        pdf_url=pdf_url or f"https://arxiv.org/pdf/{arxiv_id}",
        published_at=_parse_timestamp(_text(entry.find("atom:published", _NS))),
    )


def _parse_timestamp(value: str) -> dt.datetime | None:
    """Parse arXiv's UTC ISO-8601 timestamp, tolerating the trailing ``Z``."""
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #


def fetch_for_date(
    date: dt.date,
    *,
    days: int = 1,
    max_papers: int = MAX_PAPERS_PER_DAY,
    exclude_ids: Collection[str] = (),
    categories: Sequence[str] | None = None,
    keep: Callable[[str], bool] | None = None,
) -> list[FetchedPaper]:
    """Fetch arXiv papers announced in a window ending ``date`` that anyone wants.

    The window is ``[date - (days - 1), date + 1)`` — inclusive of ``date``
    itself, which is why the upper bound is exclusive of the following day.

    ``exclude_ids`` are version-stripped ids already stored by the caller; they
    are skipped before any matching, so a re-run over the same date is cheap and
    yields only what is genuinely new.

    Args:
        categories: arXiv categories to OR together. The caller passes the union
            of every account's configured categories; ``None`` falls back to
            :data:`~pharos.daily.directions.ARXIV_CATEGORIES`, which is what a
            fresh install with no accounts gets.
        keep: Decides whether a paper is worth storing, given its lower-cased
            "title + abstract". The caller builds this from the union of every
            account's keywords, so the shared table holds a paper if *anybody*
            asked for it. ``None`` falls back to the global default rubric,
            i.e. "keep it if :func:`match_directions` matched".

    Whatever decided to keep the paper, ``matched_domain`` and
    ``matched_keywords`` are always filled from the global default rubric. Those
    two fields are no longer the reason a paper was kept; they are a fallback
    classification for a reader who has not configured any directions of their
    own, and they are ``None``/``()`` for a paper that only some user's private
    keyword matched. Per-reader matching happens at query time — see
    :mod:`pharos.daily.user_directions`.

    Returns papers ordered by default-rubric priority then newest-first, capped
    at ``max_papers`` (keeping the most recent). Returns ``[]`` when arXiv is
    unreachable or simply announced nothing matching — both are normal, and
    neither is worth an exception on a scheduled job.
    """
    if days < 1:
        raise ValueError(f"days must be >= 1, got {days}")
    if max_papers < 1:
        raise ValueError(f"max_papers must be >= 1, got {max_papers}")

    search_categories = list(categories) if categories else list(ARXIV_CATEGORIES)
    if not search_categories:  # pragma: no cover — defensive; an empty OR is not a query
        search_categories = list(ARXIV_CATEGORIES)

    start_date = date - dt.timedelta(days=days - 1)
    end_exclusive = date + dt.timedelta(days=1)
    already_seen = {_strip_version(str(i).strip()) for i in exclude_ids if str(i).strip()}

    log.info(
        "daily sweep: window [%s .. %s], categories=%s, max=%s, excluding %d known ids",
        start_date,
        date,
        ",".join(search_categories),
        max_papers,
        len(already_seen),
    )

    kept: list[FetchedPaper] = []
    kept_ids: set[str] = set()
    older_streak = 0

    for page in range(_MAX_PAGES):
        url = _build_query_url(
            start=page * _PAGE_SIZE,
            batch=_PAGE_SIZE,
            date_from=start_date,
            date_to=date,
            categories=search_categories,
        )
        body = _http_get(url)
        if body is None:
            # One failed page after retries means arXiv is not having a good
            # time. Keep whatever earlier pages produced rather than hammering.
            log.warning("daily sweep: stopping at page %d after request failure", page + 1)
            break

        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            log.warning("daily sweep: unparseable response on page %d: %s", page + 1, exc)
            break

        entries = root.findall("atom:entry", _NS)
        if not entries:
            break

        page_kept = 0
        for entry in entries:
            paper = _parse_entry(entry)
            if paper is None:
                continue

            # Paging can repeat entries when new submissions land mid-sweep, so
            # guard against duplicates within this run as well as across runs.
            if paper.arxiv_id in already_seen or paper.arxiv_id in kept_ids:
                continue
            if paper.published_at is None:
                continue

            published_date = paper.published_at.date()
            if published_date >= end_exclusive:
                # Newer than the window. The feed is sorted descending, so keep
                # paging without letting this trip the older-than-window exit.
                continue
            if published_date < start_date:
                older_streak += 1
                continue
            older_streak = 0

            text = f"{paper.title}\n{paper.abstract}"
            # The default rubric always runs, because its answer is what gets
            # stored in matched_domain/matched_keywords. What it no longer does
            # is decide the paper's fate — `keep` does, and it speaks for every
            # account on the instance rather than for this one hard-coded table.
            domain, hits = match_directions(text)
            if keep is None:
                if domain is None:
                    continue
            elif not keep(text.lower()):
                continue

            kept.append(
                dataclasses.replace(paper, matched_domain=domain, matched_keywords=hits[:6])
            )
            kept_ids.add(paper.arxiv_id)
            page_kept += 1

        log.info(
            "daily sweep: page %d scanned %d entries, kept %d (total %d)",
            page + 1,
            len(entries),
            page_kept,
            len(kept),
        )

        if older_streak > _OLDER_STREAK_LIMIT:
            break
        # Overshoot the cap before stopping: the cap keeps the *most recent*
        # matches, so we want a comfortable surplus to choose from.
        if len(kept) >= max_papers * 2:
            break
        if page < _MAX_PAGES - 1:
            _pause(_PAGE_DELAY_SECONDS)

    if not kept:
        log.info("daily sweep: no matching papers for %s", date)
        return []

    # Cap by recency first...
    kept.sort(key=_recency_key, reverse=True)
    if len(kept) > max_papers:
        log.info("daily sweep: capping %d matches to %d most recent", len(kept), max_papers)
        kept = kept[:max_papers]

    # ...then order by the default rubric, newest first inside each direction,
    # with the id as a stable final tie-break. This is only a *storage* order
    # now — every reader is re-sorted against their own directions at query
    # time — but it keeps the ordering deterministic, and it puts the papers a
    # default-configured install cares about at the front of the table.
    # A paper kept solely by somebody's private keyword has matched_domain=None
    # and so sorts past every classified one, which is the right end of the list
    # for something the shared rubric has nothing to say about.
    kept.sort(key=lambda p: (direction_rank(p.matched_domain), _neg_recency_key(p), p.arxiv_id))
    return kept


def _recency_key(paper: FetchedPaper) -> dt.datetime:
    """Sortable publication timestamp, with a floor for the ``None`` case.

    ``published_at`` is never ``None`` for a kept paper (the window filter drops
    those), but sorting must not depend on that invariant holding forever.
    """
    if paper.published_at is None:
        return dt.datetime.min.replace(tzinfo=dt.UTC)
    return paper.published_at


def _neg_recency_key(paper: FetchedPaper) -> float:
    """Negated epoch seconds, so ascending sort yields newest-first."""
    return -_recency_key(paper).timestamp()
