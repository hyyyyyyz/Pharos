"""SQLAlchemy 2.x ORM models.

Only metadata lives in SQLite; the PDFs themselves are content-addressed on
disk (see :mod:`pharos.storage`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


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
    #: Stored as ``fernet:v1:<ciphertext>`` when a stable credential secret is
    #: configured. SQLite does not enforce VARCHAR lengths, but 512 also keeps a
    #: fresh schema honest about the encrypted value's real upper bound.
    api_key: Mapped[str] = mapped_column(String(512))
    #: Zotero's library version, so syncs can be incremental rather than full.
    library_version: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="linked")  # linked|syncing|error
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ZoteroOAuthAttempt(Base):
    """One short-lived, one-use OAuth 1.0a handshake.

    Zotero redirects through a normal browser navigation, so the callback cannot
    carry Pharos's localStorage Bearer token. The random ``state`` and request
    token bind that callback to the authenticated user who started it. The token
    secret is itself encrypted because it can complete the exchange while live.
    """

    __tablename__ = "zotero_oauth_attempts"

    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    #: SHA-256 hex digests only. The callback supplies both original values;
    #: keeping their hashes is enough to bind it without leaving replay material
    #: in a database backup.
    request_token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    browser_state_hash: Mapped[str] = mapped_column(String(64))
    request_token_secret: Mapped[str] = mapped_column(Text)
    #: ``NULL`` is treated as ``browser`` for rows created before desktop OAuth
    #: existed. New rows always write either ``browser`` or ``desktop``.
    flow_kind: Mapped[str | None] = mapped_column(String(16), default=None)
    #: Desktop OAuth completes in the system browser, then hands a one-use code
    #: back to the app. Only hashes of that code and of the app-held binding
    #: secret are stored; the temporary Zotero key stays encrypted at rest.
    handoff_code_hash: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, default=None
    )
    handoff_zotero_user_id: Mapped[str | None] = mapped_column(String(32), default=None)
    handoff_api_key: Mapped[str | None] = mapped_column(Text, default=None)
    handoff_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True
    )
    handoff_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


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
    #: full-text search (and, later, retrieval for paper AI chat). Kept on the row
    #: rather than re-parsed per query because parsing a large PDF takes seconds.
    #: NULL means extraction never ran or found nothing (a scan, say) — such a
    #: paper is simply not full-text searchable, which is honest.
    full_text: Mapped[str | None] = mapped_column(Text, default=None)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    jobs: Mapped[list[TranslationJob]] = relationship(
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
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
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

    paper: Mapped[Paper] = relationship(back_populates="jobs")


# ---------------------------------------------------------------- organisation


class Collection(Base):
    """A user's folder in the category tree. Nestable, like Zotero's.

    Owned per user rather than globally: two researchers may both have a
    "生成模型" folder and they are unrelated.
    """

    __tablename__ = "collections"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
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
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    #: Optional accent for the chip, e.g. "amber". NULL = the neutral chip.
    color: Mapped[str | None] = mapped_column(String(16), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PaperTag(Base):
    __tablename__ = "paper_tags"

    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[str] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)


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
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
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
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


# --------------------------------------------------------------- paper AI chat


class AiProviderPreference(Base):
    """One user's optional OpenAI-compatible provider override.

    The API key is encrypted before it reaches this row and is never returned to
    a client.  When no row exists the web client uses the instance-wide chat
    provider from :class:`pharos.config.Settings`, so an operator can fund the
    service centrally while advanced users may still bring their own endpoint.
    """

    __tablename__ = "ai_provider_preferences"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    base_url: Mapped[str] = mapped_column(String(1024))
    model: Mapped[str] = mapped_column(String(200))
    api_key: Mapped[str] = mapped_column(Text)
    temperature: Mapped[float] = mapped_column(Float, default=0.25)
    max_output_tokens: Mapped[int] = mapped_column(Integer, default=4096)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class PaperAiContext(Base):
    """Durable, user-scoped understanding profile for one uploaded paper."""

    __tablename__ = "paper_ai_contexts"
    __table_args__ = (
        UniqueConstraint("user_id", "paper_id", name="uq_paper_ai_context_owner_paper"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str | None] = mapped_column(Text, default=None)
    page_count: Mapped[int | None] = mapped_column(Integer, default=None)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    #: indexed | understanding | ready.  A failed model call returns to indexed
    #: and records ``error`` so the raw text remains usable for a later retry.
    status: Mapped[str] = mapped_column(String(24), default="indexed", index=True)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class AiConversation(Base):
    """One persistent conversation, isolated to an owner and a paper."""

    __tablename__ = "ai_conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(256), default="论文对话")
    source: Mapped[str] = mapped_column(String(32), default="pharos")
    source_session_id: Mapped[str | None] = mapped_column(String(256), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class AiMessage(Base):
    """One visible user/assistant turn inside an :class:`AiConversation`."""

    __tablename__ = "ai_messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("ai_conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(200), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# --------------------------------------------------------------- research lab


class ResearchProject(Base):
    """A user's durable research workspace.

    The stage is deliberately stored separately from ``status``. ``status`` is
    lifecycle state (active/archived); ``stage`` is where the work currently is
    in the evidence-to-publication workflow and may move backwards when a
    hypothesis is revised.
    """

    __tablename__ = "research_projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    research_question: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    stage: Mapped[str] = mapped_column(String(32), default="discovery", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    searches: Mapped[list[LiteratureSearch]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    sources: Mapped[list[ProjectSource]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list[ProjectArtifact]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class LiteratureSearch(Base):
    """One persisted multi-provider literature search and its outcome."""

    __tablename__ = "literature_searches"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"), index=True, default=None
    )
    query: Mapped[str] = mapped_column(String(500))
    #: JSON array of provider names requested for this run.
    sources: Mapped[str] = mapped_column(Text, default="[]")
    #: running | complete | partial | error
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    #: JSON object mapping a failed source name to a human-readable reason.
    errors: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    project: Mapped[ResearchProject | None] = relationship(back_populates="searches")
    results: Mapped[list[LiteratureResult]] = relationship(
        back_populates="search", cascade="all, delete-orphan", order_by="LiteratureResult.rank"
    )


class LiteratureResult(Base):
    """A canonical paper result, deduplicated across the providers in one search."""

    __tablename__ = "literature_results"
    __table_args__ = (
        UniqueConstraint("search_id", "dedup_key", name="uq_literature_result_search_key"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    search_id: Mapped[str] = mapped_column(
        ForeignKey("literature_searches.id", ondelete="CASCADE"), index=True
    )
    dedup_key: Mapped[str] = mapped_column(String(768))
    title: Mapped[str] = mapped_column(Text)
    #: JSON array; author names may themselves contain punctuation.
    authors: Mapped[str] = mapped_column(Text, default="[]")
    abstract: Mapped[str] = mapped_column(Text, default="")
    year: Mapped[int | None] = mapped_column(Integer, default=None)
    venue: Mapped[str | None] = mapped_column(String(512), default=None)
    doi: Mapped[str | None] = mapped_column(String(256), default=None)
    url: Mapped[str | None] = mapped_column(String(1024), default=None)
    pdf_url: Mapped[str | None] = mapped_column(String(1024), default=None)
    #: JSON array of every provider that returned this canonical result.
    sources: Mapped[str] = mapped_column(Text, default="[]")
    #: JSON object, e.g. {"arxiv": "2401.01234", "openalex": "W123"}.
    source_ids: Mapped[str] = mapped_column(Text, default="{}")
    citation_count: Mapped[int | None] = mapped_column(Integer, default=None)
    rank: Mapped[int] = mapped_column(Integer, default=0)

    #: ``rules`` until an explicitly configured LLM analysis replaces it.
    analysis_mode: Mapped[str] = mapped_column(String(16), default="rules")
    analysis_model: Mapped[str | None] = mapped_column(String(128), default=None)
    analysis_warning: Mapped[str | None] = mapped_column(Text, default=None)
    summary_zh: Mapped[str] = mapped_column(Text, default="")
    contribution: Mapped[str] = mapped_column(Text, default="")
    core_trick: Mapped[str] = mapped_column(Text, default="")
    method: Mapped[str] = mapped_column(Text, default="")
    results: Mapped[str] = mapped_column(Text, default="")
    limitations: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    search: Mapped[LiteratureSearch] = relationship(back_populates="results")
    project_sources: Mapped[list[ProjectSource]] = relationship(
        back_populates="result", cascade="all, delete-orphan"
    )


class ProjectSource(Base):
    """A saved literature result inside a project.

    ``user_id`` is intentionally duplicated from the project and search. It
    lets every lookup carry an owner predicate directly rather than trusting a
    multi-hop relationship to remain consistent.
    """

    __tablename__ = "project_sources"
    __table_args__ = (UniqueConstraint("project_id", "result_id", name="uq_project_source_result"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"), index=True
    )
    result_id: Mapped[str] = mapped_column(
        ForeignKey("literature_results.id", ondelete="CASCADE"), index=True
    )
    #: The library paper this source turned out to be, once the user actually has
    #: the PDF. NULL is the normal state and means abstract-only: a discovery
    #: result carries a title and an abstract, nothing a page number could point
    #: into. Setting it is what upgrades every piece of evidence drawn from this
    #: source from ``abstract_only`` to a real page — see :class:`Evidence`.
    #:
    #: SET NULL rather than CASCADE: deleting a paper from the library should
    #: cost the project its page-level anchoring, not the source and the
    #: researcher's note about why it was included.
    paper_id: Mapped[str | None] = mapped_column(
        ForeignKey("papers.id", ondelete="SET NULL"), index=True, default=None
    )
    note: Mapped[str | None] = mapped_column(Text, default=None)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    project: Mapped[ResearchProject] = relationship(back_populates="sources")
    result: Mapped[LiteratureResult] = relationship(back_populates="project_sources")


class ProjectArtifact(Base):
    """A user-authored research record at one workflow stage.

    These rows are records, not claims that an autonomous system ran an
    experiment. A ``result`` artifact is unverified until a person deliberately
    changes its status to ``verified`` and attaches whatever evidence they rely
    on in the body.
    """

    __tablename__ = "project_artifacts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(32), index=True)
    #: hypothesis | experiment_plan | result | claim | draft | review
    type: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(512))
    body: Mapped[str] = mapped_column(Text, default="")
    #: draft | ready | verified | rejected
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    project: Mapped[ResearchProject] = relationship(back_populates="artifacts")


# ------------------------------------------------------------------- evidence


class PaperChunk(Base):
    """One page of a paper's text, addressable by page number.

    This is the substrate under every citation Pharos will ever make. A claim is
    only checkable if a reader can be sent to the page it came from, and
    ``Paper.full_text`` -- one flat run of text with the page breaks normalised
    away -- cannot do that. Chunks keep the boundary the flat text throws out.

    **One page, one chunk.** Not a fixed token window: a window boundary is an
    artefact of the chunker and means nothing to a reader, whereas "page 7" is
    something they can act on. ``ordinal`` exists so a page can later be split
    into several chunks (a two-column layout, or a page too long for a model's
    context) without changing the page contract or the primary key.

    ``char_start``/``char_end`` are this chunk's span inside the paper's
    ``full_text``, which is exact because both are produced by the same
    extraction pass. They are NULL when the alignment is unknown -- ``full_text``
    is capped, so a long document's later pages exist as chunks but have no
    corresponding span in the truncated flat text. NULL says "we do not know"
    rather than pointing at the wrong characters.

    Rows are written once at ingestion and never edited. ``extraction_version``
    records which extractor produced them, so a later improvement can find and
    replace its predecessor's output instead of guessing whether a re-run is
    needed.
    """

    __tablename__ = "paper_chunks"
    __table_args__ = (
        UniqueConstraint("paper_id", "page_no", "ordinal", name="uq_paper_chunk_page"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    #: Duplicated from the paper, as everywhere else in this schema, so every
    #: lookup can carry an owner predicate directly rather than trusting a join
    #: to stay consistent.
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    #: 1-based, matching :class:`Highlight` and matching what a PDF reader shows.
    page_no: Mapped[int] = mapped_column(Integer)
    #: Position within the page. 0 while one page is one chunk.
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    char_start: Mapped[int | None] = mapped_column(Integer, default=None)
    char_end: Mapped[int | None] = mapped_column(Integer, default=None)
    #: Bumped when the extraction changes shape, not when its output happens to
    #: differ. Lets a backfill target exactly the rows an older version wrote.
    extraction_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Evidence(Base):
    """One statement, anchored to where it came from.

    The chain the research contract requires (``docs/RESEARCH_WORKFLOW.md`` §8)
    is claim → evidence statement → paper identity → page + section → exact
    quote. This row is the middle of it: it names a paper, says as precisely as
    it honestly can *where* in that paper, and records what kind of thing the
    text is.

    Two columns carry the weight, and they answer different questions.

    ``kind`` -- who wrote this text: a verbatim ``quote`` from the paper, a
    human's ``note``, a deterministic ``rule_summary``, or a
    ``model_inference``. Collapsing these was the failure the contract was
    written against: a model's paraphrase rendered like a quotation is a
    fabricated citation, and the difference is invisible once both are grey text
    in a panel.

    ``locator`` -- how precisely it is placed. ``page`` means ``page_no`` is a
    real page from a real extraction. ``abstract_only`` means the paper's full
    text was never available (a discovery result, a metadata-only import) and the
    statement rests on the abstract. ``unlocated`` means the text is genuine but
    its position is not known.

    The check constraint is the contract's "页面未知时不能生成看似精确的页码"
    made structural: ``page_no`` may only be non-NULL when ``locator`` is
    ``page``. A plausible-looking page number is worse than no page number,
    because it survives review -- a reader who turns to page 7 and finds nothing
    has lost their trust in every other citation too. Enforcing it in the schema
    rather than in a service means no future write path can bypass it.
    """

    __tablename__ = "evidence"
    __table_args__ = (
        CheckConstraint(
            "(locator = 'page' AND page_no IS NOT NULL) "
            "OR (locator != 'page' AND page_no IS NULL)",
            name="ck_evidence_page_requires_page_locator",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    #: The paper identity in the chain. Required: evidence that cannot name its
    #: source is not evidence.
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    #: Optional home. Evidence can be gathered while reading, before it belongs
    #: to any project.
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"), index=True, default=None
    )
    #: The chunk this was taken from, when it was taken from one. SET NULL rather
    #: than CASCADE: re-running extraction replaces chunks, and evidence must
    #: outlive that -- it keeps its own copy of the quote and its page number.
    chunk_id: Mapped[str | None] = mapped_column(
        ForeignKey("paper_chunks.id", ondelete="SET NULL"), default=None
    )
    #: "quote" | "note" | "rule_summary" | "model_inference"
    kind: Mapped[str] = mapped_column(String(16))
    #: "page" | "abstract_only" | "unlocated"
    locator: Mapped[str] = mapped_column(String(16))
    #: 1-based. NULL unless ``locator`` is "page" -- see the check constraint.
    page_no: Mapped[int | None] = mapped_column(Integer, default=None)
    #: JSON array of {x, y, w, h} in PDF user-space units at scale 1, the same
    #: convention as :class:`Highlight`, so a region can be drawn in the reader.
    #: NULL when the extraction gave text without geometry, which is the normal
    #: case -- an invented rectangle is the same lie as an invented page.
    rects: Mapped[str | None] = mapped_column(Text, default=None)
    #: The text itself: verbatim when ``kind`` is "quote", authored otherwise.
    text: Mapped[str] = mapped_column(Text)
    #: What this evidence is being offered as support for. Optional, because
    #: evidence is often collected before the claim it will serve exists.
    statement: Mapped[str | None] = mapped_column(Text, default=None)

    # --- provenance -------------------------------------------------------
    # Required by the contract for every automated product. All NULL for
    # human-authored evidence, which is itself the record that a person wrote it.
    provider: Mapped[str | None] = mapped_column(String(32), default=None)
    model: Mapped[str | None] = mapped_column(String(64), default=None)
    #: Schema/workflow version of the process that produced this row.
    workflow_version: Mapped[str | None] = mapped_column(String(16), default=None)
    #: Hash of the exact input the producer saw, so a later reader can tell
    #: whether the source has changed underneath the conclusion.
    input_sha256: Mapped[str | None] = mapped_column(String(64), default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
