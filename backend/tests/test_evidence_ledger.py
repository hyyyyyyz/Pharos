"""Evidence Ledger: placement honesty, provenance symmetry, and owner isolation.

The tests worth reading are the two edges of the quote matcher. It has to be
loose enough that a passage copied out of a PDF viewer — hard line breaks,
hyphenated across a line, curly quotes — still finds its page, and tight enough
that a paraphrase of that same passage finds nothing at all. Everything between
those two is a fabricated citation waiting to be rendered as a footnote.
"""

from __future__ import annotations

import pytest
from pharos.db.models import Evidence, Paper, PaperChunk, ResearchProject, User
from pharos.db.session import init_engine, session_scope
from pharos.services import evidence
from sqlalchemy import delete, select

OWNER = "evidence-owner"
OTHER = "evidence-other"
USERS = (OWNER, OTHER)

PAGE_ONE = (
    "We introduce Pharos, an evidence-grounded reading system. "
    "The transformer baseline achieves 92.1% accuracy on the held-out split, "
    "and we evaluate the transformer on ImageNet as well."
)
PAGE_TWO = (
    "Related work on retrieval augmentation is extensive. "
    "The transformer baseline achieves 92.1% accuracy on the held-out split, "
    "as reported by prior authors."
)


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory: pytest.TempPathFactory) -> None:
    init_engine(tmp_path_factory.mktemp("evidence") / "pharos.db")
    with session_scope() as session:
        for user_id in USERS:
            if session.get(User, user_id) is None:
                session.add(User(id=user_id, email=f"{user_id}@example.test", password_hash="x"))


@pytest.fixture(autouse=True)
def _clean() -> None:
    with session_scope() as session:
        session.execute(delete(Evidence).where(Evidence.user_id.in_(USERS)))
        session.execute(delete(PaperChunk).where(PaperChunk.user_id.in_(USERS)))
        session.execute(delete(ResearchProject).where(ResearchProject.user_id.in_(USERS)))
        session.execute(delete(Paper).where(Paper.user_id.in_(USERS)))


def _paper(
    user_id: str = OWNER,
    *,
    pages: tuple[str, ...] = (PAGE_ONE, PAGE_TWO),
    abstract: str | None = "We introduce Pharos, an evidence-grounded reading system.",
    sha: str = "sha-full",
) -> str:
    """A library paper plus its page chunks, built directly.

    Chunk *population* belongs to the extraction pass, which is a separate piece
    of work; the ledger only consumes the rows. Constructing them here keeps this
    suite independent of whatever shape that pass eventually takes, and lets the
    "no chunks at all" cases be expressed by simply passing none.
    """
    with session_scope() as session:
        paper = Paper(
            user_id=user_id,
            title="Evidence-Grounded Reading",
            orig_sha256=sha,
            orig_filename="paper.pdf",
            abstract=abstract,
        )
        session.add(paper)
        session.flush()
        for index, text in enumerate(pages, start=1):
            session.add(
                PaperChunk(
                    user_id=user_id,
                    paper_id=paper.id,
                    page_no=index,
                    ordinal=0,
                    text=text,
                )
            )
        return paper.id


def _project(user_id: str = OWNER) -> str:
    with session_scope() as session:
        row = ResearchProject(user_id=user_id, name="Ledger")
        session.add(row)
        session.flush()
        return row.id


# ------------------------------------------------------------------ placement


def test_a_verbatim_quote_resolves_to_the_page_that_contains_it() -> None:
    paper_id = _paper()
    with session_scope() as session:
        row = evidence.create_evidence(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            kind="quote",
            text="we evaluate the transformer on ImageNet",
        )
        assert (row.locator, row.page_no) == ("page", 1)
        assert row.chunk_id is not None


def test_a_quote_pasted_with_hard_line_breaks_still_finds_its_page() -> None:
    """The requirement that makes the matcher usable at all.

    A PDF viewer's clipboard breaks a passage at the rendered line, so the text
    the user pastes has newlines and column padding the chunk does not.
    """
    paper_id = _paper()
    with session_scope() as session:
        row = evidence.create_evidence(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            kind="quote",
            text="The transformer baseline\n   achieves 92.1%\naccuracy on the held-out\nsplit",
        )
        assert (row.locator, row.page_no) == ("page", 1)


