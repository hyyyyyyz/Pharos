"""Annotation service — passage highlights, and the one note per paper.

Owner-scoped throughout, with the owner id a *required keyword* on every entry
point, for the same reason it is in :mod:`pharos.services.library` and
:mod:`pharos.services.organise`: an optional owner is one a caller can forget,
and a forgotten filter here is not a wrong number on screen — it is one
researcher's marginalia rendered in another researcher's reader. Required means
the omission is a type error rather than a leak.

Two things are decided here rather than left to the caller, because both have to
be the same everywhere or stored highlights stop landing where they were drawn:

* **``rects`` are PDF points at scale 1, with a bottom-left origin**, which is
  PDF user space — not screen pixels, not CSS pixels, and not top-left. The
  reader zooms, and a browser on another machine has another device pixel ratio,
  so anything captured in device coordinates would be correct exactly once. The
  conversion is the client's job (see ``HighlightLayer.tsx``); what this module
  owns is refusing to store anything that cannot be a PDF rectangle.
* **``kind`` anchors a highlight to one rendition.** The English original, the
  Chinese rebuild and the bilingual build are three different documents whose
  pages do not correspond, so a highlight drawn on one has no position in the
  others and is never shown there.

Everything a client sends is treated as hostile input. ``rects`` in particular
is free-form JSON, so it is parsed and re-serialised here rather than stored as
received: a megabyte of rectangles per highlight, a NaN that no JSON reader can
round-trip, or a string where a number belongs must all fail at the door instead
of becoming a row that breaks the reader every time it loads.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from pharos.db.models import Highlight, Note, Paper

#: The three renditions a paper can have. Mirrors ``api/papers.py``'s ``_KINDS``;
#: a highlight names the one it was drawn on.
KINDS = frozenset({"original", "mono", "dual"})

#: Accepted highlight accents. Deliberately a closed set of *token names*, never
#: a colour value — the frontend maps each to a ``--c-*`` CSS variable, and
#: letting a hex through the API would put a hard-coded colour in the database
#: that no theme could ever override. Mirrors ``organise.TAG_COLORS``.
HIGHLIGHT_COLORS = frozenset({"amber", "green", "blue", "pink", "purple"})

DEFAULT_COLOR = "amber"

#: Rectangles per highlight. One per *line* of the selection, so a long
#: paragraph spanning a column is tens; a whole page of dense two-column text is
#: well under a hundred. The ceiling exists because a hostile client could
#: otherwise post a megabyte of rectangles per highlight and make every
#: subsequent page load carry it.
MAX_RECTS = 400

#: Largest coordinate a PDF rectangle may carry, in points. The PDF spec caps a
#: page at 14400 units (200 inches) per side, so nothing on a legal page can
#: exceed it. Rects are allowed to run slightly negative — a selection rectangle
#: rounded outward at the page edge legitimately does — but not unboundedly.
MAX_COORD = 14400.0

#: Coordinates are stored rounded to this many decimals. At 0.01pt that is about
#: 3.5 micrometres on paper: far finer than any selection is meaningful to, and
#: it keeps the stored JSON compact instead of carrying 17 float digits per
#: number that no reader could ever perceive.
_COORD_DECIMALS = 2

#: Upper bound on a page number. NOT checked against ``Paper.page_count``, and
#: that is deliberate: ``page_count`` describes the *original*, while the dual
#: rendition interleaves source and translation and so legitimately has more
#: pages than the paper it came from. Validating against it would reject valid
#: highlights on the bilingual build. This is a sanity bound, nothing more.
MAX_PAGE = 100_000

#: The selected passage, kept so a highlight is still readable if the PDF is
#: ever re-rendered. Bounded because a selection is a passage, not a document.
MAX_TEXT = 20_000

#: A comment anchored to one passage.
MAX_HIGHLIGHT_NOTE = 8_000

#: The paper-level 笔记 block. Much larger — this is where a user writes up a
#: whole paper, and truncating that would lose work.
MAX_NOTE_BODY = 200_000


class AnnotateError(Exception):
    """Base for the failures this service reports to its caller.

    Each subclass carries the HTTP status it deserves, so the API layer maps
    them in one place and cannot forget a case — see ``pharos.api.annotate``.
    """

    status_code = 400


class NotFound(AnnotateError):
    """The row does not exist, or does not belong to the caller.

    One class for both, on purpose. The API turns this into a 404, never a 403:
    403 would confirm the id is real and turn the endpoint into an oracle for
    walking ids across other users' libraries. 404 says nothing at all.
    """

    status_code = 404


class Invalid(AnnotateError):
    """The request is malformed — a bad kind, an impossible rectangle, no body."""

    status_code = 400


@dataclass(frozen=True)
class Rect:
    """One highlighted line, in PDF points at scale 1, origin bottom-left.

    ``x``/``y`` are the rectangle's *lower-left* corner, matching how every
    rectangle in a PDF is written down. Storing the top-left corner instead
    would work equally well in isolation, but only if every producer and every
    consumer agreed — and the one that eventually disagrees produces highlights
    that are subtly, unreproducibly misplaced. Naming the convention here, in
    the only place both sides have to read, is the cheapest way to hold it.
    """

    x: float
    y: float
    w: float
    h: float


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _require_owner(user_id: str) -> str:
    """Reject a falsy owner id before it can reach a WHERE clause.

    ``Paper.user_id`` is nullable so the pre-accounts migration could run, so a
    ``None`` threaded through here renders ``user_id IS NULL`` and quietly
    matches the legacy rows rather than failing. Mirrors the identical guards in
    ``library`` and ``organise``.
    """
    if not user_id:
        raise ValueError("user_id is required: every annotate query must be owner-scoped")
    return user_id


def _require_paper(session: Session, paper_id: str, *, user_id: str) -> Paper:
    """Resolve one of ``user_id``'s papers, or raise the 404 that hides the rest.

    A filtered SELECT rather than ``session.get`` plus a check afterwards: delete
    a line from this and the query fails to compile a scope, instead of quietly
    returning every user's row.
    """
    _require_owner(user_id)
    row = session.scalar(select(Paper).where(Paper.id == paper_id, Paper.user_id == user_id))
    if row is None:
        raise NotFound("Paper not found")
    return row


def _require_highlight(session: Session, highlight_id: str, *, user_id: str) -> Highlight:
    """Resolve one of the caller's highlights by id.

    Scoped on ``Highlight.user_id`` alone rather than by joining back to
    ``papers``: the highlight carries its own owner, and that column is what the
    write path set. Re-deriving ownership through the paper would make the check
    depend on a second row staying consistent with the first, which is a longer
    chain than it needs to be for the same answer.
    """
    _require_owner(user_id)
    row = session.scalar(
        select(Highlight).where(Highlight.id == highlight_id, Highlight.user_id == user_id)
    )
    if row is None:
        raise NotFound("Highlight not found")
    return row


# ------------------------------------------------------------------ validation


def _clean_kind(value: object) -> str:
    if not isinstance(value, str):
        raise Invalid(f"kind must be one of {sorted(KINDS)}")
    kind = value.strip().lower()
    if kind not in KINDS:
        raise Invalid(f"kind must be one of {sorted(KINDS)}")
    return kind


def _clean_color(value: object) -> str:
    if value is None:
        return DEFAULT_COLOR
    if not isinstance(value, str):
        raise Invalid(f"color must be one of {sorted(HIGHLIGHT_COLORS)}")
    color = value.strip().lower()
    if not color:
        return DEFAULT_COLOR
    if color not in HIGHLIGHT_COLORS:
        raise Invalid(f"color must be one of {sorted(HIGHLIGHT_COLORS)}")
    return color


def _clean_page(value: object) -> int:
    # ``bool`` is an ``int`` subclass, so ``True`` would otherwise sail through
    # as page 1 — a nonsense request accepted as a plausible one.
    if not isinstance(value, int) or isinstance(value, bool):
        raise Invalid("page must be an integer")
    if value < 1 or value > MAX_PAGE:
        raise Invalid(f"page must be between 1 and {MAX_PAGE}")
    return value


def _clean_text(value: object, *, limit: int, field: str) -> str | None:
    """Trim an optional free-text field; blank means "no value", not empty.

    Truncates rather than rejecting when it is too long, unlike every other
    guard here. A selection that runs past the cap is a user who dragged across
    half a paper, and losing the tail of the quoted text is a far better outcome
    than losing the highlight they just drew.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise Invalid(f"{field} must be a string")
    trimmed = value.strip()
    if not trimmed:
        return None
    return trimmed[:limit]


