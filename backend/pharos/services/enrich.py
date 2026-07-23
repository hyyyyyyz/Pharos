"""Authoritative metadata lookup from CrossRef and arXiv.

The PDF heuristics in :mod:`pharos.services.metadata` are guesses made from
layout and regexes. Once those guesses yield a DOI or an arXiv id we can ask the
registry that issued it and get facts instead, which is why this module exists.

Everything here is best-effort and deliberately silent: the user may be behind a
firewall, offline, rate-limited, or holding a DOI that never resolved. None of
those are errors the upload request should die on, so every failure path returns
``None`` and the caller keeps whatever the PDF gave it.

We use :mod:`urllib.request` rather than ``httpx``: httpx is only present as a
transitive FastAPI dependency, not a declared one, and two blocking GETs do not
justify taking on a new direct dependency.
"""

from __future__ import annotations

import dataclasses
import html
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from pharos.services.metadata import ExtractedMeta

__all__ = ["enrich_by_doi", "enrich_by_arxiv", "merge"]

# CrossRef asks API users to identify themselves with a contact URL so they can
# reach maintainers of misbehaving clients instead of blanket-blocking them.
_USER_AGENT = "Pharos/0.1 (https://github.com/hyyyyyyz/Pharos)"

_CROSSREF_WORK_URL = "https://api.crossref.org/works/{doi}"
_ARXIV_QUERY_URL = "http://export.arxiv.org/api/query?id_list={id}"

_ATOM_NS = "http://www.w3.org/2005/Atom"
_ARXIV_NS = "http://arxiv.org/schemas/atom"

# A response larger than this is not a metadata record we understand, so refuse
# to buffer it rather than letting a hostile or broken endpoint exhaust memory.
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
#: Body read granularity — also how often the wall-clock deadline is re-checked.
_READ_CHUNK_BYTES = 64 * 1024

# Mirror the bounds ``metadata`` enforces on PDF-extracted values, so that a
# registry record cannot land a larger value in the row than the PDF path ever
# could. Only the abstract is truncated, matching the PDF extractor: there a
# title or venue over its limit is *rejected* rather than cut, because half a
# title is a wrong title, while a clipped abstract is still a usable abstract.
_ABSTRACT_MAX_CHARS = 4000
_TITLE_MAX_CHARS = 300
_VENUE_MAX_CHARS = 120
_AUTHOR_MAX_CHARS = 80
_MAX_AUTHORS = 60
_DOI_MAX_CHARS = 100

# Publication years outside this window are data-entry noise (CrossRef contains
# 0, 1, and far-future values). Dropping them beats storing a wrong year.
_MIN_YEAR = 1500
_MAX_YEAR = 2100

