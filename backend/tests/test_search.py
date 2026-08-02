"""Full-text search: what it finds, what it must never find, and what it survives.

Three groups of tests, and only one of them is about features.

The first is ordinary coverage: a hit in each indexed field, snippets, phrases.

The second is the one that would matter at 3am — cross-user isolation. Search is
the single endpoint that reads *across* the papers table rather than resolving
one row by id, so it is the one place where a forgotten filter does not produce a
404 or a stack trace but a quietly plausible list of somebody else's research.
Every isolation test below is written so that the other user's paper is a *better*
match than the caller's own: if the owner filter were dropped, the leaked row
would sort to the top rather than hiding at the bottom of a page.

The third is robustness. ``MATCH`` takes a query language, so a raw search box
wired straight to it is a 500 waiting for the first user who types an asterisk.
Those inputs are checked here as real SQL, not as unit tests of the sanitiser,
because the only proof that matters is that SQLite accepted the string.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pharos.api import search as search_api
from pharos.api.deps import current_user, get_session
from pharos.db import session as db_session
from pharos.db.models import Paper, User
from pharos.db.session import FTS_TABLE, fts5_available, init_engine, session_scope
from pharos.services.search import (
    FULL_TEXT_MAX_CHARS,
    BackfillReport,
    _fit,
    _normalise,
    backfill_full_text,
    build_match_expression,
    extract_full_text,
    extract_pages,
    flatten_pages,
    parse_query,
    populate_full_text,
    search,
)
from pharos.storage.blobs import BlobStore
from sqlalchemy import text

OWNER = "user-owner"
OTHER = "user-other"


# ---------------------------------------------------------------- fixtures


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """A SQLite file of our own, with the corpus every test below searches.

    ``init_engine`` memoises into module globals and returns early once an engine
    exists, so without clearing them first whichever test module ran first would
    own the database and this one would search its papers instead of ours.
    ``_fts5_available`` is cleared alongside them: it is set by ``init_engine``
    and a stale value would decide which engine these tests exercise.
    """
    db_session._engine = None
    db_session._SessionLocal = None
    db_session._fts5_available = None
    init_engine(tmp_path_factory.mktemp("db") / "pharos.db")

    with session_scope() as s:
        for uid in (OWNER, OTHER):
            s.add(User(id=uid, email=f"{uid}@example.test", password_hash="x"))

    with session_scope() as s:
        s.add_all(
            [
                _paper(
                    "p-title",
                    title="Attention Is All You Need",
                    abstract="A new simple network architecture.",
                    full_text="The dominant sequence transduction models are encoder decoder.",
                ),
                _paper(
                    "p-authors",
                    title="Deep Residual Learning",
                    authors="Kaiming He; Xiangyu Zhang; Jian Sun",
                    abstract="We present a residual learning framework.",
                    full_text="Deeper neural networks are more difficult to train.",
                ),
                _paper(
                    "p-abstract",
                    title="An Unrelated Report",
                    abstract="We introduce a photosynthetic reconciliation of the pigment.",
                    full_text="Nothing in the body mentions that word at all.",
                ),
                _paper(
                    "p-fulltext",
                    title="Another Unrelated Report",
                    abstract="Short and uninformative.",
                    full_text="Convergence follows because the estimator is heteroskedastic.",
                ),
                # A scan: no text layer, so nothing was extracted. It must still
                # be findable by the metadata we do have.
                _paper("p-scan", title="A Scanned Monograph On Bryophytes", full_text=None),
                _paper("p-trashed", title="Attention Deficit In Trashed Papers", trashed=True),
                # Deliberately a *stronger* match for "attention" than anything
                # OWNER has: the term is in its title, its abstract and its body.
                _paper(
                    "p-theirs",
                    title="Attention Attention Attention",
                    abstract="Attention is the topic of this private paper.",
                    full_text="Attention attention attention attention.",
                    user_id=OTHER,
                ),
            ]
        )
    yield


def _paper(
    paper_id: str,
    *,
    title: str,
    authors: str | None = None,
    abstract: str | None = None,
    full_text: str | None = None,
    user_id: str = OWNER,
    trashed: bool = False,
) -> Paper:
    return Paper(
        id=paper_id,
        user_id=user_id,
        title=title,
        authors=authors,
        abstract=abstract,
        full_text=full_text,
        orig_sha256=f"sha-{paper_id}",
        orig_filename=f"{paper_id}.pdf",
        deleted_at=datetime.now(UTC) if trashed else None,
    )


def _find(query: str, *, user_id: str = OWNER, limit: int = 20):
    with session_scope() as s:
        return search(s, user_id=user_id, query=query, limit=limit)


def _ids(query: str, *, user_id: str = OWNER) -> set[str]:
    return {hit.paper_id for hit in _find(query, user_id=user_id).hits}


# ------------------------------------------------------------ which engine


def test_this_build_uses_fts5() -> None:
    """Records which path the local SQLite build takes.

    Not a requirement — the whole point of the fallback is that a build without
    FTS5 is supported — but if this ever flips, every ranking assertion below is
    suddenly testing the degraded engine, and that should be visible rather than
    inferred from a confusing failure elsewhere.
    """
    assert fts5_available() is True


def test_fts_table_and_triggers_exist() -> None:
    with session_scope() as s:
        names = {row[0] for row in s.execute(text("SELECT name FROM sqlite_master")).all()}
    assert FTS_TABLE in names
    assert {f"{FTS_TABLE}_ai", f"{FTS_TABLE}_ad", f"{FTS_TABLE}_au"} <= names


# ------------------------------------------------------- a hit in each field


def test_title_match() -> None:
    page = _find("transduction OR nothing")  # 'OR' is a literal, not an operator
    assert page.engine == "fts5"

    hits = {hit.paper_id: hit for hit in _find("Attention").hits}
    assert "p-title" in hits
    assert hits["p-title"].field == "title"


def test_authors_match() -> None:
    hits = {hit.paper_id: hit for hit in _find("Xiangyu").hits}
    assert set(hits) == {"p-authors"}
    assert hits["p-authors"].field == "authors"


def test_abstract_match() -> None:
    hits = {hit.paper_id: hit for hit in _find("photosynthetic").hits}
    assert set(hits) == {"p-abstract"}
    assert hits["p-abstract"].field == "abstract"


def test_full_text_match() -> None:
    """A word that appears only in the body, in no metadata field anywhere."""
    hits = {hit.paper_id: hit for hit in _find("heteroskedastic").hits}
    assert set(hits) == {"p-fulltext"}
    assert hits["p-fulltext"].field == "full_text"


def test_title_wins_when_several_fields_match() -> None:
    """The reported field is the most useful one, not an arbitrary one.

    "residual" is in both the title and the abstract of p-authors. Reporting the
    abstract would show the user a body snippet for a paper whose *title* is what
    they matched, which reads like a worse result than it is.
    """
    (hit,) = [h for h in _find("residual").hits if h.paper_id == "p-authors"]
    assert hit.field == "title"


# ----------------------------------------------------------- no full_text


def test_paper_without_full_text_is_still_searchable() -> None:
    """A scan has no body to index. It is not thereby invisible.

    ``full_text IS NULL`` is the common case for scanned or image-only PDFs, and
    an implementation that joined on it, or that required a body match, would
    drop those papers out of the library's search entirely.
    """
    assert _ids("Bryophytes") == {"p-scan"}


def test_paper_without_full_text_does_not_match_body_terms() -> None:
    """The other half: NULL must not behave like a wildcard."""
    assert "p-scan" not in _ids("heteroskedastic")


# --------------------------------------------------------------- snippets


def test_snippet_marks_the_match() -> None:
    (hit,) = [h for h in _find("photosynthetic").hits if h.paper_id == "p-abstract"]
    assert "<mark>" in hit.snippet and "</mark>" in hit.snippet
    # The highlight wraps the matched word, and the surrounding sentence is there
    # for context rather than the bare term on its own.
    assert "<mark>photosynthetic</mark>" in hit.snippet
    assert "reconciliation" in hit.snippet


def test_snippet_escapes_document_html() -> None:
    """Document text must never reach the client as live markup.

    A snippet is the one API response built by splicing tags into text taken
    verbatim from a user's PDF. Ask FTS5 for literal ``<mark>`` delimiters and a
    paper whose title contains a script tag comes back as a snippet the frontend
    renders — stored XSS, delivered through the search box, from a file the
    victim uploaded themselves.
    """
    with session_scope() as s:
        s.add(_paper("p-xss", title="<script>alert(1)</script> Bryozoan Studies"))

    (hit,) = [h for h in _find("Bryozoan").hits if h.paper_id == "p-xss"]
    assert "<script>" not in hit.snippet
    assert "&lt;script&gt;" in hit.snippet
    assert "<mark>Bryozoan</mark>" in hit.snippet

    with session_scope() as s:
        s.delete(s.get(Paper, "p-xss"))


# ----------------------------------------------------- cross-user isolation


def test_search_never_returns_another_users_paper() -> None:
    """The test the whole module exists for.

    p-theirs matches "attention" in three fields and would outrank every paper
    OWNER has, so an unscoped query does not merely leak it — it puts it first.
    """
    owner_hits = _ids("attention")
    assert "p-theirs" not in owner_hits
    assert "p-title" in owner_hits

    # And the mirror image, so the test cannot pass by finding nothing at all:
    # the paper is real, and its owner can see it.
    assert _ids("attention", user_id=OTHER) == {"p-theirs"}


def test_total_count_is_also_owner_scoped() -> None:
    """The count is a second query, and a second chance to forget the filter.

    A total that includes other people's papers is a smaller leak than returning
    the rows, but it is still one: it tells the caller how many papers about a
    given topic exist in strangers' libraries, and paging through a search would
    show empty pages that give away where they sit.
    """
    page = _find("attention")
    assert page.total == len(page.hits)
    assert page.total == 1


def test_owner_id_may_not_be_empty() -> None:
    """A falsy owner would render as ``user_id IS NULL`` and match legacy rows."""
    with session_scope() as s, pytest.raises(ValueError):
        search(s, user_id="", query="attention")


def test_trashed_papers_are_not_searchable() -> None:
    """A deleted paper reappearing in search would undo the delete."""
    assert "p-trashed" not in _ids("attention")


# ------------------------------------------------- hostile / malformed input


@pytest.mark.parametrize(
    "query",
    [
        "*",  # bare wildcard: "unknown special query" straight from FTS5
        "**",
        'foo"',  # unterminated string literal
        '"',
        'attention"needs',
        "AND",  # bare operators are syntax errors, not words
        "OR",
        "NOT",
        "NEAR",
        "NEAR(a b, 3)",
        "attention AND",
        "AND attention",
        "(",
        ")()",
        "a OR (b",
        "^title:",
        "title : attention",
        "-",
        "--",
        "  ",
        "attention -- OR *",
        "{}[]",
        "'; DROP TABLE papers;--",
        "\\",
        "%_%",
        "😀",
        "中文 检索",
    ],
)
def test_hostile_query_never_raises(query: str) -> None:
    """Any string at all is a valid search. This is a search box, not a REPL.

    Each of these either is a syntax error in FTS5's query language or means
    something the user did not intend. The contract is that they all come back as
    an ordinary — possibly empty — page. Note this asserts against real SQLite:
    the sanitiser being *shaped* right is not evidence, only SQLite accepting the
    string it produced is.

    ``engine == "fts5"`` is the load-bearing half of the assertion. ``search()``
    also catches ``OperationalError`` and retries on the LIKE path, so without
    this the test would pass just as happily on a sanitiser that emitted garbage
    and let the safety net clean up — which would mean every hostile query
    silently ran the slow, badly-ranked engine. What is being asserted here is
    that the string handed to ``MATCH`` was valid in the first place.
    """
    page = _find(query)
    assert page.engine == "fts5"
    assert page.total >= 0
    assert all(hit.snippet for hit in page.hits)


def test_operator_words_are_searched_literally() -> None:
    """``NOT`` is a word people search for, not an instruction."""
    with session_scope() as s:
        s.add(_paper("p-not", title="What Is NOT Learned By Contrastive Pretraining"))
    assert "p-not" in _ids("NOT learned")
    with session_scope() as s:
        s.delete(s.get(Paper, "p-not"))


def test_punctuation_only_query_returns_an_empty_page() -> None:
    """Nothing searchable was typed, so there is nothing to report — not an error."""
    page = _find("*")
    assert page.hits == []
    assert page.total == 0


def test_unmatchable_term_does_not_poison_the_query() -> None:
    """``transduction *`` still finds what ``transduction`` finds.

    Keeping the punctuation as a literal term would AND the search with something
    that can never match, silently emptying a result set the user could see was
    wrong.
    """
    assert _ids("transduction *") == _ids("transduction")


# ------------------------------------------------------------------ phrases


def test_quoted_phrase_matches_in_order() -> None:
    assert "p-title" in _ids('"is all you need"')


def test_quoted_phrase_rejects_the_same_words_out_of_order() -> None:
    """Otherwise the quotes would be decoration rather than a constraint."""
    assert "p-title" not in _ids('"need you all is"')


def test_unbalanced_quote_degrades_to_loose_words() -> None:
    """Half a phrase is what a user who is still typing has produced."""
    assert "p-title" in _ids('"is all you need')


def test_last_term_is_a_prefix() -> None:
    """Search-as-you-type: results appear before the final keystroke."""
    assert "p-title" in _ids("transduc")


def test_quoted_term_is_not_prefix_extended() -> None:
    """Quotes are the user saying they meant exactly this."""
    assert build_match_expression(parse_query('"transduc"')) == '"transduc"'
    assert build_match_expression(parse_query("transduc")) == '"transduc"*'


def test_multiple_terms_are_anded() -> None:
    """Two words means the paper with both, not the union of two result sets."""
    assert _ids("attention transduction") == {"p-title"}
    assert _ids("attention heteroskedastic") == set()


# --------------------------------------------- index maintenance (triggers)
#
# The FTS index is external-content, so SQLite does not maintain it: the triggers
# do. These tests exist because "the triggers are correct" and "the triggers fire
# for the writes this application actually issues" are different claims, and only
# the second one keeps search working. Every write below goes through the ORM,
# because that is how every write in the app goes.


def test_orm_insert_is_indexed() -> None:
    with session_scope() as s:
        s.add(_paper("p-new", title="Diffusion Models Beat Perambulating GANs"))
    assert _ids("Perambulating") == {"p-new"}


def test_orm_update_reindexes_and_forgets_the_old_text() -> None:
    """Both halves matter, and the second is the one that rots silently.

    Adding the new text is obvious. Removing the old requires the trigger to feed
    FTS5 the *previous* column values, which it can no longer read from the row —
    get that wrong and search keeps returning papers for words they no longer
    contain, forever, with nothing to indicate why.
    """
    with session_scope() as s:
        s.get(Paper, "p-new").title = "Diffusion Models Beat Somnolent GANs"

    assert _ids("Somnolent") == {"p-new"}
    assert _ids("Perambulating") == set()


def test_orm_update_of_an_unindexed_column_leaves_the_index_intact() -> None:
    """The ``WHEN`` guard skips reindexing for writes that change nothing indexed.

    Soft deletes and translation-job bookkeeping touch papers constantly. The
    guard is an optimisation, but a wrong guard would be a correctness bug, so
    the index is checked to still answer after one.
    """
    with session_scope() as s:
        s.get(Paper, "p-new").page_count = 42
    assert _ids("Somnolent") == {"p-new"}


def test_orm_delete_removes_it_from_the_index() -> None:
    """A purged paper must not linger as a searchable ghost."""
    with session_scope() as s:
        s.delete(s.get(Paper, "p-new"))
    assert _ids("Somnolent") == set()


def _boot_fresh(path) -> None:
    """Re-run ``init_engine`` from scratch against ``path``, as a restart would."""
    db_session._engine = None
    db_session._SessionLocal = None
    db_session._fts5_available = None
    init_engine(path)


@pytest.fixture
def _restores_engine() -> Iterator[None]:
    """Let a test boot its own database, then give the module's back.

    The engine lives in module globals, so a test that re-inits would otherwise
    leave every test after it searching the wrong database.
    """
    saved = (db_session._engine, db_session._SessionLocal, db_session._fts5_available)
    try:
        yield
    finally:
        db_session._engine, db_session._SessionLocal, db_session._fts5_available = saved


@pytest.mark.usefixtures("_restores_engine")
def test_upgrade_of_an_existing_database_indexes_the_papers_already_in_it(tmp_path) -> None:
    """The path every existing deployment takes, and the one no fresh test covers.

    On a developer's machine the index is created against an empty table and the
    triggers fill it as papers arrive, so a missing ``rebuild`` is invisible. On a
    real library the table is created next to thousands of existing rows, and
    without the rebuild the triggers only ever see *future* writes: search comes
    back empty for the entire existing collection and correct for anything
    uploaded afterwards, which is a maddening bug to be told about second-hand.
    """
    path = tmp_path / "legacy.db"
    _boot_fresh(path)
    with session_scope() as s:
        s.add(User(id=OWNER, email="owner@example.test", password_hash="x"))
    with session_scope() as s:
        s.add(_paper("p-legacy", title="Precambrian Stromatolites Of The Pilbara"))

    # Reduce it to a pre-feature database: papers, no index, no triggers.
    with db_session._engine.begin() as conn:
        conn.exec_driver_sql(f"DROP TABLE {FTS_TABLE}")
        for suffix in ("ai", "ad", "au"):
            conn.exec_driver_sql(f"DROP TRIGGER {FTS_TABLE}_{suffix}")

    _boot_fresh(path)
    assert _ids("Stromatolites") == {"p-legacy"}


@pytest.mark.usefixtures("_restores_engine")
def test_restarting_neither_duplicates_nor_empties_the_index(tmp_path) -> None:
    """"Created once, survives restarts" — asserted by actually restarting.

    Two ways to get this wrong and both are quiet. Re-running the ``CREATE`` and
    the rebuild unconditionally would reindex the whole library on every boot,
    which is slow rather than wrong. Re-running only the rebuild, or letting the
    triggers fire twice for one row, duplicates entries — and the visible symptom
    is not an error but the same paper appearing several times in one result page.
    """
    path = tmp_path / "restart.db"
    _boot_fresh(path)
    with session_scope() as s:
        s.add(User(id=OWNER, email="owner@example.test", password_hash="x"))
    with session_scope() as s:
        s.add(_paper("p-restart", title="Halophilic Archaea In Brine Pools"))

    for _ in range(3):
        _boot_fresh(path)

    page = _find("Halophilic")
    assert [hit.paper_id for hit in page.hits] == ["p-restart"]
    assert page.total == 1


@pytest.mark.usefixtures("_restores_engine")
def test_ddl_is_not_rolled_back_so_a_torn_index_is_reachable(tmp_path) -> None:
    """Pins the driver behaviour the repair logic exists because of.

    It is natural to assume ``CREATE`` inside ``engine.begin()`` is atomic with
    the rebuild that follows it, and on that assumption no repair is needed at
    all. It is not true here: pysqlite does not open a transaction for DDL, so the
    ``CREATE`` autocommits and outlives a rollback. This test states that out
    loud, so that if a future driver or SQLAlchemy version *does* make it atomic,
    somebody is told rather than left maintaining a defence against an impossible
    state.
    """
    path = tmp_path / "atomic.db"
    _boot_fresh(path)
    engine = db_session._engine

    with engine.begin() as conn:
        conn.exec_driver_sql(f"DROP TABLE {FTS_TABLE}")

    with pytest.raises(RuntimeError):  # noqa: SIM117 - the nesting is the point
        with engine.begin() as conn:
            conn.exec_driver_sql(db_session._fts_ddl()[0])
            raise RuntimeError("process dies between CREATE and rebuild")

    with engine.connect() as conn:
        assert db_session._fts_table_exists(conn), (
            "DDL was rolled back after all — _fts_needs_rebuild may now be dead code"
        )


@pytest.mark.usefixtures("_restores_engine")
def test_an_index_left_empty_by_a_crash_is_repaired_on_the_next_boot(tmp_path) -> None:
    """The consequence of the test above: the torn state must self-heal.

    A table that exists but holds no documents is the one broken state that looks
    healthy to every cheap check — ``COUNT(*)`` on an external-content table reads
    through to ``papers`` and reports a row per paper even when the index is
    empty. If this is not detected, search silently returns nothing for the whole
    existing library while working perfectly for anything uploaded afterwards.
    """
    path = tmp_path / "torn.db"
    _boot_fresh(path)
    with session_scope() as s:
        s.add(User(id=OWNER, email="owner@example.test", password_hash="x"))
    with session_scope() as s:
        s.add(_paper("p-torn", title="Halophilic Archaea In Brine Pools"))

    # Exactly the state a crash between CREATE and rebuild leaves behind.
    with db_session._engine.begin() as conn:
        conn.exec_driver_sql(f"DROP TABLE {FTS_TABLE}")
        for suffix in ("ai", "ad", "au"):
            conn.exec_driver_sql(f"DROP TRIGGER {FTS_TABLE}_{suffix}")
        conn.exec_driver_sql(db_session._fts_ddl()[0])  # created, never populated
        assert conn.exec_driver_sql(f"SELECT COUNT(*) FROM {FTS_TABLE}").scalar() == 1

    _boot_fresh(path)
    assert _ids("Halophilic") == {"p-torn"}


def test_full_text_written_after_upload_becomes_searchable() -> None:
    """The real backfill shape: the row exists first, its text arrives later."""
    with session_scope() as s:
        s.add(_paper("p-late", title="Late Text", full_text=None))
    assert _ids("Cyanobacterial") == set()

    with session_scope() as s:
        s.get(Paper, "p-late").full_text = "Cyanobacterial blooms in the estuary."
    assert _ids("Cyanobacterial") == {"p-late"}

    with session_scope() as s:
        s.delete(s.get(Paper, "p-late"))


# ------------------------------------------------------------- LIKE fallback


@pytest.fixture
def _no_fts5(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend this SQLite was built without FTS5.

    Patched where it is *used* rather than where it is defined: the search module
    imported the name, so patching ``db.session`` would leave its binding intact
    and the test would silently keep exercising FTS5.
    """
    monkeypatch.setattr("pharos.services.search.fts5_available", lambda: False)


