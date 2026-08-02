"""Page chunks: the offsets, the owner, the replace, and the upload that must not fail.

Four properties are worth more than the rest of this file put together.

**The offsets are exact or they are NULL.** ``paper.full_text[start:end]`` must
equal the chunk's own text for every chunk that claims a span. A span that is
merely close points a citation at characters the paper does not contain there,
and that survives review — the reader sees a plausible quote and only discovers
it is wrong if they go and check.

**Page numbers are the PDF's own.** A page with no text layer is not a chunk,
and must not renumber the pages after it.

**Re-running replaces.** ``uq_paper_chunk_page`` means the alternative to a
deliberate delete is an integrity error at commit time, which is to say a failed
upload.

**Nothing here can fail an upload.** Chunking is a secondary output of
ingestion. A constraint violation, a broken extractor, a hostile file: all of
them have to end with the paper in the library and no chunks, never with a 500
and a lost document.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from pharos.db import session as db_session
from pharos.db.models import Paper, PaperChunk, User
from pharos.db.session import init_engine, session_scope
from pharos.services import chunks as chunks_service
from pharos.services.chunks import (
    EXTRACTION_VERSION,
    ChunkBackfillReport,
    backfill_chunks,
    chunks_for,
    populate_chunks,
)
from pharos.services.library import LibraryService
from pharos.services.search import extract_pages, populate_full_text
from pharos.storage.blobs import BlobStore
from sqlalchemy import delete, select

#: Prefixed rather than named "owner"/"other" because ``init_engine`` memoises
#: one engine per process: ids scoped to this module cannot collide with, or be
#: deleted by, another test module's fixture users.
OWNER = "chunks-owner"
OTHER = "chunks-other"


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """This module's own SQLite file, with both users present for real.

    The globals are reset first because ``init_engine`` memoises: without it,
    whichever module ran earlier owns the database and collection order decides
    whether these tests see their own papers.
    """
    if db_session._engine is not None:
        db_session._engine.dispose()
    db_session._engine = None
    db_session._SessionLocal = None
    db_session._fts5_available = None
    init_engine(tmp_path_factory.mktemp("db") / "pharos.db")
    with session_scope() as s:
        for uid in (OWNER, OTHER):
            s.add(User(id=uid, email=f"{uid}@example.test", password_hash="x"))
    yield


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    """Wipe this module's papers between tests.

    Chunks follow by cascade, but the backfill's selection is "every paper with
    no chunks" — so a leftover paper from an earlier test would silently join
    the next test's backfill and make its counts depend on ordering.
    """
    yield
    with session_scope() as s:
        s.execute(delete(Paper).where(Paper.user_id.in_((OWNER, OTHER))))


# ---------------------------------------------------------------- helpers


def _make_pdf(path: Path, pages: list[str]) -> None:
    import pymupdf

    doc = pymupdf.open()
    for body in pages:
        page = doc.new_page()
        page.insert_text((72, 72), body, fontsize=11)
    doc.save(path)
    doc.close()


def _dense_page(n: int) -> str:
    """A page that carries about 1300 characters once extracted."""
    return "\n".join(f"Chapter {n} line {i} sedimentation." for i in range(40))


def _paper(paper_id: str, *, user_id: str = OWNER, sha: str | None = None) -> Paper:
    return Paper(
        id=paper_id,
        user_id=user_id,
        title="A Paper",
        orig_sha256=sha or f"sha-{paper_id}",
        orig_filename=f"{paper_id}.pdf",
    )


def _stored(blobs: BlobStore, tmp_path: Path, pages: list[str], name: str = "paper") -> str:
    """Write a PDF into the blob store and return its sha, as an upload would."""
    pdf = tmp_path / f"{name}.pdf"
    _make_pdf(pdf, pages)
    sha, _ = blobs.store_original(pdf.read_bytes())
    return sha


def _chunk_a_paper(
    session, blobs: BlobStore, tmp_path: Path, pages: list[str], *, paper_id: str = "p-1"
) -> Paper:
    """The whole ingestion shape in one call: store, extract, flatten, chunk."""
    sha = _stored(blobs, tmp_path, pages, name=paper_id)
    paper = _paper(paper_id, sha=sha)
    session.add(paper)
    session.flush()
    path = blobs.path(sha, "original")
    populate_full_text(paper, path)
    populate_chunks(paper, path, session)
    return paper


# ------------------------------------------------------------- alignment


def _assert_aligned(paper: Paper, rows: list[PaperChunk]) -> None:
    """The property the whole table exists to provide."""
    for row in rows:
        if row.char_start is None or row.char_end is None:
            # Both or neither: half a span is not a location.
            assert row.char_start is None and row.char_end is None
            continue
        assert paper.full_text is not None
        assert paper.full_text[row.char_start : row.char_end] == row.text


def test_offsets_are_the_chunk_s_exact_span_in_full_text(tmp_path) -> None:
    with session_scope() as s:
        blobs = BlobStore(tmp_path / "files")
        paper = _chunk_a_paper(
            s,
            blobs,
            tmp_path,
            ["Introduction to bathymetry", "Methods and materials", "Conclusions drawn"],
        )
        rows = chunks_for(s, paper)

        assert [r.page_no for r in rows] == [1, 2, 3]
        assert all(r.char_start is not None for r in rows)
        _assert_aligned(paper, rows)


def test_a_page_without_text_does_not_renumber_the_pages_after_it(tmp_path) -> None:
    with session_scope() as s:
        blobs = BlobStore(tmp_path / "files")
        paper = _chunk_a_paper(s, blobs, tmp_path, ["Alpha one", "", "Gamma three"])
        rows = chunks_for(s, paper)

        assert [r.page_no for r in rows] == [1, 3]
        assert rows[1].text == "Gamma three"
        _assert_aligned(paper, rows)


def test_pages_past_the_full_text_cap_have_null_offsets(tmp_path) -> None:
    """Truncation is where a wrong offset would otherwise be written.

    ``full_text`` is capped and trimmed back to a word boundary, so the page the
    cap lands in is only partly there and anything after it is not there at all.
    Those chunks keep their text and their page number — they are still citable
    — and say nothing about where they sit in a string that does not contain
    them.
    """
    with session_scope() as s:
        blobs = BlobStore(tmp_path / "files")
        # Real lines rather than one long one: PDF text is clipped at the page
        # margin, so a single 500-character line extracts as the 100 characters
        # that fit and nothing is ever long enough to truncate.
        sha = _stored(blobs, tmp_path, [_dense_page(n) for n in range(4)])
        paper = _paper("p-book", sha=sha)
        s.add(paper)
        s.flush()
        path = blobs.path(sha, "original")
        populate_full_text(paper, path, max_chars=2000)
        populate_chunks(paper, path, s, max_chars=2000)

        rows = chunks_for(s, paper)
        assert len(rows) > 1
        _assert_aligned(paper, rows)
        # The truncation really did cost somebody their span, or this test is
        # asserting alignment over a document that never got cut.
        assert any(r.char_start is None for r in rows)
        assert rows[0].char_start == 0


def test_offsets_are_null_when_the_paper_has_no_full_text(tmp_path) -> None:
    """A chunk without a flat text to point into is still a chunk."""
    with session_scope() as s:
        blobs = BlobStore(tmp_path / "files")
        sha = _stored(blobs, tmp_path, ["Ultramafic intrusions in the craton"])
        paper = _paper("p-notext", sha=sha)
        s.add(paper)
        s.flush()
        assert populate_chunks(paper, blobs.path(sha, "original"), s) == 1

        (row,) = chunks_for(s, paper)
        assert (row.char_start, row.char_end) == (None, None)
        assert row.text == "Ultramafic intrusions in the craton"


def test_offsets_are_null_when_full_text_came_from_somewhere_else(tmp_path) -> None:
    """The offsets are verified, not assumed — a stale column must not be trusted."""
    with session_scope() as s:
        blobs = BlobStore(tmp_path / "files")
        sha = _stored(blobs, tmp_path, ["Ultramafic intrusions in the craton"])
        paper = _paper("p-stale", sha=sha)
        paper.full_text = "Something a much older extractor wrote."
        s.add(paper)
        s.flush()
        populate_chunks(paper, blobs.path(sha, "original"), s)

        (row,) = chunks_for(s, paper)
        assert (row.char_start, row.char_end) == (None, None)


def test_chunks_record_the_extractor_that_wrote_them(tmp_path) -> None:
    with session_scope() as s:
        blobs = BlobStore(tmp_path / "files")
        paper = _chunk_a_paper(s, blobs, tmp_path, ["Alpha one"])
        (row,) = chunks_for(s, paper)
        assert row.extraction_version == EXTRACTION_VERSION
        assert row.ordinal == 0


# --------------------------------------------------------------- replace


def test_re_running_replaces_rather_than_duplicates(tmp_path) -> None:
    """The unique constraint is not the mechanism, it is the backstop."""
    with session_scope() as s:
        blobs = BlobStore(tmp_path / "files")
        paper = _chunk_a_paper(s, blobs, tmp_path, ["Alpha one", "Beta two"])
        first = {r.id for r in chunks_for(s, paper)}

        written = populate_chunks(paper, blobs.path(paper.orig_sha256, "original"), s)

        rows = chunks_for(s, paper)
        assert written == len(rows) == 2
        assert [r.page_no for r in rows] == [1, 2]
        # New rows, not the old ones survived by luck.
        assert not first & {r.id for r in rows}


def test_re_running_over_a_shorter_document_drops_the_pages_that_went(tmp_path) -> None:
    """Replace means replace: a stale page must not outlive the text it described."""
    with session_scope() as s:
        blobs = BlobStore(tmp_path / "files")
        paper = _chunk_a_paper(s, blobs, tmp_path, ["Alpha one", "Beta two", "Gamma three"])
        assert len(chunks_for(s, paper)) == 3

        shorter = _stored(blobs, tmp_path, ["Alpha one"], name="shorter")
        populate_chunks(paper, blobs.path(shorter, "original"), s)

        assert [r.page_no for r in chunks_for(s, paper)] == [1]


def test_an_extraction_that_found_nothing_leaves_the_old_chunks_alone(tmp_path) -> None:
    """An unreadable file is not evidence that the last good read was wrong.

    A purged blob or a newly-unparseable PDF must not silently empty a paper's
    evidence substrate; 0 written means nothing changed.
    """
    with session_scope() as s:
        blobs = BlobStore(tmp_path / "files")
        paper = _chunk_a_paper(s, blobs, tmp_path, ["Alpha one", "Beta two"])
        junk = tmp_path / "not-a.pdf"
        junk.write_bytes(b"this is not a pdf at all")

        assert populate_chunks(paper, junk, s) == 0
        assert len(chunks_for(s, paper)) == 2


# ----------------------------------------------------------------- owner


def test_every_chunk_carries_its_paper_s_owner(tmp_path) -> None:
    with session_scope() as s:
        blobs = BlobStore(tmp_path / "files")
        paper = _chunk_a_paper(s, blobs, tmp_path, ["Alpha one", "Beta two"])
        assert {r.user_id for r in chunks_for(s, paper)} == {OWNER}


def test_a_paper_with_no_owner_is_refused_before_it_writes_anything(tmp_path) -> None:
    """Legacy pre-accounts rows cannot be chunked, and must say so.

    ``PaperChunk.user_id`` is NOT NULL, so the alternative to this guard is an
    integrity error raised after the paper's previous chunks have already been
    deleted.
    """
    with session_scope() as s:
        blobs = BlobStore(tmp_path / "files")
        sha = _stored(blobs, tmp_path, ["Alpha one"])
        orphan = Paper(
            id="p-orphan", user_id=None, title="Legacy", orig_sha256=sha, orig_filename="x.pdf"
        )
        with pytest.raises(ValueError, match="owner"):
            populate_chunks(orphan, blobs.path(sha, "original"), s)
        assert s.scalar(select(PaperChunk.id).where(PaperChunk.paper_id == "p-orphan")) is None


def test_one_user_s_chunks_are_not_visible_through_another_s_paper(tmp_path) -> None:
    """The isolation property, at the only read this module offers."""
    with session_scope() as s:
        blobs = BlobStore(tmp_path / "files")
        mine = _chunk_a_paper(s, blobs, tmp_path, ["Alpha one"], paper_id="p-mine")
        theirs = _paper("p-theirs", user_id=OTHER, sha=mine.orig_sha256)
        s.add(theirs)
        s.flush()

        assert [r.paper_id for r in chunks_for(s, mine)] == ["p-mine"]
        assert chunks_for(s, theirs) == []


# ------------------------------------------------------- write containment


def test_a_failed_chunk_write_does_not_poison_the_transaction(tmp_path) -> None:
    """The savepoint, stated as the failure it prevents.

    A rejected chunk row inside the caller's transaction would otherwise take
    the paper down with it at commit — the upload failing *after* the code that
    catches chunking errors has already run and returned.
    """
    from sqlalchemy.exc import IntegrityError

    blobs = BlobStore(tmp_path / "files")
    sha = _stored(blobs, tmp_path, ["Alpha one"])
    with session_scope() as s:
        # Never added to the session, so its id names no row: the chunk insert
        # fails the foreign key rather than any check this module could make.
        ghost = _paper("p-ghost", sha=sha)
        with pytest.raises(IntegrityError):
            populate_chunks(ghost, blobs.path(sha, "original"), s)

        # The transaction is still usable, and this commit must succeed.
        s.add(_paper("p-real", sha=sha))

    with session_scope() as s:
        assert s.get(Paper, "p-real") is not None
        assert s.scalar(select(PaperChunk.id).where(PaperChunk.paper_id == "p-ghost")) is None


# ------------------------------------------------------------- the upload


def test_upload_writes_chunks(tmp_path) -> None:
    library = LibraryService(BlobStore(tmp_path / "files"))
    pdf = tmp_path / "upload.pdf"
    _make_pdf(pdf, ["Introduction to bathymetry", "", "Conclusions about bathymetry"])

    with session_scope() as s:
        paper = library.add_upload(s, user_id=OWNER, filename="upload.pdf", data=pdf.read_bytes())
        paper_id = paper.id

    with session_scope() as s:
        paper = s.get(Paper, paper_id)
        rows = chunks_for(s, paper)
        assert [r.page_no for r in rows] == [1, 3]
        _assert_aligned(paper, rows)
        assert all(r.char_start is not None for r in rows)


def test_upload_parses_the_pdf_once(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both outputs come from one pass. Uploads are wall-clock the user waits."""
    calls = 0
    real = extract_pages

    def _counted(path, **kwargs):
        nonlocal calls
        calls += 1
        return real(path, **kwargs)

    monkeypatch.setattr("pharos.services.library.extract_pages", _counted)
    monkeypatch.setattr("pharos.services.chunks.extract_pages", _counted)

    library = LibraryService(BlobStore(tmp_path / "files"))
    pdf = tmp_path / "once.pdf"
    _make_pdf(pdf, ["Alpha one", "Beta two"])
    with session_scope() as s:
        library.add_upload(s, user_id=OWNER, filename="once.pdf", data=pdf.read_bytes())

    assert calls == 1


