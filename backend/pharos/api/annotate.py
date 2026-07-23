"""Annotations API — highlights on a rendition, and the paper-level note.

Every endpoint requires ``current_user`` and is scoped to that user by the
service layer, which takes the owner id as a required keyword. Nothing here
compares ids by hand: a row the caller does not own is not fetched and then
rejected, it is never fetched, and the resulting 404 is indistinguishable from
one for an id that was never issued.

Note what the request bodies carry. ``rects`` is free-form JSON geometry from a
browser, and it is the only place in this API where a client supplies numbers
that are written straight to a column. It is validated twice on the way in and
neither pass is redundant — see :class:`RectIn`.
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
from pharos.db.models import Highlight, Note, User
from pharos.services import annotate
from pharos.services.annotate import AnnotateError


class _AnnotateRoute(APIRoute):
    """Map every :class:`AnnotateError` to its HTTP status, in one place.

    A route class rather than a ``try`` per handler, exactly as in
    ``api/organise.py``: the mapping then cannot be forgotten when an endpoint is
    added, and forgetting it would not be cosmetic — an uncaught ``NotFound`` is
    a 500 with a traceback where the contract promised a 404, which is both a bad
    error and a disclosure that the id was real.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except AnnotateError as exc:
                raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

        return handler


router = APIRouter(prefix="/api", tags=["annotate"], route_class=_AnnotateRoute)


# ------------------------------------------------------------------- schemas


class RectIn(BaseModel):
    """One line box of a selection, in PDF points at scale 1, origin bottom-left.

    This model exists for the *edge*: it publishes the shape in OpenAPI, rejects
    a string where a number belongs, forbids extra keys, and — via
    ``allow_inf_nan=False`` — refuses the ``NaN``/``Infinity`` that JSON has no
    way to represent and that would make the stored row unparseable by the
    browser that has to read it back.

    ``annotate.clean_rects`` then validates the same payload again for count,
    bounds, and positive area. That is not belt-and-braces for its own sake: the
    service is a public entry point in its own right (the tests call it directly,
    and any future importer or Zotero annotation sync will too), so it has to
    stand alone. Pydantic guards this door; the service guards the room.
    """

    model_config = ConfigDict(extra="forbid")

    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    w: float = Field(allow_inf_nan=False)
    h: float = Field(allow_inf_nan=False)


class RectOut(BaseModel):
    """As :class:`RectIn`, and the same convention on the way out.

    Separate from the input model so the two can never be coupled by accident:
    an output type that also validates input is one edit away from a laxer API.
    """

    x: float
    y: float
    w: float
    h: float


class HighlightOut(BaseModel):
    id: str
    paper_id: str
    #: "original" | "mono" | "dual" — the rendition this was drawn on.
    kind: str
    #: 1-based, within that rendition.
    page: int
    rects: list[RectOut]
    text: str | None = None
    #: One of ``annotate.HIGHLIGHT_COLORS``. Always a token name the frontend
    #: resolves to a ``--c-*`` CSS variable, never a colour value.
    color: str
    note: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class HighlightCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    page: int
    #: Bounded here as well as in the service so an oversized payload is refused
    #: before FastAPI builds four hundred thousand model instances out of it.
    rects: Annotated[list[RectIn], Field(min_length=1, max_length=annotate.MAX_RECTS)]
    text: str | None = None
    color: str | None = None
    note: str | None = None


class HighlightPatch(BaseModel):
    """Omitted keys are left alone; an explicit ``note: null`` clears the note.

    Geometry is absent by design — see ``annotate.update_highlight``.
    """

    model_config = ConfigDict(extra="forbid")

    color: str | None = None
    note: str | None = None


class NoteOut(BaseModel):
    """The paper-level note.

    ``body`` is ``""`` when no note has ever been written, so the client renders
    an empty editor rather than having to branch on a null. ``updated_at`` stays
    null in that case, which is what distinguishes "never written" from "written
    and then cleared" for anything that cares.
    """

    paper_id: str
    body: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class NoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Length-capped at the edge as well as in the service, so a hundred-megabyte
    #: body is rejected before it is parsed rather than after.
    body: Annotated[str, Field(max_length=annotate.MAX_NOTE_BODY)]


