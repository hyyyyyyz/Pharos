"""Zotero Web API v3 client — credential verification and metadata retrieval.

This module is deliberately *thin* and knows nothing about Pharos' database. It
turns a (zotero_user_id, api_key) pair into either a rejection or a list of
normalised bibliographic records; deciding what to do with those records is
:mod:`pharos.api.zotero`'s job.

Four rules shape everything below.

**The API key is a bearer secret for someone's real Zotero account.** It travels
only in the ``Zotero-API-Key`` request header, never in a URL: query strings end
up in proxy logs, browser history, and ``Referer`` headers, and a leaked Zotero
key gives read access to a researcher's entire library. It is also never logged
and never placed in an exception message — :func:`scrub` exists so callers can
strip it defensively from upstream text before that text is persisted or
returned.

**Zotero's rate limits are the user's own quota.** The server sends ``Backoff``
to ask for a pause and ``Retry-After`` with a 429/503 to demand one; both are
obeyed. Hammering the API on a user's behalf gets *their* key throttled, which is
a cost we impose on them, not on ourselves.

**Untrusted input is validated before it reaches a URL or a header.** The user id
is interpolated into a request path, so it must be digits and nothing else — a
value like ``../../keys/current`` would otherwise silently retarget the request.
The key goes into a header, where a newline would be header injection. Both are
checked here as well as at the API boundary, because this module is the last
place that can still refuse.

**Missing fields are the norm.** Zotero items are user-maintained and routinely
have no DOI, no venue, no date and no abstract. Every mapped field is optional
and nothing is ever invented to fill a gap.

Uses :mod:`urllib.request` rather than ``httpx``, matching
:mod:`pharos.services.enrich` and :mod:`pharos.daily.service`: httpx is only a
transitive FastAPI dependency, not a declared one.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import Message
from typing import Any

log = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_TIMEOUT",
    "ZoteroCredentialsError",
    "ZoteroError",
    "ZoteroIdentity",
    "ZoteroItem",
    "ZoteroUnavailable",
    "fetch_items",
    "scrub",
    "valid_api_key",
    "valid_user_id",
    "verify",
]

_API_ROOT = "https://api.zotero.org"
_API_HOST = "api.zotero.org"
_API_VERSION = "3"

#: Zotero asks API clients to identify themselves so a misbehaving one can be
#: contacted rather than blanket-blocked.
_USER_AGENT = "Pharos/0.1 (https://github.com/hyyyyyyz/Pharos)"

#: Per-request socket timeout. Generous enough for a cold Zotero response,
#: short enough that a wedged connection does not pin a worker thread for long.
DEFAULT_TIMEOUT = 20.0

#: Zotero caps ``limit`` at 100 regardless of what we ask for.
_PAGE_LIMIT = 100

#: Pagination guards. A malformed ``Link`` header or a server that keeps handing
#: back the same page would otherwise spin forever against someone else's API.
_MAX_PAGES = 1000
_MAX_ITEMS = 50_000

#: A single page of 100 items is a few hundred KB; anything at this scale is not
#: a response we understand, so refuse to buffer it.
_MAX_RESPONSE_BYTES = 32 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024

#: Ceiling on any server-requested pause, so a hostile or buggy ``Retry-After``
#: cannot park a sync thread for an hour.
_MAX_BACKOFF_SECONDS = 60.0
#: How many times one request is retried after a 429/503 before giving up.
_MAX_RETRIES = 3

#: The library version can change *while* we page through it, which would make
#: the collected set neither the old library nor the new one. Zotero's documented
#: remedy is to start over; this bounds how many times we will.
_MAX_RESTARTS = 2

#: A Zotero numeric user id. Interpolated into a request path — see the module
#: docstring on why this is not merely cosmetic validation.
_USER_ID_RE = re.compile(r"^[0-9]{1,20}$")
#: Zotero issues 24-character alphanumeric keys. The range is loose so a future
#: format change does not lock users out, but the charset is strict: it is what
#: guarantees the value cannot inject a header.
_API_KEY_RE = re.compile(r"^[A-Za-z0-9]{8,128}$")

#: Plausible publication years, matching :mod:`pharos.services.enrich`.
_MIN_YEAR = 1500
_MAX_YEAR = 2100
_YEAR_RE = re.compile(r"(?<!\d)(\d{4})(?!\d)")

#: ``Link: <url>; rel="next"`` — Zotero's pagination cursor.
_LINK_NEXT_RE = re.compile(r'<([^>]+)>\s*;\s*[^,]*rel\s*=\s*"?next"?', re.IGNORECASE)

#: Item types that are never a paper. Belt and braces: the request already asks
#: Zotero to exclude them, but a server-side query-syntax change must not turn
#: every PDF attachment in a library into a library entry.
_SKIP_TYPES = frozenset({"attachment", "note", "annotation"})

#: Hard ceilings applied to anything on its way into a ``String(n)`` column.
#: Zotero item data is user-authored third-party text and is bounded by nothing;
#: SQLite does not enforce ``VARCHAR`` width, so an over-long value would be
#: written silently now and only become an error on a future database.
MAX_TITLE = 512
MAX_VENUE = 256
MAX_DOI = 128
MAX_AUTHORS_JOINED = 8000
MAX_ABSTRACT = 20000
MAX_URL = 512

_DOI_PREFIX_RE = re.compile(r"^\s*(?:https?://(?:dx\.)?doi\.org/|doi:)\s*", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# errors
# --------------------------------------------------------------------------- #


class ZoteroError(RuntimeError):
    """Base class for every failure this module reports."""


class ZoteroCredentialsError(ZoteroError):
    """The API key was rejected, or does not grant the access we need.

    Distinct from :class:`ZoteroUnavailable` because the remedy is different:
    this one is the user's to fix (issue a new key, grant library read), while
    an outage is nobody's and should be retried.
    """


class ZoteroUnavailable(ZoteroError):
    """Zotero could not be reached, or answered in a way we cannot use."""


# --------------------------------------------------------------------------- #
# secret handling
# --------------------------------------------------------------------------- #


def scrub(text: str | None, *secrets: str | None) -> str | None:
    """Replace every occurrence of ``secrets`` in ``text`` with ``***``.

    Defence in depth, not the primary control. We never *put* the key in a URL
    or a message — but upstream error text is not ours to reason about (Zotero
    has echoed request context in error bodies before, and a future proxy in the
    path might), and this text goes on to be persisted in ``ZoteroLink.last_error``
    and shown to the user. Anything cheap that stands between a bearer secret and
    a durable column is worth doing.

    Short secrets are ignored: substituting a 3-character string would mangle
    unrelated text without protecting anything real.
    """
    if not text:
        return text
    cleaned = text
    for secret in secrets:
        if not secret or len(secret) < 8:
            continue
        for variant in (secret, urllib.parse.quote(secret, safe="")):
            if variant and variant in cleaned:
                cleaned = cleaned.replace(variant, "***")
    return cleaned


def valid_user_id(value: str) -> bool:
    """Whether ``value`` is safe to interpolate into a Zotero request path."""
    return bool(_USER_ID_RE.match(value or ""))


def valid_api_key(value: str) -> bool:
    """Whether ``value`` is safe to place in a request header."""
    return bool(_API_KEY_RE.match(value or ""))


def _require_credentials(zotero_user_id: str, api_key: str) -> None:
    """Reject malformed credentials before they can reach a URL or a header.

    The messages deliberately describe the *shape* that was wrong and never
    include the value: an error string is one of the places a key most easily
    leaks into a log.
    """
    if not valid_user_id(zotero_user_id):
        raise ZoteroCredentialsError("The Zotero user ID must be the numeric ID from your account.")
    if not valid_api_key(api_key):
        raise ZoteroCredentialsError(
            "The Zotero API key looks malformed; it should be the alphanumeric key "
            "shown when you created it."
        )


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Response:
    """One HTTP response, normalised across urllib's success/error split."""

    status: int
    headers: Message
    body: bytes

    def header_int(self, name: str) -> int | None:
        raw = self.headers.get(name)
        if raw is None:
            return None
        try:
            return int(str(raw).strip())
        except ValueError:
            return None

    def header_float(self, name: str) -> float | None:
        raw = self.headers.get(name)
        if raw is None:
            return None
        try:
            return float(str(raw).strip())
        except ValueError:
            # ``Retry-After`` may also be an HTTP-date. We do not parse that
            # form: treating an unreadable value as "no hint" and falling back
            # to our own pause is safer than guessing a duration.
            return None

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ZoteroUnavailable("Zotero returned a response that was not valid JSON.") from exc