_WHITESPACE_RE = re.compile(r"\s+")
_TAG_RE = re.compile(r"<[^>]*>")
# Matches a leading JATS section title that just says "Abstract" — a label, not
# content. Structured abstracts' other <title>s (Methods, Results, ...) are kept.
# Any wrapper elements opened before it are skipped: CrossRef commonly nests the
# label inside <jats:abstract>, which a strict "starts with <title>" test missed,
# leaving a stray "Abstract" word at the head of the stored text.
_LEADING_ABSTRACT_TITLE_RE = re.compile(
    r"^(\s*(?:<(?!/)[^>]*>\s*)*?)<(?:\w+:)?title>\s*abstract\s*</(?:\w+:)?title>",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #


def _clean(value: Any) -> str | None:
    """Collapse whitespace to a single-line string, or ``None`` if it is empty.

    Both APIs hard-wrap titles and abstracts across source lines, so the raw
    text is full of newlines and runs of spaces that would show up verbatim in
    the UI.
    """
    if not isinstance(value, str):
        return None
    collapsed = _WHITESPACE_RE.sub(" ", value).strip()
    return collapsed or None


def _fetch(url: str, timeout: float) -> bytes | None:
    """GET ``url`` once, returning the body or ``None`` on any failure.

    Deliberately does not retry: this runs inside a request the user is waiting
    on, and a retry loop would multiply the worst-case latency by the number of
    attempts for services that are usually down for structural reasons (GFW,
    no network) rather than transient ones.

    ``timeout`` is enforced as a *wall-clock deadline*, not just as the socket
    timeout. ``urlopen``'s timeout bounds each individual socket operation, so a
    server that trickles the body a few bytes at a time — each chunk arriving
    just inside the socket timeout — keeps the call alive indefinitely: a 1s
    timeout was measured blocking for 24s that way. Since the caller is an
    upload request holding a budget, the body is therefore read in chunks with
    the deadline re-checked after each one.
    """
    deadline = time.monotonic() + timeout
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            # ``read1`` performs at most one underlying socket read, so it
            # returns as soon as *something* arrives. ``read(n)`` instead blocks
            # until it has all n bytes, which hands control back only once the
            # whole trickle is over and defeats the deadline check entirely.
            read = getattr(response, "read1", response.read)
            chunks: list[bytes] = []
            total = 0
            while total < _MAX_RESPONSE_BYTES:
                chunk = read(min(_READ_CHUNK_BYTES, _MAX_RESPONSE_BYTES - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if time.monotonic() >= deadline:
                    # Partial bodies are never parseable JSON/XML, so a
                    # truncated read is the same outcome as no read at all.
                    return None
            return b"".join(chunks)
    except Exception:
        # HTTPError (404 for an unresolvable DOI, 429 when rate-limited),
        # URLError, socket timeouts, TLS failures, malformed URLs — the caller
        # treats all of them identically, as "no better data available".
        return None


def _year_from(value: Any) -> int | None:
    """Coerce ``value`` to a plausible publication year, else ``None``."""
    if isinstance(value, bool):  # bool is an int subclass; never a year
        return None
    if isinstance(value, int):
        year = value
    elif isinstance(value, str):
        match = re.match(r"\s*(\d{4})", value)
        if match is None:
            return None
        year = int(match.group(1))
    else:
        return None
    return year if _MIN_YEAR <= year <= _MAX_YEAR else None


def _first_str(value: Any) -> str | None:
    """Return the first non-empty string in a CrossRef list-valued field.

    CrossRef models ``title`` and ``container-title`` as arrays that are
    routinely absent, empty, or full of blanks, so indexing ``[0]`` is a crash
    waiting to happen.
    """
    if not isinstance(value, list):
        return None
    for item in value:
        cleaned = _clean(item)
        if cleaned is not None:
            return cleaned
    return None


def _truncate_abstract(text: str | None) -> str | None:
    """Bound an abstract to the same length the PDF extractor enforces.

    Nothing upstream caps this: a 5 MB response (the fetch limit) could put a
    5 MB "abstract" in the row, which every library listing would then carry.
    """
    if text is None or len(text) <= _ABSTRACT_MAX_CHARS:
        return text
    cut = text.rfind(" ", 0, _ABSTRACT_MAX_CHARS)
    return text[: cut if cut > 0 else _ABSTRACT_MAX_CHARS].rstrip()


def _bounded(text: str | None, limit: int) -> str | None:
    """Drop a value that exceeds the length the PDF extractor would have allowed.

    Returning ``None`` rather than a truncation is what keeps a mangled registry
    record from beating the PDF's own guess in :func:`merge`.
    """
    if text is None or len(text) <= limit:
        return text
    return None


def _bounded_authors(names: tuple[str, ...]) -> tuple[str, ...]:
    """Apply the extractor's all-or-nothing stance on author lists.

    CrossRef carries collaboration records with thousands of authors, and a
    single malformed entry can be arbitrarily long. Either the whole list is
    within bounds or none of it is trustworthy.
    """
    if len(names) > _MAX_AUTHORS or any(len(name) > _AUTHOR_MAX_CHARS for name in names):
        return ()
    return names


def _strip_jats(markup: str) -> str | None:
    """Flatten CrossRef's JATS-XML abstract into plain text.

    Tags become spaces rather than being deleted so that ``a</jats:p><jats:p>b``
    does not collapse into ``ab``.
    """
    without_label = _LEADING_ABSTRACT_TITLE_RE.sub(r"\1 ", markup)
    text = _TAG_RE.sub(" ", without_label)
    return _clean(html.unescape(text))


def _normalise_doi(doi: str) -> str | None:
    """Reduce any common DOI spelling to the bare ``10.x/y`` form we store."""
    candidate = (doi or "").strip()
    for prefix in (
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
        "doi:",
    ):
        if candidate.lower().startswith(prefix):
            candidate = candidate[len(prefix) :]
            break
    candidate = candidate.strip()
    if not candidate.startswith("10.") or len(candidate) > _DOI_MAX_CHARS:
        return None
    return candidate


def _normalise_arxiv_id(arxiv_id: str) -> str | None:
    """Reduce any common arXiv id spelling to what ``id_list`` expects."""
    candidate = (arxiv_id or "").strip()
    for prefix in ("https://arxiv.org/abs/", "http://arxiv.org/abs/", "arxiv:"):
        if candidate.lower().startswith(prefix):
            candidate = candidate[len(prefix) :]
            break
    candidate = candidate.strip()
    # Both the modern (2503.01234v2) and legacy (math.GT/0309136) id shapes.
    if re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", candidate):
        return candidate
    # Matched case-insensitively and re-spelled, rather than requiring arXiv's
    # own capitalisation: this is an input normaliser, and rejecting
    # "math.gt/0309136" for its case turned a valid id into a failed lookup.
    legacy = re.fullmatch(r"([a-z-]+)(?:\.([a-z]{2}))?(/\d{7}(?:v\d+)?)", candidate, re.IGNORECASE)
    if legacy is not None:
        archive, subclass, tail = legacy.groups()
        stem = archive.lower() + (f".{subclass.upper()}" if subclass else "")
        return f"{stem}{tail}"
    return None


# --------------------------------------------------------------------------- #
# CrossRef
# --------------------------------------------------------------------------- #


def _crossref_authors(message: dict[str, Any]) -> tuple[str, ...]:
    """Build display names from CrossRef's author records, in listed order.

    Entries vary: most have ``given``+``family``, some have only ``family``, and
    corporate authors have only ``name``. Anything we cannot render is skipped
    rather than guessed at.
    """
    raw = message.get("author")
    if not isinstance(raw, list):
        return ()
    names: list[str] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        given = _clean(entry.get("given"))
        family = _clean(entry.get("family"))
        if given and family:
            name: str | None = f"{given} {family}"
        else:
            name = family or given or _clean(entry.get("name"))
        if name:
            names.append(name)
    return tuple(names)


def _crossref_venue(message: dict[str, Any]) -> str | None:
    """Prefer the journal/proceedings title, falling back to the event name.

    Only what the record actually states — we do not splice the year on to
    manufacture a "NeurIPS 2017" style string, because year is its own column
    and inventing composite values is worse than showing a plain venue.
    """
    container = _first_str(message.get("container-title"))
    if container:
        return container
    event = message.get("event")
    if isinstance(event, dict):
        return _clean(event.get("name"))
    return None


def _crossref_year(message: dict[str, Any]) -> int | None:
    """Read ``issued.date-parts[0][0]``, which is nullable at every level."""
    issued = message.get("issued")
    if not isinstance(issued, dict):
        return None
    date_parts = issued.get("date-parts")
    if not isinstance(date_parts, list) or not date_parts:
        return None
    first = date_parts[0]
    if not isinstance(first, list) or not first:
        return None
    return _year_from(first[0])


def enrich_by_doi(doi: str, *, timeout: float = 8.0) -> ExtractedMeta | None:
    """Look a DOI up in CrossRef.

    Returns ``None`` when the DOI is malformed, unresolvable, the network fails,
    or the record carries nothing worth having.
    """
    normalised = _normalise_doi(doi)
    if normalised is None:
        return None

    url = _CROSSREF_WORK_URL.format(doi=urllib.parse.quote(normalised, safe="/"))
    body = _fetch(url, timeout)
    if body is None:
        return None

    try:
        payload = json.loads(body)
        message = payload["message"]
        if not isinstance(message, dict):
            return None

        abstract_markup = message.get("abstract")
        abstract = _strip_jats(abstract_markup) if isinstance(abstract_markup, str) else None
        abstract = _truncate_abstract(abstract)
        # CrossRef spells this key in uppercase; tolerate both.
        returned_doi = _clean(message.get("DOI")) or _clean(message.get("doi"))

        meta = ExtractedMeta(
            title=_bounded(_first_str(message.get("title")), _TITLE_MAX_CHARS),
            authors=_bounded_authors(_crossref_authors(message)),
            year=_crossref_year(message),
            venue=_bounded(_crossref_venue(message), _VENUE_MAX_CHARS),
            doi=_normalise_doi(returned_doi or "") or normalised,
            abstract=abstract,
            arxiv_id=None,
        )
    except Exception:
        # Malformed JSON, an unexpected shape, a truncated body — all mean the
        # same thing here.
        return None

    # A record with neither a title nor authors tells us nothing; reporting it
    # as a success would let empty values win over the PDF's guesses in merge().
    if meta.title is None and not meta.authors:
        return None
    return meta


# --------------------------------------------------------------------------- #
# arXiv
# --------------------------------------------------------------------------- #


def _arxiv_text(entry: ET.Element, tag: str, ns: str = _ATOM_NS) -> str | None:
    element = entry.find(f"{{{ns}}}{tag}")
    return _clean(element.text) if element is not None else None


def enrich_by_arxiv(arxiv_id: str, *, timeout: float = 8.0) -> ExtractedMeta | None:
    """Look an arXiv id up in the arXiv Atom API.

    Returns ``None`` when the id is malformed, unknown to arXiv, or the network
    fails.
    """
    normalised = _normalise_arxiv_id(arxiv_id)
    if normalised is None:
        return None

    url = _ARXIV_QUERY_URL.format(id=urllib.parse.quote(normalised, safe=""))
    body = _fetch(url, timeout)
    if body is None:
        return None

    try:
        entry = ET.fromstring(body).find(f"{{{_ATOM_NS}}}entry")
        if entry is None:
            return None

        # arXiv answers an unknown id with HTTP 200 and a fake entry whose id
        # points at its error documentation, so a bad id looks like success.
        entry_id = _arxiv_text(entry, "id") or ""
        if "api/errors" in entry_id:
            return None

        title = _bounded(_arxiv_text(entry, "title"), _TITLE_MAX_CHARS)
        if title is None:
            return None

        authors: list[str] = []
        for author in entry.findall(f"{{{_ATOM_NS}}}author"):
            name = _arxiv_text(author, "name")
            if name:
                authors.append(name)

        # journal_ref is set once a preprint is formally published; until then
        # the preprint server itself is the only honest venue.
        journal_ref = _bounded(_arxiv_text(entry, "journal_ref", ns=_ARXIV_NS), _VENUE_MAX_CHARS)

        # Prefer arXiv's own canonical id (it resolves versionless ids to the
        # latest version), stripping the version suffix to match how we store it.
        canonical = _normalise_arxiv_id(entry_id) or normalised

        meta = ExtractedMeta(
            title=title,
            authors=_bounded_authors(tuple(authors)),
            year=_year_from(_arxiv_text(entry, "published")),
            venue=journal_ref or "arXiv",
            doi=_normalise_doi(_arxiv_text(entry, "doi", ns=_ARXIV_NS) or ""),
            abstract=_truncate_abstract(_arxiv_text(entry, "summary")),
            arxiv_id=re.sub(r"v\d+$", "", canonical),
        )
    except Exception:
        # Not XML, unexpected structure, truncated body.
        return None

    return meta


# --------------------------------------------------------------------------- #
# merging
# --------------------------------------------------------------------------- #


def _has_value(value: Any) -> bool:
    """Whether a field carries usable data (blank strings/tuples do not)."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, tuple):
        return bool(value)
    return True


def merge(base: ExtractedMeta, better: ExtractedMeta | None) -> ExtractedMeta:
    """Overlay ``better`` onto ``base``, field by field, keeping filled values.

    A registry beats regexes on title, authors, venue and year — but the overlay
    is per-field rather than wholesale because coverage is complementary in both
    directions: CrossRef frequently omits the abstract that the PDF's first page
    hands us, and the PDF frequently lacks the venue CrossRef knows.

    Returns a new instance; the inputs are frozen and are never mutated.
    """
    if better is None:
        return base
    overrides = {
        field.name: getattr(better, field.name)
        for field in dataclasses.fields(base)
        if _has_value(getattr(better, field.name))
    }
    if not overrides:
        return base
    return dataclasses.replace(base, **overrides)