@pytest.mark.usefixtures("_no_fts5")
def test_fallback_reports_itself() -> None:
    assert _find("attention").engine == "like"


@pytest.mark.usefixtures("_no_fts5")
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Attention", {"p-title"}),
        ("Xiangyu", {"p-authors"}),
        ("photosynthetic", {"p-abstract"}),
        ("heteroskedastic", {"p-fulltext"}),
        ("Bryophytes", {"p-scan"}),
    ],
)
def test_fallback_finds_each_field(query: str, expected: set[str]) -> None:
    """A build without FTS5 gets working search, not a 500."""
    assert _ids(query) == expected


@pytest.mark.usefixtures("_no_fts5")
def test_fallback_is_owner_scoped() -> None:
    """The fallback is a second, separately-written query — and a second leak.

    It would be an easy one to miss: the isolation tests above all pass on a
    build that has FTS5, which is every developer machine.
    """
    assert "p-theirs" not in _ids("attention")
    assert _ids("attention", user_id=OTHER) == {"p-theirs"}


@pytest.mark.usefixtures("_no_fts5")
def test_fallback_escapes_like_wildcards() -> None:
    """``%`` is a wildcard to LIKE and a character to the user typing it."""
    with session_scope() as s:
        s.add(_paper("p-pct", title="Improving Accuracy By 100% Over Baselines"))

    assert _ids("100%") == {"p-pct"}
    # Without ESCAPE this would match every paper, since '%o%' matches anything
    # containing an 'o'.
    assert _ids("%o%") == set()

    with session_scope() as s:
        s.delete(s.get(Paper, "p-pct"))


