"""API response models and ORM -> schema converters."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import overload

from pydantic import BaseModel

from pharos.db.models import Paper, TranslationJob


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

    # Bibliographic metadata; None whenever extraction could not determine it.
    # Clients must render a placeholder rather than inventing a value.
    authors: list[str] = []
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    abstract: str | None = None
    meta_source: str | None = None


@overload
def as_utc(value: datetime) -> datetime: ...


@overload
def as_utc(value: None) -> None: ...


def as_utc(value: datetime | None) -> datetime | None:
    """Force a UTC offset onto a timestamp before it goes over the wire.

    SQLite has no timezone type, so a value SQLAlchemy just wrote comes back
    aware while the same value re-read on the next request comes back naive.
    Serialised, those differ only by a trailing "Z" — and a client parsing the
    naive form as local time would render the timestamp hours off, then watch it
    jump on the next refresh. Every datetime this app stores is UTC, so stamping
    it is a restatement of fact.
    """
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def split_authors(raw: str | None) -> list[str]:
    """Authors are stored as one ``"; "``-joined string; expose them as a list."""
    if not raw:
        return []
    return [a.strip() for a in raw.split(";") if a.strip()]


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
        created_at=as_utc(job.created_at),
        started_at=as_utc(job.started_at),
        finished_at=as_utc(job.finished_at),
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
        added_at=as_utc(paper.added_at),
        latest_job=job_out(latest) if latest is not None else None,
        authors=split_authors(paper.authors),
        year=paper.year,
        venue=paper.venue,
        doi=paper.doi,
        abstract=paper.abstract,
        meta_source=paper.meta_source,
    )
