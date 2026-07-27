"""Zotero API — link a Zotero account and pull its library metadata in.

A Pharos account is *not* a Zotero account. Signing in to Pharos gets you a
library; linking Zotero is an optional, revocable extra that pulls bibliographic
records out of Zotero's cloud and into that library. Every route except the
one-time OAuth callback requires a signed-in user and touches only that user's
own rows — an unfiltered query in this file would show one researcher another's
reading list.

Three things are worth reading the code for.

**The API key is write-only.** ``POST /link`` takes it, and nothing ever gives it
back. ``GET /status`` reports a boolean ``linked`` and not a masked prefix: the
first characters of a bearer secret are still bytes an attacker did not have.
Upstream error text is scrubbed with :func:`~pharos.services.zotero.scrub` before
it reaches ``ZoteroLink.last_error``, which is a column the UI renders.

**A sync runs in the background.** Paging a few thousand items with Zotero's
backoff honoured takes minutes, which is far too long to hold an HTTP request
open. This follows the pattern already established by ``POST /api/daily/refresh``
and the translation jobs: the durable row is updated *synchronously* so there is
immediately something to poll, the work happens in a task, and the client watches
``GET /api/zotero/status``.

**Synced papers have no PDF, and say so.** This prototype pulls metadata only.
See :func:`_upsert_papers` for what that means for ``orig_sha256`` and why the
honest representation matters.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import urllib.parse
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from pharos.api.deps import current_user, get_session
from pharos.api.deps import get_settings as get_runtime_settings
from pharos.api.schemas import as_utc
from pharos.config import Settings
from pharos.config import get_settings as get_app_settings
from pharos.db.models import Paper, User, ZoteroLink, ZoteroOAuthAttempt
from pharos.db.session import session_scope
from pharos.services import zotero as client
from pharos.services import zotero_oauth
from pharos.services.credentials import CredentialCipher, CredentialError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/zotero", tags=["zotero"])

#: ``ZoteroLink.last_error`` is rendered in the UI, and an upstream message can
#: be arbitrarily long.
_MAX_ERROR_CHARS = 500

#: Rows written per transaction during an upsert. A 4000-item library in one
#: transaction would hold SQLite's single write lock for the whole insert and
#: stall every other request; the batch keeps each lock acquisition short, in
#: keeping with the "sessions stay short" rule in :mod:`pharos.daily.service`.
_UPSERT_BATCH = 200

#: How many finished-sync summaries are remembered. The durable facts (item
#: count, library version, when, and any error) live in ``ZoteroLink``; the
#: added/updated split is run bookkeeping that is genuinely ephemeral, so it is
#: kept in memory and bounded rather than given a column. It is ``null`` after a
#: restart, which is the truth: this process did not run that sync.
_MAX_REMEMBERED_RUNS = 512

#: Zotero user ids are numeric; keys are alphanumeric. Both are validated again
#: in the service layer — this bound just keeps absurd payloads out of argon2-
#: adjacent code paths and out of the columns.
_MAX_USER_ID = 32
_MAX_API_KEY = 128

#: Only fixed result codes cross the unauthenticated OAuth callback into the
#: frontend URL. Provider text never does: it can be attacker-controlled and a
#: query string is copied into history, analytics, and referrer headers.
_OAUTH_RESULTS = {"connected", "cancelled", "expired", "invalid", "busy", "error"}
_DESKTOP_HANDOFF_TTL_SECONDS = 5 * 60


# --------------------------------------------------------------------------- #
# schemas
# --------------------------------------------------------------------------- #


class LinkRequest(BaseModel):
    """Zotero credentials. The key is accepted here and never echoed anywhere."""

    model_config = ConfigDict(extra="forbid")

    zotero_user_id: Annotated[str, Field(min_length=1, max_length=_MAX_USER_ID)]
    api_key: Annotated[str, Field(min_length=1, max_length=_MAX_API_KEY)]


class SyncSummary(BaseModel):
    """The outcome of one sync run.

    Counts are ``null`` while a sync is in flight — an in-progress run has no
    total, and reporting a partial one as final would be a lie the client cannot
    detect.
    """

    running: bool
    started_at: datetime | None = None
    finished_at: datetime | None = None
    added: int | None = None
    updated: int | None = None
    #: Items received from Zotero and understood, i.e. ``added + updated``.
    total: int | None = None
    #: Items Zotero returned that could not become a paper — almost always an
    #: entry with no title at all. Surfaced rather than swallowed so a user whose
    #: numbers do not add up can see why.
    skipped: int | None = None
    library_version: int | None = None
    error: str | None = None


class ZoteroStatusOut(BaseModel):
    """Everything the UI needs to render the Zotero panel.

    Deliberately absent: ``api_key``, in any form. Not the value, not a prefix,
    not a masked stand-in whose length leaks. ``linked`` is the only thing a
    client is told about the stored secret.
    """

    linked: bool
    zotero_user_id: str | None = None
    #: "linked" | "syncing" | "error"; ``null`` when nothing is linked.
    status: str | None = None
    last_sync_at: datetime | None = None
    item_count: int = 0
    last_error: str | None = None
    #: Zotero's library version we last stored, i.e. the point the next
    #: incremental sync resumes from.
    library_version: int | None = None
    #: The in-flight or most recent run, when this process knows of one.
    sync: SyncSummary | None = None
    #: Whether this server has a registered Zotero OAuth application and a
    #: stable credential-encryption secret. False keeps the manual fallback
    #: usable without rendering a dead one-click button.
    oauth_available: bool = False


class OAuthStartOut(BaseModel):
    """The Zotero consent URL created for this signed-in user."""

    authorize_url: str
    expires_at: datetime


class DesktopOAuthStartOut(OAuthStartOut):
    """Desktop consent URL plus an app-held, short-lived binding secret."""

    desktop_secret: str


class DesktopOAuthFinishRequest(BaseModel):
    """Consume the one-use code delivered through ``pharos://``."""

    model_config = ConfigDict(extra="forbid")

    code: Annotated[str, Field(min_length=32, max_length=256)]
    desktop_secret: Annotated[str, Field(min_length=32, max_length=256)]


class UnlinkResult(BaseModel):
    """``unlinked`` is false when there was nothing to unlink."""

    unlinked: bool
    #: Papers already pulled in are *kept*: they are the user's library now, not
    #: Zotero's. Reported so the client can say so instead of implying a purge.
    papers_kept: int


# --------------------------------------------------------------------------- #
# the syncer
# --------------------------------------------------------------------------- #