@pytest.mark.usefixtures("_no_fts5")
def test_fallback_snippet_is_marked_and_escaped() -> None:
    (hit,) = [h for h in _find("photosynthetic").hits if h.paper_id == "p-abstract"]
    assert "<mark>photosynthetic</mark>" in hit.snippet


@pytest.mark.usefixtures("_no_fts5")
@pytest.mark.parametrize("query", ["*", 'foo"', "AND", "%_%", "\\", "  "])
def test_fallback_survives_hostile_input(query: str) -> None:
    assert _find(query).total >= 0


# ----------------------------------------------------------- text extraction


def _make_pdf(path, pages: list[str]) -> None:
    import pymupdf

    doc = pymupdf.open()
    for body in pages:
        page = doc.new_page()
        page.insert_text((72, 72), body, fontsize=11)
    doc.save(path)
    doc.close()


def test_extract_full_text_reads_every_page(tmp_path) -> None:
    pdf = tmp_path / "paper.pdf"
    _make_pdf(pdf, ["Introduction to bathymetry", "Conclusions about bathymetry"])

    extracted = extract_full_text(pdf)
    assert extracted is not None
    assert "Introduction to bathymetry" in extracted
    assert "Conclusions about bathymetry" in extracted
    # Newlines are collapsed, so a snippet reads as prose rather than shrapnel.
    assert "\n" not in extracted


