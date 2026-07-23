"""Per-user research directions for 每日论文 — seeding, matching, and CRUD.

This module replaces the hard-coded global table in
:mod:`pharos.daily.directions` for everything that is *about a reader*. The
split it enforces is the whole design:

**The sweep stays global.** One arXiv fetch and one LLM reading serve every
user, because a paper's Chinese summary and its key points are facts about the
paper, not about who is reading it. Per-user fetching would multiply arXiv
requests (a probe already earned an HTTP 429) and multiply LLM spend by the
number of accounts, for identical output.

**Matching becomes per-user, and moves to query time.** Which papers you see,
which direction they fall under, and how relevant they are to you are the three
things that genuinely differ between readers, and all three are cheap to
recompute. Deriving them on read rather than freezing them at ingest buys a
property worth having on its own: editing a direction re-ranks your feed on the
next request, with nothing re-fetched and nothing re-read.

**Relevance is computed here, not taken from the LLM.** ``DailyPaper.scores``
carries a ``relevance`` the reader model produced while being told about the old
*global* direction list. Once two users follow different directions that number
is not merely imprecise, it is answering a question nobody asked: "how relevant
is this to the operator's hard-coded interests". :func:`relevance_for` replaces
it with a value derived from the caller's own match strength. The LLM's other
scores (recency, popularity, quality) are properties of the paper and survive
untouched — only relevance was ever reader-relative.

:func:`match_for_user` and :func:`relevance_for` are pure functions over data
already in memory. That is a hard requirement, not a preference: they run over a
whole day's papers on every request, so a query inside either of them would be a
per-paper round trip to SQLite hiding inside a list rendering.

Every write entry point takes ``user_id`` as a *required keyword*, matching
:mod:`pharos.services.organise`. An optional owner is one a caller can forget,
and a forgotten filter here does not surface as a wrong number on screen — it
edits somebody else's reading list. Required means the omission is a type error.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pharos.daily.directions import ARXIV_CATEGORIES, DIRECTIONS, keyword_matches
from pharos.db.models import UserDailyConfig, UserDirection

__all__ = [
    "Conflict",
    "Direction",
    "DirectionError",
    "Invalid",
    "NotFound",
    "config_categories",
    "create_direction",
    "delete_direction",
    "ensure_seeded",
    "get_config",
    "list_directions",
    "load_directions",
    "match_for_user",
    "parse_categories",
    "parse_keywords",
    "relevance_for",
    "reorder_directions",
    "update_config",
    "update_direction",
]


# --------------------------------------------------------------------------- #
# limits
# --------------------------------------------------------------------------- #
#
# Every ceiling below exists for one reason: a keyword is free text typed into a
# browser that ends up in a substring scan over the title and abstract of every
# paper in a day, for every user, on every request. Cost is
# ``directions × keywords × papers`` and the user controls two of those three
# factors. Unbounded, a single account can make its own feed — and only its own
# feed, but on the server's CPU — arbitrarily expensive to render.

#: Column width in ``models.py``. SQLite does not enforce ``VARCHAR`` length, so
#: without checking here an over-long name is written happily and only becomes
#: an error if the database is ever moved to one that does enforce it.
MAX_NAME_CHARS = 64

#: Directions per user. Well past any real reading list; the point is that the
#: list is finite.
MAX_DIRECTIONS = 40

#: Keywords in one direction. The hand-tuned defaults top out at twenty.
MAX_KEYWORDS = 80

#: One keyword. Longer than this is a pasted sentence, which as a substring
#: match would never fire anyway.
MAX_KEYWORD_CHARS = 80

#: Total keyword text in one direction, checked after de-duplication. Bounds the
#: scan cost even when every individual keyword is short and legal.
MAX_KEYWORDS_TOTAL_CHARS = 2000

#: arXiv categories one user may follow. The sweep fetches the union across all
#: users, so this bounds one account's contribution to a shared query string.
MAX_CATEGORIES = 24

#: Bounds on ``max_per_day``. Zero would silently empty the feed and look like a
#: bug; the ceiling keeps one user's cap from defining the sweep's workload.
MIN_PER_DAY = 1
MAX_PER_DAY = 200

#: arXiv's category grammar: an archive, optionally followed by ``.subject``.
#: Deliberately a *shape* check rather than an allow-list of the categories this
#: project happens to care about — a physicist following ``cond-mat.stat-mech``
#: or an economist following ``econ.EM`` is a user, not an attack, and a fixed
#: list would lock them out and need editing every time arXiv adds a class.
#: Archives are lower-case and may be hyphenated (``astro-ph``, ``q-bio``,
#: ``hep-th``); subject classes are two upper-case letters (``cs.RO``) or a
#: lower-case hyphenated word (``physics.flu-dyn``). Archives such as ``hep-th``
#: and ``quant-ph`` have no subject class at all, hence the optional group.
#: Case-insensitive on the way in — people type ``CS.RO`` — and canonicalised by
#: :func:`_canonical_category` on the way out.
_ARCHIVE = r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*"
_SUBJECT = r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*"
_CATEGORY_RE = re.compile(rf"^{_ARCHIVE}(?:\.{_SUBJECT})?$")

#: Belt-and-braces cap so the regex is never handed a megabyte to backtrack over.
_MAX_CATEGORY_CHARS = 32

#: Relevance floor and ceiling. A direction matching at all is already a strong
#: statement — the user wrote the keyword — so a single hit starts well above the
#: midpoint rather than at zero. See :func:`relevance_for`.
_RELEVANCE_FLOOR = 6.0
_RELEVANCE_CEILING = 10.0

#: How much of the span above the floor is earned by *depth* (how many distinct
#: keywords fired) versus *coverage* (what fraction of the direction fired). The
#: remainder is coverage's. Depth dominates because a direction with three
#: keywords should not out-rank one with thirty purely by being small.
_DEPTH_WEIGHT = 0.7


# --------------------------------------------------------------------------- #
# errors
# --------------------------------------------------------------------------- #


class DirectionError(Exception):
    """Base for failures this service reports to its caller.

    Each subclass carries the HTTP status it deserves, so the API layer maps
    them in one place and cannot forget a case — see :mod:`pharos.api.directions`.
    """

    status_code = 400


class NotFound(DirectionError):
    """The row does not exist, or does not belong to the caller.

    One class for both on purpose. The API turns this into a 404, never a 403,
    so a probe cannot tell "no such direction" from "not yours" and therefore
    cannot walk ids to learn what other researchers follow.
    """

    status_code = 404


class Invalid(DirectionError):
    """The request cannot be satisfied as asked (empty name, no keywords, …)."""

    status_code = 400


class Conflict(DirectionError):
    """Well-formed, but collides with something that already exists."""

    status_code = 409


# --------------------------------------------------------------------------- #
# parsing and validation
# --------------------------------------------------------------------------- #


def parse_keywords(raw: str | list[str] | tuple[str, ...]) -> list[str]:
    """Turn free text (or a list) into the stored keyword list.

    Accepts newline *or* comma separated input because both are what people
    actually type: a textarea invites one per line, a paste from somewhere else
    arrives comma-joined, and rejecting either would be a puzzle rather than a
    validation. Terms are trimmed, lower-cased (matching is done against
    lower-cased text, so storing mixed case would store a term that can never
    fire), blanks dropped, and duplicates removed *preserving first-seen order*
    — order is user-visible in the settings list, so re-sorting would silently
    rewrite what they typed.

    Raises:
        Invalid: The result is empty, or breaches a size ceiling. An empty list
            is rejected rather than stored, because a direction with no keywords
            matches nothing at all while looking, in the UI, exactly like one
            that works.
    """
    if isinstance(raw, str):
        parts = re.split(r"[\n,]", raw)
    else:
        # A list from the client may still hold comma-joined entries; splitting
        # again costs nothing and removes a shape the caller has to think about.
        parts = [piece for item in raw for piece in re.split(r"[\n,]", str(item))]

    seen: set[str] = set()
    keywords: list[str] = []
    for part in parts:
        term = part.strip().lower()
        if not term or term in seen:
            continue
        if len(term) > MAX_KEYWORD_CHARS:
            raise Invalid(f"keyword is too long (max {MAX_KEYWORD_CHARS} characters): {term[:40]}…")
        seen.add(term)
        keywords.append(term)

    if not keywords:
        raise Invalid("a direction needs at least one keyword")
    if len(keywords) > MAX_KEYWORDS:
        raise Invalid(f"too many keywords (max {MAX_KEYWORDS})")
    if sum(len(k) for k in keywords) > MAX_KEYWORDS_TOTAL_CHARS:
        raise Invalid(f"keyword list is too long (max {MAX_KEYWORDS_TOTAL_CHARS} characters)")
    return keywords


def _canonical_category(value: str) -> str:
    """Normalise one arXiv category to the casing arXiv itself publishes.

    ``cs.ro`` and ``cs.RO`` are the same category to a human and to arXiv's own
    search, but not to a string comparison — and these strings are compared,
    de-duplicated across users, and joined into a query. Canonicalising on the
    way in means the union the sweep builds has one entry per category rather
    than one per spelling. Two-letter subject classes are upper-case (``cs.RO``,
    ``math.AP``); longer hyphenated ones are lower-case (``cond-mat.stat-mech``).
    """
    archive, _, subject = value.partition(".")
    archive = archive.lower()
    if not subject:
        return archive
    subject = subject.upper() if len(subject) == 2 else subject.lower()
    return f"{archive}.{subject}"


def parse_categories(raw: str | list[str] | tuple[str, ...]) -> list[str]:
    """Parse and validate a category list, de-duplicated in first-seen order.

    Raises:
        Invalid: Empty, over the count cap, or containing something that is not
            shaped like an arXiv category.
    """
    if isinstance(raw, str):
        parts = re.split(r"[\n,\s]+", raw)
    else:
        parts = [piece for item in raw for piece in re.split(r"[\n,\s]+", str(item))]

    seen: set[str] = set()
    categories: list[str] = []
    for part in parts:
        token = part.strip()
        if not token:
            continue
        if len(token) > _MAX_CATEGORY_CHARS or not _CATEGORY_RE.match(token):
            raise Invalid(f"not an arXiv category: {token[:40]!r}")
        canonical = _canonical_category(token)
        if canonical in seen:
            continue
        seen.add(canonical)
        categories.append(canonical)

    if not categories:
        raise Invalid("at least one arXiv category is required")
    if len(categories) > MAX_CATEGORIES:
        raise Invalid(f"too many categories (max {MAX_CATEGORIES})")
    return categories


def _clean_name(raw: str) -> str:
    """Validate a direction name. Whitespace is collapsed, not just trimmed."""
    name = " ".join(raw.split())
    if not name:
        raise Invalid("a direction needs a name")
    if len(name) > MAX_NAME_CHARS:
        raise Invalid(f"name is too long (max {MAX_NAME_CHARS} characters)")
    return name


# --------------------------------------------------------------------------- #
# seeding
# --------------------------------------------------------------------------- #


def ensure_seeded(session: Session, *, user_id: str) -> UserDailyConfig:
    """Give ``user_id`` a config row and, exactly once, the default directions.

    Called at the top of every entry point in this module, so 每日论文 works the
    first time it is opened rather than presenting an empty settings page and
    an empty feed.

    Idempotent in both halves, and the two halves are separate. The config row
    is created if missing; the seven defaults from
    :data:`~pharos.daily.directions.DIRECTIONS` are copied in only while
    ``seeded`` is false, and the flag is set whether or not the copy inserted
    anything. That is precisely what the flag is *for*: a user who deliberately
    deletes every direction — because they want a feed built from scratch, or no
    feed at all — must not be handed the defaults back on their next request.
    Without the flag, "no directions" and "never configured" are the same state,
    and the module would keep overruling a decision the user made on purpose.

    Returns the user's :class:`~pharos.db.models.UserDailyConfig`.
    """
    config = session.get(UserDailyConfig, user_id)
    if config is None:
        config = UserDailyConfig(
            user_id=user_id,
            categories=",".join(ARXIV_CATEGORIES),
            seeded=False,
        )
        try:
            # A savepoint, not a bare flush: two concurrent first requests from
            # the same account both find no row and both insert one, and the
            # loser must be able to recover without poisoning the surrounding
            # transaction — which is what an IntegrityError on the outer unit of
            # work would do.
            #
            # The ``add`` belongs INSIDE the savepoint. Entering ``begin_nested``
            # takes a snapshot, and taking a snapshot autoflushes whatever is
            # already pending — so an object added beforehand is inserted by the
            # OUTER transaction, the savepoint never covers it, and the loser of
            # the race gets a PendingRollbackError that no amount of recovery
            # here can undo.
            with session.begin_nested():
                session.add(config)
                session.flush()
        except IntegrityError:
            # The rollback of the savepoint already evicted the pending instance,
            # so expunging it again would raise InvalidRequestError and mask the
            # real cause. Only expunge if it somehow survived.
            if config in session:
                session.expunge(config)
            config = session.get(UserDailyConfig, user_id)
            if config is None:
                # Not the duplicate-insert race after all (a failed foreign key
                # on an unknown user_id lands here too). Nothing to recover to.
                raise

    if config.seeded:
        return config

    if not config.categories:
        config.categories = ",".join(ARXIV_CATEGORIES)

    # Claim the right to seed with a conditional UPDATE rather than by reading
    # ``seeded`` and then acting on the value read. The read-then-write version
    # loses the same race the config row above defends against, one step later:
    # concurrent first requests each open their own transaction, each take a
    # snapshot from before the other committed, so each sees ``seeded`` false
    # AND sees no directions, and each inserts the full set of defaults. Six
    # parallel first requests leave forty-two directions, which is what a user's
    # very first page load then shows them.
    #
    # ``UPDATE ... WHERE seeded IS false`` moves the decision into the database,
    # which is the only party that can serialise it: SQLite admits one writer at
    # a time, so exactly one transaction finds the row still false and gets
    # ``rowcount == 1``. Everyone else gets 0 and must not seed. The flag is
    # still set whether or not the copy below inserts anything — that is what
    # keeps a deliberate "delete them all" from being undone next request.
    claim = cast(
        "CursorResult[Any]",
        session.execute(
            update(UserDailyConfig)
            .where(UserDailyConfig.user_id == user_id, UserDailyConfig.seeded.is_(False))
            .values(seeded=True)
        ),
    )
    if not claim.rowcount:
        session.refresh(config)
        return config

    # Guard against seeding on top of directions that somehow already exist
    # (a restored backup, a half-finished migration). Duplicating the defaults
    # would be worse than skipping them.
    existing = session.scalar(
        select(UserDirection.id).where(UserDirection.user_id == user_id).limit(1)
    )
    if existing is None:
        for position, (name, keywords) in enumerate(DIRECTIONS.items()):
            session.add(
                UserDirection(
                    user_id=user_id,
                    name=name,
                    # Newline-separated per the column's contract. Lower-cased
                    # only — deliberately NOT stripped. Several defaults are
                    # whitespace-delimited on purpose: ``"wam "`` and ``" dit "``
                    # are padded so they match the acronym and not the thousands
                    # of words that merely contain those letters. Stripping them
                    # turns " dit " into "dit", which fires on "edit", "audit",
                    # "credit", "condition" and "addition" — every new account's
                    # feed floods with false Diffusion matches. The padding is
                    # load-bearing configuration, so it is copied verbatim.
                    keywords="\n".join(k.lower() for k in keywords),
                    enabled=True,
                    position=position,
                )
            )
    session.flush()
    # The claim above was issued as a bulk UPDATE, so the identity-mapped config
    # still carries the pre-claim value of ``seeded``. Refresh so the object this
    # returns agrees with the row — callers render ``seeded`` straight onto the
    # settings page.
    session.refresh(config)
    return config


# --------------------------------------------------------------------------- #
# matching — pure functions over data already loaded
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Direction:
    """One user's direction, detached from the session.

    A frozen dataclass rather than the ORM row because :func:`match_for_user` is
    called once per paper in a day's listing: passing live ORM objects invites a
    lazy load inside that loop, which is the one thing this module promises not
    to do. Loading them is :func:`load_directions`' single query.
    """

    id: str
    name: str
    keywords: tuple[str, ...]
    enabled: bool
    position: int


def _to_direction(row: UserDirection) -> Direction:
    return Direction(
        id=row.id,
        name=row.name,
        keywords=tuple(k for k in (row.keywords or "").splitlines() if k.strip()),
        enabled=bool(row.enabled),
        position=int(row.position or 0),
    )


def load_directions(
    session: Session, *, user_id: str, enabled_only: bool = True
) -> list[Direction]:
    """Load ``user_id``'s directions, in match order, as plain data.

    One query, returning detached values — the call every request makes before
    looping over a day's papers. ``enabled_only`` defaults to true because the
    feed is the common caller; the settings page passes false so a disabled
    direction is still editable rather than invisible.

    Ordering is ``position`` first, and it is not cosmetic: it is the tie-break
    in :func:`match_for_user`, exactly as declaration order was in the old
    global table. ``created_at`` and ``id`` follow so the order is total and
    stable even if two rows share a position.

    Seeds first, like every other entry point. A user whose very first request
    is for the feed rather than the settings page must get the defaults too —
    otherwise 每日论文 looks empty until they happen to open settings, which
    reads as "the sweep found nothing" rather than "you have no directions yet".
    """
    ensure_seeded(session, user_id=user_id)
    stmt = select(UserDirection).where(UserDirection.user_id == user_id)
    if enabled_only:
        stmt = stmt.where(UserDirection.enabled.is_(True))
    stmt = stmt.order_by(UserDirection.position, UserDirection.created_at, UserDirection.id)
    return [_to_direction(row) for row in session.scalars(stmt)]


def match_for_user(
    directions: list[Direction], title: str, abstract: str | None
) -> tuple[str | None, tuple[str, ...]]:
    """Classify one paper against one user's directions.

    Ported semantics, verbatim, from
    :func:`pharos.daily.directions.match_directions`: a direction matches when
    ANY of its keywords occurs as a substring of the lower-cased "title +
    abstract"; when several match, the one with the MOST DISTINCT keyword hits
    wins — a paper that mentions "diffusion policy" once while being about world
    models throughout belongs under World Model — and ties break by
    ``position``, which is why position is user-editable configuration rather
    than an incidental row number.

    Returns ``(None, ())`` when nothing matches, which is the caller's signal to
    drop the paper from *this user's* feed. It stays in the shared table for
    everyone else: dropping here is a filter, never a delete.

    A keyword wrapped in double quotes — ``"wam"`` — matches as a WHOLE WORD
    instead of a substring. See :func:`keyword_matches` for why that exists.

    Pure. ``directions`` must already be sorted by position (see
    :func:`load_directions`); the strict ``>`` below is what makes first-seen —
    that is, lowest position — win a tie without a second sort.
    """
    text = f"{title}\n{abstract or ''}".lower()
    best_name: str | None = None
    best_hits: tuple[str, ...] = ()
    for direction in directions:
        hits = tuple(kw for kw in direction.keywords if keyword_matches(kw, text))
        if len(hits) > len(best_hits):
            best_name, best_hits = direction.name, hits
    return best_name, best_hits


def relevance_for(hits: tuple[str, ...] | list[str], direction: Direction | None) -> float:
    """A 0–10 relevance for this reader, derived from match strength.

    This replaces the ``relevance`` the LLM produced. That number was computed
    against the *old global* rubric — the operator's hard-coded interest list —
    so the moment two users follow different directions it is answering a
    question neither of them asked. Match strength, by contrast, is defined
    entirely by terms this user wrote down, and it recomputes for free when they
    edit them. The reader's other scores are untouched: recency, popularity and
    quality are properties of the paper and mean the same thing to everybody.

    The shape, and why:

    * **A floor of 6.0 for any match at all.** The user typed the keyword. One
      hit is already a deliberate statement of interest, not a weak signal, so
      the scale starts above the midpoint instead of at zero.
    * **Depth with diminishing returns.** Each additional distinct keyword
      closes half the remaining gap to 10. The second hit is strong evidence;
      the ninth adds almost nothing, and should not be able to shove a paper
      past one that matched fewer but rarer terms.
    * **Coverage as a minority term.** Firing four of a five-keyword direction
      is a better fit than firing four of eighty. Weighted below depth so a
      deliberately tiny direction cannot out-rank a well-specified one just by
      being small.

    Strictly increasing in the number of distinct hits for a fixed direction,
    and never above :data:`_RELEVANCE_CEILING`. ``()`` returns 0.0 — no match is
    not a weak match.
    """
    distinct = len(set(hits))
    if distinct == 0:
        return 0.0

    # Diminishing returns: 1 hit → 0.0 of the span, 2 → 0.5, 3 → 0.75, …
    depth = 1.0 - 0.5 ** (distinct - 1)

    total = len(direction.keywords) if direction is not None else 0
    coverage = min(distinct / total, 1.0) if total else 0.0

    span = _RELEVANCE_CEILING - _RELEVANCE_FLOOR
    score = _RELEVANCE_FLOOR + span * (_DEPTH_WEIGHT * depth + (1.0 - _DEPTH_WEIGHT) * coverage)
    # One decimal: this is displayed next to the LLM's scores, which are also
    # one decimal, and pretending to more precision than the model has is a lie.
    return round(min(score, _RELEVANCE_CEILING), 1)


# --------------------------------------------------------------------------- #
# direction CRUD
# --------------------------------------------------------------------------- #


def _owned(session: Session, *, user_id: str, direction_id: str) -> UserDirection:
    """Fetch one direction *by owner*, or raise :class:`NotFound`.

    The owner is part of the query, not a check after it. A row belonging to
    somebody else is never loaded, so there is no branch in which it could be
    returned by a later edit to this function.
    """
    row = session.scalar(
        select(UserDirection).where(
            UserDirection.id == direction_id, UserDirection.user_id == user_id
        )
    )
    if row is None:
        raise NotFound(direction_id)
    return row


def _assert_name_free(
    session: Session, *, user_id: str, name: str, exclude_id: str | None = None
) -> None:
    """Names are unique per user, case-insensitively.

    Two directions called ``VLA`` and ``vla`` are indistinguishable to the person
    reading the settings list and to the badge on a paper card, and the name is
    what gets stored in ``matched_domain`` — so a duplicate would make the feed
    ambiguous, not merely untidy.
    """
    folded = name.casefold()
    stmt = select(UserDirection.id, UserDirection.name).where(UserDirection.user_id == user_id)
    if exclude_id is not None:
        stmt = stmt.where(UserDirection.id != exclude_id)
    for _row_id, existing in session.execute(stmt):
        if existing.casefold() == folded:
            raise Conflict(f"a direction called {name!r} already exists")


def list_directions(session: Session, *, user_id: str) -> list[UserDirection]:
    """Every direction the user has, disabled ones included, in display order.

    Returns ORM rows rather than :class:`Direction` values: this backs the
    settings page, which needs ``created_at`` and the row id. The feed path
    wants :func:`load_directions` instead.
    """
    ensure_seeded(session, user_id=user_id)
    return list(
        session.scalars(
            select(UserDirection)
            .where(UserDirection.user_id == user_id)
            .order_by(UserDirection.position, UserDirection.created_at, UserDirection.id)
        )
    )


def create_direction(
    session: Session,
    *,
    user_id: str,
    name: str,
    keywords: str | list[str] | tuple[str, ...],
    enabled: bool = True,
) -> UserDirection:
    """Add one direction, appended to the end of the user's list."""
    ensure_seeded(session, user_id=user_id)
    clean_name = _clean_name(name)
    terms = parse_keywords(keywords)
    _assert_name_free(session, user_id=user_id, name=clean_name)

    count = len(
        list(session.scalars(select(UserDirection.id).where(UserDirection.user_id == user_id)))
    )
    if count >= MAX_DIRECTIONS:
        raise Invalid(f"too many directions (max {MAX_DIRECTIONS})")

    highest = session.scalar(
        select(UserDirection.position)
        .where(UserDirection.user_id == user_id)
        .order_by(UserDirection.position.desc())
        .limit(1)
    )
    row = UserDirection(
        user_id=user_id,
        name=clean_name,
        keywords="\n".join(terms),
        enabled=bool(enabled),
        position=(int(highest) + 1) if highest is not None else 0,
    )
    session.add(row)
    session.flush()
    return row


