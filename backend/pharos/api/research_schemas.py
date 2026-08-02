"""Wire schemas shared by research projects and literature discovery."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import object_session

from pharos.api.schemas import as_utc
from pharos.db.models import (
    LiteratureResult,
    LiteratureSearch,
    Paper,
    ProjectArtifact,
    ProjectSource,
    ResearchProject,
)
from pharos.services import projects

ProjectStatus = Literal["active", "archived"]
ProjectStage = Literal[
    "discovery",
    "ideation",
    "planning",
    "experimentation",
    "analysis",
    "claims",
    "drafting",
    "review",
    "complete",
]
ArtifactType = Literal["hypothesis", "experiment_plan", "result", "claim", "draft", "review"]
ArtifactStatus = Literal["draft", "ready", "verified", "rejected"]
DiscoverySource = Literal["arxiv", "openalex"]

AUTOMATION_NOTICE = (
    "No compute runner is connected. Project artifacts are persisted research records; "
    "Pharos does not claim that an experiment was executed automatically."
)


class LiteratureResultOut(BaseModel):
    id: str
    search_id: str
    title: str
    authors: list[str]
    abstract: str
    year: int | None
    venue: str | None
    doi: str | None
    url: str | None
    pdf_url: str | None
    sources: list[str]
    source_ids: dict[str, str]
    citation_count: int | None
    rank: int
    analysis_mode: Literal["rules", "llm"]
    analysis_model: str | None
    analysis_warning: str | None
    summary_zh: str
    contribution: str
    core_trick: str
    method: str
    results: str
    limitations: str
    created_at: datetime


class LiteratureSearchOut(BaseModel):
    id: str
    project_id: str | None
    query: str
    sources: list[str]
    status: Literal["running", "complete", "partial", "error"]
    result_count: int
    errors: dict[str, str]
    created_at: datetime
    completed_at: datetime | None
    results: list[LiteratureResultOut]


class LinkedPaperOut(BaseModel):
    """The library paper a source turned out to be, as much as a client renders.

    Deliberately not the full library shape. This is the answer to "can evidence
    from this source cite a page, and of what" — a title to show, a page count
    so the client can bound a page reference, and whether the paper is on its way
    out of the library.
    """

    id: str
    title: str
    page_count: int | None
    #: Non-null while the paper sits in the recycle bin. The link survives being
    #: trashed — only a purge clears it — so this is what separates "anchored to
    #: a paper you have" from "anchored to one you are about to lose".
    deleted_at: datetime | None


class ProjectSourceOut(BaseModel):
    id: str
    project_id: str
    result_id: str
    #: The library paper this source is, when the user has it. NULL means
    #: abstract-only: evidence drawn from this source has no page to point at.
    #:
    #: NULL is also what a *purged* paper leaves behind, because the FK is
    #: ON DELETE SET NULL and the schema keeps no tombstone. "Never linked" and
    #: "linked to a paper that has since been permanently deleted" are therefore
    #: the same value here, and a client cannot tell them apart. Trashing a
    #: paper is different and is visible: the link survives and ``paper``
    #: carries a ``deleted_at``.
    paper_id: str | None
    #: Resolved from ``paper_id`` for rendering. Absent whenever ``paper_id``
    #: is, and — failing closed — also if the id somehow names a paper this
    #: source's owner does not own.
    paper: LinkedPaperOut | None
    note: str | None
    added_at: datetime
    result: LiteratureResultOut


class ProjectArtifactOut(BaseModel):
    id: str
    project_id: str
    stage: ProjectStage
    type: ArtifactType
    title: str
    body: str
    status: ArtifactStatus
    created_at: datetime
    updated_at: datetime | None


class ProjectOut(BaseModel):
    id: str
    name: str
    description: str
    research_question: str
    status: ProjectStatus
    stage: ProjectStage
    created_at: datetime
    updated_at: datetime | None
    source_count: int
    artifact_count: int
    sources: list[ProjectSourceOut]
    artifacts: list[ProjectArtifactOut]
    automation_notice: str


class SearchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: Annotated[str, Field(min_length=2, max_length=projects.MAX_QUERY)]
    project_id: str | None = None
    sources: Annotated[list[DiscoverySource], Field(min_length=1, max_length=2)] = [
        "arxiv",
        "openalex",
    ]
    limit: Annotated[int, Field(ge=1, le=50)] = 20


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=projects.MAX_PROJECT_NAME)]
    description: Annotated[str, Field(max_length=projects.MAX_DESCRIPTION)] = ""
    research_question: Annotated[str, Field(max_length=projects.MAX_QUESTION)] = ""


class ProjectPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=projects.MAX_PROJECT_NAME)] | None = None
    description: Annotated[str, Field(max_length=projects.MAX_DESCRIPTION)] | None = None
    research_question: Annotated[str, Field(max_length=projects.MAX_QUESTION)] | None = None
    status: ProjectStatus | None = None
    stage: ProjectStage | None = None


class SourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_id: str
    note: Annotated[str, Field(max_length=projects.MAX_NOTE)] | None = None


class SourcePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: Annotated[str, Field(max_length=projects.MAX_NOTE)] | None


class SourcePaperLink(BaseModel):
    """The library paper a source is being declared to be.

    A sub-resource of its own rather than another optional field on
    ``SourcePatch``: ``note`` there is required-but-nullable, so folding the
    link in would mean no client could set one without also rewriting the
    researcher's note, and ``null`` would then have to mean both "leave it" and
    "unlink". Linking and unlinking are a PUT and a DELETE on the link itself.
    """

    model_config = ConfigDict(extra="forbid")

    paper_id: Annotated[str, Field(min_length=1, max_length=32)]


class ArtifactCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: ProjectStage
    type: ArtifactType
    title: Annotated[str, Field(min_length=1, max_length=projects.MAX_ARTIFACT_TITLE)]
    body: Annotated[str, Field(max_length=projects.MAX_ARTIFACT_BODY)] = ""
    status: ArtifactStatus = "draft"


class ArtifactPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: ProjectStage | None = None
    type: ArtifactType | None = None
    title: Annotated[str, Field(min_length=1, max_length=projects.MAX_ARTIFACT_TITLE)] | None = None
    body: Annotated[str, Field(max_length=projects.MAX_ARTIFACT_BODY)] | None = None
    status: ArtifactStatus | None = None


def result_out(row: LiteratureResult) -> LiteratureResultOut:
    authors = projects.load_json(row.authors, [])
    source_names = projects.load_json(row.sources, [])
    source_ids = projects.load_json(row.source_ids, {})
    return LiteratureResultOut(
        id=row.id,
        search_id=row.search_id,
        title=row.title,
        authors=authors if isinstance(authors, list) else [],
        abstract=row.abstract or "",
        year=row.year,
        venue=row.venue,
        doi=row.doi,
        url=row.url,
        pdf_url=row.pdf_url,
        sources=source_names if isinstance(source_names, list) else [],
        source_ids=source_ids if isinstance(source_ids, dict) else {},
        citation_count=row.citation_count,
        rank=int(row.rank or 0),
        analysis_mode=row.analysis_mode,  # type: ignore[arg-type]
        analysis_model=row.analysis_model,
        analysis_warning=row.analysis_warning,
        summary_zh=row.summary_zh or "",
        contribution=row.contribution or "",
        core_trick=row.core_trick or "",
        method=row.method or "",
        results=row.results or "",
        limitations=row.limitations or "",
        created_at=as_utc(row.created_at),
    )


def search_out(row: LiteratureSearch) -> LiteratureSearchOut:
    source_names = projects.load_json(row.sources, [])
    errors = projects.load_json(row.errors, {})
    return LiteratureSearchOut(
        id=row.id,
        project_id=row.project_id,
        query=row.query,
        sources=source_names if isinstance(source_names, list) else [],
        status=row.status,  # type: ignore[arg-type]
        result_count=int(row.result_count or 0),
        errors=errors if isinstance(errors, dict) else {},
        created_at=as_utc(row.created_at),
        completed_at=as_utc(row.completed_at),
        results=[
            result_out(result)
            for result in sorted(row.results, key=lambda r: r.rank)
            if result.user_id == row.user_id
        ],
    )


def _linked_papers(rows: Sequence[ProjectSource]) -> dict[str, Paper]:
    """Load the library papers ``rows`` point at, in one owner-scoped query.

    ``ProjectSource.paper_id`` is a plain FK with no ORM relationship behind it,
    so there is nothing for the caller to eager-load and the row has to be
    fetched here. Fetched for the whole list at once because a project renders
    every one of its sources in one response, and a per-source lookup would turn
    that into N+1 queries against the library.

    The owner predicate is redundant — only owner-scoped service code ever
    writes ``paper_id`` — and it is here anyway because the cost of being wrong
    is another user's paper title rendered inside this user's project. A row
    that fails it is simply missing from the map, so the source serialises as
    unresolved rather than as somebody else's work.
    """
    wanted = {row.paper_id for row in rows if row.paper_id}
    if not wanted:
        return {}
    session = object_session(rows[0])
    if session is None:  # pragma: no cover — every caller serialises inside one
        return {}
    owners = {row.user_id for row in rows}
    found = session.scalars(select(Paper).where(Paper.id.in_(wanted), Paper.user_id.in_(owners)))
    return {paper.id: paper for paper in found}


def _linked_paper_out(row: Paper) -> LinkedPaperOut:
    return LinkedPaperOut(
        id=row.id,
        title=row.title,
        page_count=row.page_count,
        deleted_at=as_utc(row.deleted_at),
    )


def _source_out(row: ProjectSource, papers: dict[str, Paper]) -> ProjectSourceOut:
    paper = papers.get(row.paper_id) if row.paper_id else None
    return ProjectSourceOut(
        id=row.id,
        project_id=row.project_id,
        result_id=row.result_id,
        paper_id=row.paper_id,
        paper=_linked_paper_out(paper) if paper is not None else None,
        note=row.note,
        added_at=as_utc(row.added_at),
        result=result_out(row.result),
    )


def source_out(row: ProjectSource) -> ProjectSourceOut:
    """Serialise one source. Prefer :func:`sources_out` for a list — see above."""
    return _source_out(row, _linked_papers([row]))


def sources_out(rows: Sequence[ProjectSource]) -> list[ProjectSourceOut]:
    """Serialise several sources, resolving all their linked papers in one query."""
    papers = _linked_papers(rows)
    return [_source_out(row, papers) for row in rows]


def artifact_out(row: ProjectArtifact) -> ProjectArtifactOut:
    return ProjectArtifactOut(
        id=row.id,
        project_id=row.project_id,
        stage=row.stage,  # type: ignore[arg-type]
        type=row.type,  # type: ignore[arg-type]
        title=row.title,
        body=row.body or "",
        status=row.status,  # type: ignore[arg-type]
        created_at=as_utc(row.created_at),
        updated_at=as_utc(row.updated_at),
    )


def project_out(row: ResearchProject) -> ProjectOut:
    sources = sorted(
        (source for source in row.sources if source.user_id == row.user_id),
        key=lambda item: (item.added_at, item.id),
    )
    artifacts = sorted(
        (artifact for artifact in row.artifacts if artifact.user_id == row.user_id),
        key=lambda item: (item.created_at, item.id),
    )
    return ProjectOut(
        id=row.id,
        name=row.name,
        description=row.description or "",
        research_question=row.research_question or "",
        status=row.status,  # type: ignore[arg-type]
        stage=row.stage,  # type: ignore[arg-type]
        created_at=as_utc(row.created_at),
        updated_at=as_utc(row.updated_at),
        source_count=len(sources),
        artifact_count=len(artifacts),
        sources=sources_out(sources),
        artifacts=[artifact_out(artifact) for artifact in artifacts],
        automation_notice=AUTOMATION_NOTICE,
    )