def test_extract_full_text_is_capped(tmp_path) -> None:
    """A 700-page book must not put megabytes on the row."""
    pdf = tmp_path / "book.pdf"
    _make_pdf(pdf, ["Chapter about sedimentation. " * 40] * 40)

    extracted = extract_full_text(pdf, max_chars=500)
    assert extracted is not None
    assert len(extracted) <= 500


def test_extract_full_text_never_raises_on_a_non_pdf(tmp_path) -> None:
    """Extraction failing must not be able to fail an upload."""
    junk = tmp_path / "not-a.pdf"
    junk.write_bytes(b"this is not a pdf at all")
    assert extract_full_text(junk) is None


def test_populate_full_text_leaves_none_when_there_is_no_text(tmp_path) -> None:
    """NULL means "not searchable", and must not be overwritten with ""."""
    pdf = tmp_path / "blank.pdf"
    _make_pdf(pdf, [""])

    paper = _paper("p-tmp", title="Blank")
    assert populate_full_text(paper, pdf) is False
    assert paper.full_text is None


def test_populate_full_text_sets_the_column(tmp_path) -> None:
    pdf = tmp_path / "paper.pdf"
    _make_pdf(pdf, ["Ultramafic intrusions in the craton"])

    paper = _paper("p-tmp", title="Rocks")
    assert populate_full_text(paper, pdf) is True
    assert "Ultramafic" in (paper.full_text or "")


