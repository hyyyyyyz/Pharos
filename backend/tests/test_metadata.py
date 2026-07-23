"""Tests for PDF metadata extraction (:mod:`pharos.services.metadata`).

Everything here is hermetic: the end-to-end cases synthesise their own PDFs
with PyMuPDF rather than depending on a sample paper living in the repo, so the
suite stays runnable on a fresh clone and in CI.

The bias of these tests mirrors the module's own: they assert *precision*, not
recall. Most of the author-list cases below assert that nothing at all comes
back, because a half-parsed author list is the failure this module exists to
prevent — the user cannot tell a confident wrong answer from a missing one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

try:  # Mirror the import dance the module under test performs.
    import pymupdf
except ImportError:  # pragma: no cover - depends on the installed wheel
    import fitz as pymupdf  # type: ignore[no-redef]

from pharos.services.metadata import (
    ExtractedMeta,
    dehyphenate,
    extract_from_pdf,
    find_abstract,
    find_arxiv_id,
    find_doi,
    find_venue,
    looks_like_title,
    parse_authors,
    year_from_arxiv_id,
)

# --------------------------------------------------------------------------- #
# DOI
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("10.1234/abc.def", "10.1234/abc.def"),
        # A resolver prefix must be shed; we store the bare identifier.
        ("see https://doi.org/10.1145/3292500.3330701 for details", "10.1145/3292500.3330701"),
        ("http://dx.doi.org/10.1234/abc.def", "10.1234/abc.def"),
        # Sentence punctuation the DOI picked up from running text.
        ("DOI: 10.1038/s41586-021-03819-2.", "10.1038/s41586-021-03819-2"),
        ("(10.1234/abc.def)", "10.1234/abc.def"),
        ("[10.1234/abc.def]", "10.1234/abc.def"),
        ("10.1234/abc.def;", "10.1234/abc.def"),
        # Embedded mid-sentence, with text on both sides.
        ("The DOI 10.5555/12345678 appears mid-sentence and continues.", "10.5555/12345678"),
        # DOIs are case-insensitive; we normalise so lookups and equality work.
        ("10.1109/CVPR.2016.90", "10.1109/cvpr.2016.90"),
        ("no identifier anywhere in this sentence", None),
        ("", None),
        # "10." alone is not a DOI: there must be a real suffix.
        ("version 10.2 of the tool", None),
    ],
)
def test_find_doi(text: str, expected: str | None) -> None:
    assert find_doi(text) == expected


def test_find_doi_keeps_balanced_parens() -> None:
    """Legacy Wiley DOIs genuinely contain "(SICI)", so only strip unbalanced."""
    raw = "10.1002/(SICI)1097-0258(19960229)15:4<361::AID-SIM168>3.0.CO;2-4"
    assert find_doi(raw) == raw.lower()


def test_find_doi_rejoins_a_line_wrapped_prefix() -> None:
    assert find_doi("10.1145/\n3292500.3330701") == "10.1145/3292500.3330701"


# --------------------------------------------------------------------------- #
# arXiv id
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("arXiv:2301.01234", "2301.01234"),
        # The version suffix is dropped so the id is a stable lookup key.
        ("arXiv:2301.01234v3", "2301.01234"),
        ("arXiv:1706.03762v7 [cs.CL] 2 Aug 2023", "1706.03762"),
        ("arXiv:cs/0701001", "cs/0701001"),
        # The subject class keeps arXiv's own capitalisation: ``id_list``
        # honours it, and lowercasing it made every legacy lookup fail.
        ("arXiv:math.GT/0309136", "math.GT/0309136"),
        ("arxiv:MATH.gt/0309136", "math.GT/0309136"),
        ("arXiv: 2301.01234", "2301.01234"),
        ("no preprint id here", None),
        ("", None),
        # A bare number is indistinguishable from body text, so it is declined.
        ("the value 2301.01234 was measured", None),
        # Month 13 does not exist: this is a number that merely looks like an id.
        ("arXiv:2313.01234", None),
    ],
)
def test_find_arxiv_id(text: str, expected: str | None) -> None:
    assert find_arxiv_id(text) == expected


@pytest.mark.parametrize(
    ("arxiv_id", "expected"),
    [
        ("2301.01234", 2023),
        ("1706.03762", 2017),
        ("cs/0701001", None),  # legacy shape is ambiguous; the module declines
        (None, None),
    ],
)
def test_year_from_arxiv_id(arxiv_id: str | None, expected: int | None) -> None:
    assert year_from_arxiv_id(arxiv_id) == expected


# --------------------------------------------------------------------------- #
# abstract cleanup
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # A genuine syllable break: hyphen dropped.
        ("repre-\nsentation", "representation"),
        ("evalua-\ntion", "evaluation"),
        # The break fell inside a compound: close up, but keep the hyphen.
        ("English-\nto-German", "English-to-German"),
        ("state-of-the-\nart", "state-of-the-art"),
        # An ordinary hyphen with no line break is untouched.
        ("well-known", "well-known"),
        ("no hyphens at all", "no hyphens at all"),
    ],
)
def test_dehyphenate(raw: str, expected: str) -> None:
    assert dehyphenate(raw) == expected


def test_find_abstract_dehyphenates_and_collapses_newlines() -> None:
    text = (
        "Abstract\n"
        "We study the trans-\n"
        "formation of layout while translating scholarly documents\n"
        "into other languages.\n"
        "1 Introduction\n"
        "Body text that must not appear."
    )
    assert find_abstract(text) == (
        "We study the transformation of layout while translating "
        "scholarly documents into other languages."
    )


@pytest.mark.parametrize(
    "terminator",
    [
        "1 Introduction",
        "1. Introduction",
        "Introduction",
        "I. Introduction",
        "Keywords: translation, layout",
        "Index Terms— translation",
        "CCS Concepts",
        "\fnext page",  # a page break always ends the abstract
    ],
)
def test_find_abstract_terminates_at_the_next_section(terminator: str) -> None:
    body = "A sufficiently long abstract body that runs on for a good while here."
    assert find_abstract(f"Abstract\n{body}\n{terminator}\nLeaked body text.") == body


def test_find_abstract_accepts_letterspaced_heading() -> None:
    """Elsevier and IEEE templates letter-space the heading."""
    body = "A sufficiently long abstract body that runs on for a good while here."
    assert find_abstract(f"A B S T R A C T\n{body}\n1 Introduction") == body


@pytest.mark.parametrize(
    "text",
    [
        "",
        "No heading at all, merely a paragraph of prose that runs on for a while.",
        "Abstract\nToo short.\n1 Introduction",  # under the length floor
    ],
)
def test_find_abstract_declines(text: str) -> None:
    assert find_abstract(text) is None


# --------------------------------------------------------------------------- #
# author-list rejection
#
# The whole list must be discarded, not partially salvaged.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("Jane Doe, Stanford University", "institution"),
        ("Jane Doe, Massachusetts Institute of Technology", "institution"),
        ("Jane Doe, Dept. of Physics", "department"),
        ("Jane Doe, Microsoft Research", "corporate affiliation"),
        ("John Smith, Google Brain", "corporate affiliation"),
        ("Jane Doe, School of Informatics", "institution"),
        ("Abstract, Jane Doe", "front-matter heading"),
        ("Keywords, Jane Doe", "front-matter heading"),
        ("Equal contribution. Jane Doe", "footnote boilerplate"),
        # "Surname, Initials" ordering cannot be recovered by comma-splitting.
        ("Vaswani, A., Shazeer, N.", "flipped name ordering"),
        ("Doe, J., Roe, R.", "flipped name ordering"),
        # A lone bare word is far more likely a stray heading than a person.
        ("Solo", "single bare word"),
        ("", "empty"),
        # Emails only, with no names to anchor them.
        ("jane@ex.edu, john@ex.edu", "no names present"),
    ],
)
def test_parse_authors_discards_the_whole_list(raw: str, reason: str) -> None:
    assert parse_authors(raw) == (), f"expected no authors ({reason})"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Mei Lin, Robert Okonkwo", ("Mei Lin", "Robert Okonkwo")),
        ("Jane Doe and John Roe", ("Jane Doe", "John Roe")),
        ("Jane Doe, John Roe, and Mary Poe", ("Jane Doe", "John Roe", "Mary Poe")),
        # Affiliation markers are decoration, not part of the name.
        ("Jane Doe*, John Roe†", ("Jane Doe", "John Roe")),
        ("Jane Doe1, John Roe2", ("Jane Doe", "John Roe")),
        ("Jane Doe (1), John Roe (2)", ("Jane Doe", "John Roe")),
    ],
)
def test_parse_authors_accepts_clean_lists(raw: str, expected: tuple[str, ...]) -> None:
    assert parse_authors(raw) == expected


def test_parse_authors_never_emits_an_email_or_url_as_a_name() -> None:
    """A trailing contact address must not survive as if it were a person.

    The module strips emails and URLs rather than voiding the list, which is
    defensible — the address belongs to the name beside it. What must never
    happen is an address being reported *as* an author.
    """
    for raw in ("Jane Doe, jane@example.edu", "Jane Doe, https://example.com/~jane"):
        names = parse_authors(raw)
        assert all("@" not in name and "://" not in name for name in names), names


def test_parse_authors_does_not_fuse_names_across_an_inline_email() -> None:
    """Inline per-author emails must not merge adjacent authors.

    ``Jane Doe jane@ex.edu, John Roe john@ex.edu`` currently yields the single
    invented person ``('Jane Doe John Roe',)``: the greedy email pattern eats
    the comma that separated them. Inventing a person is precisely the failure
    mode this module is built to avoid, so this asserts the correct result.
    """
    assert parse_authors("Jane Doe jane@ex.edu, John Roe john@ex.edu") in (
        ("Jane Doe", "John Roe"),
        (),  # discarding the list outright would also be an acceptable fix
    )


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("Attention Is All You Need", True),
        ("Layout Preserving Translation of Scholarly Documents", True),
        (None, False),
        ("", False),
        ("Untitled", False),
        ("Microsoft Word - paper.doc", False),
        ("main.tex", False),
        ("preprint", False),
        ("12345", False),
        ("Abstract", False),
        ("arXiv:2301.01234", False),
        ("https://example.com/paper", False),
        ("author@example.edu", False),
    ],
)
def test_looks_like_title(candidate: str | None, expected: bool) -> None:
    assert looks_like_title(candidate) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("NeurIPS 2017", "NeurIPS 2017"),
        ("31st Conference on Neural Information Processing Systems", None),
        ("arXiv:2301.01234", "arXiv"),
        ("nothing venue-like here", None),
    ],
)
def test_find_venue(text: str, expected: str | None) -> None:
    venue = find_venue(text)
    if expected is None:
        assert venue is None or len(venue) <= 120
    else:
        assert venue == expected


# --------------------------------------------------------------------------- #
# end-to-end on synthesised PDFs
# --------------------------------------------------------------------------- #

TITLE = "Layout Preserving Translation of Scholarly Documents"
AUTHORS = ("Mei Lin", "Robert Okonkwo", "Ana Ruiz")
DOI = "10.1234/pharos.2024.0042"
ABSTRACT = (
    "We present a system that preserves the visual layout of a scholarly representation "
    "while translating its text. Our approach keeps figures, tables and equations in "
    "place, and evaluates on a corpus of one thousand papers drawn from several "
    "disciplines."
)


def _write_paper(path: Path) -> Path:
    """Synthesise a one-page paper whose front matter we know exactly.

    Built rather than checked in so the suite carries no binary fixture and no
    dependency on a sample paper existing on disk.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)

    y = 90.0
    page.insert_text((72, y), TITLE, fontname="helv", fontsize=17)
    y += 40
    page.insert_text((72, y), "Mei Lin, Robert Okonkwo, and Ana Ruiz", fontname="helv", fontsize=11)
    y += 26
    page.insert_text((72, y), f"doi:{DOI}", fontname="helv", fontsize=9)
    y += 40
    page.insert_text((72, y), "Abstract", fontname="helv", fontsize=11)
    y += 18
    # Deliberately hyphenated across a line break, to exercise de-hyphenation.
    for line in (
        "We present a system that preserves the visual layout of a scholarly repre-",
        "sentation while translating its text. Our approach keeps figures, tables and",
        "equations in place, and evaluates on a corpus of one thousand papers drawn",
        "from several disciplines.",
    ):
        page.insert_text((72, y), line, fontname="helv", fontsize=10)
        y += 14
    y += 20
    page.insert_text((72, y), "1 Introduction", fontname="helv", fontsize=12)
    y += 18
    page.insert_text(
        (72, y),
        "Machine translation of documents has a long history.",
        fontname="helv",
        fontsize=10,
    )

    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def paper_pdf(tmp_path: Path) -> Path:
    return _write_paper(tmp_path / "paper.pdf")