class _Run:
    """Mutable bookkeeping for one sync, owned by :class:`ZoteroSyncer`."""

    __slots__ = (
        "added",
        "error",
        "finished_at",
        "library_version",
        "skipped",
        "started_at",
        "total",
        "updated",
    )

    def __init__(self) -> None:
        self.started_at = datetime.now(UTC)
        self.finished_at: datetime | None = None
        self.added: int | None = None
        self.updated: int | None = None
        self.total: int | None = None
        self.skipped: int | None = None
        self.library_version: int | None = None
        self.error: str | None = None

    def summary(self, *, running: bool) -> SyncSummary:
        return SyncSummary(
            running=running,
            started_at=self.started_at,
            finished_at=self.finished_at,
            added=self.added,
            updated=self.updated,
            total=self.total,
            skipped=self.skipped,
            library_version=self.library_version,
            error=self.error,
        )


class ZoteroSyncer:
    """Owns in-flight syncs, at most one per user.

    Mirrors :class:`~pharos.daily.service.DailySweeper`, with one difference that
    follows from being multi-user: the sweeper has a single global slot, whereas
    here one user's sync must not block another's, so the slot is per user.

    Two users syncing at once is fine — they hit different Zotero accounts. The
    *same* user syncing twice is not: both passes would upsert the same rows and
    the second would race the first's library-version write, which is how an
    incremental sync ends up skipping items forever.

    Holds no asyncio primitive at construction, so unlike the sweeper it can be a
    module-level singleton rather than something the app lifespan must build. The
    claim in :meth:`submit` happens with no ``await`` in between, and the event
    loop cannot switch tasks in a straight-line block, so that assignment *is*
    the mutual exclusion.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._runs: OrderedDict[str, _Run] = OrderedDict()
        #: The loop tasks were created on, captured in :meth:`submit`. Needed so
        #: :meth:`cancel` can be called from a worker thread — see there.
        self._loop: asyncio.AbstractEventLoop | None = None

    def is_running(self, user_id: str) -> bool:
        task = self._tasks.get(user_id)
        return task is not None and not task.done()

    def summary(self, user_id: str) -> SyncSummary | None:
        run = self._runs.get(user_id)
        if run is None:
            return None
        return run.summary(running=self.is_running(user_id))

    def cancel(self, user_id: str) -> None:
        """Stop this user's sync, if one is running. Safe to call when none is.

        Callable from *any* thread, which is the reason for the indirection:
        ``DELETE /link`` is a plain ``def`` endpoint and therefore runs in
        FastAPI's threadpool, and asyncio objects are not thread-safe.
        ``Task.cancel()`` from off-loop races the loop's own scheduling;
        ``call_soon_threadsafe`` is the supported way to reach in from outside.
        It is also correct when called *from* the loop — cancellation is simply
        deferred by one iteration.
        """
        task = self._tasks.get(user_id)
        if task is None or task.done():
            return
        loop = self._loop
        if loop is None or loop.is_closed():  # pragma: no cover — no task can exist yet
            return
        loop.call_soon_threadsafe(task.cancel)

    def submit(self, user_id: str) -> SyncSummary | None:
        """Start a sync for ``user_id``. ``None`` if one is already running.

        Must be called from the event loop — :func:`asyncio.create_task` needs a
        running loop, which a ``def`` endpoint running in FastAPI's threadpool
        does not have. That is why ``POST /sync`` is ``async def`` while the
        other routes here are not.
        """
        if self.is_running(user_id):
            return None
        run = _Run()
        self._runs[user_id] = run
        self._runs.move_to_end(user_id)
        while len(self._runs) > _MAX_REMEMBERED_RUNS:
            # Evict the oldest *finished* run. The dict is keyed by user, so on a
            # public instance it would otherwise grow with the user table.
            oldest, _ = next(iter(self._runs.items()))
            if self.is_running(oldest):
                break
            self._runs.pop(oldest)
        self._loop = asyncio.get_running_loop()
        self._tasks[user_id] = asyncio.create_task(self._run(user_id, run))
        return run.summary(running=True)

    async def _run(self, user_id: str, run: _Run) -> None:
        try:
            await _sync_user(user_id, run)
        except asyncio.CancelledError:
            # Shutdown, or an explicit cancel. The link must not be abandoned at
            # "syncing": nothing would ever finish it and the UI would show a
            # sync that is permanently in progress. Recorded synchronously
            # because awaiting anything inside a cancelled task is not
            # guaranteed to come back.
            #
            # Best-effort, and that is a deliberate ranking rather than
            # laziness. The most common cancel is ``DELETE /link``, whose
            # request transaction is still holding SQLite's single write lock
            # when we get here — so this write can genuinely fail with "database
            # is locked". Letting that escape would replace the CancelledError
            # with an OperationalError, break the cancellation contract, and
            # surface as an unretrieved task exception. _reconcile_stale is the
            # designed repair for a link left at "syncing", so failing here
            # costs one status correction on the next poll, and nothing else.
            run.error = "the sync was interrupted"
            run.finished_at = datetime.now(UTC)
            _try_record_failure(user_id, run.error)
            raise
        except Exception as exc:  # noqa: BLE001 — nothing awaits this task's result
            log.exception("zotero sync failed for a user")
            run.error = _clean_error(exc)
            run.finished_at = datetime.now(UTC)
            await asyncio.to_thread(_try_record_failure, user_id, run.error)
        finally:
            if run.finished_at is None:
                run.finished_at = datetime.now(UTC)
            self._tasks.pop(user_id, None)

    async def aclose(self) -> None:
        """Cancel every in-flight sync and wait for it to unwind.

        Should be called from the app's lifespan shutdown. It is not fatal if it
        is not: :func:`_reconcile_stale` repairs a link left at ``syncing`` by a
        process that died. This is the tidy path, that is the safety net.
        """
        tasks = [t for t in self._tasks.values() if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()


#: Module-level because it holds no loop-bound state until a sync is submitted.
syncer = ZoteroSyncer()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _now() -> datetime:
    return datetime.now(UTC)


def _cipher(settings: Settings | None = None) -> CredentialCipher:
    return CredentialCipher.from_settings(settings or get_app_settings())


def _safe_http_url(value: str | None) -> bool:
    """Require HTTPS, except loopback HTTP for local development."""
    if not value:
        return False
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return False
    if parsed.username or parsed.password or not parsed.hostname:
        return False
    if parsed.scheme == "https":
        return True
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _oauth_ready(settings: Settings) -> bool:
    return bool(
        settings.zotero_oauth_configured
        and _safe_http_url(settings.zotero_oauth_callback_url)
        and (
            settings.zotero_oauth_return_url is None
            or _safe_http_url(settings.zotero_oauth_return_url)
        )
    )


def _url_with_query(url: str, **updates: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    query.update(updates)
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), parsed.fragment)
    )


def _callback_url(settings: Settings, state: str) -> str:
    # _oauth_ready has already checked the value.
    return _url_with_query(str(settings.zotero_oauth_callback_url), state=state)


def _desktop_callback_url(settings: Settings, state: str) -> str:
    # Keep Zotero's registered HTTPS callback. Only the final, Pharos-generated
    # handoff crosses the custom protocol boundary.
    return _url_with_query(
        str(settings.zotero_oauth_callback_url), state=state, flow="desktop"
    )


def _return_url(settings: Settings, result: str) -> str:
    if result not in _OAUTH_RESULTS:  # pragma: no cover - all callers use constants
        result = "error"
    base = settings.zotero_oauth_return_url
    if not base:
        callback = urllib.parse.urlsplit(str(settings.zotero_oauth_callback_url))
        base = urllib.parse.urlunsplit((callback.scheme, callback.netloc, "/", "", ""))
    return _url_with_query(base, zotero=result)


def _oauth_redirect(settings: Settings, result: str) -> RedirectResponse:
    response = RedirectResponse(
        _return_url(settings, result), status_code=status.HTTP_303_SEE_OTHER
    )
    name, secure = _oauth_cookie(settings)
    response.delete_cookie(name, path="/", secure=secure, httponly=True, samesite="lax")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _desktop_oauth_redirect(result: str, *, code: str | None = None) -> RedirectResponse:
    """Return to the installed app without putting provider credentials in URL."""
    if code is not None:
        query = urllib.parse.urlencode({"code": code})
    else:
        if result not in _OAUTH_RESULTS:  # pragma: no cover - callers use constants
            result = "error"
        query = urllib.parse.urlencode({"result": result})
    response = RedirectResponse(
        f"pharos://oauth/zotero?{query}", status_code=status.HTTP_303_SEE_OTHER
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _oauth_cookie(settings: Settings) -> tuple[str, bool]:
    callback = urllib.parse.urlsplit(str(settings.zotero_oauth_callback_url))
    secure = callback.scheme == "https"
    return ("__Host-pharos-zotero-state" if secure else "pharos-zotero-state", secure)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _single_query(request: Request, name: str, *, max_length: int = 512) -> str | None:
    """One bounded callback parameter; duplicates are rejected, not guessed."""
    values = request.query_params.getlist(name)
    if len(values) != 1 or not values[0] or len(values[0]) > max_length:
        return None
    return values[0]


def _clean_error(exc: Exception) -> str:
    """Render an exception as a user-facing string with no secret in it.

    Two independent guards, because this string lands in a database column and
    in an API response. ``scrub`` cannot help here — this function does not hold
    the key — so instead only *our own* exception classes are quoted verbatim;
    anything else is reduced to its type name. An unexpected exception's message
    is arbitrary text from a library we did not write, and the one thing we can
    say for certain about it is that we have not audited it for the user's
    credentials.
    """
    if isinstance(exc, client.ZoteroError):
        return str(exc)[:_MAX_ERROR_CHARS]
    return f"The sync failed unexpectedly ({type(exc).__name__})."


def _link_for(session: Session, user_id: str) -> ZoteroLink | None:
    """This user's link, or ``None``. The only way this file loads one.

    Every read goes through the ``user_id`` filter. ``ZoteroLink.user_id`` is
    unique, so there is exactly one row per user and no ordering to get wrong.
    """
    return session.scalar(select(ZoteroLink).where(ZoteroLink.user_id == user_id))


def _paper_count(session: Session, user_id: str) -> int:
    """How many of this user's papers came from Zotero."""
    return int(
        session.scalar(
            select(func.count())
            .select_from(Paper)
            .where(Paper.user_id == user_id, Paper.zotero_key.is_not(None))
        )
        or 0
    )