def update_direction(
    session: Session, *, user_id: str, direction_id: str, changes: dict[str, Any]
) -> UserDirection:
    """Apply a partial update. Keys absent from ``changes`` are left alone.

    The caller passes ``model_dump(exclude_unset=True)``, which is what
    separates "leave this field alone" from "set it to this value".
    """
    ensure_seeded(session, user_id=user_id)
    row = _owned(session, user_id=user_id, direction_id=direction_id)

    unknown = set(changes) - {"name", "keywords", "enabled", "position"}
    if unknown:
        raise Invalid(f"unknown field(s): {', '.join(sorted(unknown))}")

    if "name" in changes:
        clean_name = _clean_name(str(changes["name"]))
        _assert_name_free(session, user_id=user_id, name=clean_name, exclude_id=direction_id)
        row.name = clean_name
    if "keywords" in changes:
        raw = changes["keywords"]
        if not isinstance(raw, (str, list, tuple)):
            raise Invalid("keywords must be text or a list of terms")
        row.keywords = "\n".join(parse_keywords(raw))
    if "enabled" in changes:
        row.enabled = bool(changes["enabled"])
    if "position" in changes:
        position = changes["position"]
        if not isinstance(position, int) or isinstance(position, bool) or position < 0:
            raise Invalid("position must be a non-negative integer")
        row.position = position

    session.flush()
    return row


