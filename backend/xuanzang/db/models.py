"""SQLAlchemy 2.x ORM models.

Only metadata lives in SQLite; the PDFs themselves are content-addressed on
disk (see :mod:`xuanzang.storage`). Highlight/Note/Chunk are stubs for future
reader annotations and RAG (created but unused in the MVP).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Paper(Base):
    __tablename__ = "papers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(512))
    orig_sha256: Mapped[str] = mapped_column(String(64), index=True)
    orig_filename: Mapped[str] = mapped_column(String(512))
    page_count: Mapped[int | None] = mapped_column(Integer, default=None)
    source: Mapped[str] = mapped_column(String(16), default="upload")  # upload | arxiv
    arxiv_id: Mapped[str | None] = mapped_column(String(32), default=None)
    source_lang: Mapped[str] = mapped_column(String(8), default="en")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    jobs: Mapped[list["TranslationJob"]] = relationship(
        back_populates="paper", cascade="all, delete-orphan", order_by="TranslationJob.created_at"
    )


class TranslationJob(Base):
    __tablename__ = "translation_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)

    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|error
    engine: Mapped[str] = mapped_column(String(32), default="babeldoc")
    translator_type: Mapped[str] = mapped_column(String(32), default="bing")
    target_lang: Mapped[str] = mapped_column(String(8), default="zh")

    progress: Mapped[float] = mapped_column(Float, default=0.0)  # 0-100
    stage: Mapped[str] = mapped_column(String(32), default="queued")

    mono_path: Mapped[str | None] = mapped_column(String(1024), default=None)
    dual_path: Mapped[str | None] = mapped_column(String(1024), default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    total_seconds: Mapped[float | None] = mapped_column(Float, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    paper: Mapped["Paper"] = relationship(back_populates="jobs")