def _replace_link(
    session: Session,
    *,
    user_id: str,
    zotero_user_id: str,
    api_key: str,
    settings: Settings,
) -> ZoteroLink:
    """Atomically replace one user's verified Zotero credential."""
    link = _link_for(session, user_id)
    if link is None:
        link = ZoteroLink(user_id=user_id)
        session.add(link)
    link.zotero_user_id = zotero_user_id
    link.api_key = _cipher(settings).protect(api_key)
    link.library_version = 0
    link.status = "linked"
    link.last_error = None
    link.last_sync_at = None
    link.item_count = _paper_count(session, user_id)
    return link


def _reconcile_stale(session: Session, link: ZoteroLink | None) -> ZoteroLink | None:
    """Repair a link stuck at ``syncing`` with no task behind it.

    A crash, a kill, or a container restart mid-sync leaves the row saying
    "syncing" forever, and a user staring at a spinner that will never resolve.
    Because the syncer is the sole authority on what is actually running in this
    process, "status says syncing but nothing is running" is unambiguous — so it
    is corrected here rather than requiring the user to guess or an operator to
    intervene.

    The row is moved to ``error`` rather than quietly back to ``linked``: the
    previous sync really did not finish, and pretending otherwise would leave a
    partially-synced library looking complete.
    """
    if link is None or link.status != "syncing":
        return link
    if syncer.is_running(link.user_id):
        return link
    link.status = "error"
    link.last_error = "The previous sync did not finish (the server restarted). Try again."
    return link


def _status_out(
    session: Session, user_id: str, *, oauth_available: bool | None = None
) -> ZoteroStatusOut:
    if oauth_available is None:
        oauth_available = _oauth_ready(get_app_settings())
    link = _reconcile_stale(session, _link_for(session, user_id))
    if link is None:
        return ZoteroStatusOut(
            linked=False, sync=syncer.summary(user_id), oauth_available=oauth_available
        )
    # A live task outranks the stored status. ``POST /sync`` returns before the
    # background task's first write has landed, so the row can still say
    # "linked" for a sync that is demonstrably underway — and answering "linked"
    # to the request that just started it would make the 202 self-contradictory.
    # The syncer knows what is running in this process; the row is the record of
    # what finished.
    live = syncer.is_running(user_id)
    return ZoteroStatusOut(
        linked=True,
        zotero_user_id=link.zotero_user_id,
        status="syncing" if live else link.status,
        last_sync_at=as_utc(link.last_sync_at),
        item_count=link.item_count,
        # The previous run's error is stale the moment a new run starts; showing
        # it next to "syncing" reads as the current attempt having already failed.
        last_error=None if live else link.last_error,
        library_version=link.library_version,
        sync=syncer.summary(user_id),
        oauth_available=oauth_available,
        # NOTE: link.api_key is deliberately not referenced. Anything added to
        # this constructor must be checked against that rule.
    )


def _status_snapshot(user_id: str) -> ZoteroStatusOut:
    """:func:`_status_out` with a session of its own, for use off the loop."""
    with session_scope() as session:
        return _status_out(session, user_id)


def _sync_precondition(user_id: str) -> bool:
    """Whether this user has a link to sync, repairing a stale one on the way."""
    with session_scope() as session:
        return _reconcile_stale(session, _link_for(session, user_id)) is not None


