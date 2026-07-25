"""Authenticated multi-source literature discovery and optional AI deep read."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from pharos.api.deps import current_user, get_session
from pharos.api.research_schemas import (
    LiteratureResultOut,
    LiteratureSearchOut,
    SearchCreate,
    result_out,
    search_out,
)
from pharos.db.models import User
from pharos.services import projects


class _DiscoveryRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except projects.ProjectError as exc:
                raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

        return handler


router = APIRouter(prefix="/api/discovery", tags=["research"], route_class=_DiscoveryRoute)
_SESSION_DEP = Depends(get_session)
_USER_DEP = Depends(current_user)


@router.post("/search", response_model=LiteratureSearchOut, status_code=201)
def search_literature(
    body: SearchCreate,
    session: Session = _SESSION_DEP,
    user: User = _USER_DEP,
) -> LiteratureSearchOut:
    row = projects.run_search(
        session,
        user_id=user.id,
        query=body.query,
        sources=list(body.sources),
        limit=body.limit,
        project_id=body.project_id,
    )
    # An all-provider outage is still a successfully persisted *search run*.
    # Returning its structured ``status=error`` and per-provider errors lets the
    # history UI reopen and explain it. 5xx is reserved for failures where the
    # application could not record an honest outcome at all.
    return search_out(row)


@router.get("/searches", response_model=list[LiteratureSearchOut])
def list_search_history(
    project_id: str | None = Query(None),
    session: Session = _SESSION_DEP,
    user: User = _USER_DEP,
) -> list[LiteratureSearchOut]:
    return [
        search_out(row)
        for row in projects.list_searches(
            session, user_id=user.id, project_id=project_id
        )
    ]


@router.get("/searches/{search_id}", response_model=LiteratureSearchOut)
def get_search_history(
    search_id: str,
    session: Session = _SESSION_DEP,
    user: User = _USER_DEP,
) -> LiteratureSearchOut:
    return search_out(projects.require_search(session, search_id, user_id=user.id))


@router.post("/results/{result_id}/analyze", response_model=LiteratureResultOut)
def analyze_literature_result(
    result_id: str,
    session: Session = _SESSION_DEP,
    user: User = _USER_DEP,
) -> LiteratureResultOut:
    return result_out(projects.analyze_result(session, user_id=user.id, result_id=result_id))
