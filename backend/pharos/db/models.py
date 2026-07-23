"""SQLAlchemy 2.x ORM models.

Only metadata lives in SQLite; the PDFs themselves are content-addressed on
disk (see :mod:`pharos.storage`). Highlight/Note/Chunk are stubs for future
reader annotations and RAG (created but unused in the MVP).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    """A Pharos account.

    Deliberately separate from any Zotero identity: Zotero is a *source* a user
    may optionally link (see :class:`ZoteroLink`), not the way they sign in.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    #: Stored casefolded so lookups are unambiguous and no two accounts differ
    #: only by capitalisation.
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    #: argon2id digest. The plaintext password is never stored or logged.
    password_hash: Mapped[str] = mapped_column(String(256))
    display_name: Mapped[str | None] = mapped_column(String(128), default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    #: Marks an operator account. The flag exists and is honoured wherever an
    #: admin-only capability is later added, but on its own it grants NOTHING —
    #: there are no admin-gated endpoints yet, so an admin is currently an
    #: ordinary user who happens to carry this bit. Kept deliberately minimal
    #: (a boolean, not a role enum) until there is a real capability to gate.
    #: ``server_default`` is required because this column is added to the
    #: already-created ``users`` table by the additive migration, which refuses
    #: a NOT NULL column with no SQL-level default. The other booleans predate
    #: accounts and were created in place, so they never needed one.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    #: Bumped to invalidate every outstanding token for this user (logout-all,
    #: password change). Tokens carry it and are rejected when it no longer matches.
    token_epoch: Mapped[int] = mapped_column(Integer, default=0)

    #: Whether whole-document PDF translation is offered to this user.
    #:
    #: Rebuilding a paper's layout in Chinese is slow and spends API budget, and
    #: plenty of readers would rather keep the original in front of them and ask
    #: about a paragraph when they get stuck. Turning this off hides the whole
    #: apparatus — the translate action, the 未译/翻译中/已译 status column, and
    #: the 中文/中英 reading modes — instead of leaving dead controls on screen.
    #: Default on, because it is the feature the product was built around.
    pdf_translation: Mapped[bool] = mapped_column(Boolean, default=True)


class ZoteroLink(Base):
    """A user's optional Zotero Web API credentials.

    One per user. The API key is a bearer secret for *their* Zotero library, so
    it is write-only from the client's perspective: it goes in over HTTPS and is
    never included in any API response.
    """

    __tablename__ = "zotero_links"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    #: Numeric Zotero user id (their "userID" in Zotero's settings).
    zotero_user_id: Mapped[str] = mapped_column(String(32))
    api_key: Mapped[str] = mapped_column(String(128))
    #: Zotero's library version, so syncs can be incremental rather than full.
    library_version: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="linked")  # linked|syncing|error
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Paper(Base):
    __tablename__ = "papers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    #: Owner. Nullable ONLY so the additive migration can run against a database
    #: written before accounts existed; those legacy rows are claimed by the
    #: first account created. Every row written from now on has an owner, and
    #: every query MUST filter on it — an unfiltered query is a data leak.
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, default=None
    )
    #: Zotero item key when this paper came from a linked library, so repeat
    #: syncs update rather than duplicate.
    zotero_key: Mapped[str | None] = mapped_column(String(32), index=True, default=None)
    title: Mapped[str] = mapped_column(String(512))
    orig_sha256: Mapped[str] = mapped_column(String(64), index=True)
    orig_filename: Mapped[str] = mapped_column(String(512))
    page_count: Mapped[int | None] = mapped_column(Integer, default=None)
    source: Mapped[str] = mapped_column(String(16), default="upload")  # upload | arxiv
    arxiv_id: Mapped[str | None] = mapped_column(String(32), default=None)
    source_lang: Mapped[str] = mapped_column(String(8), default="en")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    # --- bibliographic metadata -------------------------------------------
    # Extracted from the PDF on upload, then optionally corrected against
    # CrossRef/arXiv. Every field stays nullable: a scanned or unusual paper
    # may yield nothing, and the UI renders "—" rather than a guess.
    authors: Mapped[str | None] = mapped_column(Text, default=None)  # "A. Vaswani; N. Shazeer"
    year: Mapped[int | None] = mapped_column(Integer, default=None)
    venue: Mapped[str | None] = mapped_column(String(256), default=None)
    doi: Mapped[str | None] = mapped_column(String(128), index=True, default=None)
    abstract: Mapped[str | None] = mapped_column(Text, default=None)
    # Where the metadata came from: "pdf" | "crossref" | "arxiv" | "manual".
    meta_source: Mapped[str | None] = mapped_column(String(16), default=None)
    meta_extracted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    #: Plain text of the original PDF, extracted once on upload and used to feed
    #: full-text search (and, later, retrieval for the 领航 chat). Kept on the row
    #: rather than re-parsed per query because parsing a large PDF takes seconds.
    #: NULL means extraction never ran or found nothing (a scan, say) — such a
    #: paper is simply not full-text searchable, which is honest.
    full_text: Mapped[str | None] = mapped_column(Text, default=None)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    jobs: Mapped[list["TranslationJob"]] = relationship(
        back_populates="paper", cascade="all, delete-orphan", order_by="TranslationJob.created_at"
    )