def migrate_stored_credentials(settings: Settings | None = None) -> int:
    """Encrypt legacy plaintext and rotate ciphertext at application startup.

    The migration is deliberately best-effort per row: one corrupt credential
    must not prevent Pharos from booting, and no exception or log message ever
    includes the stored value. A bad row will surface as a reconnect prompt when
    its owner next syncs.
    """
    cipher = _cipher(settings)
    if not cipher.configured:
        return 0
    changed = 0
    with session_scope() as session:
        values: list[tuple[object, str]] = [
            *((link, "api_key") for link in session.scalars(select(ZoteroLink)).all()),
            *(
                (attempt, "request_token_secret")
                for attempt in session.scalars(select(ZoteroOAuthAttempt)).all()
            ),
            *(
                (attempt, "handoff_api_key")
                for attempt in session.scalars(select(ZoteroOAuthAttempt)).all()
                if attempt.handoff_api_key
            ),
        ]
        for row, attribute in values:
            stored = str(getattr(row, attribute))
            try:
                normalized = cipher.normalize(stored)
            except CredentialError:
                log.error("zotero: one stored credential could not be migrated")
                continue
            if normalized != stored:
                setattr(row, attribute, normalized)
                changed += 1
    if changed:
        log.info("zotero: encrypted or rotated %d stored credential(s)", changed)
    return changed


def _create_oauth_attempt(
    *,
    user_id: str,
    state: str,
    browser_secret: str,
    request_token: zotero_oauth.RequestToken,
    expires_at: datetime,
    settings: Settings,
    flow_kind: str = "browser",
) -> None:
    cipher = _cipher(settings)
    if not cipher.configured:  # guarded by _oauth_ready; fail closed anyway
        raise RuntimeError("A stable credential secret is required for Zotero OAuth.")
    with session_scope() as session:
        # The newest click wins. It overwrites the browser cookie too, so older
        # consent tabs can no longer bind successfully to this account.
        session.execute(delete(ZoteroOAuthAttempt).where(ZoteroOAuthAttempt.user_id == user_id))
        session.add(
            ZoteroOAuthAttempt(
                state=state,
                user_id=user_id,
                request_token_hash=_digest(request_token.token),
                browser_state_hash=_digest(browser_secret),
                request_token_secret=cipher.protect(request_token.secret),
                flow_kind=flow_kind,
                expires_at=expires_at,
            )
        )


class _OAuthAttemptClaim:
    __slots__ = ("request_token_secret", "user_id")

    def __init__(self, user_id: str, request_token_secret: str) -> None:
        self.user_id = user_id
        self.request_token_secret = request_token_secret


def _claim_oauth_attempt(
    *, state: str, request_token: str, browser_secret: str, settings: Settings
) -> tuple[str, _OAuthAttemptClaim | None]:
    """Atomically consume an OAuth attempt.

    Returns a fixed result code and, only for a valid live attempt, the material
    needed for the access exchange. A repeated callback loses the conditional
    update and therefore cannot replay the verifier.
    """
    now = _now()
    with session_scope() as session:
        attempt = session.scalar(
            select(ZoteroOAuthAttempt).where(ZoteroOAuthAttempt.state == state)
        )
        if attempt is None:
            return "invalid", None
        if attempt.flow_kind not in (None, "browser"):
            return "invalid", None
        if not secrets.compare_digest(attempt.request_token_hash, _digest(request_token)):
            return "invalid", None
        if not secrets.compare_digest(attempt.browser_state_hash, _digest(browser_secret)):
            return "invalid", None
        if as_utc(attempt.expires_at) <= now:
            attempt.used_at = now
            return "expired", None
        claimed = session.execute(
            update(ZoteroOAuthAttempt)
            .where(
                ZoteroOAuthAttempt.state == state,
                ZoteroOAuthAttempt.used_at.is_(None),
            )
            .values(used_at=now)
        )
        if claimed.rowcount != 1:
            return "invalid", None
        try:
            token_secret = _cipher(settings).reveal(attempt.request_token_secret)
        except CredentialError:
            log.error("zotero: OAuth request secret could not be decrypted")
            return "error", None
        return "ok", _OAuthAttemptClaim(attempt.user_id, token_secret)


def _claim_desktop_oauth_attempt(
    *, state: str, request_token: str, settings: Settings
) -> tuple[str, _OAuthAttemptClaim | None]:
    """Consume the provider callback for a desktop flow.

    The app-held binding secret is intentionally *not* present in the system
    browser. It is checked later when the installed app consumes the handoff.
    """
    now = _now()
    with session_scope() as session:
        attempt = session.scalar(
            select(ZoteroOAuthAttempt).where(ZoteroOAuthAttempt.state == state)
        )
        if attempt is None or attempt.flow_kind != "desktop":
            return "invalid", None
        if not secrets.compare_digest(attempt.request_token_hash, _digest(request_token)):
            return "invalid", None
        if as_utc(attempt.expires_at) <= now:
            attempt.used_at = now
            return "expired", None
        claimed = session.execute(
            update(ZoteroOAuthAttempt)
            .where(
                ZoteroOAuthAttempt.state == state,
                ZoteroOAuthAttempt.flow_kind == "desktop",
                ZoteroOAuthAttempt.used_at.is_(None),
            )
            .values(used_at=now)
        )
        if claimed.rowcount != 1:
            return "invalid", None
        try:
            token_secret = _cipher(settings).reveal(attempt.request_token_secret)
        except CredentialError:
            log.error("zotero: desktop OAuth request secret could not be decrypted")
            return "error", None
        return "ok", _OAuthAttemptClaim(attempt.user_id, token_secret)


def _complete_desktop_handoff(
    *,
    state: str,
    user_id: str,
    zotero_user_id: str,
    api_key: str,
    code: str,
    settings: Settings,
) -> None:
    """Store an encrypted, user-bound handoff after Zotero verifies consent."""
    expires_at = _now() + timedelta(seconds=_DESKTOP_HANDOFF_TTL_SECONDS)
    protected_key = _cipher(settings).protect(api_key)
    with session_scope() as session:
        completed = session.execute(
            update(ZoteroOAuthAttempt)
            .where(
                ZoteroOAuthAttempt.state == state,
                ZoteroOAuthAttempt.user_id == user_id,
                ZoteroOAuthAttempt.flow_kind == "desktop",
                ZoteroOAuthAttempt.used_at.is_not(None),
                ZoteroOAuthAttempt.handoff_code_hash.is_(None),
            )
            .values(
                handoff_code_hash=_digest(code),
                handoff_zotero_user_id=zotero_user_id,
                handoff_api_key=protected_key,
                handoff_expires_at=expires_at,
            )
        )
        if completed.rowcount != 1:
            raise RuntimeError("handoff")


