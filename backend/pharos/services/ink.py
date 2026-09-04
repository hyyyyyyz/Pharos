"""Ink service — handwritten strokes on a rendition, captured by a stylus.

Owner-scoped throughout with the owner id a *required keyword*, mirroring
:mod:`pharos.services.annotate`: an optional owner is one a caller can forget,
and a forgotten filter here renders one researcher's handwriting in another
researcher's reader.

Two contracts are decided here rather than left to the caller, because both
have to hold everywhere or stored strokes stop landing where they were drawn:

* **``points`` are PDF points at scale 1, bottom-left origin** — the same
  convention as highlight rects, and for the same reason: the reader zooms and
  devices disagree about pixels, so anything captured in screen coordinates is
  correct exactly once. Pressure (``p``, 0..1) rides on every sample because it
  is captured per sample and is what makes the stroke swell and thin like ink.
* **``kind`` anchors a stroke to one rendition.** A stroke drawn on the
  bilingual build has no position on the original's pages and is never shown
  there.

Everything a client sends is treated as hostile input, exactly as in
:mod:`pharos.services.annotate`. ``points`` is free-form JSON from a browser
and the easiest thing in this API to make hostile: a stroke with four hundred
thousand samples, a ``NaN`` that no JSON reader can round-trip, or a negative
width. Each fails at the door instead of becoming a row that breaks the reader
every time the page loads.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from pharos.db.models import InkStroke, Paper
from pharos.services.annotate import (
    KINDS,
    MAX_COORD,
    MAX_PAGE,
    Invalid,
    NotFound,
    _clean_kind,
    _clean_page,
    _require_paper,
)

#: Reused from annotate on purpose: the rendition set is a property of papers,
#: not of annotations, and two copies would drift the first time a new
#: rendition is added.
__all__ = [
    "INK_COLORS",
    "MAX_POINTS",
    "MAX_WIDTH",
    "MIN_WIDTH",
    "Point",
    "clean_points",
    "create_stroke",
    "delete_stroke",
    "dump_points",
    "list_strokes",
    "load_points",
]

INK_COLORS = frozenset({"ink", "amber", "green", "blue", "pink", "purple"})

DEFAULT_COLOR = "ink"

#: Samples per stroke. A stroke is one pen-down to pen-up gesture: even at a
#: 240 Hz digitiser reporting every coalesced sample, a five-second stroke is
#: ~1200 samples. The ceiling keeps a hostile client from posting a megabyte
#: per stroke and making every page load carry it.
MAX_POINTS = 2000

#: Stroke width bounds, in PDF points at scale 1. The floor keeps a
#: sub-pixel hairline from becoming invisible at low zoom, and the ceiling
#: keeps one hostile row from painting half the page black.
MIN_WIDTH = 0.2
MAX_WIDTH = 24.0

DEFAULT_WIDTH = 2.0

#: Pressure, 0..1. Devices without a pressure digitiser report 0.5; anything
#: outside the range would only distort the width mapping downstream.
MAX_PRESSURE = 1.0

#: Coordinates are stored rounded like highlight rects — see
#: ``annotate._COORD_DECIMALS`` for the reasoning.
_COORD_DECIMALS = 2

#: Pressure is rounded coarser: the eye cannot read two decimals of pressure
#: back out of a rendered line, and strokes carry one sample per point.
_PRESSURE_DECIMALS = 3


@dataclass(frozen=True)
class Point:
    """One ink sample: PDF points at scale 1, bottom-left origin, plus pressure."""

    x: float
    y: float
    p: float


def _pressure(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Invalid("point.p must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise Invalid("point.p must be finite")
    if number < 0.0 or number > MAX_PRESSURE:
        raise Invalid("point.p must be between 0 and 1")
    return round(number, _PRESSURE_DECIMALS)


def clean_points(value: object) -> list[Point]:
    """Validate client-supplied stroke samples into the list this module stores.

    The whole stroke is rejected on the first bad sample, matching
    ``clean_rects``: a stroke is one visual object, and silently truncating it
    would paint handwriting the user did not write. A stroke shorter than two
    samples is also rejected client-side, but a single-sample dot is legal
    (a tap with the pen) so the floor here is one.
    """
    if not isinstance(value, list):
        raise Invalid("points must be a list")
    if not value:
        raise Invalid("points must not be empty")
    if len(value) > MAX_POINTS:
        raise Invalid(f"a stroke may have at most {MAX_POINTS} points")

    out: list[Point] = []
    for item in value:
        if not isinstance(item, dict):
            raise Invalid("each point must be an object with x, y and p")
        unknown = set(item) - {"x", "y", "p"}
        if unknown:
            raise Invalid(f"unexpected point keys: {sorted(unknown)}")
        missing = {"x", "y"} - set(item)
        if missing:
            raise Invalid(f"point is missing {sorted(missing)}")
        for key in ("x", "y"):
            raw = item[key]
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise Invalid(f"point.{key} must be a number")
            number = float(raw)
            if not math.isfinite(number):
                raise Invalid(f"point.{key} must be finite")
            if abs(number) > MAX_COORD:
                raise Invalid(f"point.{key} must be within ±{MAX_COORD:g} PDF points")
        out.append(
            Point(
                x=round(float(item["x"]), _COORD_DECIMALS),
                y=round(float(item["y"]), _COORD_DECIMALS),
                p=_pressure(item.get("p", 0.5)),
            )
        )
    return out


def dump_points(points: list[Point]) -> str:
    """Serialise validated samples to the column's JSON form.

    ``allow_nan=False`` is belt and braces over ``clean_points`` — the column
    is read back by a browser, and one ``NaN`` token would make ``JSON.parse``
    throw on the whole response, costing the user every stroke on the paper.
    """
    return json.dumps(
        [{"x": p.x, "y": p.y, "p": p.p} for p in points],
        separators=(",", ":"),
        allow_nan=False,
    )


def load_points(raw: str | None) -> list[Point]:
    """Read a stored ``points`` column back, tolerating a row we did not write.

    Returns an empty list rather than raising, exactly as ``load_rects`` does:
    one unreadable stroke must not take down the reader, and the API filters
    empty strokes out so the user simply does not see that mark.
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return clean_points(parsed)
    except (TypeError, ValueError, Invalid):
        return []


