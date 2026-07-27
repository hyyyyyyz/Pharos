"""Portable, versioned backups for the 每日论文 module.

The database is the online working copy; a Daily Vault is the user-owned,
portable copy.  The DTOs in this module deliberately contain no database ids,
account ids, library ids, access tokens, or provider credentials.  A vault can
therefore be opened on another device and imported into whichever Pharos
account is currently authenticated without smuggling ownership across systems.

The filesystem layout is implemented by the clients (browser File System
Access API and Tauri).  The backend deals only in a stable archive DTO: clients
split it into ``profile/profile.json`` and one immutable snapshot per day, then
reassemble the same DTO for restore.  Keeping the wire and directory contracts
separate lets browsers upload a JSON fallback while every platform shares the
same validation and merge semantics here.
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.parse
from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from pharos.daily import service, user_directions
from pharos.db.models import DailyPaper, UserDailyConfig, UserDirection

__all__ = [
    "DailyVaultArchive",
    "DailyVaultImportResult",
    "build_archive",
    "import_archive",
]

_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
_MAX_DAYS = 3_660
_MAX_PAPERS_PER_DAY = 500
_MAX_ARCHIVE_PAPERS = 50_000

ShortText = Annotated[str, Field(max_length=512)]
LongText = Annotated[str, Field(max_length=100_000)]
Score = Annotated[float, Field(ge=0.0, le=10.0)]
DailyReadStatus = Literal["pending", "done", "error"]
DailyRunStatus = Literal["running", "done", "error"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DailyVaultSettings(_StrictModel):
    kind: Literal["pharos.daily.settings"] = "pharos.daily.settings"
    schema_version: Literal[1] = 1
    categories: Annotated[
        list[Annotated[str, Field(max_length=32)]], Field(min_length=1, max_length=24)
    ]
    max_per_day: Annotated[int, Field(ge=1, le=200)]
    enabled: bool


class DailyVaultDirection(_StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=64)]
    keywords: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=80)]],
        Field(min_length=1, max_length=80),
    ]
    enabled: bool
    position: Annotated[int, Field(ge=0, le=10_000)]

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("direction name cannot be blank")
        return cleaned

    @field_validator("keywords")
    @classmethod
    def _preserve_valid_keywords(cls, values: list[str]) -> list[str]:
        """Validate without stripping load-bearing word-boundary whitespace."""
        lowered = [value.lower() for value in values]
        if any(not value.strip() for value in lowered):
            raise ValueError("direction keywords cannot be blank")
        if sum(len(value) for value in lowered) > 2_000:
            raise ValueError("direction keyword list is too long")
        if len(lowered) != len(set(lowered)):
            raise ValueError("direction keywords must be unique")
        return lowered


class DailyVaultProfile(_StrictModel):
    kind: Literal["pharos.daily.profile"] = "pharos.daily.profile"
    schema_version: Literal[1] = 1
    timezone: Annotated[str, Field(max_length=128)] | None = None
    settings: DailyVaultSettings
    directions: Annotated[list[DailyVaultDirection], Field(max_length=40)]
    updated_at: dt.datetime | None = None

    @model_validator(mode="after")
    def _unique_direction_names(self) -> DailyVaultProfile:
        folded = [direction.name.casefold() for direction in self.directions]
        if len(folded) != len(set(folded)):
            raise ValueError("direction names must be unique")
        return self


class DailyVaultRun(_StrictModel):
    status: DailyRunStatus
    fetched: Annotated[int, Field(ge=0)]
    read_done: Annotated[int, Field(ge=0)]
    read_failed: Annotated[int, Field(ge=0)]
    error: LongText | None = None
    started_at: dt.datetime
    finished_at: dt.datetime | None = None


class DailyVaultPaper(_StrictModel):
    kind: Literal["pharos.daily.paper"] = "pharos.daily.paper"
    schema_version: Literal[1] = 1
    arxiv_id: Annotated[str, Field(min_length=1, max_length=32)]
    date: Annotated[str, Field(pattern=_DATE_PATTERN)]
    rank: Annotated[int, Field(ge=1, le=_MAX_PAPERS_PER_DAY)]
    title: Annotated[str, Field(min_length=1, max_length=20_000)]
    authors: Annotated[list[ShortText], Field(max_length=1_000)] = Field(default_factory=list)
    abstract: LongText | None = None
    categories: Annotated[
        list[Annotated[str, Field(max_length=32)]], Field(max_length=100)
    ] = Field(default_factory=list)
    matched_direction: Annotated[str, Field(max_length=64)] | None = None
    matched_keywords: Annotated[
        list[Annotated[str, Field(max_length=80)]], Field(max_length=80)
    ] = Field(default_factory=list)
    arxiv_url: Annotated[str, Field(max_length=2_048)] | None = None
    pdf_url: Annotated[str, Field(max_length=2_048)] | None = None
    published_at: dt.datetime | None = None
    venue: Annotated[str, Field(max_length=512)] | None = None
    read_status: DailyReadStatus
    summary_zh: LongText | None = None
    highlights: Annotated[
        dict[Annotated[str, Field(max_length=64)], LongText], Field(max_length=16)
    ] | None = None
    scores: Annotated[
        dict[Annotated[str, Field(max_length=64)], Score], Field(max_length=16)
    ] | None = None
    read_model: Annotated[str, Field(max_length=128)] | None = None
    read_at: dt.datetime | None = None
    read_error: LongText | None = None
    created_at: dt.datetime

    @field_validator("arxiv_url", "pdf_url")
    @classmethod
    def _safe_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("paper URLs must be absolute HTTP(S) URLs")
        return value

    @model_validator(mode="after")
    def _reading_shape_matches_status(self) -> DailyVaultPaper:
        if self.read_status != "done" and any(
            value is not None for value in (self.summary_zh, self.highlights, self.scores)
        ):
            raise ValueError(
                "only a completed reading may contain a summary, highlights, or scores"
            )
        return self


class DailyVaultDay(_StrictModel):
    kind: Literal["pharos.daily.issue"] = "pharos.daily.issue"
    schema_version: Literal[1] = 1
    date: Annotated[str, Field(pattern=_DATE_PATTERN)]
    run: DailyVaultRun | None = None
    papers: Annotated[list[DailyVaultPaper], Field(max_length=_MAX_PAPERS_PER_DAY)]

    @model_validator(mode="after")
    def _paper_dates_and_ranks(self) -> DailyVaultDay:
        if any(paper.date != self.date for paper in self.papers):
            raise ValueError("every paper in an issue must use the issue date")
        ranks = [paper.rank for paper in self.papers]
        if len(ranks) != len(set(ranks)):
            raise ValueError("paper ranks must be unique within an issue")
        return self


class DailyVaultArchive(_StrictModel):
    kind: Literal["pharos.daily.archive"] = "pharos.daily.archive"
    schema_version: Literal[1] = 1
    vault_id: Annotated[str, Field(max_length=64)] | None = None
    exported_at: dt.datetime
    generator: Annotated[str, Field(min_length=1, max_length=128)] = "Pharos"
    profile: DailyVaultProfile
    days: Annotated[list[DailyVaultDay], Field(max_length=_MAX_DAYS)]

    @model_validator(mode="after")
    def _archive_is_unambiguous(self) -> DailyVaultArchive:
        dates = [day.date for day in self.days]
        if len(dates) != len(set(dates)):
            raise ValueError("an archive may contain only one issue per date")

        paper_ids = [paper.arxiv_id for day in self.days for paper in day.papers]
        if len(paper_ids) > _MAX_ARCHIVE_PAPERS:
            raise ValueError(f"archive contains more than {_MAX_ARCHIVE_PAPERS} papers")
        if len(paper_ids) != len(set(paper_ids)):
            raise ValueError("an arXiv paper may appear only once in an archive")
        return self


class DailyVaultImportResult(_StrictModel):
    days_seen: int
    papers_added: int
    papers_updated: int
    papers_unchanged: int
    directions_restored: int
    profile_restored: bool


def _utc(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def _split(raw: str | None, separator: str) -> list[str]:
    return [part.strip() for part in (raw or "").split(separator) if part.strip()]


def _keyword_lines(raw: str | None) -> list[str]:
    """Split stored keywords while preserving intentional surrounding spaces."""
    return [part for part in (raw or "").splitlines() if part.strip()]


def _object(raw: str | None) -> dict[str, object] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _paper_snapshot(feed: service.FeedPaper, rank: int) -> DailyVaultPaper:
    paper = feed.paper
    highlights = _object(paper.highlights)
    scores = feed.scores
    status = (
        cast(DailyReadStatus, paper.read_status)
        if paper.read_status in {"pending", "done", "error"}
        else "pending"
    )
    if status != "done":
        highlights = None
        scores = None
    return DailyVaultPaper(
        arxiv_id=paper.arxiv_id,
        date=paper.date,
        rank=rank,
        title=paper.title,
        authors=_split(paper.authors, ";"),
        abstract=paper.abstract,
        categories=_split(paper.categories, ","),
        matched_direction=feed.direction,
        matched_keywords=list(feed.keywords),
        arxiv_url=paper.arxiv_url,
        pdf_url=paper.pdf_url,
        published_at=_utc(paper.published_at),
        venue=paper.venue,
        read_status=status,
        summary_zh=paper.summary_zh if status == "done" else None,
        highlights=(
            {str(key): str(value) for key, value in highlights.items()} if highlights else None
        ),
        scores=scores,
        read_model=paper.read_model,
        read_at=_utc(paper.read_at),
        read_error=paper.read_error if status == "error" else None,
        created_at=_utc(paper.created_at) or dt.datetime.now(dt.UTC),
    )


def build_archive(session: Session, *, user_id: str) -> DailyVaultArchive:
    """Build the authenticated user's complete, portable Daily snapshot."""
    config = user_directions.get_config(session, user_id=user_id)
    directions = user_directions.list_directions(session, user_id=user_id)
    profile = DailyVaultProfile(
        settings=DailyVaultSettings(
            categories=user_directions.config_categories(config),
            max_per_day=int(config.max_per_day),
            enabled=bool(config.enabled),
        ),
        directions=[
            DailyVaultDirection(
                name=row.name,
                keywords=_keyword_lines(row.keywords),
                enabled=bool(row.enabled),
                position=int(row.position),
            )
            for row in directions
        ],
        updated_at=_utc(config.updated_at),
    )

    days: list[DailyVaultDay] = []
    for summary in service.date_summaries_for_user(session, user_id):
        feed = service.papers_for_user(session, summary.date, user_id)
        run = service.run_for_date(session, summary.date)
        days.append(
            DailyVaultDay(
                date=summary.date,
                run=(
                    DailyVaultRun(
                        status=cast(DailyRunStatus, run.status),
                        fetched=run.fetched,
                        read_done=run.read_done,
                        read_failed=run.read_failed,
                        error=run.error,
                        started_at=_utc(run.started_at) or dt.datetime.now(dt.UTC),
                        finished_at=_utc(run.finished_at),
                    )
                    if run is not None and run.status in {"running", "done", "error"}
                    else None
                ),
                papers=[_paper_snapshot(item, rank) for rank, item in enumerate(feed, start=1)],
            )
        )

    return DailyVaultArchive(
        exported_at=dt.datetime.now(dt.UTC),
        profile=profile,
        days=days,
    )