class _DesktopHandoffClaim:
    __slots__ = ("api_key", "state", "zotero_user_id")

    def __init__(self, state: str, zotero_user_id: str, api_key: str) -> None:
        self.state = state
        self.zotero_user_id = zotero_user_id
        self.api_key = api_key


def _claim_desktop_handoff(
    *, code: str, desktop_secret: str, user_id: str, settings: Settings
) -> tuple[str, _DesktopHandoffClaim | None]:
    """Atomically consume a handoff for the same signed-in Pharos user."""
    now = _now()
    code_hash = _digest(code)
    with session_scope() as session:
        attempt = session.scalar(
            select(ZoteroOAuthAttempt).where(
                ZoteroOAuthAttempt.handoff_code_hash == code_hash,
                ZoteroOAuthAttempt.user_id == user_id,
                ZoteroOAuthAttempt.flow_kind == "desktop",
            )
        )
        if attempt is None:
            return "invalid", None
        if not secrets.compare_digest(
            attempt.browser_state_hash, _digest(desktop_secret)
        ):
            return "invalid", None
        if (
            attempt.handoff_expires_at is None
            or as_utc(attempt.handoff_expires_at) <= now
        ):
            attempt.handoff_used_at = now
            return "expired", None
        if not attempt.handoff_zotero_user_id or not attempt.handoff_api_key:
            return "invalid", None
        claimed = session.execute(
            update(ZoteroOAuthAttempt)
            .where(
                ZoteroOAuthAttempt.state == attempt.state,
                ZoteroOAuthAttempt.handoff_code_hash == code_hash,
                ZoteroOAuthAttempt.handoff_used_at.is_(None),
            )
            .values(handoff_used_at=now)
        )
        if claimed.rowcount != 1:
            return "invalid", None
        try:
            api_key = _cipher(settings).reveal(attempt.handoff_api_key)
        except CredentialError:
            log.error("zotero: desktop OAuth handoff key could not be decrypted")
            return "error", None
        return "ok", _DesktopHandoffClaim(
            attempt.state, attempt.handoff_zotero_user_id, api_key
        )


def _clear_desktop_handoff(state: str) -> None:
    """Remove temporary provider credentials after they reach ZoteroLink."""
    with session_scope() as session:
        session.execute(
            update(ZoteroOAuthAttempt)
            .where(ZoteroOAuthAttempt.state == state)
            .values(handoff_code_hash=None, handoff_api_key=None)
        )


def _verified_oauth_identity(
    access: zotero_oauth.AccessToken,
) -> client.ZoteroIdentity:
    """Verify the returned API key and its actual least-privilege access."""
    identity = client.verify(access.user_id, access.api_key)
    if identity is None:
        raise zotero_oauth.ZoteroOAuthError("Zotero rejected the newly issued API key.")
    if not identity.matches_claim:
        raise zotero_oauth.ZoteroOAuthError("Zotero returned inconsistent account identity.")
    if not identity.library_read:
        raise zotero_oauth.ZoteroOAuthError("The Zotero authorization lacks library read access.")
    return identity


def _store_oauth_link(
    *, user_id: str, zotero_user_id: str, api_key: str, settings: Settings
) -> None:
    if syncer.is_running(user_id):
        raise RuntimeError("syncing")
    with session_scope() as session:
        _replace_link(
            session,
            user_id=user_id,
            zotero_user_id=zotero_user_id,
            api_key=api_key,
            settings=settings,
        )


# --------------------------------------------------------------------------- #
# sync — runs in a background task, never inside a request
# --------------------------------------------------------------------------- #


def _begin_sync(user_id: str) -> tuple[str, str, int]:
    """Claim the link for a sync. Returns ``(zotero_user_id, api_key, since)``.

    Runs before any network call so that a client polling the instant ``POST
    /sync`` responds already sees ``syncing`` rather than a stale ``linked`` it
    would have to guess the meaning of.

    Raises:
        LookupError: Nothing is linked for this user.
    """
    with session_scope() as session:
        link = _link_for(session, user_id)
        if link is None:
            raise LookupError("no Zotero account is linked")
        try:
            api_key = _cipher().reveal(link.api_key)
            normalized = _cipher().normalize(link.api_key)
        except CredentialError:
            link.status = "error"
            link.last_error = "Stored Zotero credentials are unavailable. Reconnect Zotero."
            raise client.ZoteroCredentialsError(link.last_error) from None
        if normalized != link.api_key:
            link.api_key = normalized
        link.status = "syncing"
        link.last_error = None
        return link.zotero_user_id, api_key, int(link.library_version or 0)


def _record_failure(user_id: str, message: str) -> None:
    """Mark the link failed. Leaves ``library_version`` exactly as it was.

    Not advancing the version on failure is the whole reason this is a separate
    function: a partially-applied sync that recorded the new version would make
    the *next* incremental sync skip precisely the items that failed to land, and
    they would never be seen again.
    """
    with session_scope() as session:
        link = _link_for(session, user_id)
        if link is None:  # pragma: no cover — unlinked mid-sync
            return
        link.status = "error"
        link.last_error = message[:_MAX_ERROR_CHARS]


def _try_record_failure(user_id: str, message: str) -> None:
    """:func:`_record_failure` that never raises. See the cancel path in ``_run``.

    This is the *error* path already; a second failure while recording the first
    must not escalate into an escaping exception on a task nobody is awaiting.
    A link left at "syncing" is repaired by :func:`_reconcile_stale`.
    """
    try:
        _record_failure(user_id, message)
    except Exception:  # noqa: BLE001 — see the docstring
        log.warning("zotero: could not record a sync failure; status will self-heal on next poll")


def _finish_sync(user_id: str, library_version: int) -> int:
    """Mark the link healthy and record the new version. Returns the item count."""
    with session_scope() as session:
        link = _link_for(session, user_id)
        if link is None:  # pragma: no cover — unlinked mid-sync
            return 0
        link.status = "linked"
        link.last_error = None
        link.last_sync_at = _now()
        link.library_version = library_version
        link.item_count = _paper_count(session, user_id)
        return link.item_count


def _apply_item(paper: Paper, item: client.ZoteroItem, *, is_new: bool) -> bool:
    """Copy a Zotero record onto a paper. Returns whether anything changed.

    Zotero is authoritative for the rows that came from Zotero — that is the
    point of linking it — but authority is only claimed over fields Zotero
    actually filled in. A blank ``publicationTitle`` means "the user has not
    recorded a venue", not "delete the venue", so a missing field never erases a
    stored value. The user's remedy for a wrong field is to fix it in Zotero,
    where the rest of their workflow already lives.
    """
    changed = is_new
    values: dict[str, object] = {}
    if item.title:
        values["title"] = item.title
    if item.creators:
        joined = "; ".join(item.creators)
        values["authors"] = joined[: client.MAX_AUTHORS_JOINED]
    if item.year is not None:
        values["year"] = item.year
    if item.venue:
        values["venue"] = item.venue
    if item.doi:
        values["doi"] = item.doi
    if item.abstract:
        values["abstract"] = item.abstract

    for field, value in values.items():
        if getattr(paper, field) != value:
            setattr(paper, field, value)
            changed = True

    if changed:
        paper.meta_source = "zotero"
        paper.meta_extracted_at = _now()
    return changed


