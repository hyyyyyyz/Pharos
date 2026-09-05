"""Tape service — movable strips that cover ink or text until tapped (胶带).

Same shape as :mod:`pharos.services.ink`, and for the same reasons: owner-scoped
throughout with the owner id a required keyword, coordinates are PDF user-space
points at scale 1 with a bottom-left origin, and every field a client sends is
treated as hostile input and validated at the door rather than trusted because
a earlier caller already checked it.

A tape mark is a plain rectangle — a centre point, a length and thickness, a
rotation, and a covered/revealed flag — never a sampled path, so unlike a
stroke it is edited in place (`update_tape`) rather than replaced: a resize
rewrites some numbers, it does not mint a new row the way moving a lasso
selection does for ink.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from pharos.db.models import TapeMark
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
    "MAX_PATH_POINTS",
    "MAX_SIZE",
    "MIN_SIZE",
    "TapeSpec",
    "create_tape",
    "delete_tape",
    "dump_path",
    "list_tapes",
    "load_path",
    "update_tape",
]

#: Samples in a freehand strip's path. A cover laid over a line or two of text
#: is tens of points; the ceiling is here for the same reason ink has one —
#: so a hostile client cannot post a megabyte per strip and make every page
#: load carry it. Lower than ink's, because tape is a broad cover rather than
#: handwriting and has no fine detail to lose.
MAX_PATH_POINTS = 600

#: A tape strip's own width/height bounds, in PDF points at scale 1. The floor
#: keeps a mis-tap from minting an invisible sliver; the ceiling is a shade
#: over a large page's own diagonal, past which "resize" and "cover the whole
#: document" stop being different requests.
MIN_SIZE = 4.0
MAX_SIZE = 2000.0

_COORD_DECIMALS = 2


@dataclass(frozen=True)
class TapeSpec:
    """A validated tape rectangle, ready to store."""

    x: float
    y: float
    w: float
    h: float
    angle: float
    revealed: bool


def _clean_coord(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Invalid(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise Invalid(f"{name} must be finite")
    if abs(number) > MAX_COORD:
        raise Invalid(f"{name} must be within ±{MAX_COORD:g} PDF points")
    return round(number, _COORD_DECIMALS)


def _clean_size(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Invalid(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise Invalid(f"{name} must be finite")
    if number < MIN_SIZE or number > MAX_SIZE:
        raise Invalid(f"{name} must be between {MIN_SIZE} and {MAX_SIZE} PDF points")
    return round(number, _COORD_DECIMALS)


def _clean_angle(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Invalid("angle must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise Invalid("angle must be finite")
    # Normalise into [0, 360) rather than reject a client that sent -90 or 720
    # — a full turn (or three) is still a well-defined orientation.
    return round(number % 360.0, 2)


def _clean_revealed(value: object) -> bool:
    if value is None:
        return False
    if not isinstance(value, bool):
        raise Invalid("revealed must be a boolean")
    return value


def clean_path(value: object) -> list[tuple[float, float]]:
    """Validate a freehand strip's path, the way ``ink.clean_points`` does.

    Rejects the whole path on the first bad sample rather than truncating: a
    strip is one visual object, and half a cover is not a cover. Two points is
    the floor — one point is a dot, which the straight-strip fields already
    describe better.
    """
    if not isinstance(value, list):
        raise Invalid("points must be a list")
    if len(value) < 2:
        raise Invalid("a freehand strip needs at least two points")
    if len(value) > MAX_PATH_POINTS:
        raise Invalid(f"a strip may have at most {MAX_PATH_POINTS} points")
    out: list[tuple[float, float]] = []
    for item in value:
        if not isinstance(item, dict):
            raise Invalid("each point must be an object with x and y")
        unknown = set(item) - {"x", "y"}
        if unknown:
            raise Invalid(f"unexpected point keys: {sorted(unknown)}")
        if {"x", "y"} - set(item):
            raise Invalid("point is missing x or y")
        out.append((_clean_coord(item["x"], "point.x"), _clean_coord(item["y"], "point.y")))
    return out


def dump_path(path: list[tuple[float, float]]) -> str:
    """Serialise a validated path. ``allow_nan=False`` is belt and braces over
    ``clean_path``: one ``NaN`` token would make ``JSON.parse`` throw in the
    browser and cost the reader every strip on the page, not just this one."""
    return json.dumps(
        [{"x": x, "y": y} for x, y in path], separators=(",", ":"), allow_nan=False
    )


def load_path(raw: str | None) -> list[tuple[float, float]] | None:
    """Read a stored path back, tolerating a row we did not write. ``None``
    means "straight strip" — both for a NULL column and for an unreadable one,
    since a strip that still has its bounding box is better shown straight
    than not shown at all."""
    if not raw:
        return None
    try:
        return clean_path(json.loads(raw))
    except (TypeError, ValueError, Invalid):
        return None


def _require_tape(session: Session, tape_id: str, *, user_id: str) -> TapeMark:
    if not user_id:
        raise ValueError("user_id is required: every tape query must be owner-scoped")
    row = session.scalar(select(TapeMark).where(TapeMark.id == tape_id, TapeMark.user_id == user_id))
    if row is None:
        raise NotFound("Tape mark not found")
    return row


def list_tapes(session: Session, *, user_id: str, paper_id: str, kind: str | None = None) -> list[TapeMark]:
    """The caller's tape marks on one of their papers, oldest first."""
    paper = _require_paper(session, paper_id, user_id=user_id)
    stmt = select(TapeMark).where(TapeMark.paper_id == paper.id, TapeMark.user_id == user_id)
    if kind is not None:
        stmt = stmt.where(TapeMark.kind == _clean_kind(kind))
    return list(session.scalars(stmt.order_by(TapeMark.created_at, TapeMark.id)))


