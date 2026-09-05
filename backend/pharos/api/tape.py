"""Tape API — movable strips that cover ink or text until tapped (胶带).

Structure mirrors :mod:`pharos.api.ink` exactly: every endpoint requires
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
from pharos.db.models import TapeMark, User
from pharos.services import tape
from pharos.services.annotate import AnnotateError, MAX_COORD


class _TapeRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except AnnotateError as exc:
                raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

        return handler


router = APIRouter(prefix="/api", tags=["tape"], route_class=_TapeRoute)


# ------------------------------------------------------------------- schemas


class TapeOut(BaseModel):
    id: str
    paper_id: str
    kind: str
    page: int
    x: float
    y: float
    w: float
    h: float
    angle: float
    revealed: bool
    created_at: datetime
    updated_at: datetime | None


class TapeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    page: int
    x: float = Field(allow_inf_nan=False, ge=-MAX_COORD, le=MAX_COORD)
    y: float = Field(allow_inf_nan=False, ge=-MAX_COORD, le=MAX_COORD)
    w: float = Field(allow_inf_nan=False, ge=tape.MIN_SIZE, le=tape.MAX_SIZE)
    h: float = Field(allow_inf_nan=False, ge=tape.MIN_SIZE, le=tape.MAX_SIZE)
    angle: float | None = None
    revealed: bool | None = None


class TapeUpdate(BaseModel):
    """A resize, a straighten, or a reveal/cover tap: any subset of fields.

    A field left out of the request body is untouched; the service tells that
    apart from a field sent as ``null`` (which resets `angle`/`revealed` to
    their default) via `model_fields_set` below.
    """

    model_config = ConfigDict(extra="forbid")

    x: float | None = Field(default=None, allow_inf_nan=False, ge=-MAX_COORD, le=MAX_COORD)
    y: float | None = Field(default=None, allow_inf_nan=False, ge=-MAX_COORD, le=MAX_COORD)
    w: float | None = Field(default=None, allow_inf_nan=False, ge=tape.MIN_SIZE, le=tape.MAX_SIZE)
    h: float | None = Field(default=None, allow_inf_nan=False, ge=tape.MIN_SIZE, le=tape.MAX_SIZE)
    angle: float | None = None
    revealed: bool | None = None


# ---------------------------------------------------------------- converters


def _tape_out(row: TapeMark) -> TapeOut:
    return TapeOut(
        id=row.id,
        paper_id=row.paper_id,
        kind=row.kind,
        page=row.page,
        x=row.x,
        y=row.y,
        w=row.w,
        h=row.h,
        angle=row.angle,
        revealed=row.revealed,
        created_at=as_utc(row.created_at),
        updated_at=as_utc(row.updated_at) if row.updated_at else None,
    )


# -------------------------------------------------------------------- routes


@router.get("/papers/{paper_id}/tape", response_model=list[TapeOut])
def list_tapes(
    paper_id: str,
    kind: str | None = Query(None, description="Limit to one rendition: original | mono | dual."),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> list[TapeOut]:
    rows = tape.list_tapes(session, user_id=user.id, paper_id=paper_id, kind=kind)
    return [_tape_out(r) for r in rows]


@router.post("/papers/{paper_id}/tape", response_model=TapeOut, status_code=201)
def create_tape(
    paper_id: str,
    payload: TapeCreate,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> TapeOut:
    row = tape.create_tape(
        session,
        user_id=user.id,
        paper_id=paper_id,
        kind=payload.kind,
        page=payload.page,
        x=payload.x,
        y=payload.y,
        w=payload.w,
        h=payload.h,
        angle=payload.angle,
        revealed=payload.revealed,
    )
    return _tape_out(row)


@router.patch("/tape/{tape_id}", response_model=TapeOut)
def update_tape(
    tape_id: str,
    payload: TapeUpdate,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> TapeOut:
    """Resize, straighten, or flip covered/revealed — whichever fields the
    caller actually sent."""
    sent = payload.model_fields_set
    row = tape.update_tape(
        session,
        user_id=user.id,
        tape_id=tape_id,
        x=payload.x if "x" in sent else ...,
        y=payload.y if "y" in sent else ...,
        w=payload.w if "w" in sent else ...,
        h=payload.h if "h" in sent else ...,
        angle=payload.angle if "angle" in sent else ...,
        revealed=payload.revealed if "revealed" in sent else ...,
    )
    return _tape_out(row)


@router.delete("/tape/{tape_id}", status_code=204)
def delete_tape(
    tape_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> Response:
    tape.delete_tape(session, user_id=user.id, tape_id=tape_id)
    return Response(status_code=204)