def _coord(value: object, *, field: str) -> float:
    """One rectangle component: a real, finite number within page bounds.

    ``bool`` is excluded explicitly for the same reason as in ``_clean_page``,
    and non-finite values are rejected because ``json.dumps`` writes ``NaN`` and
    ``Infinity`` — tokens that are not JSON, that ``JSON.parse`` in the browser
    refuses outright, and that would therefore make the highlight unreadable to
    the only client that will ever ask for it.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Invalid(f"rect.{field} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise Invalid(f"rect.{field} must be a finite number")
    if abs(number) > MAX_COORD:
        raise Invalid(f"rect.{field} must be within ±{MAX_COORD:g} PDF points")
    return round(number, _COORD_DECIMALS)


def clean_rects(value: object) -> list[Rect]:
    """Validate client-supplied ``rects`` into the list this module will store.

    The whole payload is rejected on the first bad rectangle rather than the bad
    one being dropped. A highlight is one visual object made of several line
    boxes: silently storing three of a passage's four lines would paint a
    highlight the user did not draw and give them no way to notice, whereas an
    error is a thing they can see and retry.

    Zero-area rectangles are rejected rather than skipped for the same reason —
    they are evidence the caller's coordinate maths went wrong, and swallowing
    them hides the bug this validation exists to surface.
    """
    if not isinstance(value, list):
        raise Invalid("rects must be a list")
    if not value:
        raise Invalid("rects must not be empty")
    if len(value) > MAX_RECTS:
        raise Invalid(f"a highlight may have at most {MAX_RECTS} rectangles")

    out: list[Rect] = []
    for item in value:
        if not isinstance(item, dict):
            raise Invalid("each rect must be an object with x, y, w and h")
        unknown = set(item) - {"x", "y", "w", "h"}
        if unknown:
            raise Invalid(f"unexpected rect keys: {sorted(unknown)}")
        missing = {"x", "y", "w", "h"} - set(item)
        if missing:
            raise Invalid(f"rect is missing {sorted(missing)}")
        rect = Rect(
            x=_coord(item["x"], field="x"),
            y=_coord(item["y"], field="y"),
            w=_coord(item["w"], field="w"),
            h=_coord(item["h"], field="h"),
        )
        if rect.w <= 0 or rect.h <= 0:
            raise Invalid("rect.w and rect.h must be greater than zero")
        out.append(rect)
    return out


def dump_rects(rects: list[Rect]) -> str:
    """Serialise validated rectangles to the column's JSON form.

    ``allow_nan=False`` is belt and braces over ``_coord``: the column is read
    back by a browser, and a single ``NaN`` token would make ``JSON.parse``
    throw on the whole response — every highlight on the paper lost, not just
    the bad one. Cheap enough to assert twice.
    """
    return json.dumps(
        [{"x": r.x, "y": r.y, "w": r.w, "h": r.h} for r in rects],
        separators=(",", ":"),
        allow_nan=False,
    )


def load_rects(raw: str | None) -> list[Rect]:
    """Read a stored ``rects`` column back, tolerating a row we did not write.

    Everything written through this module is already valid, so this only fires
    for a hand-edited database or a row from some future importer. It returns an
    empty list rather than raising, because one unreadable highlight must not
    take down the whole reader — the API filters those out and the user simply
    does not see that mark.
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    try:
        return clean_rects(parsed)
    except Invalid:
        return []