class DailyPaper(Base):
    """One arXiv paper surfaced by the daily sweep.

    The fetched half (title/abstract/categories/domain match) is always
    present. The *read* half — Chinese summary, highlights, scores — is filled
    in by an LLM afterwards and stays NULL until then, so the module is fully
    usable before any API key exists: papers still arrive, they are just
    marked ``pending`` instead of pretending to be understood.
    """

    __tablename__ = "daily_papers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    #: Version-stripped arXiv id, e.g. "2607.08448".
    arxiv_id: Mapped[str] = mapped_column(String(32), index=True)
    #: Announcement date this paper belongs to, "YYYY-MM-DD".
    date: Mapped[str] = mapped_column(String(10), index=True)

    title: Mapped[str] = mapped_column(Text)
    authors: Mapped[str | None] = mapped_column(Text, default=None)  # "; "-joined
    abstract: Mapped[str | None] = mapped_column(Text, default=None)  # original English
    categories: Mapped[str | None] = mapped_column(String(256), default=None)  # comma-joined
    matched_domain: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    matched_keywords: Mapped[str | None] = mapped_column(Text, default=None)  # comma-joined
    arxiv_url: Mapped[str | None] = mapped_column(String(512), default=None)
    pdf_url: Mapped[str | None] = mapped_column(String(512), default=None)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    venue: Mapped[str | None] = mapped_column(String(128), default=None)

    # --- LLM reading layer (NULL until a provider is configured and runs) ---
    #: "pending" | "done" | "error"
    read_status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    summary_zh: Mapped[str | None] = mapped_column(Text, default=None)
    #: JSON object: {contribution, innovation, method, results}
    highlights: Mapped[str | None] = mapped_column(Text, default=None)
    #: JSON object: {relevance, recency, popularity, quality, recommendation}
    scores: Mapped[str | None] = mapped_column(Text, default=None)
    #: Denormalised from ``scores`` purely so the list can ORDER BY it.
    score_recommendation: Mapped[float | None] = mapped_column(Float, default=None)
    read_model: Mapped[str | None] = mapped_column(String(64), default=None)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    read_error: Mapped[str | None] = mapped_column(Text, default=None)

    #: Set once the user pulls this paper into their 文库.
    imported_paper_id: Mapped[str | None] = mapped_column(String(32), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class UserDirection(Base):
    """One research direction a user follows in 每日论文.

    Replaces the hard-coded global ``DIRECTIONS`` table. Each user keeps their
    own list, seeded from the defaults on first use so the module works before
    anyone configures anything.

    The daily sweep stays GLOBAL — one arXiv fetch and one LLM reading serve
    every user, because a paper's summary and key points are facts about the
    paper, not about who is reading it. What is per-user is *matching*: which
    papers you see, which direction they land under, and how relevant they are
    to you. That moves from ingest time to query time, which also means editing
    a direction re-ranks your feed immediately without re-reading anything.
    """

    __tablename__ = "user_directions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(64))
    #: Newline-separated, lower-cased match terms. A paper matches the direction
    #: when any term appears in its title or abstract.
    keywords: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    #: Display order, and the tie-break when a paper matches several directions.
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class UserDailyConfig(Base):
    """A user's 每日论文 settings beyond their direction list."""

    __tablename__ = "user_daily_configs"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    #: Comma-joined arXiv categories to follow, e.g. "cs.RO,cs.CV". The sweep
    #: fetches the UNION across all users, so adding one here widens the shared
    #: net rather than starting a private crawl.
    categories: Mapped[str] = mapped_column(String(256), default="")
    #: Cap on how many papers a day this user wants surfaced.
    max_per_day: Mapped[int] = mapped_column(Integer, default=40)
    #: False hides the module's contents without deleting the configuration.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    #: Set once the defaults have been copied in, so a user who deliberately
    #: deletes every direction is not handed them back on the next request.
    seeded: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class DailyRun(Base):
    """One execution of the daily sweep, for observability and idempotency."""

    __tablename__ = "daily_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    date: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|done|error
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    read_done: Mapped[int] = mapped_column(Integer, default=0)
    read_failed: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


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


# ---------------------------------------------------------------- organisation


class Collection(Base):
    """A user's folder in the category tree. Nestable, like Zotero's.

    Owned per user rather than globally: two researchers may both have a
    "生成模型" folder and they are unrelated.
    """

    __tablename__ = "collections"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(256))
    #: Self-reference for nesting. NULL = a top-level folder.
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), index=True, default=None
    )
    #: Set when this folder mirrors a Zotero collection, so a re-sync updates it
    #: rather than creating a duplicate.
    zotero_key: Mapped[str | None] = mapped_column(String(32), index=True, default=None)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PaperCollection(Base):
    """Membership. A paper can sit in several folders, as in Zotero."""

    __tablename__ = "paper_collections"

    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )
    collection_id: Mapped[str] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True
    )
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Tag(Base):
    """A free-form label, scoped to one user."""

    __tablename__ = "tags"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    #: Optional accent for the chip, e.g. "amber". NULL = the neutral chip.
    color: Mapped[str | None] = mapped_column(String(16), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PaperTag(Base):
    __tablename__ = "paper_tags"

    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[str] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )


# ------------------------------------------------------------------ annotation


class Highlight(Base):
    """A marked passage in one rendition of a paper.

    ``rects`` are stored in PDF user-space units at scale 1, NOT in screen
    pixels: the reader is zoomable, so anything captured in device pixels would
    land in the wrong place at any other zoom level or on any other screen.

    ``kind`` matters because the three renditions are different documents — a
    highlight drawn on the Chinese rebuild has no meaningful position in the
    English original, so each is anchored to the rendition it was made on.
    """

    __tablename__ = "highlights"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), index=True
    )
    #: "original" | "mono" | "dual"
    kind: Mapped[str] = mapped_column(String(16), default="original")
    #: 1-based page number within that rendition.
    page: Mapped[int] = mapped_column(Integer)
    #: JSON array of {x, y, w, h} in PDF points, one per line of the selection.
    rects: Mapped[str] = mapped_column(Text)
    #: The selected text, kept so the highlight is searchable and still readable
    #: if the PDF is ever re-rendered.
    text: Mapped[str | None] = mapped_column(Text, default=None)
    color: Mapped[str] = mapped_column(String(16), default="amber")
    #: The user's comment on this passage, if any.
    note: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Note(Base):
    """A paper-level note — the 笔记 block in the detail panel.

    Separate from :class:`Highlight`'s ``note``, which is anchored to a passage;
    this one is about the paper as a whole.
    """

    __tablename__ = "notes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), index=True
    )
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
