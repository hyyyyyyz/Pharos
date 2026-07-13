"""API response models and ORM -> schema converters."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from xuanzang.db.models import Paper, TranslationJob


class JobOut(BaseModel):
    id: str
    paper_id: str
    status: str
    stage: str
    progress: float
    translator_type: str
    target_lang: str
    error: str | None = None
    tokens: int | None = None
    total_seconds: float | None = None
    has_mono: bool = False
    has_dual: bool = False
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class PaperOut(BaseModel):
    id: str
    title: str
    orig_filename: str
    page_count: int | None = None
    source: str
    source_lang: str
    added_at: datetime
    latest_job: JobOut | None = None


def job_out(job: TranslationJob) -> JobOut:
    return JobOut(
        id=job.id,
        paper_id=job.paper_id,
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        translator_type=job.translator_type,
        target_lang=job.target_lang,
        error=job.error,
        tokens=job.tokens,
        total_seconds=job.total_seconds,
        has_mono=bool(job.mono_path),
        has_dual=bool(job.dual_path),
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def paper_out(paper: Paper) -> PaperOut:
    latest = paper.jobs[-1] if paper.jobs else None
    return PaperOut(
        id=paper.id,
        title=paper.title,
        orig_filename=paper.orig_filename,
        page_count=paper.page_count,
        source=paper.source,
        source_lang=paper.source_lang,
        added_at=paper.added_at,
        latest_job=job_out(latest) if latest is not None else None,
    )
