"""Jobs API: start a translation, poll a job, and stream live progress (SSE)."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from pharos.api.auth import pdf_translation_enabled
from pharos.api.deps import current_user
from pharos.api.schemas import JobOut, job_out
from pharos.db.models import Paper, TranslationJob, User
from pharos.db.session import session_scope
from pharos.services.translation import create_job

router = APIRouter(prefix="/api", tags=["jobs"])

_HEARTBEAT_SECONDS = 15


def _start_job_row(
    paper_id: str, user_id: str, translator_type: str, target_lang: str
) -> tuple[str, str, str] | None:
    """Create a queued job for one of ``user_id``'s papers.

    Returns ``(job_id, sha256, source_lang)``, or ``None`` when the paper is
    absent, trashed, or somebody else's — three cases the caller deliberately
    collapses into one 404.

    A trashed paper is treated as absent. Beyond not spending an engine slot on
    something the user deleted, this closes a real race: a purge running
    concurrently would ``rmtree`` the blob directory out from under the
    BabelDOC subprocess that is mid-way through writing mono/dual PDFs into it.

    The owner filter is part of the same SELECT rather than a check afterwards,
    so translating a paper you do not own is not a thing this function can be
    talked into doing — it simply finds no row.
    """
    with session_scope() as s:
        paper = s.scalar(select(Paper).where(Paper.id == paper_id, Paper.user_id == user_id))
        if paper is None or paper.deleted_at is not None:
            return None
        job = create_job(s, paper, translator_type=translator_type, target_lang=target_lang)
        s.flush()
        return job.id, paper.orig_sha256, paper.source_lang


def _job_snapshot(job_id: str, user_id: str) -> dict | None:
    """One job's state, but only if the caller owns the paper it belongs to.

    A ``TranslationJob`` carries no ``user_id`` of its own — ownership lives one
    hop away on ``Paper``. Without this join a job id is a bearer token for
    someone else's translation: job ids are opaque, but they are also handed out
    freely and end up in logs, and the snapshot names the paper id, which then
    unlocks nothing on the paper endpoints but confirms the library membership
    those endpoints exist to hide. The join makes the ownership hop mandatory
    instead of something each caller has to remember.
    """
    with session_scope() as s:
        job = s.scalar(
            select(TranslationJob)
            .join(Paper, TranslationJob.paper_id == Paper.id)
            .where(TranslationJob.id == job_id, Paper.user_id == user_id)
        )
        if job is None:
            return None
        out = job_out(job)
    return out.model_dump(mode="json")


@router.post("/papers/{paper_id}/translate", response_model=JobOut, status_code=202)
async def start_translation(
    paper_id: str,
    request: Request,
    pages: str | None = None,
    user: User = Depends(current_user),
) -> JobOut:
    """Start translating a paper. ``pages`` (e.g. "1", "1-3,5") limits which pages;
    omit for the whole document.

    Translating is a write against the caller's own library and it consumes a
    shared engine slot, so an unowned paper 404s before any work is queued —
    otherwise a stranger's id would be enough to spend this instance's compute.

    A user who has turned whole-document translation off gets 409 and is told
    plainly that the setting is what refused, which is the opposite of how the
    404 above behaves and deliberately so. The 404 is a privacy measure: it hides
    whether a paper exists. This is not a privacy question at all — it is the
    caller's own preference, they can already read it from ``GET /auth/me``, and
    a client that cannot tell "not yours" from "you switched this off" would
    render a wrong error and give the user nothing to act on.

    The preference is checked *before* the paper is looked up. That ordering is
    safe precisely because the answer depends only on the caller's own account:
    the response is the same 409 for every paper id, including ids that do not
    exist and ids belonging to other people, so it carries no information about
    anyone's library and cannot be walked as an oracle. It also declines the work
    without opening a transaction or writing a job row.
    """
    if not pdf_translation_enabled(user):
        raise HTTPException(
            status_code=409,
            detail=(
                "Whole-document PDF translation is turned off for this account. "
                "Enable it in settings (PATCH /api/auth/me with "
                '{"pdf_translation": true}) to translate papers.'
            ),
        )

    settings = request.app.state.settings
    job_manager = request.app.state.job_manager
    blobs = request.app.state.blobs

    translator_type = settings.translator_config().type
    created = await asyncio.to_thread(_start_job_row, paper_id, user.id, translator_type, "zh")
    if created is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    job_id, sha256, source_lang = created

    source_pdf = blobs.path(sha256, "original")
    job_manager.submit(job_id, sha256, source_pdf, target_lang="zh", pages=pages)

    snapshot = await asyncio.to_thread(_job_snapshot, job_id, user.id)
    return JobOut.model_validate(snapshot)


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(job_id: str, user: User = Depends(current_user)) -> JobOut:
    snapshot = await asyncio.to_thread(_job_snapshot, job_id, user.id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobOut.model_validate(snapshot)


@router.get("/jobs/{job_id}/events")
async def job_events(
    job_id: str, request: Request, user: User = Depends(current_user)
) -> EventSourceResponse:
    """Live progress for one job.

    Note for clients: this requires the same ``Authorization: Bearer`` header as
    every other endpoint, and the browser's native ``EventSource`` cannot send
    one. Consumers must use a fetch-based SSE reader. The token deliberately does
    NOT fall back to a query parameter — a URL is logged by proxies, kept in
    history, and forwarded in ``Referer``, which is a poor place for a 14-day
    credential.
    """
    job_manager = request.app.state.job_manager

    # Subscribe BEFORE the snapshot so no event can slip through the gap. The
    # authorisation check has already happened in the dependency, so an
    # unauthorised caller never reaches the subscriber set — otherwise it could
    # hold a queue attached to a stranger's job and receive its progress events
    # even while the snapshot below refused it.
    queue = job_manager.subscribe(job_id)
    snapshot = await asyncio.to_thread(_job_snapshot, job_id, user.id)
    if snapshot is None:
        job_manager.unsubscribe(job_id, queue)
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_stream():
        try:
            yield {"event": "snapshot", "data": json.dumps(snapshot)}
            # Terminal already? send nothing more.
            if snapshot["status"] in ("done", "error"):
                return
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}  # keep the connection alive
                    continue
                yield {"event": ev.get("type", "message"), "data": json.dumps(ev)}
                if ev.get("type") == "end":
                    break
        finally:
            job_manager.unsubscribe(job_id, queue)

    return EventSourceResponse(event_stream())
