"""Translation service — job creation, the async worker pool, and the live event bus.

A translation takes minutes, so the HTTP layer never runs it inline. Instead:
  * ``create_job`` inserts a ``queued`` TranslationJob and returns immediately.
  * ``JobManager.submit`` starts a bounded asyncio task that drives the engine,
    persists progress to SQLite (so a browser refresh can re-attach), and
    publishes events to any subscribed SSE clients.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from pharos.db.models import Paper, TranslationJob
from pharos.db.session import session_scope
from pharos.engines.base import (
    EngineError,
    TranslationEngine,
    TranslationProgress,
    TranslationRequest,
    TranslationResult,
)
from pharos.storage.blobs import BlobStore

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_job(session: Session, paper: Paper, translator_type: str, target_lang: str = "zh") -> TranslationJob:
    job = TranslationJob(
        paper_id=paper.id,
        translator_type=translator_type,
        target_lang=target_lang,
        status="queued",
        stage="queued",
    )
    session.add(job)
    session.flush()
    return job


class JobManager:
    """Owns the running translation tasks and per-job subscriber queues."""

    def __init__(self, engine: TranslationEngine, blobs: BlobStore, max_concurrent: int = 2) -> None:
        self._engine = engine
        self._blobs = blobs
        self._sem = asyncio.Semaphore(max_concurrent)
        self._subscribers: dict[str, set[asyncio.Queue[dict]]] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    # ------------------------------ pub/sub ------------------------------

    def subscribe(self, job_id: str) -> asyncio.Queue[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue()
        self._subscribers.setdefault(job_id, set()).add(q)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue[dict]) -> None:
        subs = self._subscribers.get(job_id)
        if subs:
            subs.discard(q)
            if not subs:
                self._subscribers.pop(job_id, None)

    def _publish(self, job_id: str, event: dict) -> None:
        for q in list(self._subscribers.get(job_id, ())):
            q.put_nowait(event)

    def is_active(self, job_id: str) -> bool:
        task = self._tasks.get(job_id)
        return task is not None and not task.done()

    # ------------------------------ running ------------------------------

    def submit(self, job_id: str, sha256: str, source_pdf: Path, target_lang: str, pages: str | None = None) -> None:
        task = asyncio.create_task(self._run(job_id, sha256, source_pdf, target_lang, pages))
        self._tasks[job_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(job_id, None))

    async def _run(self, job_id: str, sha256: str, source_pdf: Path, target_lang: str, pages: str | None) -> None:
        async with self._sem:
            await asyncio.to_thread(self._db_running, job_id)
            self._publish(job_id, {"type": "status", "status": "running", "percent": 0.0})

            request = TranslationRequest(
                source_pdf=source_pdf,
                output_dir=self._blobs.work_dir(sha256),
                target_lang=target_lang,
                pages=pages,
            )
            last_persisted = -10.0
            try:
                async for ev in self._engine.translate(request):
                    if isinstance(ev, TranslationProgress):
                        self._publish(
                            job_id,
                            {
                                "type": "progress",
                                "percent": ev.percent,
                                "stage": ev.stage.value,
                                "message": ev.message,
                            },
                        )
                        if ev.percent - last_persisted >= 2.0:
                            await asyncio.to_thread(self._db_progress, job_id, ev.percent, ev.stage.value)
                            last_persisted = ev.percent
                    elif isinstance(ev, TranslationResult):
                        mono, dual = await asyncio.to_thread(self._adopt_and_finish, job_id, sha256, ev)
                        self._publish(
                            job_id,
                            {"type": "done", "percent": 100.0, "mono": mono, "dual": dual},
                        )
            except EngineError as e:
                log.warning("job %s failed: %s", job_id, e)
                await asyncio.to_thread(self._db_error, job_id, str(e), e.details)
                self._publish(job_id, {"type": "error", "error": str(e)})
            except Exception as e:  # noqa: BLE001 — surface any failure to the client
                log.exception("job %s crashed", job_id)
                await asyncio.to_thread(self._db_error, job_id, str(e), "")
                self._publish(job_id, {"type": "error", "error": str(e)})
            finally:
                self._publish(job_id, {"type": "end"})

    # --------------------------- DB writers (sync) ---------------------------

    def _db_running(self, job_id: str) -> None:
        with session_scope() as s:
            job = s.get(TranslationJob, job_id)
            if job:
                job.status = "running"
                job.stage = "parsing"
                job.started_at = _now()

    def _db_progress(self, job_id: str, percent: float, stage: str) -> None:
        with session_scope() as s:
            job = s.get(TranslationJob, job_id)
            if job:
                job.progress = percent
                job.stage = stage

    def _adopt_and_finish(self, job_id: str, sha256: str, result: TranslationResult) -> tuple[bool, bool]:
        mono = dual = False
        if result.mono_pdf:
            self._blobs.adopt_output(sha256, "mono", result.mono_pdf)
            mono = True
        if result.dual_pdf:
            self._blobs.adopt_output(sha256, "dual", result.dual_pdf)
            dual = True
        with session_scope() as s:
            job = s.get(TranslationJob, job_id)
            if job:
                job.status = "done"
                job.stage = "done"
                job.progress = 100.0
                job.mono_path = str(self._blobs.path(sha256, "mono")) if mono else None
                job.dual_path = str(self._blobs.path(sha256, "dual")) if dual else None
                job.total_seconds = result.total_seconds
                job.tokens = result.tokens
                job.finished_at = _now()
        return mono, dual

    def _db_error(self, job_id: str, message: str, details: str) -> None:
        with session_scope() as s:
            job = s.get(TranslationJob, job_id)
            if job:
                job.status = "error"
                job.stage = "error"
                job.error = (message + ("\n\n" + details if details else ""))[:4000]
                job.finished_at = _now()