def _stored_scores(scores: dict[str, float] | None) -> str | None:
    """Remove reader-relative values before writing a shared DailyPaper row."""
    if not scores:
        return None
    shared = {
        key: float(value)
        for key, value in scores.items()
        if key not in {"relevance", "recommendation"}
    }
    return json.dumps(shared, ensure_ascii=False, separators=(",", ":")) if shared else None


def _restore_reading(row: DailyPaper, paper: DailyVaultPaper) -> bool:
    """Upgrade an unread shared row from a completed portable reading."""
    if paper.read_status != "done" or row.read_status == "done":
        return False
    row.read_status = "done"
    row.summary_zh = paper.summary_zh
    row.highlights = (
        json.dumps(paper.highlights, ensure_ascii=False, separators=(",", ":"))
        if paper.highlights
        else None
    )
    row.scores = _stored_scores(paper.scores)
    # The archive's recommendation is personal to its exporting account.  The
    # feed recomputes it after import from restored directions and shared scores.
    row.score_recommendation = None
    row.read_model = paper.read_model
    row.read_at = _utc(paper.read_at)
    row.read_error = None
    return True


def _fill_missing_metadata(row: DailyPaper, paper: DailyVaultPaper) -> bool:
    changed = False
    candidates: tuple[tuple[str, object | None], ...] = (
        ("authors", "; ".join(paper.authors) or None),
        ("abstract", paper.abstract),
        ("categories", ",".join(paper.categories) or None),
        ("arxiv_url", paper.arxiv_url),
        ("pdf_url", paper.pdf_url),
        ("published_at", _utc(paper.published_at)),
        ("venue", paper.venue),
    )
    for name, value in candidates:
        if getattr(row, name) in {None, ""} and value not in {None, ""}:
            setattr(row, name, value)
            changed = True
    return changed