# ------------------------------------------------------------------ highlights


def list_highlights(
    session: Session, *, user_id: str, paper_id: str, kind: str | None = None
) -> list[Highlight]:
    """The caller's highlights on one of their papers, oldest first.

    ``kind`` narrows to a single rendition, which is what the reader asks for:
    it is showing one document, and highlights from the other two have no
    position in it.

    Ordered by creation so a highlight the user just made appears last and
    stably, rather than jumping around as the list is refetched. Both the paper
    and the highlights are scoped to the caller — the paper because an id that
    is not theirs must 404, the highlights because their own owner column is the
    one the write path set.
    """
    paper = _require_paper(session, paper_id, user_id=user_id)
    stmt = select(Highlight).where(
        Highlight.paper_id == paper.id, Highlight.user_id == user_id
    )
    if kind is not None:
        stmt = stmt.where(Highlight.kind == _clean_kind(kind))
    return list(session.scalars(stmt.order_by(Highlight.created_at, Highlight.id)))


def create_highlight(
    session: Session,
    *,
    user_id: str,
    paper_id: str,
    kind: str,
    page: int,
    rects: object,
    text: object = None,
    color: object = None,
    note: object = None,
) -> Highlight:
    """Store one marked passage.

    ``rects`` is typed as ``object`` rather than ``list[Rect]`` on purpose: this
    is the boundary where untrusted JSON becomes a row, and accepting the parsed
    type would move the validation to whichever caller happened to run first —
    which is exactly the arrangement that lets the second caller skip it.
    """
    paper = _require_paper(session, paper_id, user_id=user_id)
    highlight = Highlight(
        user_id=user_id,
        paper_id=paper.id,
        kind=_clean_kind(kind),
        page=_clean_page(page),
        rects=dump_rects(clean_rects(rects)),
        text=_clean_text(text, limit=MAX_TEXT, field="text"),
        color=_clean_color(color),
        note=_clean_text(note, limit=MAX_HIGHLIGHT_NOTE, field="note"),
    )
    session.add(highlight)
    session.flush()  # populate highlight.id
    return highlight