def _read_body(response: Any) -> bytes:
    """Read a response body, refusing to buffer more than the size ceiling."""
    read = getattr(response, "read1", response.read)
    chunks: list[bytes] = []
    total = 0
    while total < _MAX_RESPONSE_BYTES:
        chunk = read(min(_READ_CHUNK_BYTES, _MAX_RESPONSE_BYTES - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _sleep_for(seconds: float | None, *, default: float = 1.0) -> None:
    """Pause for a server-requested interval, clamped to something sane."""
    delay = default if seconds is None or seconds <= 0 else seconds
    time.sleep(min(delay, _MAX_BACKOFF_SECONDS))


def _get(
    url: str,
    api_key: str,
    *,
    extra_headers: dict[str, str] | None = None,
    timeout: float,
) -> _Response:
    """One authenticated GET, with 429/503 retries. Never logs the key or body.

    Retries only the statuses Zotero uses to say "later" (429, 503). A 4xx that
    is not 429 is a decision, not a hiccup, and retrying it would just spend the
    user's quota confirming the same answer.
    """
    headers = {
        "Zotero-API-Version": _API_VERSION,
        # The key rides in a header, never the query string. See the module
        # docstring — this is the single most important line in the file.
        "Zotero-API-Key": api_key,
        "User-Agent": _USER_AGENT,
        "Accept": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    last_error: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as raw:  # noqa: S310
                response = _Response(
                    status=getattr(raw, "status", 200), headers=raw.headers, body=_read_body(raw)
                )
        except urllib.error.HTTPError as exc:
            # urllib routes every non-2xx here, 304 included — which is a normal
            # and *expected* answer for an incremental sync, not a failure.
            response = _Response(status=exc.code, headers=exc.headers, body=_read_body(exc))
            exc.close()
        except Exception as exc:  # URLError, socket timeout, TLS, malformed URL
            # str(exc) can quote the URL, which is why the key is never in it.
            last_error = exc
            if attempt >= _MAX_RETRIES:
                raise ZoteroUnavailable(f"Could not reach Zotero: {exc}") from exc
            _sleep_for(None, default=2.0 * (attempt + 1))
            continue

        if response.status in (429, 503) and attempt < _MAX_RETRIES:
            # The user's own quota is what is being throttled here; waiting the
            # requested interval is the whole point of the header.
            wait = response.header_float("Retry-After") or response.header_float("Backoff")
            log.info("zotero: throttled (HTTP %s), pausing before retry", response.status)
            _sleep_for(wait, default=2.0 * (attempt + 1))
            continue
        return response

    raise ZoteroUnavailable(  # pragma: no cover — the loop always returns or raises
        f"Could not reach Zotero: {last_error}"
    )


def _raise_for_status(response: _Response) -> None:
    """Translate an unusable status into the right exception class.

    The upstream body is never surfaced: it is attacker-influencable in the
    general case and, more prosaically, useless to the user. Only our own
    sentence and the status code travel outward.
    """
    if 200 <= response.status < 300 or response.status == 304:
        return
    if response.status in (401, 403):
        raise ZoteroCredentialsError(
            "Zotero rejected these credentials. Check the user ID and that the API "
            "key still exists and grants read access to your library."
        )
    if response.status == 404:
        raise ZoteroCredentialsError(
            "Zotero has no library for that user ID. Check the numeric user ID in "
            "your Zotero settings."
        )
    if response.status == 429:
        raise ZoteroUnavailable(
            "Zotero is rate-limiting this API key. Wait a few minutes and try again."
        )
    raise ZoteroUnavailable(f"Zotero returned an unexpected response (HTTP {response.status}).")


# --------------------------------------------------------------------------- #
# verify
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ZoteroIdentity:
    """What an API key turns out to be, and what it is allowed to do.

    Note what is *absent*: ``GET /keys/current`` echoes the API key back to you
    in its ``key`` field, and that value is deliberately never captured here. A
    secret that is not in the object cannot be logged by a stray ``repr()``.
    """

    #: The numeric user id the key actually belongs to, per Zotero.
    user_id: str
    username: str | None
    #: Whether the key may read the user's personal library — the one permission
    #: Pharos actually needs.
    library_read: bool
    #: Whether the key may read file attachments. Not used by this prototype
    #: (we sync metadata only), but reported so the UI can be honest about it.
    files_read: bool
    #: Whether the id the user typed matches the key's real owner. A mismatch is
    #: usually a typo, but it is also what a pasted-from-a-colleague key looks
    #: like, so the caller is told rather than silently corrected.
    matches_claim: bool


def verify(
    zotero_user_id: str, api_key: str, *, timeout: float = DEFAULT_TIMEOUT
) -> ZoteroIdentity | None:
    """Confirm credentials work and discover what they grant. ``None`` if rejected.

    Called *before* the credentials are stored. Persisting an unverified key
    would leave the user with a link that looks healthy in the UI and fails on
    every sync, with nothing to point at.

    Returns:
        The identity behind the key, or ``None`` when Zotero rejects it or the
        credentials are malformed. A rejection is a normal answer here.

    Raises:
        ZoteroUnavailable: Zotero could not be reached. Deliberately *not*
            folded into ``None``: "your key is wrong" and "we could not check"
            call for completely different responses, and conflating them would
            tell users to regenerate a perfectly good key during an outage.
    """
    try:
        _require_credentials(zotero_user_id, api_key)
    except ZoteroCredentialsError:
        return None

    try:
        response = _get(f"{_API_ROOT}/keys/current", api_key, timeout=timeout)
        _raise_for_status(response)
    except ZoteroCredentialsError:
        return None

    payload = response.json()
    if not isinstance(payload, dict):
        raise ZoteroUnavailable("Zotero returned an unexpected key description.")

    owner = payload.get("userID")
    if owner is None:
        raise ZoteroUnavailable("Zotero did not say which account this key belongs to.")
    owner_id = str(owner).strip()

    access = payload.get("access")
    user_access = access.get("user") if isinstance(access, dict) else None
    if not isinstance(user_access, dict):
        user_access = {}

    username = payload.get("username")
    return ZoteroIdentity(
        user_id=owner_id,
        username=str(username).strip() or None if isinstance(username, str) else None,
        library_read=bool(user_access.get("library")),
        files_read=bool(user_access.get("files")),
        matches_claim=owner_id == zotero_user_id.strip(),
    )


# --------------------------------------------------------------------------- #
# items
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ZoteroItem:
    """One Zotero library entry, normalised to the fields Pharos stores.

    Every bibliographic field is optional because in a real library every one of
    them is routinely blank. ``title`` is the exception the caller must handle:
    ``None`` means the item has no name at all, and a paper with no title cannot
    be rendered, so such items are dropped rather than given an invented one.
    """

    key: str
    version: int
    item_type: str
    title: str | None
    #: Author display names, already joined-ready. Institutional creators
    #: (``{"name": "WHO"}``) appear as a single name, personal ones as
    #: ``"First Last"``.
    creators: tuple[str, ...]
    year: int | None
    venue: str | None
    doi: str | None
    abstract: str | None
    url: str | None
    #: Whether Zotero reports a PDF attachment. Pharos does **not** download it
    #: in this prototype — this flag is metadata about the Zotero library, not a
    #: claim that Pharos holds the file.
    has_pdf: bool


def _text(value: Any, limit: int) -> str | None:
    """Collapse whitespace and clamp; blank becomes ``None``, never ``""``."""
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    if not cleaned:
        return None
    if len(cleaned) <= limit:
        return cleaned
    cut = cleaned.rfind(" ", 0, limit)
    return cleaned[: cut if cut > limit // 2 else limit].rstrip()


def _paragraphs(value: Any, limit: int) -> str | None:
    """Like :func:`_text` but keeps line breaks — abstracts are multi-paragraph."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) <= limit:
        return cleaned
    cut = cleaned.rfind(" ", 0, limit)
    return cleaned[: cut if cut > 0 else limit].rstrip()


def _creator_names(raw: Any) -> tuple[str, ...]:
    """Flatten Zotero's creator array into display names.

    Zotero stores a creator in one of two shapes and both are common:
    ``{"firstName": "Ada", "lastName": "Lovelace"}`` for a person, and
    ``{"name": "World Health Organization"}`` for an institution (Zotero calls
    this "single field" mode, and users toggle it per creator).

    Authors are preferred over other creator types, but a work whose only
    credited people are editors or translators falls back to those rather than
    reporting no authors at all — an edited volume with "—" where its editors
    should be is less useful than one that names them.

    Semicolons are stripped because the caller joins on ``"; "``; leaving one in
    a name would silently split it into two people on the way back out.
    """
    if not isinstance(raw, list):
        return ()
    authors: list[str] = []
    others: list[str] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        single = entry.get("name")
        if isinstance(single, str) and single.strip():
            name = " ".join(single.replace(";", " ").split())
        else:
            first = entry.get("firstName")
            last = entry.get("lastName")
            parts = [
                " ".join(str(p).replace(";", " ").split())
                for p in (first, last)
                if isinstance(p, str) and p.strip()
            ]
            name = " ".join(p for p in parts if p)
        if not name:
            continue
        if entry.get("creatorType") == "author":
            authors.append(name)
        else:
            others.append(name)
    return tuple(authors or others)


def _year_from(value: Any) -> int | None:
    """Pull a plausible year out of Zotero's free-text ``date`` field.

    ``date`` is whatever the user or an import filter typed: ``2017-06-12``,
    ``June 2017``, ``2017``, ``in press``, ``n.d.``. The first four-digit number
    in a sensible range is the only reliable signal, and anything outside that
    range is data-entry noise that is better dropped than stored.
    """
    if not isinstance(value, str):
        return None
    for match in _YEAR_RE.finditer(value):
        year = int(match.group(1))
        if _MIN_YEAR <= year <= _MAX_YEAR:
            return year
    return None


def _doi_from(data: dict[str, Any]) -> str | None:
    """The bare ``10.x/y`` identifier, however the user spelled it.

    Zotero only has a real ``DOI`` field on some item types; for the rest people
    put it in ``extra`` as ``DOI: 10.…``, which is common enough to be worth
    reading.
    """
    raw = data.get("DOI")
    if not isinstance(raw, str) or not raw.strip():
        extra = data.get("extra")
        if isinstance(extra, str):
            match = re.search(r"^\s*DOI\s*:\s*(\S+)\s*$", extra, re.IGNORECASE | re.MULTILINE)
            raw = match.group(1) if match else None
    if not isinstance(raw, str):
        return None
    return _text(_DOI_PREFIX_RE.sub("", raw), MAX_DOI)


def _has_pdf(item: dict[str, Any]) -> bool:
    """Whether Zotero reports a PDF attachment on this item.

    Zotero exposes the best attachment under ``links.attachment`` and names its
    content type as ``attachmentType`` (older responses used
    ``attachmentContentType``); both spellings are checked.
    """
    links = item.get("links")
    attachment = links.get("attachment") if isinstance(links, dict) else None
    if not isinstance(attachment, dict):
        return False
    for field in ("attachmentType", "attachmentContentType"):
        value = attachment.get(field)
        if isinstance(value, str) and "pdf" in value.lower():
            return True
    return False


def _map_item(item: Any) -> ZoteroItem | None:
    """Turn one raw Zotero item into a :class:`ZoteroItem`, or ``None`` to skip.

    Skipping is the right answer for attachments, notes, and anything whose
    shape we do not recognise: a sync that drops an unmappable row is a smaller
    problem than one that aborts a 4000-item library over a single malformed
    entry.
    """
    if not isinstance(item, dict):
        return None
    data = item.get("data")
    if not isinstance(data, dict):
        return None

    key = data.get("key") or item.get("key")
    if not isinstance(key, str) or not key.strip():
        return None
    item_type = data.get("itemType")
    if not isinstance(item_type, str) or item_type in _SKIP_TYPES:
        return None

    version = data.get("version") or item.get("version") or 0
    venue = (
        _text(data.get("publicationTitle"), MAX_VENUE)
        or _text(data.get("proceedingsTitle"), MAX_VENUE)
        or _text(data.get("bookTitle"), MAX_VENUE)
    )
    return ZoteroItem(
        key=key.strip(),
        version=int(version) if isinstance(version, int) else 0,
        item_type=item_type,
        # shortTitle is a genuine alternative name the user chose, not a guess.
        title=_text(data.get("title"), MAX_TITLE) or _text(data.get("shortTitle"), MAX_TITLE),
        creators=_creator_names(data.get("creators")),
        year=_year_from(data.get("date")),
        venue=venue,
        doi=_doi_from(data),
        abstract=_paragraphs(data.get("abstractNote"), MAX_ABSTRACT),
        url=_text(data.get("url"), MAX_URL),
        has_pdf=_has_pdf(item),
    )


def _items_url(zotero_user_id: str, *, start: int) -> str:
    """Build one page's URL. Contains no secret — the key is a header."""
    query = urllib.parse.urlencode(
        {
            "format": "json",
            # Zotero accepts one negated item type, but rejects a negated union
            # such as ``-attachment||note`` with HTTP 400. Attachments dominate
            # many libraries, so exclude those server-side; ``_map_item`` still
            # drops notes and annotations defensively after the response arrives.
            "itemType": "-attachment",
            "limit": _PAGE_LIMIT,
            "start": start,
        }
    )
    return f"{_API_ROOT}/users/{zotero_user_id}/items?{query}"


def _next_start(link_header: str | None, current_start: int) -> int | None:
    """The ``start`` offset of ``rel="next"``, or ``None`` when the page is last.

    Only the *offset* is taken from the header, never the URL itself. A ``Link``
    header is server-controlled data, and handing an arbitrary URL from it
    straight to ``urlopen`` — with the user's API key attached — is how a
    compromised or spoofed upstream turns pagination into credential
    exfiltration. Rebuilding our own URL from a parsed integer makes that
    impossible, and the host check below refuses a redirect elsewhere outright.

    A ``next`` that does not advance is treated as the end: it is a server bug
    or a parse failure, and following it is an infinite loop.
    """
    if not link_header:
        return None
    match = _LINK_NEXT_RE.search(link_header)
    if match is None:
        return None
    parsed = urllib.parse.urlsplit(match.group(1).strip())
    if parsed.scheme not in ("", "https") or (parsed.hostname or _API_HOST).lower() != _API_HOST:
        log.warning("zotero: ignoring a next-page link pointing away from %s", _API_HOST)
        return None
    values = urllib.parse.parse_qs(parsed.query).get("start")
    if not values:
        return None
    try:
        start = int(values[0])
    except ValueError:
        return None
    return start if start > current_start else None


def fetch_items(
    zotero_user_id: str,
    api_key: str,
    *,
    since: int = 0,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[list[ZoteroItem], int]:
    """Fetch a user's library items. Returns ``(items, library_version)``.

    Args:
        zotero_user_id: The numeric Zotero user id. Validated, because it is
            interpolated into the request path.
        api_key: The user's Zotero API key. Sent as a header, never in the URL.
        since: The library version the caller already has. Sent as
            ``If-Modified-Since-Version``; a 304 means nothing has changed and
            comes back as ``([], since)`` — an empty list with an *unchanged*
            version, which is different from "the library is empty".
        timeout: Per-request socket timeout.

    Returns:
        The mapped items and the library version they describe. Store the
        version only after the items are safely persisted: recording a version
        for items you failed to write means the next incremental sync will skip
        them forever.

    Raises:
        ZoteroCredentialsError: The key was rejected or the id is wrong.
        ZoteroUnavailable: Zotero was unreachable, throttled past our retries,
            or answered with something unusable.
    """
    _require_credentials(zotero_user_id, api_key)

    for restart in range(_MAX_RESTARTS + 1):
        outcome = _fetch_pages(zotero_user_id, api_key, since=since, timeout=timeout)
        if outcome is not None:
            return outcome
        # The library changed underneath us; the collected pages are a mix of
        # two states. Start over rather than persist an incoherent snapshot.
        log.info("zotero: library changed mid-sync, restarting (attempt %d)", restart + 2)
    raise ZoteroUnavailable(
        "The Zotero library kept changing while it was being read. Try again in a moment."
    )


def _fetch_pages(
    zotero_user_id: str,
    api_key: str,
    *,
    since: int,
    timeout: float,
) -> tuple[list[ZoteroItem], int] | None:
    """One complete pagination pass. ``None`` if the library moved under us."""
    extra: dict[str, str] = {}
    if since > 0:
        extra["If-Modified-Since-Version"] = str(since)

    items: list[ZoteroItem] = []
    version: int | None = None
    start = 0

    for page in range(_MAX_PAGES):
        response = _get(
            _items_url(zotero_user_id, start=start), api_key, extra_headers=extra, timeout=timeout
        )
        if response.status == 304:
            # Nothing has changed since ``since``. Not an error, and not an
            # empty library — the caller must not overwrite anything on this.
            return [], since
        _raise_for_status(response)

        page_version = response.header_int("Last-Modified-Version")
        if version is None:
            version = page_version
        elif page_version is not None and page_version != version:
            return None

        payload = response.json()
        if not isinstance(payload, list):
            raise ZoteroUnavailable("Zotero returned an unexpected items payload.")
        for raw in payload:
            mapped = _map_item(raw)
            if mapped is not None:
                items.append(mapped)
        if len(items) >= _MAX_ITEMS:
            log.warning("zotero: stopping at %d items; library is larger", len(items))
            break

        next_start = _next_start(response.headers.get("Link"), start)
        if next_start is None:
            break

        # ``Backoff`` is an advisory "ease off" that arrives on a *successful*
        # response. Honouring it between pages is what keeps a large library's
        # sync from turning into the 429 the header exists to prevent.
        _pause = response.header_float("Backoff")
        if _pause:
            log.info("zotero: honouring a %.1fs backoff between pages", _pause)
            _sleep_for(_pause)
        start = next_start
        if page == _MAX_PAGES - 1:  # pragma: no cover — 100k items
            log.warning("zotero: stopping after %d pages", _MAX_PAGES)

    # A library that has never been modified reports no version; 0 is the
    # correct "I know nothing" value and keeps the next sync a full one.
    return items, version if version is not None else since