def _clean_color(value: object) -> str:
    if value is None:
        return DEFAULT_COLOR
    if not isinstance(value, str):
        raise Invalid(f"color must be one of {sorted(INK_COLORS)}")
    color = value.strip().lower()
    if not color:
        return DEFAULT_COLOR
    if color not in INK_COLORS:
        raise Invalid(f"color must be one of {sorted(INK_COLORS)}")
    return color


def _clean_width(value: object) -> float:
    if value is None:
        return DEFAULT_WIDTH
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Invalid("width must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise Invalid("width must be finite")
    if number < MIN_WIDTH or number > MAX_WIDTH:
        raise Invalid(f"width must be between {MIN_WIDTH} and {MAX_WIDTH} PDF points")
    return round(number, _COORD_DECIMALS)


def _require_stroke(session: Session, stroke_id: str, *, user_id: str) -> InkStroke:
    """Resolve one of the caller's strokes by id, or the 404 that hides the rest.

    Scoped on ``InkStroke.user_id`` alone, as in ``annotate._require_highlight``:
    the stroke carries its own owner column, and re-deriving ownership through
    the paper would depend on a second row staying consistent.
    """
    if not user_id:
        raise ValueError("user_id is required: every ink query must be owner-scoped")
    row = session.scalar(select(InkStroke).where(InkStroke.id == stroke_id, InkStroke.user_id == user_id))
    if row is None:
        raise NotFound("Stroke not found")
    return row


def list_strokes(
    session: Session, *, user_id: str, paper_id: str, kind: str | None = None
) -> list[InkStroke]:
    """The caller's strokes on one of their papers, oldest first.

    ``kind`` narrows to one rendition — the reader shows one document, and ink
    from the other two has no position in it. An empty-stroke row (a load-time
    casualty, per ``load_points``) is filtered here so the API never serves a
    stroke the client cannot paint.
    """
    paper = _require_paper(session, paper_id, user_id=user_id)
    stmt = select(InkStroke).where(InkStroke.paper_id == paper.id, InkStroke.user_id == user_id)
    if kind is not None:
        stmt = stmt.where(InkStroke.kind == _clean_kind(kind))
    rows = list(session.scalars(stmt.order_by(InkStroke.created_at, InkStroke.id)))
    return [r for r in rows if load_points(r.points)]


def create_stroke(
    session: Session,
    *,
    user_id: str,
    paper_id: str,
    kind: str,
    page: int,
    points: object,
    color: object = None,
    width: object = None,
) -> InkStroke:
    """Store one finished stroke — written the moment the pen lifts.

    ``points`` is typed as ``object`` deliberately, exactly as
    ``create_highlight`` receives ``rects``: this is the boundary where
    untrusted JSON becomes a row, and accepting a pre-validated type would move
    validation to whichever caller happened to run first.
    """
    paper = _require_paper(session, paper_id, user_id=user_id)
    stroke = InkStroke(
        user_id=user_id,
        paper_id=paper.id,
        kind=_clean_kind(kind),
        page=_clean_page(page),
        points=dump_points(clean_points(points)),
        color=_clean_color(color),
        width=_clean_width(width),
    )
    session.add(stroke)
    session.flush()  # populate stroke.id
    return stroke


def delete_stroke(session: Session, *, user_id: str, stroke_id: str) -> None:
    """Remove one stroke. The eraser's whole contract."""
    stroke = _require_stroke(session, stroke_id, user_id=user_id)
    session.delete(stroke)
    session.flush()