def test_a_quote_hyphenated_across_a_line_break_still_finds_its_page() -> None:
    paper_id = _paper()
    with session_scope() as session:
        placement = evidence.resolve_quote(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            quote="we evaluate the trans-\nformer on ImageNet",
        )
        assert (placement.outcome, placement.page_no) == ("page", 1)


def test_typographic_quotes_and_ligatures_do_not_defeat_the_match() -> None:
    with session_scope() as session:
        paper = Paper(
            user_id=OWNER,
            title="Typography",
            orig_sha256="sha-typo",
            orig_filename="t.pdf",
        )
        session.add(paper)
        session.flush()
        session.add(
            PaperChunk(
                user_id=OWNER,
                paper_id=paper.id,
                page_no=3,
                text='The "efficient" classifier is significant.',
            )
        )
        paper_id = paper.id
    with session_scope() as session:
        placement = evidence.resolve_quote(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            quote="The “efficient” classiﬁer is signiﬁcant.",
        )
        assert (placement.outcome, placement.page_no) == ("page", 3)


def test_a_paraphrase_is_not_placed_and_never_acquires_a_page() -> None:
    """The failure this whole subsystem exists to prevent.

    Every word of the paraphrase appears on page 1; only the wording differs. A
    matcher fuzzy enough to accept it would mint a citation to a sentence the
    paper never wrote, and the page number would make it survive review.
    """
    paper_id = _paper()
    with session_scope() as session:
        placement = evidence.resolve_quote(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            quote="The transformer baseline reaches 92.1 percent accuracy on the held-out split",
        )
        assert placement.outcome == evidence.NOT_IN_PAPER
        assert placement.page_no is None


def test_a_quote_absent_from_an_extracted_paper_is_refused_not_downgraded() -> None:
    paper_id = _paper()
    with session_scope() as session, pytest.raises(evidence.QuoteNotInPaper):
        evidence.create_evidence(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            kind="quote",
            text="Pharos solves alignment once and for all.",
        )
    with session_scope() as session:
        assert session.scalars(select(Evidence).where(Evidence.user_id == OWNER)).all() == []


def test_a_repeated_quote_takes_the_earliest_page_deterministically() -> None:
    paper_id = _paper()
    quote = "The transformer baseline achieves 92.1% accuracy on the held-out split"
    with session_scope() as session:
        first = evidence.resolve_quote(session, user_id=OWNER, paper_id=paper_id, quote=quote)
        second = evidence.resolve_quote(session, user_id=OWNER, paper_id=paper_id, quote=quote)
    assert first.page_no == 1
    assert second.page_no == first.page_no


def test_a_verified_page_hint_selects_the_occurrence_the_reader_opened() -> None:
    paper_id = _paper()
    quote = "The transformer baseline achieves 92.1% accuracy on the held-out split"
    rects = [{"x": 10, "y": 20, "w": 100, "h": 12}]
    with session_scope() as session:
        placement = evidence.resolve_quote(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            quote=quote,
            page_hint=2,
        )
        assert placement.page_no == 2

        row = evidence.create_evidence(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            kind="quote",
            text=quote,
            page_hint=2,
            rects=rects,
        )
        assert (row.locator, row.page_no) == ("page", 2)
        assert evidence.annotate.load_rects(row.rects) == [evidence.annotate.Rect(**rects[0])]


