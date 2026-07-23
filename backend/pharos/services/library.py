"""Library service — importing, describing and retiring papers."""

from __future__ import annotations

import contextlib
import dataclasses
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from pharos.db.models import Paper
from pharos.services.enrich import enrich_by_arxiv, enrich_by_doi, merge
from pharos.services.metadata import ExtractedMeta, extract_from_pdf
from pharos.services.search import populate_full_text
from pharos.storage.blobs import BlobStore

#: Total wall-clock budget for registry lookups during an upload. Parsing the
#: PDF is local and cheap, but enrichment is a third-party HTTP call that may be
#: slow or down. Whatever has not finished when the budget runs out is skipped
#: and the paper is saved regardless.
#:
#: Note this *is* wall-clock the user waits: upload is a synchronous endpoint,
#: so a hung registry delays the response by up to this much (a refused
#: connection fails instantly, which is the common case). Kept short enough to
#: stay under the threshold where an upload feels stalled — the metadata is
#: nice-to-have, and ``POST /papers/{id}/metadata/refresh`` exists precisely so
#: a miss can be corrected later without holding up ingestion now.
UPLOAD_ENRICH_BUDGET = 2.5
#: A user-initiated refresh is an explicit "please try harder", so it is allowed
#: to wait considerably longer than an upload.
REFRESH_ENRICH_BUDGET = 15.0
#: Starting a request with less than this left would only buy a timeout.
_MIN_LOOKUP_SECONDS = 0.5
#: What ``enrich_by_arxiv`` reports for a preprint with no ``journal_ref`` yet.
_ARXIV_PLACEHOLDER_VENUE = "arXiv"

#: Hard ceilings applied to anything on its way into the papers table.
#:
#: Both producers now bound their own output: ``metadata.py`` caps what it
#: extracts from a PDF, and ``enrich.py`` caps what it accepts from CrossRef or
#: arXiv (300/120/100 for title/venue/DOI, dropping anything longer rather than
#: truncating it, so a mangled registry record loses to the PDF's own guess in
#: ``merge`` instead of overwriting it). So nothing here is expected to fire.
#: That is the point: this is the last thing between third-party text and the
#: schema, not the only thing. It stays because SQLite does not enforce
#: ``VARCHAR`` length — an over-long value would be written silently and only
#: become an error if the library were ever moved to a database that does, i.e.
#: the corruption would be introduced now and discovered much later.
#:
#: The first three track their column widths (``Paper.title`` is ``String(512)``,
#: ``venue`` ``String(256)``, ``doi`` ``String(128)``) and are deliberately
#: looser than the producers' caps. Restating 300/120/100 here would put one
#: editorial judgement in two files with nothing keeping them in step, and the
#: stale copy would start silently truncating values the other had already judged
#: fine. Tracking the column instead gives this clamp a rule of its own that
#: cannot drift: whatever a producer thinks is worth storing, we store, and the
#: only thing we refuse is a value the column physically cannot hold.
_MAX_TITLE = 512
_MAX_VENUE = 256
_MAX_DOI = 128
#: These two are not column guards — ``authors`` and ``abstract`` are ``Text``,
#: which has no width to overflow. They bound how much a single row can weigh
#: when every library listing has to carry it, so they are sized by what is
#: reasonable to hand a client rather than by the schema.
_MAX_AUTHORS_JOINED = 8000
_MAX_ABSTRACT = 20000