def test_a_chunking_failure_does_not_reject_a_good_pdf(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The discipline ``_index_text`` inherits from ``_index_full_text``.

    Patched where library.py *uses* the name: patching the chunks module would
    leave the imported binding intact and quietly test nothing.
    """

    def _explode(*_args, **_kwargs):
        raise RuntimeError("chunking is broken today")

    monkeypatch.setattr("pharos.services.library.populate_chunks", _explode)

    library = LibraryService(BlobStore(tmp_path / "files"))
    pdf = tmp_path / "resilient.pdf"
    _make_pdf(pdf, ["Palaeomagnetic reversals in the Deccan traps"])
    with session_scope() as s:
        paper = library.add_upload(
            s, user_id=OWNER, filename="resilient.pdf", data=pdf.read_bytes()
        )
        paper_id = paper.id

    with session_scope() as s:
        paper = s.get(Paper, paper_id)
        assert paper is not None
        # Full text still landed: the two outputs fail independently.
        assert "Palaeomagnetic" in (paper.full_text or "")
        assert chunks_for(s, paper) == []


def test_re_uploading_chunks_a_paper_that_predates_chunking(tmp_path) -> None:
    """The upgrade path a user can reach without an operator.

    Mirrors the existing ``full_text is None`` guard: a paper indexed by the
    older version has text but no chunks, and re-uploading is how it catches up.
    """
    library = LibraryService(BlobStore(tmp_path / "files"))
    pdf = tmp_path / "again.pdf"
    _make_pdf(pdf, ["Alpha one", "Beta two"])
    data = pdf.read_bytes()

    with session_scope() as s:
        paper = library.add_upload(s, user_id=OWNER, filename="again.pdf", data=data)
        paper_id = paper.id
    with session_scope() as s:
        # Exactly the state the older version left behind.
        s.execute(delete(PaperChunk).where(PaperChunk.paper_id == paper_id))

    with session_scope() as s:
        library.add_upload(s, user_id=OWNER, filename="again.pdf", data=data)
    with session_scope() as s:
        assert [r.page_no for r in chunks_for(s, s.get(Paper, paper_id))] == [1, 2]


def test_re_uploading_a_fully_indexed_paper_does_not_re_parse_it(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard is there to *avoid* work; assert it still does."""
    library = LibraryService(BlobStore(tmp_path / "files"))
    pdf = tmp_path / "twice.pdf"
    _make_pdf(pdf, ["Alpha one"])
    data = pdf.read_bytes()
    with session_scope() as s:
        library.add_upload(s, user_id=OWNER, filename="twice.pdf", data=data)

    def _explode(*_args, **_kwargs):
        raise AssertionError("re-parsed a paper that was already indexed")

    monkeypatch.setattr("pharos.services.library.extract_pages", _explode)
    with session_scope() as s:
        library.add_upload(s, user_id=OWNER, filename="twice.pdf", data=data)


# -------------------------------------------------------------- backfill


def test_backfill_chunks_papers_uploaded_before_this_existed(tmp_path) -> None:
    blobs = BlobStore(tmp_path / "files")
    sha = _stored(blobs, tmp_path, ["Palaeomagnetic reversals", "in the Deccan traps"])
    with session_scope() as s:
        paper = _paper("p-old", sha=sha)
        s.add(paper)
        s.flush()
        populate_full_text(paper, blobs.path(sha, "original"))

    with session_scope() as s:
        report = backfill_chunks(s, blobs, user_id=OWNER)
        assert isinstance(report, ChunkBackfillReport)
        assert (report.scanned, report.updated) == (1, 1)

    with session_scope() as s:
        paper = s.get(Paper, "p-old")
        rows = chunks_for(s, paper)
        assert [r.page_no for r in rows] == [1, 2]
        _assert_aligned(paper, rows)
        assert all(r.char_start is not None for r in rows)


def test_backfill_is_idempotent(tmp_path) -> None:
    blobs = BlobStore(tmp_path / "files")
    sha = _stored(blobs, tmp_path, ["Alpha one"])
    with session_scope() as s:
        s.add(_paper("p-old", sha=sha))

    with session_scope() as s:
        assert backfill_chunks(s, blobs, user_id=OWNER).updated == 1
    with session_scope() as s:
        # Nothing left to do, and nothing rewritten: a paper that has chunks is
        # not selected at all.
        assert backfill_chunks(s, blobs, user_id=OWNER).scanned == 0
    with session_scope() as s:
        assert len(chunks_for(s, s.get(Paper, "p-old"))) == 1


def test_backfill_tolerates_a_missing_blob(tmp_path) -> None:
    """A purged or never-stored file is not an error, just unchunkable."""
    blobs = BlobStore(tmp_path / "files")
    with session_scope() as s:
        s.add(_paper("p-nofile"))

    with session_scope() as s:
        report = backfill_chunks(s, blobs, user_id=OWNER)
    assert (report.scanned, report.missing_file, report.updated) == (1, 1, 0)


def test_backfill_counts_a_paper_with_no_text_layer(tmp_path) -> None:
    """A scan is scanned, not updated — and is looked at again next run."""
    blobs = BlobStore(tmp_path / "files")
    sha = _stored(blobs, tmp_path, [""])
    with session_scope() as s:
        s.add(_paper("p-scan", sha=sha))

    with session_scope() as s:
        report = backfill_chunks(s, blobs, user_id=OWNER)
    assert (report.scanned, report.no_text, report.updated) == (1, 1, 0)


def test_backfill_can_be_scoped_to_one_user(tmp_path) -> None:
    blobs = BlobStore(tmp_path / "files")
    sha = _stored(blobs, tmp_path, ["Alpha one"])
    with session_scope() as s:
        s.add(_paper("p-mine", sha=sha))
        s.add(_paper("p-theirs", user_id=OTHER, sha=sha))

    with session_scope() as s:
        assert backfill_chunks(s, blobs, user_id=OTHER).updated == 1
    with session_scope() as s:
        assert chunks_for(s, s.get(Paper, "p-mine")) == []
        assert len(chunks_for(s, s.get(Paper, "p-theirs"))) == 1


def test_backfill_over_every_user_is_reachable_but_deliberate(tmp_path) -> None:
    """``user_id=None`` is the global pass, and has to be typed to be reached."""
    blobs = BlobStore(tmp_path / "files")
    sha = _stored(blobs, tmp_path, ["Alpha one"])
    with session_scope() as s:
        s.add(_paper("p-mine", sha=sha))
        s.add(_paper("p-theirs", user_id=OTHER, sha=sha))

    with session_scope() as s:
        assert backfill_chunks(s, blobs, user_id=None).updated == 2
    with pytest.raises(TypeError), session_scope() as s:
        backfill_chunks(s, blobs)  # type: ignore[call-arg]


def test_backfill_skips_ownerless_legacy_rows(tmp_path) -> None:
    """They cannot carry chunks, so they are not counted as work either."""
    blobs = BlobStore(tmp_path / "files")
    sha = _stored(blobs, tmp_path, ["Alpha one"])
    with session_scope() as s:
        s.add(
            Paper(
                id="p-legacy",
                user_id=None,
                title="Legacy",
                orig_sha256=sha,
                orig_filename="legacy.pdf",
            )
        )

    with session_scope() as s:
        assert backfill_chunks(s, blobs, user_id=None).scanned == 0
        s.execute(delete(Paper).where(Paper.id == "p-legacy"))


def test_backfill_honours_a_limit(tmp_path) -> None:
    blobs = BlobStore(tmp_path / "files")
    sha = _stored(blobs, tmp_path, ["Alpha one"])
    with session_scope() as s:
        for n in range(3):
            s.add(_paper(f"p-{n}", sha=sha))

    with session_scope() as s:
        assert backfill_chunks(s, blobs, user_id=OWNER, limit=2).scanned == 2


def test_backfill_report_matches_its_sibling_s_shape() -> None:
    """The two backfills are read side by side; they must tally the same way."""
    from pharos.services.search import BackfillReport

    assert ChunkBackfillReport().__dataclass_fields__.keys() == (
        BackfillReport().__dataclass_fields__.keys()
    )
    assert chunks_service.backfill_chunks.__doc__


def test_backfill_is_not_reachable_from_the_http_surface() -> None:
    """Mirrors ``backfill_full_text``: a maintenance entry point, not an endpoint.

    Asserted rather than assumed because the sibling's docstring leans on it —
    ``user_id=None`` is safe *because* no request can reach the function.
    """
    import pharos.main  # noqa: F401  (imports every router)
    from fastapi.routing import APIRoute
    from pharos.main import app

    paths = [r.path for r in app.routes if isinstance(r, APIRoute)]
    assert not [p for p in paths if "chunk" in p or "backfill" in p]