def update_highlight(
    session: Session, *, user_id: str, highlight_id: str, changes: dict[str, object]
) -> Highlight:
    """Recolour a highlight or edit its note.

    Only those two are mutable. Geometry is not: ``page``, ``kind`` and ``rects``
    describe *where the user dragged*, and a highlight that could be moved to
    another page by a PATCH is no longer a record of anything. Re-marking the
    right passage is a delete plus a create, which is also what the UI does.

    ``changes`` carries only the keys the client actually sent, so clearing a
    note (``note: null``) stays distinguishable from leaving it alone.
    """
    highlight = _require_highlight(session, highlight_id, user_id=user_id)
    if "color" in changes:
        # An explicit null means "back to the default", not "no colour": the
        # column is NOT NULL and a highlight with no colour cannot be painted.
        highlight.color = _clean_color(changes["color"])
    if "note" in changes:
        highlight.note = _clean_text(
            changes["note"], limit=MAX_HIGHLIGHT_NOTE, field="note"
        )
    highlight.updated_at = _now()
    session.flush()
    return highlight


def delete_highlight(session: Session, *, user_id: str, highlight_id: str) -> None:
    """Remove a highlight. Nothing else is touched.

    No soft delete: unlike a paper, a highlight is a few hundred bytes the user
    can redraw in a second, and a recycle bin nobody would ever open is just a
    second place for their reader to disagree with itself.
    """
    highlight = _require_highlight(session, highlight_id, user_id=user_id)
    session.delete(highlight)
    session.flush()


# ----------------------------------------------------------------------- notes


def get_note(session: Session, *, user_id: str, paper_id: str) -> Note | None:
    """The caller's note on one of their papers, or ``None`` when never written.

    ``None`` rather than a blank ``Note``: "nobody has written anything" and
    "somebody wrote something and then cleared it" are different facts, and only
    the caller knows whether the difference matters to them. The API happens to
    render both as ``""``.

    Ordered by ``created_at`` and matched to ``set_note``'s ordering below. The
    schema has no unique constraint on ``(user_id, paper_id)`` — ``models.py`` is
    fixed and not ours to change — so a duplicate pair is possible in principle
    (two concurrent first-writes). Both paths picking the *same* row by the same
    rule is what guarantees a client reads back what it just wrote, rather than
    the answer depending on SQLite's row order.
    """
    _require_paper(session, paper_id, user_id=user_id)
    return session.scalar(
        select(Note)
        .where(Note.paper_id == paper_id, Note.user_id == user_id)
        .order_by(Note.created_at, Note.id)
        .limit(1)
    )


def set_note(session: Session, *, user_id: str, paper_id: str, body: object) -> Note:
    """Create or replace the caller's note on a paper.

    An upsert rather than separate POST/PUT because there is exactly one note per
    paper per user: making the client discover which verb applies would mean it
    has to know whether a note already exists, and it would get that wrong on
    the first save of a paper opened in two tabs.

    An empty body is stored rather than rejected — clearing a note is a thing a
    user does on purpose, and the row keeps its ``created_at`` so the history of
    when they first wrote about the paper survives.
    """
    paper = _require_paper(session, paper_id, user_id=user_id)
    if not isinstance(body, str):
        raise Invalid("body must be a string")
    if len(body) > MAX_NOTE_BODY:
        raise Invalid(f"body must be at most {MAX_NOTE_BODY} characters")

    note = get_note(session, user_id=user_id, paper_id=paper.id)
    if note is None:
        note = Note(user_id=user_id, paper_id=paper.id, body=body)
        session.add(note)
    else:
        note.body = body
        note.updated_at = _now()
    session.flush()
    return note
