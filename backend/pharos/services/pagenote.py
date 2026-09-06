"""Page-note service — typed text placed on a page: 文本框 and 便利贴.

Same shape as :mod:`pharos.services.tape`, and for the same reasons:
owner-scoped throughout with the owner id a required keyword, coordinates are
PDF user-space points at scale 1 with a bottom-left origin, and every field a
client sends is treated as hostile input and validated at the door rather than
trusted because an earlier caller already checked it.

Like a tape strip and unlike an ink stroke, a page note is edited IN PLACE
(:func:`update_note`) rather than replaced: retyping its text or dragging it
somewhere rewrites some columns, it does not mint a new row. A stroke has to be
replaced because its geometry *is* its samples; a note's is four numbers.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from pharos.db.models import PageNote
from pharos.services.annotate import (
    MAX_COORD,
    Invalid,
    NotFound,
    _clean_kind,
    _clean_page,
    _now,
    _require_paper,
)

__all__ = [
    "MAX_BODY",
    "MAX_FONT",
    "MAX_SIZE",
    "MIN_FONT",
    "MIN_SIZE",
    "NOTE_COLORS",
    "NOTE_STYLES",
    "NoteSpec",
    "create_note",
    "delete_note",
    "list_notes",
    "update_note",
]

#: Characters in one note. A page annotation is a remark, not a document; the
#: ceiling is here for the same reason ink caps its sample count — so a hostile
#: client cannot post a megabyte per note and make every page load carry it.
MAX_BODY = 4000

#: A note box's own width/height bounds, in PDF points at scale 1. Mirrors
#: :mod:`pharos.services.tape`: the floor keeps a mis-tap from minting an
#: unselectable sliver, the ceiling is a shade over a large page's diagonal.
MIN_SIZE = 8.0
MAX_SIZE = 2000.0

#: Font size bounds, PDF points at scale 1. The floor is the point below which
#: text is decoration rather than writing; the ceiling is a heading on a poster.
MIN_FONT = 4.0
MAX_FONT = 96.0

#: The two presentations. Deliberately a closed set, and deliberately about
#: presentation only — both are a position, a size, some text and a colour.
NOTE_STYLES = frozenset({"text", "note"})

#: Token names, never hexes — same contract as ink colours, so the palette
#: stays one CSS file's business and an old row cannot pin a retired colour.
NOTE_COLORS = frozenset(
    {"ink", "red", "amber", "brown", "green", "teal", "blue", "purple", "pink", "gray"}
)

_COORD_DECIMALS = 2


@dataclass(frozen=True)
class NoteSpec:
    """A validated note box, ready to store."""

    x: float
    y: float
    w: float
    h: float


def _clean_coord(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Invalid(f"{name} must be a number")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise Invalid(f"{name} must be finite")
    if abs(number) > MAX_COORD:
        raise Invalid(f"{name} must be within ±{MAX_COORD:g} PDF points")
    return round(number, _COORD_DECIMALS)


def _clean_size(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Invalid(f"{name} must be a number")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise Invalid(f"{name} must be finite")
    if number < MIN_SIZE or number > MAX_SIZE:
        raise Invalid(f"{name} must be between {MIN_SIZE} and {MAX_SIZE} PDF points")
    return round(number, _COORD_DECIMALS)


def _clean_font(value: object) -> float:
    if value is None:
        return 12.0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Invalid("size must be a number")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise Invalid("size must be finite")
    if number < MIN_FONT or number > MAX_FONT:
        raise Invalid(f"size must be between {MIN_FONT} and {MAX_FONT} PDF points")
    return round(number, _COORD_DECIMALS)


def _clean_style(value: object) -> str:
    if value is None:
        return "text"
    if not isinstance(value, str) or value not in NOTE_STYLES:
        raise Invalid(f"style must be one of {sorted(NOTE_STYLES)}")
    return value


def _clean_color(value: object) -> str:
    if value is None:
        return "ink"
    if not isinstance(value, str) or value not in NOTE_COLORS:
        raise Invalid(f"color must be one of {sorted(NOTE_COLORS)}")
    return value


def _clean_body(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise Invalid("body must be a string")
    # NULs cannot round-trip through every driver and mean nothing in a note.
    text = value.replace("\x00", "")
    if len(text) > MAX_BODY:
        raise Invalid(f"a note may hold at most {MAX_BODY} characters")
    return text


def _require_note(session: Session, note_id: str, *, user_id: str) -> PageNote:
    if not user_id:
        raise ValueError("user_id is required: every page-note query must be owner-scoped")
    row = session.scalar(
        select(PageNote).where(PageNote.id == note_id, PageNote.user_id == user_id)
    )
    if row is None:
        raise NotFound("Note not found")
    return row


def list_notes(
    session: Session, *, user_id: str, paper_id: str, kind: str | None = None
) -> list[PageNote]:
    """The caller's page notes on one of their papers, oldest first."""
    paper = _require_paper(session, paper_id, user_id=user_id)
    stmt = select(PageNote).where(PageNote.paper_id == paper.id, PageNote.user_id == user_id)
    if kind is not None:
        stmt = stmt.where(PageNote.kind == _clean_kind(kind))
    return list(session.scalars(stmt.order_by(PageNote.created_at, PageNote.id)))


def create_note(
    session: Session,
    *,
    user_id: str,
    paper_id: str,
    kind: str,
    page: int,
    x: object,
    y: object,
    w: object,
    h: object,
    style: object = None,
    color: object = None,
    size: object = None,
    body: object = None,
) -> PageNote:
    """Place one text box or sticky note.

    Created EMPTY by default, on purpose: the gesture that makes one is a tap,
    and what follows is typing. Demanding the text up front would mean the
    client had to run its own editor before anything existed to edit.
    """
    paper = _require_paper(session, paper_id, user_id=user_id)
    note = PageNote(
        user_id=user_id,
        paper_id=paper.id,
        kind=_clean_kind(kind),
        page=_clean_page(page),
        x=_clean_coord(x, "x"),
        y=_clean_coord(y, "y"),
        w=_clean_size(w, "w"),
        h=_clean_size(h, "h"),
        style=_clean_style(style),
        color=_clean_color(color),
        size=_clean_font(size),
        body=_clean_body(body),
    )
    session.add(note)
    session.flush()
    return note


def update_note(
    session: Session,
    *,
    user_id: str,
    note_id: str,
    x: object = ...,
    y: object = ...,
    w: object = ...,
    h: object = ...,
    style: object = ...,
    color: object = ...,
    size: object = ...,
    body: object = ...,
) -> PageNote:
    """Change any subset of a note's fields.

    ``...`` (not sent) leaves a field untouched — distinct from ``None``, which
    the ``_clean_*`` helpers read as "reset to the default". Typing, dragging
    and recolouring are all just "some of these changed", not three operations.
    """
    note = _require_note(session, note_id, user_id=user_id)
    if x is not ...:
        note.x = _clean_coord(x, "x")
    if y is not ...:
        note.y = _clean_coord(y, "y")
    if w is not ...:
        note.w = _clean_size(w, "w")
    if h is not ...:
        note.h = _clean_size(h, "h")
    if style is not ...:
        note.style = _clean_style(style)
    if color is not ...:
        note.color = _clean_color(color)
    if size is not ...:
        note.size = _clean_font(size)
    if body is not ...:
        note.body = _clean_body(body)
    note.updated_at = _now()
    session.flush()
    return note


def delete_note(session: Session, *, user_id: str, note_id: str) -> None:
    note = _require_note(session, note_id, user_id=user_id)
    session.delete(note)
    session.flush()