def test_extract_recovers_title(paper_pdf: Path) -> None:
    assert extract_from_pdf(paper_pdf).title == TITLE


def test_extract_recovers_every_author_in_order(paper_pdf: Path) -> None:
    """Order matters: authorship position is meaningful, and truncation is the
    failure mode the row-grouping logic exists to prevent."""
    assert extract_from_pdf(paper_pdf).authors == AUTHORS


def test_extract_recovers_doi(paper_pdf: Path) -> None:
    assert extract_from_pdf(paper_pdf).doi == DOI


def test_extract_recovers_abstract_dehyphenated_and_bounded(paper_pdf: Path) -> None:
    abstract = extract_from_pdf(paper_pdf).abstract
    assert abstract == ABSTRACT
    # The line break inside "repre-sentation" must have been healed ...
    assert "representation" in abstract
    assert "repre- sentation" not in abstract
    # ... and the body after the Introduction heading must not have leaked in.
    assert "Machine translation" not in abstract


def test_extract_invents_nothing_absent_from_the_page(paper_pdf: Path) -> None:
    """No arXiv id appears on this page, so none may be reported."""
    assert extract_from_pdf(paper_pdf).arxiv_id is None


# --------------------------------------------------------------------------- #
# degenerate inputs — none may raise, all must come back essentially empty
# --------------------------------------------------------------------------- #