def test_an_unverified_page_hint_saves_quote_but_drops_quote_geometry() -> None:
    paper_id = _paper()
    quote = "we evaluate the transformer on ImageNet"
    with session_scope() as session:
        # A hint alone is only a preference: because page 2 does not contain the
        # quote, resolution falls back to the independently proven page 1.
        placement = evidence.resolve_quote(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            quote=quote,
            page_hint=2,
        )
        assert placement.page_no == 1

    with session_scope() as session:
        row = evidence.create_evidence(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            kind="quote",
            text=quote,
            page_hint=2,
            rects=[{"x": 10, "y": 20, "w": 100, "h": 12}],
        )
        assert (row.locator, row.page_no, row.rects) == ("page", 1, None)

    with session_scope() as session:
        # A missing hint is also backwards-compatible quote-only evidence.
        row = evidence.create_evidence(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            kind="quote",
            text=quote,
            rects=[{"x": 10, "y": 20, "w": 100, "h": 12}],
        )
        assert (row.locator, row.page_no, row.rects) == ("page", 1, None)


def test_quote_geometry_requires_valid_geometry_when_the_hint_is_verified() -> None:
    paper_id = _paper()
    with session_scope() as session, pytest.raises(evidence.Invalid, match="greater than zero"):
        evidence.create_evidence(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            kind="quote",
            text="we evaluate the transformer on ImageNet",
            page_hint=1,
            rects=[{"x": 10, "y": 20, "w": 0, "h": 12}],
        )


def test_notes_cannot_use_a_quote_page_hint() -> None:
    paper_id = _paper()
    with session_scope() as session, pytest.raises(evidence.Invalid, match="only valid for quote"):
        evidence.create_evidence(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            kind="note",
            text="My reading.",
            page_hint=1,
        )


def test_a_paper_with_no_chunks_but_an_abstract_is_abstract_only() -> None:
    paper_id = _paper(pages=(), abstract="A metadata-only import.", sha="")
    with session_scope() as session:
        row = evidence.create_evidence(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            kind="quote",
            text="A metadata-only import.",
        )
        assert (row.locator, row.page_no, row.chunk_id) == ("abstract_only", None, None)


def test_a_paper_with_neither_chunks_nor_abstract_is_unlocated() -> None:
    paper_id = _paper(pages=(), abstract=None, sha="")
    with session_scope() as session:
        row = evidence.create_evidence(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            kind="quote",
            text="Something the scan certainly says.",
        )
        assert (row.locator, row.page_no) == ("unlocated", None)


def test_a_quote_too_short_to_identify_a_position_is_refused() -> None:
    paper_id = _paper()
    with session_scope() as session, pytest.raises(evidence.Invalid):
        evidence.resolve_quote(session, user_id=OWNER, paper_id=paper_id, quote="the")


# ------------------------------------------------- caller-supplied page numbers


def test_a_quote_may_not_carry_a_caller_supplied_page() -> None:
    paper_id = _paper()
    with session_scope() as session, pytest.raises(evidence.Invalid):
        evidence.create_evidence(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            kind="quote",
            text="we evaluate the transformer on ImageNet",
            page_no=2,
        )


def test_a_note_may_name_a_page_that_was_actually_extracted() -> None:
    paper_id = _paper()
    with session_scope() as session:
        row = evidence.create_evidence(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            kind="note",
            text="The held-out split is never described.",
            page_no=2,
        )
        assert (row.locator, row.page_no) == ("page", 2)
        assert row.chunk_id is not None


def test_a_note_may_not_name_a_page_no_extraction_produced() -> None:
    """A page number with nothing behind it is the contract's forbidden case."""
    paper_id = _paper()
    with session_scope() as session, pytest.raises(evidence.Invalid, match="no extracted text"):
        evidence.create_evidence(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            kind="note",
            text="Surely page 47 says something.",
            page_no=47,
        )


def test_a_note_with_no_page_on_an_extracted_paper_is_unlocated_not_abstract_only() -> None:
    paper_id = _paper()
    with session_scope() as session:
        row = evidence.create_evidence(
            session, user_id=OWNER, paper_id=paper_id, kind="note", text="Worth revisiting."
        )
        assert row.locator == "unlocated"


# ----------------------------------------------------------------- provenance


