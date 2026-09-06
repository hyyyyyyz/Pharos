"""Page-note API — typed text placed on a page: 文本框 and 便利贴.

Structure mirrors :mod:`pharos.api.tape` exactly: every endpoint requires
``current_user`` and is scoped by the service layer, the route class maps
``AnnotateError`` onto its HTTP status in one place, and Pydantic models with
``extra="forbid"`` publish the wire shape while refusing payloads the service
would only have to reject again.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from pharos.api.deps import current_user, get_session
from pharos.api.schemas import as_utc
from pharos.db.models import PageNote, User
from pharos.services import pagenote
from pharos.services.annotate import AnnotateError, MAX_COORD


class _NoteRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except AnnotateError as exc:
                raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

        return handler


router = APIRouter(prefix="/api", tags=["pagenote"], route_class=_NoteRoute)


# ------------------------------------------------------------------- schemas


class PageNoteOut(BaseModel):
    id: str
    paper_id: str
    kind: str
    page: int
    x: float
    y: float
    w: float
    h: float
    #: "text" | "note".
    style: str
    #: A token name the frontend resolves to a CSS variable, never a hex.
    color: str
    #: Font size in PDF points at scale 1.
    size: float
    body: str
    created_at: datetime
    updated_at: datetime | None


class PageNoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    page: int
    x: float = Field(allow_inf_nan=False, ge=-MAX_COORD, le=MAX_COORD)
    y: float = Field(allow_inf_nan=False, ge=-MAX_COORD, le=MAX_COORD)
    w: float = Field(allow_inf_nan=False, ge=pagenote.MIN_SIZE, le=pagenote.MAX_SIZE)
    h: float = Field(allow_inf_nan=False, ge=pagenote.MIN_SIZE, le=pagenote.MAX_SIZE)
    style: str | None = None
    color: str | None = None
    size: float | None = Field(default=None, allow_inf_nan=False, ge=pagenote.MIN_FONT, le=pagenote.MAX_FONT)
    #: Bounded here as well as in the service so an oversized body is refused
    #: at the door rather than after it has been parsed.
    body: str | None = Field(default=None, max_length=pagenote.MAX_BODY)


class PageNoteUpdate(BaseModel):
    """Typing, dragging, resizing or recolouring: any subset of fields.

    A field left out of the request body is untouched; the service tells that
    apart from a field sent as ``null`` (which resets it to its default) via
    ``model_fields_set`` below.
    """

    model_config = ConfigDict(extra="forbid")

    x: float | None = Field(default=None, allow_inf_nan=False, ge=-MAX_COORD, le=MAX_COORD)
    y: float | None = Field(default=None, allow_inf_nan=False, ge=-MAX_COORD, le=MAX_COORD)
    w: float | None = Field(default=None, allow_inf_nan=False, ge=pagenote.MIN_SIZE, le=pagenote.MAX_SIZE)
    h: float | None = Field(default=None, allow_inf_nan=False, ge=pagenote.MIN_SIZE, le=pagenote.MAX_SIZE)
    style: str | None = None
    color: str | None = None
    size: float | None = Field(default=None, allow_inf_nan=False, ge=pagenote.MIN_FONT, le=pagenote.MAX_FONT)
    body: str | None = Field(default=None, max_length=pagenote.MAX_BODY)


# ---------------------------------------------------------------- converters


def _note_out(row: PageNote) -> PageNoteOut:
    return PageNoteOut(
        id=row.id,
        paper_id=row.paper_id,
        kind=row.kind,
        page=row.page,
        x=row.x,
        y=row.y,
        w=row.w,
        h=row.h,
        style=row.style,
        color=row.color,
        size=row.size,
        body=row.body,
        created_at=as_utc(row.created_at),
        updated_at=as_utc(row.updated_at) if row.updated_at else None,
    )


# -------------------------------------------------------------------- routes


@router.get("/papers/{paper_id}/notes", response_model=list[PageNoteOut])
def list_page_notes(
    paper_id: str,
    kind: str | None = Query(None, description="Limit to one rendition: original | mono | dual."),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> list[PageNoteOut]:
    rows = pagenote.list_notes(session, user_id=user.id, paper_id=paper_id, kind=kind)
    return [_note_out(r) for r in rows]


@router.post("/papers/{paper_id}/notes", response_model=PageNoteOut, status_code=201)
def create_page_note(
    paper_id: str,
    payload: PageNoteCreate,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> PageNoteOut:
    row = pagenote.create_note(
        session,
        user_id=user.id,
        paper_id=paper_id,
        kind=payload.kind,
        page=payload.page,
        x=payload.x,
        y=payload.y,
        w=payload.w,
        h=payload.h,
        style=payload.style,
        color=payload.color,
        size=payload.size,
        body=payload.body,
    )
    return _note_out(row)


@router.patch("/notes/{note_id}", response_model=PageNoteOut)
def update_page_note(
    note_id: str,
    payload: PageNoteUpdate,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> PageNoteOut:
    sent = payload.model_fields_set
    row = pagenote.update_note(
        session,
        user_id=user.id,
        note_id=note_id,
        x=payload.x if "x" in sent else ...,
        y=payload.y if "y" in sent else ...,
        w=payload.w if "w" in sent else ...,
        h=payload.h if "h" in sent else ...,
        style=payload.style if "style" in sent else ...,
        color=payload.color if "color" in sent else ...,
        size=payload.size if "size" in sent else ...,
        body=payload.body if "body" in sent else ...,
    )
    return _note_out(row)


@router.delete("/notes/{note_id}", status_code=204)
def delete_page_note(
    note_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> Response:
    pagenote.delete_note(session, user_id=user.id, note_id=note_id)
    return Response(status_code=204)