def _upsert_papers(user_id: str, items: list[client.ZoteroItem]) -> tuple[int, int]:
    """Upsert Zotero items into this user's library. Returns ``(added, updated)``.

    Keyed on ``(user_id, zotero_key)``, which is what makes a repeat sync update
    rather than duplicate. The ``user_id`` half is not optional: ``zotero_key``
    alone is unique only within one Zotero account, and two Pharos users can
    perfectly well have imported the same public item.

    **No PDF.** This prototype pulls metadata only, and ``Paper.orig_sha256`` is
    NOT NULL, so a Zotero row stores the empty string — "there are no bytes" —
    alongside an empty ``orig_filename``. That is the honest encoding available
    without a model change, and it is safe against the existing readers:
    ``BlobStore.path("")`` names a file that is never written, so the PDF
    endpoints answer their normal "not available yet" 404 rather than crashing,
    and ``LibraryService._remove_blobs`` already returns early on a falsy hash so
    a purge cannot delete anything. It is *not* self-describing, though, and the
    handover note for this slice asks for a ``has_pdf`` on ``PaperOut`` (and a
    guard on the translate endpoint) so nothing downstream has to infer
    "metadata-only" from an empty string.

    A soft-deleted row is updated but deliberately **not** restored. Re-uploading
    a trashed file is an explicit act by a user holding that file, so
    ``add_upload`` restoring it is right; a bulk background sync is not, and
    resurrecting everything the user trashed on every sync would make the recycle
    bin useless.
    """
    added = 0
    updated = 0
    for offset in range(0, len(items), _UPSERT_BATCH):
        batch = items[offset : offset + _UPSERT_BATCH]
        with session_scope() as session:
            keys = [item.key for item in batch]
            existing = {
                paper.zotero_key: paper
                for paper in session.scalars(
                    # Both predicates matter. Dropping user_id would let one
                    # user's sync overwrite another user's paper.
                    select(Paper).where(Paper.user_id == user_id, Paper.zotero_key.in_(keys))
                )
            }
            for item in batch:
                paper = existing.get(item.key)
                is_new = paper is None
                if paper is None:
                    paper = Paper(
                        user_id=user_id,
                        zotero_key=item.key,
                        title=item.title or item.key,
                        orig_sha256="",  # see the docstring: no bytes are stored
                        orig_filename="",
                        source="zotero",
                        page_count=None,
                    )
                    session.add(paper)
                if _apply_item(paper, item, is_new=is_new):
                    if is_new:
                        added += 1
                    else:
                        updated += 1
    return added, updated


async def _sync_user(user_id: str, run: _Run) -> None:
    """One full sync: claim, fetch, upsert, finalise.

    Every step is a blocking call handed to a worker thread, so the event loop
    keeps serving requests — including the ``GET /status`` polls that are
    watching this very sync — while Zotero is paged through.
    """
    zotero_user_id, api_key, since = await asyncio.to_thread(_begin_sync, user_id)
    try:
        items, version = await asyncio.to_thread(
            client.fetch_items, zotero_user_id, api_key, since=since
        )
    except client.ZoteroError as exc:
        # The message is ours, but scrub anyway: this is the one call site that
        # holds both the key and text on its way to a durable column, and the
        # cost of being wrong here is the user's Zotero credentials in a log.
        #
        # ``from None`` on purpose, and it is the point rather than a style
        # choice: chaining would hang the *unscrubbed* original off __cause__,
        # where the traceback printed by the task's ``log.exception`` would put
        # it straight back into the log this line exists to keep it out of.
        raise client.ZoteroError(
            client.scrub(str(exc), api_key) or "The Zotero sync failed."
        ) from None

    usable = [item for item in items if item.title]
    run.skipped = len(items) - len(usable)
    added, updated = await asyncio.to_thread(_upsert_papers, user_id, usable)

    run.added = added
    run.updated = updated
    run.total = len(usable)
    run.library_version = version
    run.finished_at = _now()
    await asyncio.to_thread(_finish_sync, user_id, version)
    log.info("zotero sync finished: %d added, %d updated", added, updated)


# --------------------------------------------------------------------------- #
# endpoints — every one requires a signed-in user
# --------------------------------------------------------------------------- #


@router.get("/status", response_model=ZoteroStatusOut)
def get_status(
    user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_runtime_settings)],
) -> ZoteroStatusOut:
    """Whether Zotero is linked, and what the last sync did.

    The first thing the UI asks. Never includes the API key in any form.
    """
    return _status_out(session, user.id, oauth_available=_oauth_ready(settings))


@router.post("/oauth/start", response_model=OAuthStartOut)
def start_oauth(
    response: Response,
    user: Annotated[User, Depends(current_user)],
    settings: Annotated[Settings, Depends(get_runtime_settings)],
) -> OAuthStartOut:
    """Create a one-use flow and send the browser to Zotero's consent page."""
    if not _oauth_ready(settings):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Zotero one-click authorization is not configured on this server.",
        )
    if syncer.is_running(user.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A sync is running. Wait for it to finish before reconnecting Zotero.",
        )

    state_value = secrets.token_urlsafe(32)
    browser_secret = secrets.token_urlsafe(32)
    ttl = max(60, min(int(settings.zotero_oauth_attempt_ttl_seconds), 3600))
    expires_at = _now() + timedelta(seconds=ttl)
    try:
        temporary = zotero_oauth.request_token(
            str(settings.zotero_oauth_client_key),
            str(settings.zotero_oauth_client_secret),
            _callback_url(settings, state_value),
        )
        _create_oauth_attempt(
            user_id=user.id,
            state=state_value,
            browser_secret=browser_secret,
            request_token=temporary,
            expires_at=expires_at,
            settings=settings,
        )
    except zotero_oauth.ZoteroOAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not start Zotero authorization. Try again.",
        ) from exc
    except Exception as exc:
        log.error("zotero: could not persist OAuth attempt (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not start Zotero authorization. Try again.",
        ) from None

    cookie_name, secure = _oauth_cookie(settings)
    response.set_cookie(
        cookie_name,
        browser_secret,
        max_age=ttl,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return OAuthStartOut(
        authorize_url=zotero_oauth.authorization_url(temporary.token),
        expires_at=expires_at,
    )


@router.post("/oauth/desktop/start", response_model=DesktopOAuthStartOut)
def start_desktop_oauth(
    response: Response,
    user: Annotated[User, Depends(current_user)],
    settings: Annotated[Settings, Depends(get_runtime_settings)],
) -> DesktopOAuthStartOut:
    """Create a system-browser flow bound to the installed Pharos app."""
    if not _oauth_ready(settings):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Zotero one-click authorization is not configured on this server.",
        )
    if syncer.is_running(user.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A sync is running. Wait for it to finish before reconnecting Zotero.",
        )

    state_value = secrets.token_urlsafe(32)
    desktop_secret = secrets.token_urlsafe(32)
    ttl = max(60, min(int(settings.zotero_oauth_attempt_ttl_seconds), 3600))
    expires_at = _now() + timedelta(seconds=ttl)
    try:
        temporary = zotero_oauth.request_token(
            str(settings.zotero_oauth_client_key),
            str(settings.zotero_oauth_client_secret),
            _desktop_callback_url(settings, state_value),
        )
        _create_oauth_attempt(
            user_id=user.id,
            state=state_value,
            browser_secret=desktop_secret,
            request_token=temporary,
            expires_at=expires_at,
            settings=settings,
            flow_kind="desktop",
        )
    except zotero_oauth.ZoteroOAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not start Zotero authorization. Try again.",
        ) from exc
    except Exception as exc:
        log.error("zotero: could not persist desktop OAuth attempt (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not start Zotero authorization. Try again.",
        ) from None

    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return DesktopOAuthStartOut(
        authorize_url=zotero_oauth.authorization_url(temporary.token),
        expires_at=expires_at,
        desktop_secret=desktop_secret,
    )


