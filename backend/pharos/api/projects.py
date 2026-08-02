"""Research project, saved evidence, and research-record CRUD."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from pharos.api.deps import current_user, get_session
from pharos.api.research_schemas import (
    ArtifactCreate,
    ArtifactPatch,
    ProjectArtifactOut,
    ProjectCreate,
    ProjectOut,
    ProjectPatch,
    ProjectSourceOut,
    SourceCreate,
    SourcePaperLink,
    SourcePatch,
    artifact_out,
    project_out,
    source_out,
    sources_out,
)
from pharos.db.models import User
from pharos.services import projects


class _ProjectRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except projects.ProjectError as exc:
                raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

        return handler


router = APIRouter(prefix="/api/projects", tags=["research"], route_class=_ProjectRoute)
_SESSION_DEP = Depends(get_session)
_USER_DEP = Depends(current_user)


@router.get("", response_model=list[ProjectOut])
def list_projects(
    session: Session = _SESSION_DEP, user: User = _USER_DEP
) -> list[ProjectOut]:
    return [project_out(row) for row in projects.list_projects(session, user_id=user.id)]


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(
    body: ProjectCreate,
    session: Session = _SESSION_DEP,
    user: User = _USER_DEP,
) -> ProjectOut:
    row = projects.create_project(session, user_id=user.id, **body.model_dump())
    return project_out(row)


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: str,
    session: Session = _SESSION_DEP,
    user: User = _USER_DEP,
) -> ProjectOut:
    return project_out(projects.require_project(session, project_id, user_id=user.id))


@router.patch("/{project_id}", response_model=ProjectOut)
def patch_project(
    project_id: str,
    body: ProjectPatch,
    session: Session = _SESSION_DEP,
    user: User = _USER_DEP,
) -> ProjectOut:
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    return project_out(
        projects.update_project(
            session, user_id=user.id, project_id=project_id, changes=changes
        )
    )


@router.delete("/{project_id}", status_code=204)
def delete_project(
    project_id: str,
    session: Session = _SESSION_DEP,
    user: User = _USER_DEP,
) -> Response:
    projects.delete_project(session, user_id=user.id, project_id=project_id)
    return Response(status_code=204)


@router.post("/{project_id}/advance", response_model=ProjectOut)
def advance_project(
    project_id: str,
    session: Session = _SESSION_DEP,
    user: User = _USER_DEP,
) -> ProjectOut:
    return project_out(projects.advance_project(session, user_id=user.id, project_id=project_id))


@router.post("/{project_id}/sources", response_model=ProjectSourceOut)
def add_project_source(
    project_id: str,
    body: SourceCreate,
    session: Session = _SESSION_DEP,
    user: User = _USER_DEP,
) -> ProjectSourceOut:
    return source_out(
        projects.add_source(
            session,
            user_id=user.id,
            project_id=project_id,
            result_id=body.result_id,
            note=body.note,
        )
    )


@router.patch("/{project_id}/sources/{source_id}", response_model=ProjectSourceOut)
def patch_project_source(
    project_id: str,
    source_id: str,
    body: SourcePatch,
    session: Session = _SESSION_DEP,
    user: User = _USER_DEP,
) -> ProjectSourceOut:
    return source_out(
        projects.update_source_note(
            session,
            user_id=user.id,
            project_id=project_id,
            source_id=source_id,
            note=body.note,
        )
    )


@router.post("/{project_id}/sources/autolink", response_model=list[ProjectSourceOut])
def autolink_project_sources(
    project_id: str,
    session: Session = _SESSION_DEP,
    user: User = _USER_DEP,
) -> list[ProjectSourceOut]:
    """Link every unlinked source whose paper is now in the library.

    Returns only the sources this call actually changed, so a client can say
    what happened rather than diffing the project. An empty list is the normal
    answer and is not an error: it means nothing new matched.

    Declared before ``/sources/{source_id}/...`` matters not at all here — no
    other POST route under ``sources`` takes a path parameter — but it is kept
    adjacent to its siblings so a later addition cannot shadow it unnoticed.
    """
    return sources_out(
        projects.autolink_project_sources(session, user_id=user.id, project_id=project_id)
    )


@router.put("/{project_id}/sources/{source_id}/paper", response_model=ProjectSourceOut)
def link_source_paper(
    project_id: str,
    source_id: str,
    body: SourcePaperLink,
    session: Session = _SESSION_DEP,
    user: User = _USER_DEP,
) -> ProjectSourceOut:
    """Anchor a source to a paper in the caller's library.

    A ``paper_id`` naming a paper the caller does not own answers 404, the same
    as an id that names nothing at all — see ``projects._require_library_paper``
    for why the two must be indistinguishable.
    """
    return source_out(
        projects.link_source_paper(
            session,
            user_id=user.id,
            project_id=project_id,
            source_id=source_id,
            paper_id=body.paper_id,
        )
    )


@router.delete("/{project_id}/sources/{source_id}/paper", response_model=ProjectSourceOut)
def unlink_source_paper(
    project_id: str,
    source_id: str,
    session: Session = _SESSION_DEP,
    user: User = _USER_DEP,
) -> ProjectSourceOut:
    """Return a source to abstract-only. The source itself is untouched.

    Answers 200 with the updated source rather than 204: unlike the DELETEs
    around it this removes a field, not a resource, and the caller wants to see
    what the source now looks like.
    """
    return source_out(
        projects.unlink_source_paper(
            session, user_id=user.id, project_id=project_id, source_id=source_id
        )
    )


@router.delete("/{project_id}/sources/{source_id}", status_code=204)
def delete_project_source(
    project_id: str,
    source_id: str,
    session: Session = _SESSION_DEP,
    user: User = _USER_DEP,
) -> Response:
    projects.remove_source(
        session, user_id=user.id, project_id=project_id, source_id=source_id
    )
    return Response(status_code=204)


@router.get("/{project_id}/artifacts", response_model=list[ProjectArtifactOut])
def list_project_artifacts(
    project_id: str,
    session: Session = _SESSION_DEP,
    user: User = _USER_DEP,
) -> list[ProjectArtifactOut]:
    return [
        artifact_out(row)
        for row in projects.list_artifacts(session, user_id=user.id, project_id=project_id)
    ]


@router.post("/{project_id}/artifacts", response_model=ProjectArtifactOut, status_code=201)
def create_project_artifact(
    project_id: str,
    body: ArtifactCreate,
    session: Session = _SESSION_DEP,
    user: User = _USER_DEP,
) -> ProjectArtifactOut:
    return artifact_out(
        projects.create_artifact(
            session,
            user_id=user.id,
            project_id=project_id,
            **body.model_dump(),
        )
    )


@router.get("/{project_id}/artifacts/{artifact_id}", response_model=ProjectArtifactOut)
def get_project_artifact(
    project_id: str,
    artifact_id: str,
    session: Session = _SESSION_DEP,
    user: User = _USER_DEP,
) -> ProjectArtifactOut:
    return artifact_out(
        projects.require_artifact(
            session, user_id=user.id, project_id=project_id, artifact_id=artifact_id
        )
    )


@router.patch("/{project_id}/artifacts/{artifact_id}", response_model=ProjectArtifactOut)
def patch_project_artifact(
    project_id: str,
    artifact_id: str,
    body: ArtifactPatch,
    session: Session = _SESSION_DEP,
    user: User = _USER_DEP,
) -> ProjectArtifactOut:
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    return artifact_out(
        projects.update_artifact(
            session,
            user_id=user.id,
            project_id=project_id,
            artifact_id=artifact_id,
            changes=changes,
        )
    )


@router.delete("/{project_id}/artifacts/{artifact_id}", status_code=204)
def delete_project_artifact(
    project_id: str,
    artifact_id: str,
    session: Session = _SESSION_DEP,
    user: User = _USER_DEP,
) -> Response:
    projects.delete_artifact(
        session,
        user_id=user.id,
        project_id=project_id,
        artifact_id=artifact_id,
    )
    return Response(status_code=204)