def delete_direction(session: Session, *, user_id: str, direction_id: str) -> None:
    """Remove one direction. Nothing else changes.

    Deleting the last one leaves the user with an empty feed, deliberately, and
    :func:`ensure_seeded` will not undo it — see its docstring. No paper row is
    touched: ``DailyPaper`` is shared, and matching is derived at query time, so
    a direction's disappearance is already fully expressed by its absence.
    """
    ensure_seeded(session, user_id=user_id)
    row = _owned(session, user_id=user_id, direction_id=direction_id)
    session.delete(row)
    session.flush()


def reorder_directions(
    session: Session, *, user_id: str, direction_ids: list[str]
) -> list[UserDirection]:
    """Rewrite ``position`` from an explicit id order. Returns the new order.

    Position is not merely display: it is the tie-break when a paper matches
    several directions, so a drag in the settings list genuinely changes which
    badge a paper wears. Ids the user does not own raise :class:`NotFound`, and
    a partial list is accepted — anything unmentioned keeps its relative order
    after the listed ones, so a client that reorders a filtered view cannot
    scramble the rest.
    """
    ensure_seeded(session, user_id=user_id)
    if len(direction_ids) != len(set(direction_ids)):
        raise Invalid("duplicate id in the requested order")

    rows = list_directions(session, user_id=user_id)
    by_id = {row.id: row for row in rows}
    missing = [i for i in direction_ids if i not in by_id]
    if missing:
        raise NotFound(missing[0])

    ordered = [by_id[i] for i in direction_ids]
    ordered += [row for row in rows if row.id not in set(direction_ids)]
    for position, row in enumerate(ordered):
        row.position = position
    session.flush()
    return ordered


