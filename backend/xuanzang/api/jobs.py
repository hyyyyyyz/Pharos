"""Jobs API: start a translation, poll a job, and stream live progress (SSE)."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from xuanzang.api.schemas import JobOut, job_out
from xuanzang.db.models import Paper, TranslationJob
from xuanzang.db.session import session_scope
from xuanzang.services.translation import create_job

router = APIRouter(prefix="/api", tags=["jobs"])

_HEARTBEAT_SECONDS = 15


def _start_job_row(paper_id: str, translator_type: str, target_lang: str) -> tuple[str, str, str] | None:
    """Create a queued job for a paper. Returns (job_id, sha256, original_pdf_path)."""
    with session_scope() as s:
        paper = s.get(Paper, paper_id)
        if paper is None:
            return None
        job = create_job(s, paper, translator_type=translator_type, target_lang=target_lang)
        s.flush()
        return job.id, paper.orig_sha256, paper.source_lang


def _job_snapshot(job_id: str) -> dict | None:
    with session_scope() as s:
        job = s.get(TranslationJob, job_id)
        if job is None:
            return None
        out = job_out(job)
    return out.model_dump(mode="json")


@router.post("/papers/{paper_id}/translate", response_model=JobOut, status_code=202)
async def start_translation(paper_id: str, request: Request, pages: str | None = None) -> JobOut:
    """Start translating a paper. ``pages`` (e.g. "1", "1-3,5") limits which pages;
    omit for the whole document."""
    settings = request.app.state.settings
    job_manager = request.app.state.job_manager
    blobs = request.app.state.blobs

    translator_type = settings.translator_config().type
    created = await asyncio.to_thread(_start_job_row, paper_id, translator_type, "zh")
    if created is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    job_id, sha256, source_lang = created

    source_pdf = blobs.path(sha256, "original")
    job_manager.submit(job_id, sha256, source_pdf, target_lang="zh", pages=pages)

    snapshot = await asyncio.to_thread(_job_snapshot, job_id)
    return JobOut.model_validate(snapshot)


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(job_id: str) -> JobOut:
    snapshot = await asyncio.to_thread(_job_snapshot, job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobOut.model_validate(snapshot)


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request) -> EventSourceResponse:
    job_manager = request.app.state.job_manager

    # Subscribe BEFORE the snapshot so no event can slip through the gap.
    queue = job_manager.subscribe(job_id)
    snapshot = await asyncio.to_thread(_job_snapshot, job_id)
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