# ---------------------------------------------------------------- converters


def _highlight_out(row: Highlight) -> HighlightOut:
    """Render a stored highlight.

    ``load_rects`` returns an empty list for a row this service did not write
    (a hand-edited database, a future importer). Such a highlight is served with
    no geometry rather than failing the request: one unpaintable mark must not
    cost the user every other highlight on the paper.
    """
    return HighlightOut(
        id=row.id,
        paper_id=row.paper_id,
        kind=row.kind,
        page=row.page,
        rects=[RectOut(x=r.x, y=r.y, w=r.w, h=r.h) for r in annotate.load_rects(row.rects)],
        text=row.text,
        color=row.color,
        note=row.note,
        created_at=as_utc(row.created_at),
        updated_at=as_utc(row.updated_at),
    )


def _note_out(paper_id: str, row: Note | None) -> NoteOut:
    if row is None:
        return NoteOut(paper_id=paper_id, body="")
    return NoteOut(
        paper_id=paper_id,
        body=row.body,
        created_at=as_utc(row.created_at),
        updated_at=as_utc(row.updated_at),
    )


# ------------------------------------------------------------------ highlights


@router.get("/papers/{paper_id}/highlights", response_model=list[HighlightOut])
def list_highlights(
    paper_id: str,
    kind: str | None = Query(
        None, description="Limit to one rendition: original | mono | dual."
    ),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> list[HighlightOut]:
    """The caller's highlights on this paper, oldest first.

    Only ever the caller's. A paper is a shared *document* — two researchers can
    upload the same PDF and share the blob on disk — but a highlight is a private
    reading of it, and there is no view in this product where one user sees
    another's marks.
    """
    rows = annotate.list_highlights(session, user_id=user.id, paper_id=paper_id, kind=kind)
    return [_highlight_out(r) for r in rows]


@router.post("/papers/{paper_id}/highlights", response_model=HighlightOut, status_code=201)
def create_highlight(
    paper_id: str,
    payload: HighlightCreate,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> HighlightOut:
    row = annotate.create_highlight(
        session,
        user_id=user.id,
        paper_id=paper_id,
        kind=payload.kind,
        page=payload.page,
        # Dumped back to plain dicts so the service validates the same untyped
        # shape it would get from any other caller, rather than a pre-blessed one.
        rects=[r.model_dump() for r in payload.rects],
        text=payload.text,
        color=payload.color,
        note=payload.note,
    )
    return _highlight_out(row)


@router.patch("/highlights/{highlight_id}", response_model=HighlightOut)
def patch_highlight(
    highlight_id: str,
    patch: HighlightPatch,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> HighlightOut:
    # exclude_unset is what separates "leave this alone" from "set this to null".
    changes = patch.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No fields provided")
    row = annotate.update_highlight(
        session, user_id=user.id, highlight_id=highlight_id, changes=changes
    )
    return _highlight_out(row)


@router.delete("/highlights/{highlight_id}", status_code=204)
def delete_highlight(
    highlight_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> Response:
    annotate.delete_highlight(session, user_id=user.id, highlight_id=highlight_id)
    return Response(status_code=204)


# ----------------------------------------------------------------------- note


@router.get("/papers/{paper_id}/note", response_model=NoteOut)
def get_note(
    paper_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> NoteOut:
    """The caller's note on this paper; ``body`` is ``""`` when there is none."""
    return _note_out(paper_id, annotate.get_note(session, user_id=user.id, paper_id=paper_id))


@router.put("/papers/{paper_id}/note", response_model=NoteOut)
def put_note(
    paper_id: str,
    payload: NoteIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> NoteOut:
    """Upsert the note. PUT rather than POST/PATCH: there is exactly one per
    paper per user, and sending the whole body is the only operation."""
    row = annotate.set_note(session, user_id=user.id, paper_id=paper_id, body=payload.body)
    return _note_out(paper_id, row)