def create_tape(
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
    angle: object = None,
    revealed: object = None,
    points: object = None,
) -> TapeMark:
    """Place one tape strip — covered by default, exactly a fresh piece of tape.

    ``points`` present means a freehand strip that follows the pen's own path;
    absent (or None) means a straight run described by ``(x, y, w, h, angle)``.
    Either way those five carry the bounding box, so nothing downstream has to
    know which kind it got before it can place a popover or hit-test a tap.
    """
    paper = _require_paper(session, paper_id, user_id=user_id)
    tape = TapeMark(
        user_id=user_id,
        paper_id=paper.id,
        kind=_clean_kind(kind),
        page=_clean_page(page),
        x=_clean_coord(x, "x"),
        y=_clean_coord(y, "y"),
        w=_clean_size(w, "w"),
        h=_clean_size(h, "h"),
        angle=_clean_angle(angle),
        revealed=_clean_revealed(revealed),
        points=None if points is None else dump_path(clean_path(points)),
    )
    session.add(tape)
    session.flush()
    return tape


def update_tape(
    session: Session,
    *,
    user_id: str,
    tape_id: str,
    x: object = ...,
    y: object = ...,
    w: object = ...,
    h: object = ...,
    angle: object = ...,
    revealed: object = ...,
) -> TapeMark:
    """Change any subset of a tape mark's fields — a resize, a straighten, or a
    reveal/cover tap are all just "some of these fields changed", not three
    different operations. ``...`` (not sent) leaves a field untouched, unlike
    ``None`` on `angle`/`revealed`, which those clean_* functions treat as
    "reset to the default" — the two must stay distinguishable, which is why
    this is the one function in the module that accepts a sentinel instead of
    `None` for "not sent".
    """
    tape = _require_tape(session, tape_id, user_id=user_id)
    if x is not ...:
        tape.x = _clean_coord(x, "x")
    if y is not ...:
        tape.y = _clean_coord(y, "y")
    if w is not ...:
        tape.w = _clean_size(w, "w")
    if h is not ...:
        tape.h = _clean_size(h, "h")
    if angle is not ...:
        tape.angle = _clean_angle(angle)
    if revealed is not ...:
        tape.revealed = _clean_revealed(revealed)
    tape.updated_at = _now()
    session.flush()
    return tape


def delete_tape(session: Session, *, user_id: str, tape_id: str) -> None:
    tape = _require_tape(session, tape_id, user_id=user_id)
    session.delete(tape)
    session.flush()