@router.get("/oauth/callback", response_class=RedirectResponse)
async def finish_oauth(
    request: Request,
    settings: Annotated[Settings, Depends(get_runtime_settings)],
) -> RedirectResponse:
    """Consume Zotero's HTTPS callback for either web or desktop clients."""
    if not settings.zotero_oauth_callback_url or not _safe_http_url(
        settings.zotero_oauth_callback_url
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Zotero one-click authorization is not configured on this server.",
        )
    flow = _single_query(request, "flow", max_length=16)
    desktop_flow = flow == "desktop"
    redirect = (
        (lambda result: _desktop_oauth_redirect(result))
        if desktop_flow
        else (lambda result: _oauth_redirect(settings, result))
    )
    if flow not in (None, "desktop") or not _oauth_ready(settings):
        return redirect("error")

    state_value = _single_query(request, "state", max_length=128)
    oauth_token = _single_query(request, "oauth_token")
    denied = _single_query(request, "denied")
    oauth_problem = _single_query(request, "oauth_problem", max_length=128)
    request_token_value = oauth_token or denied
    if (
        state_value is None
        or request_token_value is None
        or (oauth_token is not None and denied is not None)
    ):
        return redirect("invalid")

    if desktop_flow:
        claim_result, claim = await asyncio.to_thread(
            _claim_desktop_oauth_attempt,
            state=state_value,
            request_token=request_token_value,
            settings=settings,
        )
    else:
        cookie_name, _secure = _oauth_cookie(settings)
        browser_secret = request.cookies.get(cookie_name)
        if browser_secret is None or len(browser_secret) > 512:
            return _oauth_redirect(settings, "invalid")
        claim_result, claim = await asyncio.to_thread(
            _claim_oauth_attempt,
            state=state_value,
            request_token=request_token_value,
            browser_secret=browser_secret,
            settings=settings,
        )
    if claim is None:
        return redirect(claim_result)
    if denied is not None or oauth_problem == "user_refused":
        return redirect("cancelled")

    verifier = _single_query(request, "oauth_verifier")
    if verifier is None:
        return redirect("invalid")

    try:
        access = await asyncio.to_thread(
            zotero_oauth.access_token,
            str(settings.zotero_oauth_client_key),
            str(settings.zotero_oauth_client_secret),
            request_token_value,
            claim.request_token_secret,
            verifier,
        )
        identity = await asyncio.to_thread(_verified_oauth_identity, access)
        if desktop_flow:
            handoff_code = secrets.token_urlsafe(32)
            await asyncio.to_thread(
                _complete_desktop_handoff,
                state=state_value,
                user_id=claim.user_id,
                zotero_user_id=identity.user_id,
                api_key=access.api_key,
                code=handoff_code,
                settings=settings,
            )
        else:
            await asyncio.to_thread(
                _store_oauth_link,
                user_id=claim.user_id,
                zotero_user_id=identity.user_id,
                api_key=access.api_key,
                settings=settings,
            )
    except RuntimeError as exc:
        if str(exc) == "syncing":
            return redirect("busy")
        log.error("zotero: OAuth link storage failed (%s)", type(exc).__name__)
        return redirect("error")
    except (zotero_oauth.ZoteroOAuthError, client.ZoteroError) as exc:
        log.warning("zotero: OAuth exchange failed (%s)", type(exc).__name__)
        return redirect("error")
    except Exception as exc:  # no provider/credential text reaches the log
        log.error("zotero: OAuth callback failed (%s)", type(exc).__name__)
        return redirect("error")

    if desktop_flow:
        log.info("zotero: desktop OAuth provider step completed")
        return _desktop_oauth_redirect("connected", code=handoff_code)

    # The callback runs on the event loop, so it can start the existing
    # background syncer directly. A status poll after the 303 sees "syncing".
    syncer.submit(claim.user_id)
    log.info("zotero: account linked through OAuth")
    return _oauth_redirect(settings, "connected")


@router.post("/oauth/desktop/finish", response_model=ZoteroStatusOut)
def finish_desktop_oauth(
    payload: DesktopOAuthFinishRequest,
    user: Annotated[User, Depends(current_user)],
    settings: Annotated[Settings, Depends(get_runtime_settings)],
) -> ZoteroStatusOut:
    """Bind the system-browser result to the installed, signed-in client."""
    claim_result, claim = _claim_desktop_handoff(
        code=payload.code,
        desktop_secret=payload.desktop_secret,
        user_id=user.id,
        settings=settings,
    )
    if claim is None:
        detail = (
            "The Zotero authorization expired. Start it again."
            if claim_result == "expired"
            else "The Zotero authorization is invalid or has already been used."
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
    try:
        _store_oauth_link(
            user_id=user.id,
            zotero_user_id=claim.zotero_user_id,
            api_key=claim.api_key,
            settings=settings,
        )
    except RuntimeError as exc:
        if str(exc) == "syncing":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A sync is running. Wait for it to finish before reconnecting Zotero.",
            ) from None
        log.error("zotero: desktop OAuth link storage failed (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not save the Zotero link. Try again.",
        ) from None
    except Exception as exc:
        log.error("zotero: desktop OAuth link storage failed (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not save the Zotero link. Try again.",
        ) from None
    finally:
        _clear_desktop_handoff(claim.state)

    syncer.submit(user.id)
    log.info("zotero: account linked through desktop OAuth")
    return _status_snapshot(user.id)


