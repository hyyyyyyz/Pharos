"""Page-addressable chunks — the substrate under every citation Pharos makes.

``Paper.full_text`` is one flat run of characters with the page breaks
normalised away. That is the right shape for the search index and the wrong
shape for a citation: a claim is only checkable if a reader can be sent to the
page it came from, and the flat text has thrown that away by the time anything
reads it. :class:`~pharos.db.models.PaperChunk` keeps the boundary, one row per
page, and records where each page sits *inside* the flat text so a search hit
and a citation are demonstrably talking about the same characters.

This is a separate module from :mod:`pharos.services.search` rather than more
of it, because they answer to different consumers. Search owns "find me the
paper"; chunks own "show me the page", which is the evidence chain in
``docs/RESEARCH_WORKFLOW.md`` and which will grow retrieval and evidence
extraction on top. They share the *extraction* — one parse of the PDF produces
both the flat column and these rows, and that sharing is the reason the offsets
below can be exact rather than a best guess — so extraction stays in search.py
where the flat column lives and this module imports it.

Owner scoping works as it does everywhere else in this package (see
:func:`pharos.services.library._require_owner`): every row carries the owning
``user_id`` copied from the paper, and every statement here names it. A chunk
is a verbatim copy of a private document's text, so a query that lost its owner
predicate would not surface as a wrong count on a screen — it would put one
researcher's paper into another's evidence panel.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from pharos.db.models import Paper, PaperChunk
from pharos.services.search import FULL_TEXT_MAX_CHARS, PageText, extract_pages
from pharos.storage.blobs import BlobStore

#: Which extractor wrote a row, stamped on every chunk this module creates.
#:
#: Bumped when extraction changes *shape* — a different page model, chunks
#: smaller than a page — not when its output merely happens to differ on some
#: file. The number is what lets a later improvement find and replace exactly
#: its predecessor's rows instead of re-chunking a library to discover that
#: nothing changed.
EXTRACTION_VERSION = 1


def _require_owner(user_id: str | None) -> str:
    """Reject a paper that has no owner before it can reach a chunk row.

    ``Paper.user_id`` is nullable for the sake of pre-accounts rows;
    ``PaperChunk.user_id`` is not. So an ownerless paper cannot be chunked at
    all, and the choice is only about where that is discovered: here, as a
    caller error naming the problem, or four statements later as an integrity
    error inside a flush that has already deleted the paper's previous rows.

    The same guard as ``library._require_owner`` and ``search._require_owner``,
    restated rather than imported for the same reason they restate each other:
    a write path that silently loses its owner is the exact failure it exists to
    prevent, and one import away is one refactor away.
    """
    if not user_id:
        raise ValueError("paper.user_id is required: every chunk must carry its owner")
    return user_id


def _spans(
    pages: Sequence[PageText], full_text: str | None
) -> list[tuple[int, int] | tuple[None, None]]:
    """Locate each page inside ``full_text``, or admit that it is not in there.

    The candidate offsets are arithmetic rather than a search:
    :func:`~pharos.services.search.flatten_pages` joins these exact strings with
    one space, so page *n* begins at the sum of the preceding lengths plus one
    separator each. Searching for the text instead would be both slower and
    wrong — a repeated header line would match the first page that carries it.

    Every candidate is then *verified* against the stored string before it is
    used, which is what makes the NULLs in this table honest. Two things make
    the arithmetic answer not always the true one:

    * ``full_text`` is capped and trimmed to a word boundary, so the last page
      it reaches is usually cut mid-sentence and the pages after that are not in
      it at all.
    * The column need not have come from *this* extraction. A row written
      before the cap changed, or edited by hand, still has to yield either a
      correct span or none.

    A slice comparison costs one page of characters and turns "probably right"
    into "known right"; where it fails, both offsets are NULL. That is the model
    docstring's rule — an offset pointing at the wrong characters is worse than
    no offset, because it survives review while quoting a passage the paper does
    not contain at that position.
    """
    located: list[tuple[int, int] | tuple[None, None]] = []
    offset = 0
    for page in pages:
        start, end = offset, offset + len(page.text)
        offset = end + 1  # the single space flatten_pages joins pages with
        if full_text is not None and full_text[start:end] == page.text:
            located.append((start, end))
        else:
            located.append((None, None))
    return located


def populate_chunks(
    paper: Paper,
    path: Path,
    session: Session,
    *,
    pages: Sequence[PageText] | None = None,
    max_chars: int = FULL_TEXT_MAX_CHARS,
) -> int:
    """Write one chunk per page of ``path`` for ``paper``. Returns how many.

    ``pages`` is the escape hatch that keeps ingestion honest and cheap: the
    upload path has already extracted them to build ``paper.full_text`` and
    hands them straight over, so a PDF is parsed once per upload and both
    outputs provably describe the same characters. Left ``None``, this parses
    the file itself — the backfill's case, where there is no earlier pass to
    share.

    **Re-running replaces.** This paper's previous rows are deleted first, in
    the same savepoint that writes the new ones, so re-chunking is a deliberate
    replacement rather than a collision with ``uq_paper_chunk_page`` that some
    retry has to clean up after. Pages shift between extractor versions; an
    insert-and-hope would leave a paper holding two generations of rows, half of
    them with offsets into a ``full_text`` that no longer says that. Evidence
    rows that pointed at the old chunks survive with ``chunk_id`` set to NULL,
    which is the schema's own answer to this (see :class:`PaperChunk`) — they
    keep their quote and their page number.

    **Nothing extracted replaces nothing.** An extraction that comes back empty
    leaves the existing rows alone and returns 0. It is not evidence that the
    previous extraction was wrong — a purged blob, a file that PyMuPDF has
    started choking on — and silently emptying a paper's evidence substrate is a
    far worse outcome than keeping rows that were right when they were written.

    The whole write runs inside a SAVEPOINT. Chunking is a secondary output of
    ingestion and must never be able to fail the upload, but "the caller catches
    it" is not enough on its own: a failed flush poisons the surrounding
    transaction, so without the savepoint a rejected chunk row would take the
    paper down with it at commit time — the failure mode the caller's ``except``
    was written to prevent, arriving after the ``except`` has already run.
    """
    user_id = _require_owner(paper.user_id)
    if pages is None:
        pages = extract_pages(path, max_chars=max_chars)
    if not pages:
        return 0

    spans = _spans(pages, paper.full_text)
    with session.begin_nested():
        session.execute(
            delete(PaperChunk).where(PaperChunk.user_id == user_id, PaperChunk.paper_id == paper.id)
        )
        session.add_all(
            [
                PaperChunk(
                    user_id=user_id,
                    paper_id=paper.id,
                    page_no=page.page_no,
                    # One page is one chunk today. The column exists so a page
                    # can later be split without moving the page contract.
                    ordinal=0,
                    text=page.text,
                    char_start=start,
                    char_end=end,
                    extraction_version=EXTRACTION_VERSION,
                )
                for page, (start, end) in zip(pages, spans, strict=True)
            ]
        )
        # Flushed inside the savepoint on purpose: an error that surfaced at the
        # caller's commit instead would be outside anything that can contain it.
        session.flush()
    return len(pages)


def chunks_for(session: Session, paper: Paper) -> list[PaperChunk]:
    """This paper's chunks in reading order, scoped to its owner.

    Ordered by ``(page_no, ordinal)`` rather than by insertion: the row order a
    database returns unordered is not a promise, and every consumer of chunks —
    a citation, a retrieval window, a re-assembled page — reads them as a
    document.
    """
    user_id = _require_owner(paper.user_id)
    return list(
        session.scalars(
            select(PaperChunk)
            .where(PaperChunk.user_id == user_id, PaperChunk.paper_id == paper.id)
            .order_by(PaperChunk.page_no, PaperChunk.ordinal)
        )
    )


@dataclass(frozen=True)
class ChunkBackfillReport:
    """What a :func:`backfill_chunks` pass did, for the operator running it.

    Deliberately the same four counters as
    :class:`~pharos.services.search.BackfillReport` rather than that class
    itself: the two backfills are read side by side and should tally the same
    way, but ``updated`` counts different things (a column set, versus a set of
    rows written) and sharing the type would make one of them impossible to
    extend without disturbing the other.
    """

    scanned: int = 0
    updated: int = 0
    missing_file: int = 0
    no_text: int = 0


def backfill_chunks(
    session: Session,
    blobs: BlobStore,
    *,
    user_id: str | None,
    limit: int | None = None,
) -> ChunkBackfillReport:
    """Chunk the papers that were uploaded before this module existed.

    Mirrors :func:`~pharos.services.search.backfill_full_text` exactly, down to
    the ``user_id`` keyword being *required* and explicitly nullable for "every
    paper in the database". That is the one query here without an owner
    predicate, and it is safe for the same stated reason: it is a write path
    that returns counts and never rows, so nothing a caller learns from it
    depends on whose papers were touched. Required-but-nullable means reaching
    the global case is something somebody typed rather than inherited. Like its
    sibling it is a maintenance entry point — no endpoint reaches it, and there
    is no CLI; it is called from a shell or a test inside ``session_scope``.

    Idempotent because the selection is "papers with no chunks at all", so a
    second pass over the same library scans nothing. The one row that is
    rescanned every time is the paper whose PDF yields no text — exactly as in
    ``backfill_full_text``, where a paper that extracts to nothing keeps a NULL
    ``full_text`` and is looked at again next run. Cheap, and the alternative is
    a "we tried" marker that would have to be invalidated by hand the day the
    extractor improves.

    Re-chunking rows an *older* extractor wrote is a different job and not this
    one: it would select on ``extraction_version`` (see
    :data:`EXTRACTION_VERSION`) and must replace rows rather than skip papers
    that have some, so conflating the two would give this function a mode where
    it destroys current data.

    Not committed here: the caller owns the transaction.
    """
    has_chunks = select(PaperChunk.id).where(PaperChunk.paper_id == Paper.id).exists()
    stmt = select(Paper).where(
        ~has_chunks,
        # Pre-accounts rows have no owner and ``PaperChunk.user_id`` is NOT
        # NULL, so they cannot be chunked. Excluded in SQL rather than skipped
        # in the loop so they are not counted as scanned — a backfill that
        # reported work it could never do would look like a bug that never
        # converges.
        Paper.user_id.is_not(None),
    )
    if user_id is not None:
        stmt = stmt.where(Paper.user_id == user_id)
    if limit is not None:
        stmt = stmt.limit(limit)

    scanned = updated = missing = empty = 0
    # Materialised before the loop, unlike its sibling: the selection is defined
    # by a NOT EXISTS over the very table the loop writes to, and a result still
    # being streamed while rows are inserted underneath it is not a cursor whose
    # remaining contents are worth reasoning about.
    for paper in session.scalars(stmt).all():
        scanned += 1
        path = blobs.path(paper.orig_sha256, "original")
        if not path.exists():
            # The blob was purged, or this row predates the current store. Not
            # an error: it just cannot be chunked.
            missing += 1
            continue
        if populate_chunks(paper, path, session):
            updated += 1
        else:
            empty += 1
    return ChunkBackfillReport(
        scanned=scanned, updated=updated, missing_file=missing, no_text=empty
    )