def _replace_profile(session: Session, *, user_id: str, profile: DailyVaultProfile) -> int:
    """Replace only this user's Daily settings and directions, transactionally."""
    # Validate through the same service functions used by normal settings
    # requests before deleting anything.  If any value is invalid, the import
    # fails without changing the current profile.
    categories = user_directions.parse_categories(profile.settings.categories)
    config = session.get(UserDailyConfig, user_id)
    if config is None:
        config = UserDailyConfig(user_id=user_id, seeded=True)
        session.add(config)
    config.categories = ",".join(categories)
    config.max_per_day = profile.settings.max_per_day
    config.enabled = profile.settings.enabled
    config.seeded = True
    config.updated_at = dt.datetime.now(dt.UTC)

    session.execute(delete(UserDirection).where(UserDirection.user_id == user_id))
    session.flush()
    ordered = sorted(profile.directions, key=lambda item: (item.position, item.name.casefold()))
    for position, direction in enumerate(ordered):
        session.add(
            UserDirection(
                user_id=user_id,
                name=direction.name,
                keywords="\n".join(direction.keywords),
                enabled=direction.enabled,
                position=position,
            )
        )
    session.flush()
    return len(ordered)


def import_archive(
    session: Session,
    *,
    user_id: str,
    archive: DailyVaultArchive,
    restore_profile: bool = True,
) -> DailyVaultImportResult:
    """Merge a validated archive into the online working copy, idempotently.

    Existing papers are keyed by their version-free arXiv id.  Their digest date
    is never moved because the row is shared by every account on an instance;
    moving it to satisfy one restore would rewrite everybody else's history.
    Missing metadata is filled, and a completed archived reading may upgrade a
    pending/error row, but a completed server reading is never overwritten.
    """
    directions_restored = (
        _replace_profile(session, user_id=user_id, profile=archive.profile)
        if restore_profile
        else 0
    )

    existing = {
        row.arxiv_id: row for row in session.scalars(select(DailyPaper)) if row.arxiv_id
    }
    added = 0
    updated = 0
    unchanged = 0

    for day in archive.days:
        for paper in day.papers:
            row = existing.get(paper.arxiv_id)
            if row is None:
                row = DailyPaper(
                    arxiv_id=paper.arxiv_id,
                    date=paper.date,
                    title=paper.title,
                    authors="; ".join(paper.authors) or None,
                    abstract=paper.abstract,
                    categories=",".join(paper.categories) or None,
                    # Matches are per-user and are recomputed from the restored
                    # profile.  Persisting the exporter's badge on a shared row
                    # would mislabel this paper for every other account.
                    matched_domain=None,
                    matched_keywords=None,
                    arxiv_url=paper.arxiv_url,
                    pdf_url=paper.pdf_url,
                    published_at=_utc(paper.published_at),
                    venue=paper.venue,
                    read_status=paper.read_status,
                    summary_zh=paper.summary_zh if paper.read_status == "done" else None,
                    highlights=(
                        json.dumps(paper.highlights, ensure_ascii=False, separators=(",", ":"))
                        if paper.read_status == "done" and paper.highlights
                        else None
                    ),
                    scores=_stored_scores(paper.scores) if paper.read_status == "done" else None,
                    score_recommendation=None,
                    read_model=paper.read_model,
                    read_at=_utc(paper.read_at),
                    read_error=paper.read_error if paper.read_status == "error" else None,
                    created_at=_utc(paper.created_at) or dt.datetime.now(dt.UTC),
                )
                session.add(row)
                existing[paper.arxiv_id] = row
                added += 1
                continue

            changed = _fill_missing_metadata(row, paper)
            changed = _restore_reading(row, paper) or changed
            if changed:
                updated += 1
            else:
                unchanged += 1

    session.flush()
    return DailyVaultImportResult(
        days_seen=len(archive.days),
        papers_added=added,
        papers_updated=updated,
        papers_unchanged=unchanged,
        directions_restored=directions_restored,
        profile_restored=restore_profile,
    )