@pytest.mark.parametrize("kind", sorted(evidence.AUTOMATED_KINDS))
def test_machine_evidence_without_full_provenance_is_refused(kind: str) -> None:
    paper_id = _paper()
    with session_scope() as session, pytest.raises(evidence.Invalid, match="provenance"):
        evidence.create_evidence(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            kind=kind,
            text="The paper reports a 12.5% gain.",
            provider="pharos",
            model="rules@1",
        )


@pytest.mark.parametrize("kind", sorted(evidence.AUTOMATED_KINDS))
def test_machine_evidence_with_full_provenance_is_stored(kind: str) -> None:
    paper_id = _paper()
    with session_scope() as session:
        row = evidence.create_evidence(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            kind=kind,
            text="The paper reports a 12.5% gain.",
            provider="pharos",
            model="rules@1",
            workflow_version="v1",
            input_sha256="a" * 64,
        )
        assert (row.provider, row.model, row.workflow_version) == ("pharos", "rules@1", "v1")


def test_a_human_note_claiming_machine_provenance_is_refused() -> None:
    """The same lie told backwards, and just as unrecoverable once rendered."""
    paper_id = _paper()
    with session_scope() as session, pytest.raises(evidence.Invalid, match="human-authored"):
        evidence.create_evidence(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            kind="note",
            text="I think this is the key result.",
            provider="openai",
            model="gpt-x",
            workflow_version="v1",
            input_sha256="b" * 64,
        )


def test_partial_provenance_on_a_quote_is_refused_but_complete_provenance_is_kept() -> None:
    paper_id = _paper()
    with session_scope() as session, pytest.raises(evidence.Invalid, match="all-or-nothing"):
        evidence.create_evidence(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            kind="quote",
            text="we evaluate the transformer on ImageNet",
            provider="pharos",
        )
    with session_scope() as session:
        row = evidence.create_evidence(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            kind="quote",
            text="we evaluate the transformer on ImageNet",
            provider="pharos",
            model="extract@1",
            workflow_version="v1",
            input_sha256="c" * 64,
        )
        assert row.provider == "pharos"


# --------------------------------------------------------------------- update


def test_editing_a_quote_reresolves_its_page_instead_of_keeping_the_old_one() -> None:
    paper_id = _paper()
    with session_scope() as session:
        row_id = evidence.create_evidence(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            kind="quote",
            text="we evaluate the transformer on ImageNet",
        ).id
    with session_scope() as session:
        moved = evidence.update_evidence(
            session,
            user_id=OWNER,
            evidence_id=row_id,
            changes={"text": "Related work on retrieval augmentation is extensive."},
        )
        assert moved.page_no == 2


def test_editing_a_quote_clears_geometry_that_described_the_old_text() -> None:
    paper_id = _paper()
    with session_scope() as session:
        row_id = evidence.create_evidence(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            kind="quote",
            text="we evaluate the transformer on ImageNet",
            page_hint=1,
            rects=[{"x": 10, "y": 20, "w": 100, "h": 12}],
        ).id
    with session_scope() as session:
        moved = evidence.update_evidence(
            session,
            user_id=OWNER,
            evidence_id=row_id,
            changes={"text": "Related work on retrieval augmentation is extensive."},
        )
        assert moved.page_no == 2
        assert moved.rects is None


def test_editing_quote_text_and_geometry_together_is_rejected() -> None:
    paper_id = _paper()
    with session_scope() as session:
        row_id = evidence.create_evidence(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            kind="quote",
            text="we evaluate the transformer on ImageNet",
            page_hint=1,
            rects=[{"x": 10, "y": 20, "w": 100, "h": 12}],
        ).id
    with session_scope() as session, pytest.raises(evidence.Invalid, match="separate requests"):
        evidence.update_evidence(
            session,
            user_id=OWNER,
            evidence_id=row_id,
            changes={
                "text": "Related work on retrieval augmentation is extensive.",
                "rects": [{"x": 20, "y": 30, "w": 100, "h": 12}],
            },
        )


