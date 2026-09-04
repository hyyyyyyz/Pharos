"""Ink API — handwritten strokes captured by a stylus, one row per stroke.

Structure mirrors :mod:`pharos.api.annotate` exactly: every endpoint requires
``current_user`` and is scoped by the service layer, the route class maps
``AnnotateError`` (which the ink service reuses) onto its HTTP status in one
place, and Pydantic models with ``extra="forbid"`` publish the wire shape while
refusing payloads the service would only have to reject again.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from pharos.api.deps import current_user, get_session
from pharos.api.schemas import as_utc
from pharos.db.models import InkStroke, User
from pharos.services import ink
from pharos.services.annotate import AnnotateError


class _InkRoute(APIRoute):
    """Map every :class:`AnnotateError` to its HTTP status, in one place.

    Same shape as ``api/annotate.py``'s route class: an uncaught ``NotFound``
    would otherwise surface as a 500 with a traceback where the contract
    promised a 404.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except AnnotateError as exc:
                raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

        return handler


router = APIRouter(prefix="/api", tags=["ink"], route_class=_InkRoute)


# ------------------------------------------------------------------- schemas


class PointIn(BaseModel):
    """One ink sample: PDF points at scale 1, bottom-left origin, plus pressure.

    ``p`` is optional and defaults to 0.5 — the value every pointer backend
    reports for a device without a pressure digitiser — so a stroke from a
    mouse or a trackpad never needs to invent pressure.
    """

    model_config = ConfigDict(extra="forbid")

    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    p: float = Field(default=0.5, ge=0.0, le=1.0, allow_inf_nan=False)


class PointOut(BaseModel):
    """As :class:`PointIn`, on the way out."""

    x: float
    y: float
    p: float


class InkOut(BaseModel):
    id: str
    paper_id: str
    #: "original" | "mono" | "dual" — the rendition this stroke lives on.
    kind: str
    #: 1-based, within that rendition.
    page: int
    points: list[PointOut]
    #: A token name the frontend resolves to a ``--c-ink-*`` CSS variable.
    color: str
    #: Stroke width in PDF points at scale 1, so width scales with zoom.
    width: float
    created_at: datetime


class InkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    page: int
    #: Bounded here as well as in the service so an oversized payload is
    #: refused before FastAPI builds two thousand model instances out of it.
    points: Annotated[list[PointIn], Field(min_length=1, max_length=ink.MAX_POINTS)]
    color: str | None = None
    width: float | None = Field(default=None, ge=ink.MIN_WIDTH, le=ink.MAX_WIDTH)


# ---------------------------------------------------------------- converters


def _stroke_out(row: InkStroke) -> InkOut:
    return InkOut(
        id=row.id,
        paper_id=row.paper_id,
        kind=row.kind,
        page=row.page,
        points=[PointOut(x=p.x, y=p.y, p=p.p) for p in ink.load_points(row.points)],
        color=row.color,
        width=row.width,
        created_at=as_utc(row.created_at),
    )


# ------------------------------------------------------------------- strokes


@router.get("/papers/{paper_id}/ink", response_model=list[InkOut])
def list_strokes(
    paper_id: str,
    kind: str | None = Query(None, description="Limit to one rendition: original | mono | dual."),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> list[InkOut]:
    """The caller's strokes on this paper, oldest first.

    Private like every annotation: two researchers can share the PDF blob, and
    neither ever sees the other's handwriting.
    """
    rows = ink.list_strokes(session, user_id=user.id, paper_id=paper_id, kind=kind)
    return [_stroke_out(r) for r in rows]


@router.post("/papers/{paper_id}/ink", response_model=InkOut, status_code=201)
def create_stroke(
    paper_id: str,
    payload: InkCreate,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> InkOut:
    """Store one finished stroke. Written the moment the pen lifts."""
    row = ink.create_stroke(
        session,
        user_id=user.id,
        paper_id=paper_id,
        kind=payload.kind,
        page=payload.page,
        points=[p.model_dump() for p in payload.points],
        color=payload.color,
        width=payload.width,
    )
    return _stroke_out(row)


@router.delete("/ink/{stroke_id}", status_code=204)
def delete_stroke(
    stroke_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> Response:
    """Remove one stroke. The eraser's whole contract."""
    ink.delete_stroke(session, user_id=user.id, stroke_id=stroke_id)
    return Response(status_code=204)
