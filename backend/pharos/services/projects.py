"""Research-project persistence and owner-scoped workflow operations."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, with_loader_criteria

from pharos.daily import reader
from pharos.db.models import (
    LiteratureResult,
    LiteratureSearch,
    ProjectArtifact,
    ProjectSource,
    ResearchProject,
)
from pharos.services import discovery

PROJECT_STATUSES = frozenset({"active", "archived"})
PROJECT_STAGES = (
    "discovery",
    "ideation",
    "planning",
    "experimentation",
    "analysis",
    "claims",
    "drafting",
    "review",
    "complete",
)
ARTIFACT_TYPES = frozenset(
    {"hypothesis", "experiment_plan", "result", "claim", "draft", "review"}
)
ARTIFACT_STATUSES = frozenset({"draft", "ready", "verified", "rejected"})

MAX_PROJECT_NAME = 256
MAX_DESCRIPTION = 50_000
MAX_QUESTION = 20_000
MAX_QUERY = 500
MAX_NOTE = 20_000
MAX_ARTIFACT_TITLE = 512
MAX_ARTIFACT_BODY = 500_000


class ProjectError(Exception):
    status_code = 400


class NotFound(ProjectError):
    status_code = 404


class Invalid(ProjectError):
    status_code = 400


class Conflict(ProjectError):
    status_code = 409


class ProviderUnavailable(ProjectError):
    status_code = 503


def _now() -> datetime:
    return datetime.now(UTC)


def _owner(user_id: str) -> str:
    if not user_id:
        raise ValueError("user_id is required for every project query")
    return user_id


def dump_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def load_json(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default
    return value


def _text(
    value: object,
    *,
    field: str,
    limit: int,
    required: bool = False,
    nullable: bool = False,
) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise Invalid(f"{field} must be a string")
    cleaned = value.strip()
    if required and not cleaned:
        raise Invalid(f"{field} cannot be empty")
    if len(cleaned) > limit:
        raise Invalid(f"{field} must be at most {limit} characters")
    return cleaned


def _project_options(user_id: str):
    return (
        selectinload(ResearchProject.sources).selectinload(ProjectSource.result),
        selectinload(ResearchProject.artifacts),
        with_loader_criteria(
            ProjectSource, ProjectSource.user_id == user_id, include_aliases=True
        ),
        with_loader_criteria(
            ProjectArtifact, ProjectArtifact.user_id == user_id, include_aliases=True
        ),
        with_loader_criteria(
            LiteratureResult, LiteratureResult.user_id == user_id, include_aliases=True
        ),
    )


def require_project(session: Session, project_id: str, *, user_id: str) -> ResearchProject:
    _owner(user_id)
    row = session.scalar(
        select(ResearchProject)
        .where(ResearchProject.id == project_id, ResearchProject.user_id == user_id)
        .options(*_project_options(user_id))
    )
    if row is None:
        raise NotFound("Project not found")
    return row


def create_project(
    session: Session,
    *,
    user_id: str,
    name: object,
    description: object = "",
    research_question: object = "",
) -> ResearchProject:
    _owner(user_id)
    row = ResearchProject(
        user_id=user_id,
        name=_text(name, field="name", limit=MAX_PROJECT_NAME, required=True),
        description=_text(description, field="description", limit=MAX_DESCRIPTION) or "",
        research_question=_text(
            research_question, field="research_question", limit=MAX_QUESTION
        )
        or "",
    )
    session.add(row)
    session.flush()
    row.sources = []
    row.artifacts = []
    return row


def list_projects(session: Session, *, user_id: str) -> list[ResearchProject]:
    _owner(user_id)
    return list(
        session.scalars(
            select(ResearchProject)
            .where(ResearchProject.user_id == user_id)
            .options(*_project_options(user_id))
            .order_by(ResearchProject.created_at.desc(), ResearchProject.id)
        )
    )


def update_project(
    session: Session, *, user_id: str, project_id: str, changes: dict[str, object]
) -> ResearchProject:
    row = require_project(session, project_id, user_id=user_id)
    allowed = {"name", "description", "research_question", "status", "stage"}
    unknown = set(changes) - allowed
    if unknown:
        raise Invalid(f"unexpected project fields: {sorted(unknown)}")
    if not changes:
        raise Invalid("No project fields provided")
    if "name" in changes:
        row.name = _text(
            changes["name"], field="name", limit=MAX_PROJECT_NAME, required=True
        )
    if "description" in changes:
        row.description = (
            _text(changes["description"], field="description", limit=MAX_DESCRIPTION) or ""
        )
    if "research_question" in changes:
        row.research_question = (
            _text(
                changes["research_question"],
                field="research_question",
                limit=MAX_QUESTION,
            )
            or ""
        )
    if "status" in changes:
        status = _text(changes["status"], field="status", limit=16, required=True)
        if status not in PROJECT_STATUSES:
            raise Invalid(f"status must be one of {sorted(PROJECT_STATUSES)}")
        row.status = status
    if "stage" in changes:
        # Manual backwards movement is a first-class research operation: a
        # failed experiment may send the project back to ideation. It is also
        # allowed while archived because this PATCH only corrects persisted
        # workflow metadata; it does not launch work. ``advance`` remains the
        # guarded active-only shortcut for forward execution.
        stage = _text(changes["stage"], field="stage", limit=32, required=True)
        if stage not in PROJECT_STAGES:
            raise Invalid(f"stage must be one of {list(PROJECT_STAGES)}")
        row.stage = stage
    row.updated_at = _now()
    session.flush()
    return row


def delete_project(session: Session, *, user_id: str, project_id: str) -> None:
    row = require_project(session, project_id, user_id=user_id)
    session.delete(row)
    session.flush()


def advance_project(session: Session, *, user_id: str, project_id: str) -> ResearchProject:
    row = require_project(session, project_id, user_id=user_id)
    if row.status != "active":
        raise Conflict("Archived projects cannot advance; reactivate the project first")
    try:
        index = PROJECT_STAGES.index(row.stage)
    except ValueError as exc:
        raise Conflict(f"Project has an unknown stage: {row.stage}") from exc
    if index == len(PROJECT_STAGES) - 1:
        raise Conflict("Project is already complete")
    row.stage = PROJECT_STAGES[index + 1]
    row.updated_at = _now()
    session.flush()
    return row


def run_search(
    session: Session,
    *,
    user_id: str,
    query: object,
    sources: list[str],
    limit: int,
    project_id: str | None = None,
) -> LiteratureSearch:
    _owner(user_id)
    clean_query = _text(query, field="query", limit=MAX_QUERY, required=True)
    if len(clean_query) < 2:
        raise Invalid("query must contain at least 2 characters")
    if not 1 <= limit <= 50:
        raise Invalid("limit must be between 1 and 50")
    if not sources:
        raise Invalid("at least one source is required")
    if len(sources) != len(set(sources)):
        raise Invalid("sources must not contain duplicates")
    unknown = set(sources) - discovery.SOURCES
    if unknown:
        raise Invalid(f"unsupported sources: {sorted(unknown)}")
    if project_id is not None:
        require_project(session, project_id, user_id=user_id)

    search = LiteratureSearch(
        user_id=user_id,
        project_id=project_id,
        query=clean_query,
        sources=dump_json(sources),
        status="running",
    )
    session.add(search)
    session.flush()

    batch = discovery.discover(clean_query, sources, limit)
    for rank, paper in enumerate(batch.papers, start=1):
        summary = discovery.rule_summary(paper.title, paper.abstract)
        search.results.append(
            LiteratureResult(
                user_id=user_id,
                dedup_key=discovery.dedup_key(paper),
                title=paper.title,
                authors=dump_json(list(paper.authors)),
                abstract=paper.abstract,
                year=paper.year,
                venue=paper.venue,
                doi=paper.doi,
                url=paper.url,
                pdf_url=paper.pdf_url,
                sources=dump_json(list(paper.sources)),
                source_ids=dump_json(discovery.source_ids_dict(paper)),
                citation_count=paper.citation_count,
                rank=rank,
                **summary,
            )
        )
    search.result_count = len(batch.papers)
    search.errors = dump_json(batch.errors)
    if len(batch.errors) == len(sources):
        search.status = "error"
    elif batch.errors:
        search.status = "partial"
    else:
        search.status = "complete"
    search.completed_at = _now()
    session.flush()
    return search


def _search_options(user_id: str):
    return (
        selectinload(LiteratureSearch.results),
        with_loader_criteria(
            LiteratureResult, LiteratureResult.user_id == user_id, include_aliases=True
        ),
    )


def list_searches(
    session: Session, *, user_id: str, project_id: str | None = None
) -> list[LiteratureSearch]:
    _owner(user_id)
    statement = select(LiteratureSearch).where(LiteratureSearch.user_id == user_id)
    if project_id is not None:
        require_project(session, project_id, user_id=user_id)
        statement = statement.where(LiteratureSearch.project_id == project_id)
    return list(
        session.scalars(
            statement.options(*_search_options(user_id)).order_by(
                LiteratureSearch.created_at.desc(), LiteratureSearch.id
            )
        )
    )


def require_search(session: Session, search_id: str, *, user_id: str) -> LiteratureSearch:
    _owner(user_id)
    row = session.scalar(
        select(LiteratureSearch)
        .where(LiteratureSearch.id == search_id, LiteratureSearch.user_id == user_id)
        .options(*_search_options(user_id))
    )
    if row is None:
        raise NotFound("Search not found")
    return row


def require_result(session: Session, result_id: str, *, user_id: str) -> LiteratureResult:
    _owner(user_id)
    row = session.scalar(
        select(LiteratureResult).where(
            LiteratureResult.id == result_id, LiteratureResult.user_id == user_id
        )
    )
    if row is None:
        raise NotFound("Literature result not found")
    return row


def analyze_result(
    session: Session, *, user_id: str, result_id: str
) -> LiteratureResult:
    """Replace heuristic fields only after a validated real provider response."""
    result = require_result(session, result_id, user_id=user_id)
    if not result.abstract.strip():
        raise Invalid("Literature result has no abstract to analyze")
    try:
        reading = reader.read_paper(
            result.title,
            result.abstract,
            authors=load_json(result.authors, []),
        )
    except reader.ReaderUnavailable as exc:
        # Preserve every rules field. The failed optional upgrade changes no row.
        raise Conflict(str(exc)) from exc
    except reader.ReaderError as exc:
        raise ProviderUnavailable(str(exc)) from exc

    result.analysis_mode = "llm"
    result.analysis_model = reading.model
    result.analysis_warning = None
    result.summary_zh = reading.summary_zh
    result.contribution = reading.highlights["contribution"]
    result.core_trick = reading.highlights["innovation"]
    result.method = reading.highlights["method"]
    result.results = reading.highlights["results"]
    # The daily reader is constrained to the supplied abstract but does not ask
    # for limitations. Keeping the extractive rules value is more honest than
    # making a model fill a field it was never asked to produce.
    session.flush()
    return result


def add_source(
    session: Session,
    *,
    user_id: str,
    project_id: str,
    result_id: str,
    note: object = None,
) -> ProjectSource:
    project = require_project(session, project_id, user_id=user_id)
    result = require_result(session, result_id, user_id=user_id)
    clean_note = _text(note, field="note", limit=MAX_NOTE, nullable=True)
    existing = session.scalar(
        select(ProjectSource).where(
            ProjectSource.project_id == project.id,
            ProjectSource.result_id == result.id,
            ProjectSource.user_id == user_id,
        )
    )
    if existing is not None:
        return existing
    row = ProjectSource(
        user_id=user_id,
        project_id=project.id,
        result_id=result.id,
        note=clean_note,
    )
    try:
        # The check above makes ordinary retries cheap; the savepoint makes two
        # genuinely concurrent first-adds idempotent as well. Adding inside the
        # nested transaction is load-bearing because entering a savepoint
        # autoflushes anything already pending into the outer transaction.
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        if row in session:
            session.expunge(row)
        existing = session.scalar(
            select(ProjectSource).where(
                ProjectSource.project_id == project.id,
                ProjectSource.result_id == result.id,
                ProjectSource.user_id == user_id,
            )
        )
        if existing is None:
            raise
        return existing
    return row


def require_source(
    session: Session, *, user_id: str, project_id: str, source_id: str
) -> ProjectSource:
    require_project(session, project_id, user_id=user_id)
    row = session.scalar(
        select(ProjectSource)
        .join(LiteratureResult, ProjectSource.result_id == LiteratureResult.id)
        .where(
            ProjectSource.id == source_id,
            ProjectSource.project_id == project_id,
            ProjectSource.user_id == user_id,
            LiteratureResult.user_id == user_id,
        )
        .options(selectinload(ProjectSource.result))
    )
    if row is None:
        raise NotFound("Project source not found")
    return row


def update_source_note(
    session: Session,
    *,
    user_id: str,
    project_id: str,
    source_id: str,
    note: object,
) -> ProjectSource:
    row = require_source(
        session, user_id=user_id, project_id=project_id, source_id=source_id
    )
    row.note = _text(note, field="note", limit=MAX_NOTE, nullable=True)
    session.flush()
    return row


def remove_source(
    session: Session, *, user_id: str, project_id: str, source_id: str
) -> None:
    row = require_source(
        session, user_id=user_id, project_id=project_id, source_id=source_id
    )
    session.delete(row)
    session.flush()


def list_artifacts(
    session: Session, *, user_id: str, project_id: str
) -> list[ProjectArtifact]:
    require_project(session, project_id, user_id=user_id)
    return list(
        session.scalars(
            select(ProjectArtifact)
            .where(
                ProjectArtifact.project_id == project_id,
                ProjectArtifact.user_id == user_id,
            )
            .order_by(ProjectArtifact.created_at, ProjectArtifact.id)
        )
    )


def require_artifact(
    session: Session, *, user_id: str, project_id: str, artifact_id: str
) -> ProjectArtifact:
    require_project(session, project_id, user_id=user_id)
    row = session.scalar(
        select(ProjectArtifact).where(
            ProjectArtifact.id == artifact_id,
            ProjectArtifact.project_id == project_id,
            ProjectArtifact.user_id == user_id,
        )
    )
    if row is None:
        raise NotFound("Project artifact not found")
    return row


def _artifact_field(value: object, *, field: str) -> str:
    cleaned = _text(value, field=field, limit=32, required=True)
    accepted = {
        "stage": frozenset(PROJECT_STAGES),
        "type": ARTIFACT_TYPES,
        "status": ARTIFACT_STATUSES,
    }[field]
    if cleaned not in accepted:
        raise Invalid(f"{field} must be one of {sorted(accepted)}")
    return cleaned


def create_artifact(
    session: Session,
    *,
    user_id: str,
    project_id: str,
    stage: object,
    type: object,
    title: object,
    body: object = "",
    status: object = "draft",
) -> ProjectArtifact:
    project = require_project(session, project_id, user_id=user_id)
    row = ProjectArtifact(
        user_id=user_id,
        project_id=project.id,
        stage=_artifact_field(stage, field="stage"),
        type=_artifact_field(type, field="type"),
        title=_text(title, field="title", limit=MAX_ARTIFACT_TITLE, required=True),
        body=_text(body, field="body", limit=MAX_ARTIFACT_BODY) or "",
        status=_artifact_field(status, field="status"),
    )
    session.add(row)
    session.flush()
    return row


def update_artifact(
    session: Session,
    *,
    user_id: str,
    project_id: str,
    artifact_id: str,
    changes: dict[str, object],
) -> ProjectArtifact:
    row = require_artifact(
        session, user_id=user_id, project_id=project_id, artifact_id=artifact_id
    )
    allowed = {"stage", "type", "title", "body", "status"}
    unknown = set(changes) - allowed
    if unknown:
        raise Invalid(f"unexpected artifact fields: {sorted(unknown)}")
    if not changes:
        raise Invalid("No artifact fields provided")
    if "stage" in changes:
        row.stage = _artifact_field(changes["stage"], field="stage")
    if "type" in changes:
        row.type = _artifact_field(changes["type"], field="type")
    if "title" in changes:
        row.title = _text(
            changes["title"], field="title", limit=MAX_ARTIFACT_TITLE, required=True
        )
    if "body" in changes:
        row.body = _text(changes["body"], field="body", limit=MAX_ARTIFACT_BODY) or ""
    if "status" in changes:
        row.status = _artifact_field(changes["status"], field="status")
    row.updated_at = _now()
    session.flush()
    return row


def delete_artifact(
    session: Session, *, user_id: str, project_id: str, artifact_id: str
) -> None:
    row = require_artifact(
        session, user_id=user_id, project_id=project_id, artifact_id=artifact_id
    )
    session.delete(row)
    session.flush()