def test_editing_a_quote_into_text_the_paper_never_said_is_refused() -> None:
    """Otherwise the cheapest fabrication in the subsystem: keep the page, swap
    the words, and the row still reads as a verified citation."""
    paper_id = _paper()
    with session_scope() as session:
        row_id = evidence.create_evidence(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            kind="quote",
            text="we evaluate the transformer on ImageNet",
        ).id
    with session_scope() as session, pytest.raises(evidence.QuoteNotInPaper):
        evidence.update_evidence(
            session,
            user_id=OWNER,
            evidence_id=row_id,
            changes={"text": "we evaluate the transformer on every benchmark ever built"},
        )
    with session_scope() as session:
        assert evidence.require_evidence(session, row_id, user_id=OWNER).page_no == 1


@pytest.mark.parametrize("field", ["kind", "provider", "model", "locator", "chunk_id"])
def test_authorship_and_provenance_are_not_patchable(field: str) -> None:
    paper_id = _paper()
    with session_scope() as session:
        row_id = evidence.create_evidence(
            session, user_id=OWNER, paper_id=paper_id, kind="note", text="A thought."
        ).id
    with session_scope() as session, pytest.raises(evidence.Invalid, match="unexpected"):
        evidence.update_evidence(
            session, user_id=OWNER, evidence_id=row_id, changes={field: "quote"}
        )


def test_dropping_a_notes_page_falls_back_to_an_honest_locator_and_clears_geometry() -> None:
    paper_id = _paper()
    with session_scope() as session:
        row_id = evidence.create_evidence(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            kind="note",
            text="Anchored here.",
            page_no=1,
            rects=[{"x": 10, "y": 20, "w": 100, "h": 12}],
        ).id
    with session_scope() as session:
        row = evidence.update_evidence(
            session, user_id=OWNER, evidence_id=row_id, changes={"page_no": None}
        )
        assert (row.locator, row.page_no, row.rects) == ("unlocated", None, None)


def test_rects_are_refused_on_evidence_that_has_no_page() -> None:
    paper_id = _paper()
    with session_scope() as session, pytest.raises(evidence.Invalid, match="rects require"):
        evidence.create_evidence(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            kind="note",
            text="No page, but here is a rectangle.",
            rects=[{"x": 10, "y": 20, "w": 100, "h": 12}],
        )


def test_evidence_can_be_attached_to_and_detached_from_a_project() -> None:
    paper_id = _paper()
    project_id = _project()
    with session_scope() as session:
        row_id = evidence.create_evidence(
            session, user_id=OWNER, paper_id=paper_id, kind="note", text="A thought."
        ).id
    with session_scope() as session:
        attached = evidence.update_evidence(
            session, user_id=OWNER, evidence_id=row_id, changes={"project_id": project_id}
        )
        assert attached.project_id == project_id
    with session_scope() as session:
        detached = evidence.update_evidence(
            session, user_id=OWNER, evidence_id=row_id, changes={"project_id": None}
        )
        assert detached.project_id is None


# ------------------------------------------------------------ owner isolation


def test_another_users_paper_is_not_found_rather_than_forbidden() -> None:
    theirs = _paper(OTHER)
    with session_scope() as session, pytest.raises(evidence.NotFound):
        evidence.create_evidence(
            session, user_id=OWNER, paper_id=theirs, kind="note", text="Peeking."
        )


def test_another_users_evidence_is_not_found_on_every_entry_point() -> None:
    paper_id = _paper(OTHER)
    with session_scope() as session:
        theirs = evidence.create_evidence(
            session, user_id=OTHER, paper_id=paper_id, kind="note", text="Private."
        ).id
    with session_scope() as session:
        with pytest.raises(evidence.NotFound):
            evidence.require_evidence(session, theirs, user_id=OWNER)
        with pytest.raises(evidence.NotFound):
            evidence.update_evidence(
                session, user_id=OWNER, evidence_id=theirs, changes={"statement": "mine now"}
            )
        with pytest.raises(evidence.NotFound):
            evidence.delete_evidence(session, user_id=OWNER, evidence_id=theirs)


