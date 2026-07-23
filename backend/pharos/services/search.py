"""Full-text search over one user's library, plus the text extraction that feeds it.

Three things live here, in the order the data moves:

1. **Extraction** — pulling a plain-text rendering out of the original PDF and
   parking it on ``Paper.full_text`` (:func:`populate_full_text`, and
   :func:`backfill_full_text` for papers that predate this module).
2. **Query sanitisation** — turning an arbitrary human-typed string into
   something FTS5's ``MATCH`` will actually accept.
3. **Searching** — :func:`search`, which runs over FTS5 when the SQLite build
   has it and degrades to ``LIKE`` when it does not.

The one invariant that outranks everything else: a result set contains only the
caller's own papers. Both query paths take the owner id as a required keyword
and put it in the ``WHERE`` clause, mirroring
:class:`~pharos.services.library.LibraryService`. There is deliberately no
"search everything" entry point for a request to reach by accident.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy import text as sql_text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from pharos.db.models import Paper
from pharos.db.session import FTS_TABLE, fts5_available
from pharos.storage.blobs import BlobStore

# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------

#: How much of a paper's text we are willing to keep on the row.
#:
#: This is a storage bound, not a quality judgement. ``full_text`` is loaded as
#: part of a ``Paper`` row, so an uncapped value means a 700-page book puts
#: several megabytes into SQLite and drags them into memory on every ORM load of
#: that paper. 400k characters is roughly 150 dense pages — comfortably the
#: whole of any conference paper, thesis chapter or report the library is
#: actually for.
#:
#: Past the cap we stop reading further pages and keep what we have, so a long
#: book is searchable up to roughly that point and silent beyond it. Truncating
#: is the honest failure here: the alternative, refusing to index the document at
#: all, would make a book unfindable by its own title.
FULL_TEXT_MAX_CHARS = 400_000

#: Control characters have no business in extracted text, and two of them are
#: load-bearing below: ``\x02``/``\x03`` are the sentinels FTS5 wraps matches in
#: before we convert them to ``<mark>``. Stripping them here means text we
#: extracted can never smuggle a highlight into a snippet.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")


def _normalise(raw: str) -> str:
    """Flatten PDF text into a single whitespace-normalised run.

    A PDF's text layer is full of hard line breaks at column and page
    boundaries. They carry no meaning once the document is a search corpus, and
    keeping them makes snippets render as shredded fragments, so every run of
    whitespace collapses to one space. Layout is not lost here in any sense that
    matters — the PDF itself remains the thing the user reads; this is only the
    index behind the search box.
    """
    return _WHITESPACE.sub(" ", _CONTROL.sub(" ", raw)).strip()


def _fit(value: str, limit: int) -> str:
    """Trim to ``limit`` characters, preferring a word boundary."""
    if len(value) <= limit:
        return value
    cut = value.rfind(" ", 0, limit)
    return value[: cut if cut > limit // 2 else limit].rstrip()


def extract_full_text(path: Path, *, max_chars: int = FULL_TEXT_MAX_CHARS) -> str | None:
    """Return the plain text of a PDF, or ``None`` if it has none to give.

    Never raises. This runs inside the upload request, and a paper that cannot
    be parsed — a scan with no text layer, an encrypted file, a truncated
    download — is still a perfectly good paper to keep in the library. It is
    simply not full-text searchable, and ``None`` says exactly that. Failing the
    upload over it would be trading a whole feature for a partial one.

    ``None`` rather than ``""`` on purpose: the column's documented meaning is
    "extraction never ran or found nothing", and an empty string would be a
    third state that reads as "we know the paper is empty".
    """
    chunks: list[str] = []
    budget = max_chars
    try:
        import pymupdf

        with pymupdf.open(path) as doc:
            for page in doc:
                page_text = page.get_text("text")
                if not page_text:
                    continue
                chunks.append(page_text)
                budget -= len(page_text)
                if budget <= 0:
                    # Stop at a page boundary rather than reading a 700-page
                    # book to completion only to throw most of it away.
                    break
    except Exception:
        return None

    joined = _normalise("\n".join(chunks))
    if not joined:
        return None
    return _fit(joined, max_chars)


def populate_full_text(paper: Paper, path: Path, *, max_chars: int = FULL_TEXT_MAX_CHARS) -> bool:
    """Extract ``path``'s text onto ``paper.full_text``. True if anything was found.

    This is the hook the upload path calls; see :func:`extract_full_text` for why
    it cannot raise. A paper that yields nothing keeps ``full_text = None``
    rather than being marked with a blank, so a later backfill can tell "not
    searchable" from "never looked at".
    """
    extracted = extract_full_text(path, max_chars=max_chars)
    if extracted is None:
        return False
    paper.full_text = extracted
    return True


@dataclass(frozen=True)
class BackfillReport:
    """What a :func:`backfill_full_text` pass did, for the operator running it."""

    scanned: int = 0
    updated: int = 0
    missing_file: int = 0
    no_text: int = 0


def backfill_full_text(
    session: Session,
    blobs: BlobStore,
    *,
    user_id: str | None,
    limit: int | None = None,
) -> BackfillReport:
    """Fill in ``full_text`` for papers uploaded before this module existed.

    ``user_id`` is a *required* keyword that may explicitly be ``None`` for "every
    paper in the database". That is the one place in this module a query is not
    owner-scoped, and it is safe for a reason worth stating: this is a write path
    that returns counts, never rows. Nothing a caller learns from it depends on
    whose papers were touched, so there is no view of another user's library to
    leak. It is a maintenance entry point — no endpoint calls it — and making the
    argument required rather than defaulted means reaching the global case is a
    decision somebody typed, not one they inherited.

    Not committed here: the caller owns the transaction, so a backfill can be run
    inside ``session_scope`` alongside whatever else it wants to do.
    """
    stmt = select(Paper).where(Paper.full_text.is_(None))
    if user_id is not None:
        stmt = stmt.where(Paper.user_id == user_id)
    if limit is not None:
        stmt = stmt.limit(limit)

    scanned = updated = missing = empty = 0
    for paper in session.scalars(stmt):
        scanned += 1
        path = blobs.path(paper.orig_sha256, "original")
        if not path.exists():
            # The blob was purged, or this row predates the current store. Not an
            # error: it just cannot be indexed.
            missing += 1
            continue
        if populate_full_text(paper, path):
            updated += 1
        else:
            empty += 1
    return BackfillReport(scanned=scanned, updated=updated, missing_file=missing, no_text=empty)


# ---------------------------------------------------------------------------
# query sanitisation
# ---------------------------------------------------------------------------

#: FTS5's ``MATCH`` takes a query *language*, not a string of words. Handing it
#: raw user input is a 500 waiting to happen: ``*``, a stray ``"``, and the bare
#: words ``AND``/``OR``/``NOT``/``NEAR`` are all either syntax errors or silent
#: operators. Every term below is therefore wrapped in double quotes, which makes
#: it an FTS5 string literal — inert, whatever it contains.
_QUOTED_SEGMENT = re.compile(r'"([^"]*)"')

#: How many terms of a query we honour. A search box is not a query language;
#: past this the extra terms only make the AND-chain slower and emptier.
_MAX_TERMS = 16
#: Longest single term. Beyond this it is not a word.
_MAX_TERM_CHARS = 64


@dataclass(frozen=True)
class _Term:
    """One unit of a parsed query."""

    text: str
    #: True when the user wrapped it in quotes, i.e. asked for an exact phrase.
    #: Phrases are never prefix-extended — the quotes are the user saying they
    #: meant precisely this.
    phrase: bool


def _has_word_content(value: str) -> bool:
    """Would FTS5's tokenizer get a single token out of this?

    Purely punctuation (``*``, ``--``, ``()``) tokenizes to nothing. Such a term
    is dropped rather than kept as an unmatchable literal, so ``transformer *``
    still searches for ``transformer`` instead of ANDing it with something that
    can never match.
    """
    return any(ch.isalnum() for ch in value)


def parse_query(raw: str) -> list[_Term]:
    """Split a user's search string into terms, honouring ``"quoted phrases"``.

    An unbalanced quote is not an error. ``"foo bar`` has no closing quote, so it
    is read as two ordinary words — which is what somebody who is still typing
    meant, and is the case a naive implementation turns into a 500.
    """
    terms: list[_Term] = []
    position = 0
    for match in _QUOTED_SEGMENT.finditer(raw):
        terms.extend(_Term(word, phrase=False) for word in raw[position : match.start()].split())
        inner = match.group(1).strip()
        if inner:
            terms.append(_Term(inner, phrase=True))
        position = match.end()
    terms.extend(_Term(word, phrase=False) for word in raw[position:].split())

    cleaned: list[_Term] = []
    for term in terms:
        # Quotes are stripped, not escaped. FTS5 spells an embedded quote as ""
        # inside a literal, but a quote character can never be part of a token
        # anyway, so deleting it removes an entire class of escaping bug and
        # loses nothing a user could have been searching for.
        stripped = term.text.replace('"', " ").strip()
        if not _has_word_content(stripped):
            continue
        cleaned.append(_Term(_fit(stripped, _MAX_TERM_CHARS), phrase=term.phrase))
        if len(cleaned) >= _MAX_TERMS:
            break
    return cleaned


def build_match_expression(terms: list[_Term]) -> str:
    """Render parsed terms as an FTS5 ``MATCH`` expression.

    Terms are ANDed: someone typing two words wants the paper that has both, not
    the union. The final term gets a ``*`` when it was not an explicit phrase, so
    a half-typed ``transform`` finds *Transformer* — the search box is used
    incrementally, and requiring a whole word means results appear only on the
    last keystroke.

    Every term arrives here already stripped of quote characters by
    :func:`parse_query`, so wrapping in quotes cannot produce an unbalanced
    literal and the result is always syntactically valid.
    """
    if not terms:
        return ""
    parts = []
    for index, term in enumerate(terms):
        literal = f'"{term.text}"'
        if index == len(terms) - 1 and not term.phrase:
            literal += "*"
        parts.append(literal)
    return " AND ".join(parts)


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------

#: Fields in the order we would rather report a match in. A title hit is what the
#: user most likely meant; a body hit is the last resort. This decides both the
#: reported ``field`` and which snippet is shown when several columns matched.
_FIELDS: tuple[str, ...] = ("title", "abstract", "authors", "full_text")
#: The same four in the column order the FTS5 table declares them.
_FTS_COLUMN_ORDER: tuple[str, ...] = ("title", "authors", "abstract", "full_text")

#: FTS5 wraps matched terms in these before we HTML-escape the surrounding text
#: and swap them for ``<mark>``. They are control characters precisely so no
#: document can contain them: if we asked FTS5 for literal ``<mark>`` tags, a
#: paper whose text contained ``<script>`` would come back as a snippet the
#: frontend renders as live markup — stored XSS delivered through search results.
#: Marking first and escaping second makes that impossible while still letting the
#: client render the highlight.
_MARK_OPEN = "\x02"
_MARK_CLOSE = "\x03"

#: Snippet width, in tokens. FTS5 caps this at 64.
_SNIPPET_TOKENS_SHORT = 14
_SNIPPET_TOKENS_LONG = 24
_ELLIPSIS = "…"


@dataclass(frozen=True)
class SearchHit:
    """One matching paper."""

    paper_id: str
    title: str
    #: HTML-safe: everything is escaped except the ``<mark>`` pair around the
    #: matched terms, which the client is meant to render.
    snippet: str
    #: Which indexed field this hit is being reported against — one of
    #: ``title``/``abstract``/``authors``/``full_text``.
    field: str
    #: Higher is more relevant. Only comparable *within* one response: see
    #: :class:`SearchPage.engine`.
    rank: float


@dataclass(frozen=True)
class SearchPage:
    """A slice of results, plus what it took to produce them."""

    hits: list[SearchHit]
    total: int
    limit: int
    offset: int
    #: ``"fts5"`` or ``"like"``. Worth surfacing because the two produce
    #: genuinely different relevance: FTS5 ranks by BM25, while the fallback can
    #: only count which fields matched. Scores from one are meaningless against
    #: the other, and a client that shows a relevance bar needs to know which it
    #: is looking at.
    engine: str


def _to_html(marked: str | None) -> str:
    """Escape snippet text, then re-admit only the highlight tags.

    Order matters and is the whole point — see :data:`_MARK_OPEN`.
    """
    if not marked:
        return ""
    escaped = html.escape(marked, quote=True)
    return escaped.replace(_MARK_OPEN, "<mark>").replace(_MARK_CLOSE, "</mark>")


def _require_owner(user_id: str) -> str:
    """Reject a falsy owner id before it can reach a ``WHERE`` clause.

    ``Paper.user_id`` is nullable for the sake of pre-accounts rows, so a ``None``
    threaded through here would render as ``user_id IS NULL`` and quietly return
    those ownerless rows to whoever asked. An empty string is no better. This is
    the same guard ``LibraryService`` applies, restated rather than imported
    because a search path that silently loses its owner filter is the exact
    failure it exists to prevent.
    """
    if not user_id:
        raise ValueError("user_id is required: search must be owner-scoped")
    return user_id


# ---------------------------------------------------------------------------
# the FTS5 path
# ---------------------------------------------------------------------------

# Column weights for BM25. A term in the title says far more about relevance
# than the same term buried in the body, where it may be a passing citation.
_BM25_WEIGHTS = "10.0, 4.0, 5.0, 1.0"  # title, authors, abstract, full_text

_FTS_WHERE = f"""
      FROM {FTS_TABLE}
      JOIN papers AS p ON p.rowid = {FTS_TABLE}.rowid
     WHERE {FTS_TABLE} MATCH :match
       AND p.user_id = :user_id
       AND p.deleted_at IS NULL