@router.post("/link", response_model=ZoteroStatusOut)
def link_zotero(
    payload: LinkRequest,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_runtime_settings)],
) -> ZoteroStatusOut:
    """Verify Zotero credentials, then store them. Replaces any existing link.

    A plain ``def``, so FastAPI runs the whole thing in a worker thread: the
    round-trip to Zotero blocks, and blocking in a worker is correct while
    blocking on the event loop would stall every other request. It also keeps
    the request's ORM session confined to a single thread.

    Verification happens *before* anything is persisted. Storing an unchecked key
    would give the user a link that reads as healthy and fails on every sync,
    with no way to tell a typo from an outage — so a rejected credential is a 400
    here and nothing is written at all.

    ``library_version`` resets to 0, so the first sync after linking is a full
    pull. Relinking means "these are different credentials, start fresh", and a
    full pull is idempotent anyway thanks to the ``(user_id, zotero_key)`` upsert
    key. Carrying an old version across a credential change is how a sync
    silently returns nothing.
    """
    if syncer.is_running(user.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A sync is running. Wait for it to finish before changing credentials.",
        )

    zotero_user_id = payload.zotero_user_id.strip()
    api_key = payload.api_key.strip()
    if not client.valid_user_id(zotero_user_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The Zotero user ID must be the numeric ID shown in your Zotero settings.",
        )
    if not client.valid_api_key(api_key):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That does not look like a Zotero API key (letters and digits only).",
        )

    try:
        identity = client.verify(zotero_user_id, api_key)
    except client.ZoteroUnavailable as exc:
        # 502, not 400: the credentials may be perfectly good and Zotero is the
        # thing that failed. Telling the user to regenerate a working key during
        # an outage is worse than telling them to try again.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=client.scrub(str(exc), api_key),
        ) from exc

    if identity is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Zotero rejected those credentials. Check the numeric user ID and "
                "create a fresh API key at zotero.org/settings/keys."
            ),
        )
    if not identity.matches_claim:
        # Not a security boundary — it is the user's own key either way — but a
        # key whose owner is someone else syncs a library the user did not
        # expect, and saying so beats silently pulling in a stranger's items.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"That API key belongs to Zotero user {identity.user_id}, not "
                f"{zotero_user_id}. Use the user ID shown on your Zotero key page."
            ),
        )
    if not identity.library_read:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "That API key cannot read your personal library. Edit the key at "
                "zotero.org/settings/keys and allow read access."
            ),
        )

    _replace_link(
        session,
        user_id=user.id,
        zotero_user_id=zotero_user_id,
        api_key=api_key,
        settings=settings,
    )
    try:
        session.flush()
    except Exception as exc:
        # The one statement in this app whose bound parameters include a bearer
        # secret. SQLAlchemy renders those parameters into the exception message
        # ("[parameters: ('...', 'the-api-key', ...)]"), and an unhandled 500
        # here would hand that string straight to the server log — the exact
        # outcome every other line in this module is written to prevent. So the
        # original never propagates: it is replaced, not chained (``from None``),
        # because __cause__ would carry the same text into the traceback.
        session.rollback()
        log.error("zotero: storing the link failed (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not save the Zotero link. Try again.",
        ) from None

    log.info("zotero: account linked")  # no id, no key — neither belongs in a log
    return _status_out(session, user.id, oauth_available=_oauth_ready(settings))


@router.delete("/link", response_model=UnlinkResult)
def unlink_zotero(
    user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> UnlinkResult:
    """Forget the Zotero credentials. Papers already pulled in are kept.

    Deleting the row deletes the stored key, which is the point: "unlink" has to
    mean the secret is gone, not merely hidden.

    The synced papers stay. They are in the user's library because the user put
    them there, and unlinking a source is not a request to empty a bookshelf —
    deleting papers has its own endpoint, with a recycle bin. ``zotero_key`` is
    left on those rows too, so relinking the same account updates them in place
    instead of creating a second copy of everything.

    Refusing while a sync runs would be the wrong call: revoking access is
    exactly what a user does when something is wrong, so an in-flight sync is
    cancelled and the credentials go regardless.
    """
    link = _link_for(session, user.id)
    kept = _paper_count(session, user.id)
    session.execute(delete(ZoteroOAuthAttempt).where(ZoteroOAuthAttempt.user_id == user.id))
    if link is None:
        return UnlinkResult(unlinked=False, papers_kept=kept)
    session.delete(link)
    session.flush()
    # Stop an in-flight sync rather than letting it keep using credentials the
    # user has just withdrawn. It would fail harmlessly anyway — every write
    # helper reloads the link and returns early once it is gone — but "unlink"
    # has to mean the key stops being used now, not at the end of the current
    # page. cancel() is a no-op when nothing is running.
    syncer.cancel(user.id)
    log.info("zotero: account unlinked")
    return UnlinkResult(unlinked=True, papers_kept=kept)


@router.post("/sync", response_model=ZoteroStatusOut, status_code=status.HTTP_202_ACCEPTED)
async def start_sync(user: Annotated[User, Depends(current_user)]) -> ZoteroStatusOut:
    """Pull the linked library's metadata in. Returns immediately with 202.

    The response is the *status*, not the result: a library of a few thousand
    items takes minutes to page through with Zotero's backoff honoured, and
    holding an HTTP request open for that is how a proxy timeout turns into a
    half-applied sync nobody can see. The summary — added, updated, total,
    library_version — appears under ``sync`` in this same shape once the run
    finishes, so a client polls ``GET /api/zotero/status`` and reads it there.

    409 when a sync is already running for this user: reporting that is more
    useful than queueing a second pass the caller cannot observe, and two
    concurrent passes would race each other's library-version write.

    ``async def`` because :meth:`ZoteroSyncer.submit` needs a running event loop,
    which is exactly what a threadpool-dispatched ``def`` endpoint lacks. The
    consequence is that this function body runs *on* the loop, so it takes no
    session dependency and every database touch goes to a worker thread — the
    same discipline :func:`pharos.api.daily.refresh` follows.
    """
    user_id = user.id
    if not await asyncio.to_thread(_sync_precondition, user_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Link a Zotero account before syncing.",
        )
    if syncer.submit(user_id) is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="A sync is already running."
        )
    # Reported as "syncing" by _status_out on the strength of the live task, so
    # the 202 is self-consistent even though the task's own first write has not
    # landed yet. No marker write here means no second writer racing _begin_sync.
    return await asyncio.to_thread(_status_snapshot, user_id)