def test_another_users_chunks_cannot_place_this_users_quote() -> None:
    """Chunks are owner-scoped too, or the ledger becomes a full-text oracle."""
    with session_scope() as session:
        mine = Paper(user_id=OWNER, title="Shell", orig_sha256="sha-shell", orig_filename="s.pdf")
        session.add(mine)
        session.flush()
        # A chunk on the caller's paper, written by someone else. Contrived, and
        # exactly the row an owner-blind query would happily read.
        session.add(
            PaperChunk(
                user_id=OTHER,
                paper_id=mine.id,
                page_no=9,
                text="A secret sentence from somebody else's extraction.",
            )
        )
        paper_id = mine.id
    with session_scope() as session:
        placement = evidence.resolve_quote(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            quote="A secret sentence from somebody else's extraction.",
        )
        assert placement.outcome == "unlocated"
        assert placement.page_no is None


def test_listing_is_scoped_and_filterable() -> None:
    paper_id = _paper()
    other_paper = _paper(OTHER)
    project_id = _project()
    with session_scope() as session:
        evidence.create_evidence(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            kind="quote",
            text="we evaluate the transformer on ImageNet",
            project_id=project_id,
        )
        evidence.create_evidence(
            session, user_id=OWNER, paper_id=paper_id, kind="note", text="A thought."
        )
        evidence.create_evidence(
            session, user_id=OTHER, paper_id=other_paper, kind="note", text="Theirs."
        )
    with session_scope() as session:
        assert len(evidence.list_evidence(session, user_id=OWNER)) == 2
        assert len(evidence.list_evidence(session, user_id=OWNER, kind="quote")) == 1
        assert len(evidence.list_evidence(session, user_id=OWNER, locator="page")) == 1
        assert len(evidence.list_evidence(session, user_id=OWNER, project_id=project_id)) == 1
        with pytest.raises(evidence.NotFound):
            evidence.list_evidence(session, user_id=OWNER, paper_id=other_paper)


def test_an_owner_id_is_required_rather_than_optional() -> None:
    with session_scope() as session, pytest.raises(ValueError):
        evidence.list_evidence(session, user_id="")


# ------------------------------------------------------- the schema's last word


def test_the_check_constraint_is_a_net_the_service_never_makes_the_client_hit() -> None:
    """Both halves of the invariant, from both directions.

    The service refuses the bad combinations with a 400 *before* a flush, because
    an IntegrityError surfacing from the database is a 500 and poisons the
    transaction. The constraint is still there and still rejects a hand-built
    row — which is the point of putting it in the schema rather than only here.
    """
    from sqlalchemy.exc import IntegrityError

    paper_id = _paper()
    constraint = "ck_evidence_page_requires_page_locator"
    for locator, page_no in (("unlocated", 7), ("abstract_only", 3), ("page", None)):
        with pytest.raises(IntegrityError, match=constraint), session_scope() as session:
            session.add(
                Evidence(
                    user_id=OWNER,
                    paper_id=paper_id,
                    kind="note",
                    locator=locator,
                    page_no=page_no,
                    text="A placement the schema will not hold.",
                )
            )
            session.flush()

    # And the same three combinations, refused by the service before a flush.
    with session_scope() as session:
        for locator, page_no in (("unlocated", 7), ("abstract_only", 3), ("page", None)):
            with pytest.raises(evidence.Invalid):
                evidence._assert_locator_contract(locator, page_no)


def test_deleting_evidence_leaves_the_paper_and_its_chunks_alone() -> None:
    paper_id = _paper()
    with session_scope() as session:
        row_id = evidence.create_evidence(
            session,
            user_id=OWNER,
            paper_id=paper_id,
            kind="quote",
            text="we evaluate the transformer on ImageNet",
        ).id
    with session_scope() as session:
        evidence.delete_evidence(session, user_id=OWNER, evidence_id=row_id)
    with session_scope() as session:
        assert evidence.list_evidence(session, user_id=OWNER) == []
        assert session.get(Paper, paper_id) is not None
        assert (
            len(session.scalars(select(PaperChunk).where(PaperChunk.paper_id == paper_id)).all())
            == 2
        )