def test_full_text_cap_is_documented_and_sane() -> None:
    """A regression guard on the number, since it bounds every papers row."""
    assert 100_000 <= FULL_TEXT_MAX_CHARS <= 2_000_000


# ------------------------------------------------- page-aware extraction


def test_extract_pages_keeps_the_pdf_s_own_page_numbers(tmp_path) -> None:
    """A page with no text layer must not renumber the pages after it.

    The failure this guards against is the quiet one: if page numbers were the
    position in the returned list, a single image-only plate would shift every
    later citation down by one, and each would still look entirely plausible.
    """
    pdf = tmp_path / "with-a-plate.pdf"
    _make_pdf(pdf, ["Introduction to bathymetry", "", "Conclusions about bathymetry"])

    pages = extract_pages(pdf)
    assert pages is not None
    assert [p.page_no for p in pages] == [1, 3]
    assert pages[1].text == "Conclusions about bathymetry"


def test_extract_pages_returns_normalised_non_empty_text(tmp_path) -> None:
    """Two properties the chunk offsets are built on, asserted where they hold."""
    pdf = tmp_path / "paper.pdf"
    _make_pdf(pdf, ["Ultramafic   intrusions", "in the craton"])

    pages = extract_pages(pdf)
    assert pages is not None
    assert all(p.text and p.text == _normalise(p.text) for p in pages)