def _fit(value: str, limit: int) -> str:
    """Trim ``value`` to ``limit`` characters, preferring a word boundary."""
    if len(value) <= limit:
        return value
    cut = value.rfind(" ", 0, limit)
    return value[: cut if cut > limit // 2 else limit].rstrip()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _pdf_metadata(path: Path, fallback_name: str) -> tuple[int | None, str]:
    """Return (page_count, title) for a PDF, tolerating malformed files."""
    title = Path(fallback_name).stem
    page_count: int | None = None
    try:
        import pymupdf

        with pymupdf.open(path) as doc:
            page_count = doc.page_count
            meta_title = (doc.metadata or {}).get("title")
            if meta_title and meta_title.strip():
                title = meta_title.strip()
    except Exception:
        pass
    return page_count, title


def _enrich(meta: ExtractedMeta, budget: float) -> tuple[ExtractedMeta, str]:
    """Overlay registry data onto a PDF extraction. Returns (meta, source).

    A DOI is tried before an arXiv id because CrossRef describes the version of
    record, while arXiv describes a preprint that may predate peer review. The
    two lookups share one deadline rather than getting one each, so a hanging
    CrossRef cannot double the caller's worst case.

    Any failure — network, parse, or a registry that simply has never heard of
    this paper — degrades to the local extraction rather than to an error.
    """
    deadline = time.monotonic() + budget
    for identifier, fetch, source in (
        (meta.doi, enrich_by_doi, "crossref"),
        (meta.arxiv_id, enrich_by_arxiv, "arxiv"),
    ):
        if not identifier:
            continue
        remaining = deadline - time.monotonic()
        if remaining < _MIN_LOOKUP_SECONDS:
            break
        try:
            better = fetch(identifier, timeout=remaining)
        except Exception:
            better = None
        if better is not None:
            return _overlay(meta, better, source), source
    return meta, "pdf"


def _overlay(base: ExtractedMeta, better: ExtractedMeta, source: str) -> ExtractedMeta:
    """``merge`` plus one call-site correction the registry cannot make itself.

    arXiv names *itself* as the venue for anything without a ``journal_ref``.
    That is the honest answer to "where was this published" in isolation, but as
    an overlay it is a downgrade: the Attention paper's own front page says
    "NIPS 2017", and letting the placeholder win replaces a real venue with one
    that carries no information. Only enrich.py knows the value is a fallback;
    only we know what it would be overwriting — so the reconciliation belongs
    here rather than in either module alone.
    """
    merged = merge(base, better)
    if source == "arxiv" and base.venue and merged.venue == _ARXIV_PLACEHOLDER_VENUE:
        return dataclasses.replace(merged, venue=base.venue)
    return merged


def _collect(path: Path, budget: float) -> tuple[ExtractedMeta, str]:
    """Parse ``path`` and enrich it, within ``budget`` seconds of network time."""
    try:
        base = extract_from_pdf(path)
    except Exception:
        # extract_from_pdf documents that it never raises; belt and braces, since
        # a parser bug must not be able to reject an otherwise valid upload.
        base = ExtractedMeta()
    if budget <= 0:
        return base, "pdf"
    return _enrich(base, budget)


def _apply_metadata(paper: Paper, meta: ExtractedMeta, source: str) -> None:
    """Copy ``meta`` onto ``paper``, never replacing a known value with a blank.

    A re-parse that finds less than the first one must not erase what the first
    one found: in the UI, "—" has to mean "nobody ever knew", not "we forgot".

    ``meta_extracted_at`` is stamped unconditionally — it records the *attempt*,
    which is what tells a later caller that a metadata-free paper is a scan we
    already gave up on rather than one we never looked at. ``meta_source`` is
    only set when something was actually found, because labelling the provenance
    of nothing is meaningless.
    """
    found = False
    if meta.title:
        # Extraction only ever returns a title that passed its own plausibility
        # check, so it always beats the filename stem we started with.
        paper.title = _fit(meta.title, _MAX_TITLE)
        found = True
    if meta.authors:
        paper.authors = _fit("; ".join(meta.authors), _MAX_AUTHORS_JOINED)
        found = True
    if meta.year is not None:
        paper.year = meta.year
        found = True
    if meta.venue:
        paper.venue = _fit(meta.venue, _MAX_VENUE)
        found = True
    if meta.doi:
        paper.doi = _fit(meta.doi, _MAX_DOI)
        found = True
    if meta.abstract:
        paper.abstract = _fit(meta.abstract, _MAX_ABSTRACT)
        found = True
    if meta.arxiv_id:
        # Note: Paper.source stays "upload". It records how the file entered the
        # library, not where its bibliographic data came from.
        paper.arxiv_id = meta.arxiv_id
        found = True

    paper.meta_extracted_at = _now()
    if found:
        paper.meta_source = source


def _index_full_text(paper: Paper, path: Path) -> None:
    """Extract ``path``'s text onto ``paper`` for search, never failing the upload.

    Search is a secondary feature of ingestion; keeping the file is the primary
    one. A paper whose text cannot be read — a scan with no text layer, an
    encrypted PDF, a file that PyMuPDF chokes on — is still a paper the user
    wants in their library, and it simply is not full-text searchable. So every
    failure here degrades to "not searchable" rather than propagating.

    ``populate_full_text`` already documents that it does not raise, and this
    still wraps it, for the same reason ``_collect`` wraps ``extract_from_pdf``
    just above: the guarantee lives in another module that is free to change,
    and the cost of being wrong is a rejected upload of a perfectly good file.
    Belt and braces is the right trade when one side is a whole lost document.
    """
    # Suppressed rather than logged-and-re-raised: nothing about a text
    # extraction failure should be able to reach the uploader.
    with contextlib.suppress(Exception):
        populate_full_text(paper, path)


def _require_owner(user_id: str) -> str:
    """Reject a falsy owner id before it reaches a WHERE clause.

    Every query below is scoped by ``Paper.user_id == user_id``. If a caller ever
    threads through ``None``, SQLAlchemy renders ``user_id IS NULL``, which
    matches exactly the pre-accounts legacy rows — a silently ownerless view of
    the library instead of an error. An empty string is just as wrong. Failing
    here turns a caller bug into a 500 the operator sees, never a wrong-user
    result the client renders as their own.
    """
    if not user_id:
        raise ValueError("user_id is required: every library query must be owner-scoped")
    return user_id


class LibraryService:
    """Owner-scoped access to the papers table.

    Every method that *looks a paper up by id* takes the owning ``user_id`` as a
    required keyword argument. Required rather than optional on purpose: an
    optional owner is one a caller can forget, and a forgotten filter here is not
    a bug that shows up as a wrong number on screen — it is one user's private
    library rendered in another user's browser. Required means the type checker
    catches the omission before a reviewer has to.

    The mutators (``refresh_metadata``, ``soft_delete``, ``restore``, ``purge``)
    take an already-resolved ``Paper`` instead, and so inherit the scoping of
    whatever fetched it. That is safe only because the single lookup path,
    ``get_paper``, is owner-scoped — so any new caller must obtain its ``Paper``
    through a filter it cannot skip. Do not add a second, unscoped way to load a
    ``Paper`` for these to consume.
    """

    def __init__(self, blobs: BlobStore) -> None:
        self.blobs = blobs

    # ------------------------------------------------------------ importing

    def add_upload(self, session: Session, *, user_id: str, filename: str, data: bytes) -> Paper:
        """Store an uploaded PDF and create (or reuse) *this user's* Paper row.

        Content addressing means re-uploading the same file returns the existing
        paper instead of duplicating it — but "existing" is now scoped to the
        uploader. Two researchers uploading the same well-known paper is the
        expected case, not a collision: they legitimately share the blob on disk,
        and each gets their own row, their own metadata corrections, and their
        own trash state. Matching on ``orig_sha256`` alone would have handed the
        second uploader the first one's row, which is both a data leak and a
        write into someone else's record.
        """
        _require_owner(user_id)
        sha256, path = self.blobs.store_original(data)
        existing = session.scalar(
            select(Paper).where(Paper.orig_sha256 == sha256, Paper.user_id == user_id)
        )
        if existing is not None:
            # Re-uploading a paper the user had trashed is a clear statement that
            # they want it back; returning the row untouched would leave them
            # staring at a library that silently refuses to accept the file.
            existing.deleted_at = None
            if existing.meta_extracted_at is None:
                meta, source = _collect(path, UPLOAD_ENRICH_BUDGET)
                _apply_metadata(existing, meta, source)
            if existing.full_text is None:
                # Re-uploading is how a user reaches a paper that predates search
                # (or whose first extraction found nothing) without an operator
                # running a backfill. Guarded on None so a re-upload never spends
                # the parse on a paper that is already indexed.
                _index_full_text(existing, path)
            return existing

        page_count, title = _pdf_metadata(path, filename)
        paper = Paper(
            user_id=user_id,
            title=title,
            orig_sha256=sha256,
            orig_filename=filename,
            page_count=page_count,
        )
        session.add(paper)
        session.flush()  # populate paper.id
        meta, source = _collect(path, UPLOAD_ENRICH_BUDGET)
        _apply_metadata(paper, meta, source)
        _index_full_text(paper, path)
        return paper

    def refresh_metadata(
        self, session: Session, paper: Paper, *, budget: float = REFRESH_ENRICH_BUDGET
    ) -> bool:
        """Re-parse and re-enrich the stored original. False if it is missing.

        This is the user's escape hatch from a bad first parse, so every field the
        file *does* yield overwrites what is stored, manual edits included: asking
        for a refresh is asking for the file to be believed over the database.

        A field the file does not yield is left as it was, so a manually entered
        DOI survives a refresh that could not find one. Keeping a value the user
        typed beats silently discarding it; clearing it is one explicit PATCH.
        """
        path = self.blobs.path(paper.orig_sha256, "original")
        if not path.exists():
            return False
        meta, source = _collect(path, budget)
        _apply_metadata(paper, meta, source)
        return True

    # -------------------------------------------------------------- reading

    def list_papers(self, session: Session, *, user_id: str, trash: bool = False) -> list[Paper]:
        """One user's library, or — with ``trash`` — their recycle bin instead.

        The two are mutually exclusive on purpose: there is no view in which a
        deleted paper should appear alongside live ones.
        """
        _require_owner(user_id)
        stmt = select(Paper).where(Paper.user_id == user_id)
        if trash:
            stmt = stmt.where(Paper.deleted_at.is_not(None)).order_by(Paper.deleted_at.desc())
        else:
            stmt = stmt.where(Paper.deleted_at.is_(None)).order_by(Paper.added_at.desc())
        return list(session.scalars(stmt))

    def get_paper(self, session: Session, paper_id: str, *, user_id: str) -> Paper | None:
        """Fetch one of *this user's* papers, soft-deleted ones included.

        Soft-deleted rows are returned because the trash view needs them; rows
        belonging to anyone else are not returned at all. Returning ``None`` for
        a paper that exists but is not theirs is deliberate and load-bearing: the
        callers turn ``None`` into a 404, so a probe cannot tell "no such paper"
        apart from "not yours" and therefore cannot enumerate another user's
        library by id.

        This is a filtered SELECT rather than ``session.get`` plus an ownership
        check afterwards. Both are correct today, but only one of them stays
        correct if someone later deletes a line: drop the ``where`` and the query
        fails to compile a scope rather than quietly returning every user's row.
        """
        _require_owner(user_id)
        return session.scalar(select(Paper).where(Paper.id == paper_id, Paper.user_id == user_id))

    # ------------------------------------------------------------- retiring

    def soft_delete(self, session: Session, paper: Paper) -> None:
        """Move a paper to the recycle bin, keeping every byte it owns.

        Idempotent, and it keeps the original timestamp on a repeat call so a
        stray second DELETE cannot quietly reset a retention clock.
        """
        if paper.deleted_at is None:
            paper.deleted_at = _now()

    def restore(self, session: Session, paper: Paper) -> None:
        paper.deleted_at = None

    def purge(self, session: Session, paper: Paper) -> None:
        """Permanently delete a paper, its jobs, and — only if unshared — its blobs.

        The blob directory is content-addressed, so two Paper rows can name the
        same sha256; the sibling check is what keeps one purge from destroying
        another paper's PDF.

        That check is deliberately NOT scoped to the owner, and it is the one
        query in this module where adding a ``user_id`` filter would be the bug.
        Under multi-user the common sharing case is precisely cross-user: two
        researchers upload the same paper, get two rows, and share one blob. An
        owner-scoped sibling check would see no siblings and ``rmtree`` bytes the
        other user's row still points at — their library would keep listing a
        paper whose PDF had silently vanished. Ownership decides who may purge a
        *row*; reference counting decides when the *bytes* go, and those are
        different questions. The caller has already established the row is
        theirs (``get_paper`` is owner-scoped), so this query only ever runs on a
        paper the user is entitled to destroy.

        The bytes are removed only *after* the transaction commits. Flushing the
        DELETE is not the same as committing it: the request's transaction stays
        open until the endpoint returns, and a commit can still fail afterwards —
        SQLite under WAL will raise on a busy database. Unlinking at flush time
        would mean a failed commit rolls the row back into existence while its
        PDF is already gone, which is precisely the permanently broken library
        entry this ordering exists to prevent. Deferring to ``after_commit``
        makes the surviving failure mode an orphaned directory: recoverable disk
        waste, and the strictly better half of the trade.
        """
        sha256 = paper.orig_sha256
        shared = session.scalar(
            select(Paper.id).where(Paper.orig_sha256 == sha256, Paper.id != paper.id).limit(1)
        )
        session.delete(paper)  # jobs follow via the relationship cascade
        session.flush()
        if shared is not None:
            return

        # No SQL in this handler: SQLAlchemy considers the session's transaction
        # already concluded here, and re-querying would open a new one behind the
        # caller's back. The sibling check above ran under the same write lock
        # that the DELETE holds, so it is still the answer at commit time.
        @event.listens_for(session, "after_commit", once=True)
        def _drop_blobs(_session: Session) -> None:
            self._remove_blobs(sha256)

    def _remove_blobs(self, sha256: str) -> None:
        """Delete ``<files_dir>/<sha256>/``, refusing anything outside the store."""
        if not sha256:
            return
        try:
            root = self.blobs.files_dir.resolve()
            target = self.blobs.paper_dir(sha256).resolve()
            if target.parent != root or not target.is_dir():
                return
            shutil.rmtree(target)
        except OSError:
            # The row is already gone; a locked or vanished file is not a reason
            # to fail the request the user asked for.
            pass
