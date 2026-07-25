"""Wire schemas shared by research projects and literature discovery."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from pharos.api.schemas import as_utc
from pharos.db.models import (
    LiteratureResult,
    LiteratureSearch,
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


class ProjectSourceOut(BaseModel):
    id: str
    project_id: str
    result_id: str
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


def source_out(row: ProjectSource) -> ProjectSourceOut:
    return ProjectSourceOut(
        id=row.id,
        project_id=row.project_id,
        result_id=row.result_id,
        note=row.note,
        added_at=as_utc(row.added_at),
        result=result_out(row.result),
    )


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
        sources=[source_out(source) for source in sources],
        artifacts=[artifact_out(artifact) for artifact in artifacts],
        automation_notice=AUTOMATION_NOTICE,
    )