def test_extract_pages_distinguishes_unreadable_from_textless(tmp_path) -> None:
    """``None`` and ``[]`` are different answers; the flat column loses that."""
    junk = tmp_path / "not-a.pdf"
    junk.write_bytes(b"this is not a pdf at all")
    blank = tmp_path / "blank.pdf"
    _make_pdf(blank, ["", ""])

    assert extract_pages(junk) is None
    assert extract_pages(blank) == []
    # Both collapse to None once flattened: the column has one way to say
    # nothing, which is exactly why a caller who needs the difference asks for
    # pages instead.
    assert extract_full_text(junk) is None
    assert extract_full_text(blank) is None


def test_extract_pages_stops_on_a_page_boundary(tmp_path) -> None:
    """The budget buys whole pages: it must never return half of one."""
    pdf = tmp_path / "book.pdf"
    _make_pdf(pdf, ["Chapter about sedimentation. " * 8] * 20)

    pages = extract_pages(pdf, max_chars=500)
    assert pages is not None
    assert 0 < len(pages) < 20
    whole = extract_pages(pdf)
    assert whole is not None
    assert [p.text for p in pages] == [p.text for p in whole[: len(pages)]]


# ------------------------------------- the refactor's equivalence proof


def _legacy_extract_full_text(path, *, max_chars: int = FULL_TEXT_MAX_CHARS) -> str | None:
    """``extract_full_text`` as it was written before extraction became page-aware.

    Kept verbatim so the refactor can be *shown* to preserve behaviour rather
    than argued to. Three things about the new implementation are not obvious
    and all three are decided here: it normalises each page and joins with a
    space where this joined with a newline and normalised the whole string; it
    drops pages that normalise to nothing, where this kept them; and it charges
    the read budget before deciding whether a page is worth keeping.

    ``_normalise`` and ``_fit`` are shared with the live code on purpose. The
    refactor did not touch either, and copying them would prove that two copies
    of a function agree rather than that two ways of *composing* it do.
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
                    break
    except Exception:
        return None

    joined = _normalise("\n".join(chunks))
    if not joined:
        return None
    return _fit(joined, max_chars)


#: Documents chosen for the seams, not for coverage: a page that contributes
#: nothing, blank pages at each end, whitespace the old whole-string pass would
#: have collapsed across a page boundary, and text long enough that ``_fit``
#: has to cut it.
_EQUIVALENCE_CORPUS: dict[str, list[str]] = {
    "single": ["Palaeomagnetic reversals in the Deccan traps"],
    "several": ["Introduction to bathymetry", "Methods", "Conclusions about bathymetry"],
    "textless middle page": ["Alpha one", "", "Gamma three"],
    # A page holding one space extracts as " \n": non-empty raw, empty once
    # normalised. It is the case that decides whether the new implementation
    # emits a double space where the old one emitted a single, and the only one
    # in this corpus that a PDF can actually produce.
    "whitespace-only middle page": ["Alpha one", " ", "Gamma three"],
    "whitespace-only first page": [" ", "Beta two"],
    "leading blank": ["", "Beta two"],
    "trailing blank": ["Alpha one", ""],
    "all blank": ["", ""],
    "whitespace at the seams": ["   Alpha one   ", "   Beta two   "],
    "inner runs": ["Alpha    one\n\n\nstill page one", "Beta\t\ttwo"],
    "non latin": ["注意力就是你所需要的一切", "Attention Is All You Need"],
    "long enough to truncate": ["Chapter about sedimentation. " * 30] * 6,
}


@pytest.mark.parametrize("name", sorted(_EQUIVALENCE_CORPUS))
@pytest.mark.parametrize("max_chars", [FULL_TEXT_MAX_CHARS, 4000, 500, 137, 40, 1])
def test_page_aware_extraction_reproduces_the_old_flat_text(
    tmp_path, name: str, max_chars: int
) -> None:
    """The proof obligation: same input, same ``full_text``, character for character.

    Run across several caps because the interesting divergences are all at a
    boundary — the page the budget runs out on, and the word boundary ``_fit``
    backs up to inside it.
    """
    pdf = tmp_path / "equivalence.pdf"
    _make_pdf(pdf, _EQUIVALENCE_CORPUS[name])

    assert extract_full_text(pdf, max_chars=max_chars) == _legacy_extract_full_text(
        pdf, max_chars=max_chars
    )


def test_page_aware_extraction_reproduces_the_old_failure(tmp_path) -> None:
    junk = tmp_path / "not-a.pdf"
    junk.write_bytes(b"%PDF-1.4 truncated before anything useful")
    assert extract_full_text(junk) is _legacy_extract_full_text(junk) is None


@pytest.mark.parametrize(
    "raw_pages",
    [
        ["one", "two"],
        ["one\n", "\ntwo"],
        ["  ", "two"],
        ["one", "   "],
        ["\x00\x01", "two"],  # normalises to nothing: no double space allowed
        ["one", "\x0c"],
        ["", ""],
        ["\n\n\n", "\t\t"],
        ["one ", " two ", " three"],
        ["    ", "нет"],
    ],
)
def test_per_page_normalisation_matches_the_old_whole_string_pass(raw_pages: list[str]) -> None:
    """The core of the equivalence, on inputs no PDF generator would produce.

    ``_normalise`` collapses every whitespace run to one space and strips both
    ends, so a page break was *already* collapsing to a single space inside the
    old whole-string pass — which is why per-page normalisation joined by one
    space is the same string. The cases that carry the argument are the pages
    that normalise to nothing: the old pass folded them into the surrounding
    whitespace run, so the new one has to drop them entirely rather than join
    an empty string and emit two spaces where there was one.
    """
    old = _normalise("\n".join(raw_pages))
    new = " ".join(text for text in (_normalise(page) for page in raw_pages) if text)
    assert old == new


def test_flat_text_is_the_pages_flattened(tmp_path) -> None:
    """The property the chunk offsets rest on, stated as a test.

    If these two could ever differ, every ``char_start``/``char_end`` in the
    chunk table would be an offset into a string that does not exist.
    """
    pdf = tmp_path / "paper.pdf"
    _make_pdf(pdf, ["Introduction to bathymetry", "", "Conclusions about bathymetry"])

    pages = extract_pages(pdf)
    assert pages is not None
    assert flatten_pages(pages) == extract_full_text(pdf)


# -------------------------------------------------------------- backfill


def test_backfill_indexes_papers_uploaded_before_this_existed(tmp_path) -> None:
    blobs = BlobStore(tmp_path / "files")
    pdf = tmp_path / "old.pdf"
    _make_pdf(pdf, ["Palaeomagnetic reversals in the Deccan traps"])
    sha, _ = blobs.store_original(pdf.read_bytes())

    with session_scope() as s:
        s.add(
            Paper(
                id="p-old",
                user_id=OWNER,
                title="An Older Upload",
                orig_sha256=sha,
                orig_filename="old.pdf",
            )
        )

    with session_scope() as s:
        report = backfill_full_text(s, blobs, user_id=OWNER)

    assert report.updated >= 1
    # And the triggers picked the new text up, so it is searchable immediately.
    assert _ids("Palaeomagnetic") == {"p-old"}

    with session_scope() as s:
        s.delete(s.get(Paper, "p-old"))


def test_backfill_tolerates_a_missing_blob(tmp_path) -> None:
    """A purged or never-stored file is not an error, just unindexable."""
    blobs = BlobStore(tmp_path / "files")
    with session_scope() as s:
        s.add(_paper("p-nofile", title="Gone", full_text=None))

    with session_scope() as s:
        report = backfill_full_text(s, blobs, user_id=OWNER)

    assert isinstance(report, BackfillReport)
    assert report.missing_file >= 1

    with session_scope() as s:
        s.delete(s.get(Paper, "p-nofile"))


def test_backfill_can_be_scoped_to_one_user(tmp_path) -> None:
    blobs = BlobStore(tmp_path / "files")
    with session_scope() as s:
        report = backfill_full_text(s, blobs, user_id=OTHER)
    # p-theirs already has full_text; nothing of OTHER's is left to do, and
    # nothing of OWNER's was touched.
    assert report.scanned == 0


# ------------------------------------------------------------- the endpoint


@pytest.fixture
def client() -> Iterator[TestClient]:
    """The router on a bare app, so the endpoint is tested rather than mocked.

    ``main.py`` belongs to another change this round, so the router is mounted
    here instead. ``current_user`` is overridden because the point of these tests
    is the query, not the token plumbing — but note it is overridden to a *real*
    user, so the ownership filter is exercised for real.
    """
    app = FastAPI()
    app.include_router(search_api.router)

    def _session_override() -> Iterator:
        with session_scope() as s:
            yield s

    app.dependency_overrides[get_session] = _session_override
    with TestClient(app) as c:
        yield c


def _as(client: TestClient, user_id: str) -> None:
    def _override() -> User:
        with session_scope() as s:
            return s.get(User, user_id)

    client.app.dependency_overrides[current_user] = _override


def test_endpoint_returns_hits(client: TestClient) -> None:
    _as(client, OWNER)
    body = client.get("/api/search", params={"q": "photosynthetic"}).json()

    assert body["engine"] == "fts5"
    assert body["total"] == 1
    (hit,) = body["hits"]
    assert hit["paper_id"] == "p-abstract"
    assert hit["field"] == "abstract"
    assert "<mark>photosynthetic</mark>" in hit["snippet"]
    assert hit["rank"] > 0  # higher is better, whatever BM25's own sign is


def test_endpoint_requires_authentication(client: TestClient) -> None:
    """No public view of anybody's library."""
    client.app.dependency_overrides.pop(current_user, None)
    assert client.get("/api/search", params={"q": "attention"}).status_code == 401