"""

_FTS_COUNT_SQL = f"SELECT COUNT(*) {_FTS_WHERE}"

_FTS_SELECT_SQL = f"""
    SELECT p.id AS paper_id,
           p.title AS title,
           snippet({FTS_TABLE}, 0, :o, :c, :e, {_SNIPPET_TOKENS_SHORT}) AS s_title,
           snippet({FTS_TABLE}, 1, :o, :c, :e, {_SNIPPET_TOKENS_SHORT}) AS s_authors,
           snippet({FTS_TABLE}, 2, :o, :c, :e, {_SNIPPET_TOKENS_LONG}) AS s_abstract,
           snippet({FTS_TABLE}, 3, :o, :c, :e, {_SNIPPET_TOKENS_LONG}) AS s_full_text,
           bm25({FTS_TABLE}, {_BM25_WEIGHTS}) AS score
    {_FTS_WHERE}
  ORDER BY score
     LIMIT :limit OFFSET :offset
"""


def _fts_hit(row) -> SearchHit:
    """Pick the field to report, from the per-column snippets FTS5 gave back.

    FTS5 has no "which column matched" function, but it does not need one: a
    snippet for a column that did not match comes back with no highlight
    sentinels in it. So the columns carrying a sentinel *are* the matching
    columns, and :data:`_FIELDS` breaks the tie in the user's favour.
    """
    snippets = {
        "title": row.s_title,
        "authors": row.s_authors,
        "abstract": row.s_abstract,
        "full_text": row.s_full_text,
    }
    for field in _FIELDS:
        candidate = snippets[field]
        if candidate and _MARK_OPEN in candidate:
            return SearchHit(
                paper_id=row.paper_id,
                title=row.title,
                snippet=_to_html(candidate),
                field=field,
                # BM25 in SQLite is *negative*, and better matches are more
                # negative, so `ORDER BY score` ascending is already best-first.
                # Negating it here gives clients the sign they expect — bigger
                # means better — instead of exporting a footgun.
                rank=-float(row.score),
            )
    # No column carried a highlight. Should not happen for a row that matched,
    # but a snippet window can in principle land away from the hit, and an
    # unhighlighted snippet beats no result at all.
    return SearchHit(
        paper_id=row.paper_id,
        title=row.title,
        snippet=_to_html(snippets["title"] or row.title),
        field="title",
        rank=-float(row.score),
    )


def _search_fts(
    session: Session, *, user_id: str, terms: list[_Term], limit: int, offset: int
) -> SearchPage:
    params = {"match": build_match_expression(terms), "user_id": user_id}
    total = int(session.execute(sql_text(_FTS_COUNT_SQL), params).scalar_one())
    rows = session.execute(
        sql_text(_FTS_SELECT_SQL),
        {
            **params,
            "o": _MARK_OPEN,
            "c": _MARK_CLOSE,
            "e": _ELLIPSIS,
            "limit": limit,
            "offset": offset,
        },
    ).all()
    return SearchPage(
        hits=[_fts_hit(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
        engine="fts5",
    )


# ---------------------------------------------------------------------------
# the LIKE fallback
# ---------------------------------------------------------------------------

#: Scores for the fallback, mirroring the BM25 weights' intent at much lower
#: resolution: it can only answer "did this field contain the term", so a hit is
#: worth its field's weight and nothing distinguishes one occurrence from twenty.
_LIKE_WEIGHTS = {"title": 8, "abstract": 4, "authors": 4, "full_text": 1}
#: Characters around the match in a hand-built snippet.
_LIKE_SNIPPET_CHARS = 160


def _like_pattern(term: str) -> str:
    """``%term%``, with LIKE's own wildcards neutralised.

    Without this a search for ``100%`` or ``a_b`` silently becomes a wildcard
    query matching far more than the user asked for.
    """
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _like_conditions(terms: list[_Term]) -> tuple[str, str, dict[str, str]]:
    """Build the WHERE fragment, the ranking expression, and their parameters."""
    clauses: list[str] = []
    scores: list[str] = []
    params: dict[str, str] = {}
    for index, term in enumerate(terms):
        key = f"t{index}"
        params[key] = _like_pattern(term.text)
        per_field = [f"p.{field} LIKE :{key} ESCAPE '\\'" for field in _FTS_COLUMN_ORDER]
        clauses.append("(" + " OR ".join(per_field) + ")")
        scores.extend(
            f"(CASE WHEN p.{field} LIKE :{key} ESCAPE '\\' THEN {weight} ELSE 0 END)"
            for field, weight in _LIKE_WEIGHTS.items()
        )
    return " AND ".join(clauses), " + ".join(scores), params


def _like_snippet(value: str, terms: list[_Term]) -> str | None:
    """Build a highlighted window around the first match, or ``None`` if absent.

    The hand-rolled equivalent of FTS5's ``snippet()``. It marks with the same
    sentinels so the escaping in :func:`_to_html` protects this path identically —
    the fallback must not be the one that ships raw document text to the browser.
    """
    if not value:
        return None
    lowered = value.lower()
    positions = [(lowered.find(t.text.lower()), t.text) for t in terms]
    found = [(at, needle) for at, needle in positions if at >= 0]
    if not found:
        return None

    first_at = min(at for at, _ in found)
    start = max(0, first_at - _LIKE_SNIPPET_CHARS // 3)
    window = value[start : start + _LIKE_SNIPPET_CHARS]

    # Mark longest-first so a short term nested in a longer one cannot chop the
    # longer term's highlight in half.
    for needle in sorted({needle for _, needle in found}, key=len, reverse=True):
        window = re.sub(
            re.escape(needle),
            lambda m: f"{_MARK_OPEN}{m.group(0)}{_MARK_CLOSE}",
            window,
            flags=re.IGNORECASE,
        )
    prefix = _ELLIPSIS if start > 0 else ""
    suffix = _ELLIPSIS if start + _LIKE_SNIPPET_CHARS < len(value) else ""
    return f"{prefix}{window}{suffix}"


def _search_like(
    session: Session, *, user_id: str, terms: list[_Term], limit: int, offset: int
) -> SearchPage:
    """The path taken when SQLite was built without FTS5.

    Correct but unranked in any real sense, and it scans the papers table. That
    is the deal: a build with no FTS5 gets working search rather than a 500, and
    ``SearchPage.engine`` tells the client which it got.
    """
    where, score_expr, params = _like_conditions(terms)
    base = f"FROM papers AS p WHERE p.user_id = :user_id AND p.deleted_at IS NULL AND ({where})"
    params["user_id"] = user_id

    total = int(session.execute(sql_text(f"SELECT COUNT(*) {base}"), params).scalar_one())
    rows = session.execute(
        sql_text(
            f"SELECT p.id AS paper_id, p.title AS title, p.authors AS authors, "
            f"p.abstract AS abstract, p.full_text AS full_text, ({score_expr}) AS score "
            f"{base} ORDER BY score DESC, p.added_at DESC LIMIT :limit OFFSET :offset"
        ),
        {**params, "limit": limit, "offset": offset},
    ).all()

    hits: list[SearchHit] = []
    for row in rows:
        values = {
            "title": row.title,
            "abstract": row.abstract,
            "authors": row.authors,
            "full_text": row.full_text,
        }
        for field in _FIELDS:
            snippet = _like_snippet(values[field] or "", terms)
            if snippet is not None:
                hits.append(
                    SearchHit(
                        paper_id=row.paper_id,
                        title=row.title,
                        snippet=_to_html(snippet),
                        field=field,
                        rank=float(row.score),
                    )
                )
                break
    return SearchPage(hits=hits, total=total, limit=limit, offset=offset, engine="like")


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def search(
    session: Session, *, user_id: str, query: str, limit: int = 20, offset: int = 0
) -> SearchPage:
    """Search ``user_id``'s live papers for ``query``.

    Only that user's papers, and only ones not in the trash: a deleted paper
    resurfacing through search would undo the delete from the user's point of
    view.

    A query with nothing searchable in it — blank, or all punctuation like ``*`` —
    returns an empty page rather than an error. It is not a failure worth a 4xx;
    the user simply has not typed anything to look for yet.
    """
    _require_owner(user_id)
    terms = parse_query(query)
    engine_name = "fts5" if fts5_available() else "like"
    if not terms:
        return SearchPage(hits=[], total=0, limit=limit, offset=offset, engine=engine_name)

    if fts5_available():
        try:
            return _search_fts(session, user_id=user_id, terms=terms, limit=limit, offset=offset)
        except OperationalError:
            # Belt and braces. build_match_expression is designed so this cannot
            # fire, but the cost of being wrong is a 500 on a search box, and the
            # fallback is right here. A degraded result beats an error page.
            session.rollback()
    return _search_like(session, user_id=user_id, terms=terms, limit=limit, offset=offset)
