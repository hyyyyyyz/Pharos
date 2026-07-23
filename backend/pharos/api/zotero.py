"""Zotero API — link a Zotero account and pull its library metadata in.

A Pharos account is *not* a Zotero account. Signing in to Pharos gets you a
library; linking Zotero is an optional, revocable extra that pulls bibliographic
records out of Zotero's cloud and into that library. Every route here requires a
signed-in user and touches only that user's own rows — an unfiltered query in
this file would show one researcher another's reading list.

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
import logging
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pharos.api.deps import current_user, get_session
from pharos.api.schemas import as_utc
from pharos.db.models import Paper, User, ZoteroLink
from pharos.db.session import session_scope
from pharos.services import zotero as client

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
        self.started_at = datetime.now(timezone.utc)
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
            run.finished_at = datetime.now(timezone.utc)
            _try_record_failure(user_id, run.error)
            raise
        except Exception as exc:  # noqa: BLE001 — nothing awaits this task's result
            log.exception("zotero sync failed for a user")
            run.error = _clean_error(exc)
            run.finished_at = datetime.now(timezone.utc)
            await asyncio.to_thread(_try_record_failure, user_id, run.error)
        finally:
            if run.finished_at is None:
                run.finished_at = datetime.now(timezone.utc)
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
    return datetime.now(timezone.utc)


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


def _status_out(session: Session, user_id: str) -> ZoteroStatusOut:
    link = _reconcile_stale(session, _link_for(session, user_id))
    if link is None:
        return ZoteroStatusOut(linked=False, sync=syncer.summary(user_id))
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
        link.status = "syncing"
        link.last_error = None
        return link.zotero_user_id, link.api_key, int(link.library_version or 0)


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
) -> ZoteroStatusOut:
    """Whether Zotero is linked, and what the last sync did.

    The first thing the UI asks. Never includes the API key in any form.
    """
    return _status_out(session, user.id)


@router.post("/link", response_model=ZoteroStatusOut)
def link_zotero(
    payload: LinkRequest,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
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

    link = _link_for(session, user.id)
    if link is None:
        link = ZoteroLink(user_id=user.id)
        session.add(link)
    link.zotero_user_id = zotero_user_id
    link.api_key = api_key
    link.library_version = 0
    link.status = "linked"
    link.last_error = None
    link.last_sync_at = None
    link.item_count = _paper_count(session, user.id)
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
    return _status_out(session, user.id)


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