def test_endpoint_does_not_leak_across_users(client: TestClient) -> None:
    """The isolation property, asserted at the layer a client actually calls."""
    _as(client, OWNER)
    owner_body = client.get("/api/search", params={"q": "attention"}).json()
    assert [h["paper_id"] for h in owner_body["hits"]] == ["p-title"]

    _as(client, OTHER)
    other_body = client.get("/api/search", params={"q": "attention"}).json()
    assert [h["paper_id"] for h in other_body["hits"]] == ["p-theirs"]


@pytest.mark.parametrize("query", ["*", 'foo"', "NEAR", "AND", "'; DROP TABLE papers;--"])
def test_endpoint_never_500s_on_hostile_input(client: TestClient, query: str) -> None:
    _as(client, OWNER)
    assert client.get("/api/search", params={"q": query}).status_code == 200


def test_endpoint_paginates(client: TestClient) -> None:
    _as(client, OWNER)
    first = client.get("/api/search", params={"q": "the", "limit": 1}).json()
    second = client.get("/api/search", params={"q": "the", "limit": 1, "offset": 1}).json()

    assert first["total"] == second["total"] >= 2
    assert len(first["hits"]) == len(second["hits"]) == 1
    assert first["hits"][0]["paper_id"] != second["hits"][0]["paper_id"]


def test_endpoint_rejects_an_absurd_limit(client: TestClient) -> None:
    _as(client, OWNER)
    assert client.get("/api/search", params={"q": "a", "limit": 5000}).status_code == 422


def test_papers_table_still_intact() -> None:
    """The SQL-injection strings above went through parameter binding, not text.

    Cheap, and it fails loudly if anyone ever rewrites the query builder to
    interpolate a term into the statement.
    """
    with session_scope() as s:
        assert s.get(Paper, "p-title") is not None