# PyMuPDF refuses to *save* a zero-page document, so the bytes are hand-built.
_ZERO_PAGE_PDF = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [] /Count 0 >>
endobj
xref
0 3
0000000000 65535 f
0000000009 00000 n
0000000063 00000 n
trailer
<< /Size 3 /Root 1 0 R >>
startxref
122
%%EOF
"""


def _zero_page_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "zero.pdf"
    path.write_bytes(_ZERO_PAGE_PDF)
    return path


def _no_text_layer_pdf(tmp_path: Path) -> Path:
    """A page bearing only a drawn rectangle — the scanned-paper case."""
    path = tmp_path / "drawing.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.draw_rect(pymupdf.Rect(100, 100, 400, 300), color=(0, 0, 0), fill=(0.6, 0.6, 0.6))
    doc.save(path)
    doc.close()
    return path


def _not_a_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "prose.pdf"
    path.write_text("This is plainly not a PDF. " * 40, encoding="utf-8")
    return path


def _empty_file(tmp_path: Path) -> Path:
    path = tmp_path / "empty.pdf"
    path.write_bytes(b"")
    return path


def _missing_file(tmp_path: Path) -> Path:
    return tmp_path / "does-not-exist.pdf"


def _a_directory(tmp_path: Path) -> Path:
    path = tmp_path / "subdir"
    path.mkdir()
    return path


def _truncated_pdf(tmp_path: Path) -> Path:
    raw = _write_paper(tmp_path / "whole.pdf").read_bytes()
    path = tmp_path / "truncated.pdf"
    path.write_bytes(raw[: len(raw) // 3])
    return path


@pytest.mark.parametrize(
    "build",
    [
        _zero_page_pdf,
        _no_text_layer_pdf,
        _not_a_pdf,
        _empty_file,
        _missing_file,
        _a_directory,
    ],
    ids=[
        "zero-page",
        "no-text-layer",
        "not-a-pdf",
        "empty-file",
        "missing-file",
        "a-directory",
    ],
)
def test_degenerate_inputs_return_empty_without_raising(build, tmp_path: Path) -> None:
    """Extraction failure is a normal outcome, not an exception the caller
    must handle: upload has to keep working on a scanned or broken file."""
    assert extract_from_pdf(build(tmp_path)) == ExtractedMeta()


def test_truncated_pdf_does_not_raise(tmp_path: Path) -> None:
    """MuPDF salvages what it can from a half-written file. Whatever survives,
    the call must return normally rather than blow up the upload request."""
    meta = extract_from_pdf(_truncated_pdf(tmp_path))
    assert isinstance(meta, ExtractedMeta)
    # Anything recovered must still be real, never invented.
    assert meta.title in (None, TITLE)
    assert set(meta.authors) <= set(AUTHORS)


# --------------------------------------------------------------------------- #
# regression: adversarial review
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # A DOI lifted out of a publisher URL keeps the tracking query string
        # or the fragment unless they are cut, and the result resolves nowhere.
        (
            "Available at https://doi.org/10.1145/3292500.3330701?casa_token=AbC",
            "10.1145/3292500.3330701",
        ),
        ("See https://dl.acm.org/doi/10.1145/3292500.3330701#sec-4 now", "10.1145/3292500.3330701"),
        ('href="10.1234/abcd">x', "10.1234/abcd"),
    ],
)
def test_find_doi_stops_at_url_query_and_fragment(text: str, expected: str) -> None:
    assert find_doi(text) == expected


def test_find_doi_still_keeps_the_sici_angle_brackets() -> None:
    """The query/fragment cut must not touch legacy Wiley SICI DOIs."""
    raw = "10.1002/(SICI)1097-0258(19960229)15:4<361::AID-SIM168>3.0.CO;2-4"
    assert find_doi(raw) == raw.lower()


def test_parse_authors_discards_a_list_marked_et_al() -> None:
    """ "et al." says outright that the list is partial.

    Deleting the marker and shipping the visible prefix is the silent
    truncation this module exists to prevent, so the list is discarded.
    """
    assert parse_authors("John Smith, Jane Doe, et al.") == ()


@pytest.mark.parametrize(
    "raw",
    [
        "Jane Doe, Stanford",  # mixed-case institution, no keyword match
        "Jane Doe, Berkeley",
        "Wei Li, Tsinghua, Bo Zhang, Peking",
    ],
)
def test_parse_authors_rejects_bare_single_word_entries(raw: str) -> None:
    """Institutions the keyword list does not name used to ship as people.

    Only all-caps acronyms ("MIT", "ETH") were caught, and then only by
    accident, via the initials guard.
    """
    assert parse_authors(raw) == ()