# --------------------------------------------------------------------------- #
# daily config
# --------------------------------------------------------------------------- #


def get_config(session: Session, *, user_id: str) -> UserDailyConfig:
    """The user's 每日论文 settings, seeding them if this is the first look."""
    return ensure_seeded(session, user_id=user_id)


def update_config(session: Session, *, user_id: str, changes: dict[str, Any]) -> UserDailyConfig:
    """Apply a partial update to the user's daily settings."""
    config = ensure_seeded(session, user_id=user_id)

    unknown = set(changes) - {"categories", "max_per_day", "enabled"}
    if unknown:
        raise Invalid(f"unknown field(s): {', '.join(sorted(unknown))}")

    if "categories" in changes:
        raw = changes["categories"]
        if not isinstance(raw, (str, list, tuple)):
            raise Invalid("categories must be text or a list")
        config.categories = ",".join(parse_categories(raw))
    if "max_per_day" in changes:
        value = changes["max_per_day"]
        if not isinstance(value, int) or isinstance(value, bool):
            raise Invalid("max_per_day must be an integer")
        if not MIN_PER_DAY <= value <= MAX_PER_DAY:
            raise Invalid(f"max_per_day must be between {MIN_PER_DAY} and {MAX_PER_DAY}")
        config.max_per_day = value
    if "enabled" in changes:
        config.enabled = bool(changes["enabled"])

    config.updated_at = datetime.now(UTC)
    session.flush()
    return config


def config_categories(config: UserDailyConfig) -> list[str]:
    """The stored comma-joined categories as a list, falling back to the defaults.

    A user whose row predates a migration, or who somehow has an empty string,
    should see the standard sweep rather than nothing at all.
    """
    parts = [c.strip() for c in (config.categories or "").split(",") if c.strip()]
    return parts or list(ARXIV_CATEGORIES)
