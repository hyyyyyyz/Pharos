"""Tests for registry enrichment (:mod:`pharos.services.enrich`).

**No test here touches the network.** Every case monkeypatches
``enrich._fetch``, the single choke point through which both lookups reach the
outside world, and feeds it recorded payloads. That is a hard requirement, not
a convenience: the suite has to pass behind the GFW and in a sandboxed CI
runner, and a test that silently succeeds only when CrossRef is reachable is
worse than no test at all.

The payloads below are shaped after real responses, including the parts of
CrossRef's data model that are routinely absent or null.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from typing import Any

import pytest
from pharos.services import enrich
from pharos.services.enrich import enrich_by_arxiv, enrich_by_doi, merge
from pharos.services.metadata import ExtractedMeta

# --------------------------------------------------------------------------- #
# network isolation
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if a test reaches the real ``urlopen``.

    Guards against a future edit adding a code path that bypasses ``_fetch``:
    such a test would otherwise pass on a developer laptop and hang in CI.
    """

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a test attempted a live network call")

    monkeypatch.setattr(enrich.urllib.request, "urlopen", _boom)


@pytest.fixture
def serve(monkeypatch: pytest.MonkeyPatch) -> Callable[[bytes | str | None], list[str]]:
    """Make ``_fetch`` return a canned body; hand back the list of URLs asked for."""
    requested: list[str] = []

    def _install(body: bytes | str | None) -> list[str]:
        payload = body.encode() if isinstance(body, str) else body

        def _fake_fetch(url: str, timeout: float) -> bytes | None:
            requested.append(url)
            return payload

        monkeypatch.setattr(enrich, "_fetch", _fake_fetch)
        return requested

    return _install


def _crossref(message: dict[str, Any]) -> str:
    return json.dumps({"status": "ok", "message-type": "work", "message": message})


# --------------------------------------------------------------------------- #
# CrossRef parsing
# --------------------------------------------------------------------------- #

_FULL_RECORD = {
    "DOI": "10.1109/cvpr.2016.90",
    "title": ["Deep Residual Learning for Image Recognition"],
    "author": [
        {"given": "Kaiming", "family": "He", "sequence": "first"},
        {"given": "Xiangyu", "family": "Zhang", "sequence": "additional"},
    ],
    "issued": {"date-parts": [[2016, 6, 27]]},
    "container-title": ["2016 IEEE Conference on Computer Vision and Pattern Recognition (CVPR)"],
    "abstract": "<jats:p>Deeper neural networks are more difficult to train.</jats:p>",
    "type": "proceedings-article",
    "publisher": "IEEE",
}


def test_crossref_full_record(serve: Callable[..., list[str]]) -> None:
    urls = serve(_crossref(_FULL_RECORD))
    meta = enrich_by_doi("10.1109/CVPR.2016.90")

    assert meta is not None
    assert meta.title == "Deep Residual Learning for Image Recognition"
    assert meta.authors == ("Kaiming He", "Xiangyu Zhang")
    assert meta.year == 2016
    assert meta.venue == ("2016 IEEE Conference on Computer Vision and Pattern Recognition (CVPR)")
    assert meta.doi == "10.1109/cvpr.2016.90"
    assert meta.abstract == "Deeper neural networks are more difficult to train."
    assert meta.arxiv_id is None
    assert urls and urls[0].startswith("https://api.crossref.org/works/")


@pytest.mark.parametrize(
    "spelling",
    [
        "10.1109/CVPR.2016.90",
        "https://doi.org/10.1109/CVPR.2016.90",
        "http://doi.org/10.1109/CVPR.2016.90",
        "https://dx.doi.org/10.1109/CVPR.2016.90",
        "doi:10.1109/CVPR.2016.90",
        "DOI:10.1109/CVPR.2016.90",  # prefix matching is case-insensitive
        "  10.1109/CVPR.2016.90  ",
    ],
)
def test_crossref_accepts_any_doi_spelling(serve: Callable[..., list[str]], spelling: str) -> None:
    serve(_crossref(_FULL_RECORD))
    assert enrich_by_doi(spelling) is not None


def test_crossref_accepts_plain_http_dx_doi_org(serve: Callable[..., list[str]]) -> None:
    """The legacy ``http://dx.doi.org/`` form is still widespread in the wild.

    ``https://dx.doi.org/``, ``http://doi.org/`` and ``https://doi.org/`` are all
    stripped, so omitting this one combination is an oversight rather than a
    deliberate narrowing: the DOI is silently treated as malformed and the
    lookup is skipped entirely.
    """
    serve(_crossref(_FULL_RECORD))
    assert enrich_by_doi("http://dx.doi.org/10.1109/CVPR.2016.90") is not None


def test_crossref_missing_container_title_falls_back_to_event(
    serve: Callable[..., list[str]],
) -> None:
    """Conference papers often carry the venue only under ``event.name``."""
    serve(
        _crossref(
            {
                "title": ["Attention Is All You Need"],
                "author": [{"given": "Ashish", "family": "Vaswani"}],
                "event": {"name": "Thirty-first Conference on Neural Information Processing"},
            }
        )
    )
    meta = enrich_by_doi("10.1234/x")
    assert meta is not None
    assert meta.venue == "Thirty-first Conference on Neural Information Processing"


@pytest.mark.parametrize(
    "message_extra",
    [
        {},  # no container-title key at all
        {"container-title": []},  # present but empty
        {"container-title": ["", "   "]},  # present but blank
    ],
    ids=["absent", "empty-list", "blank-strings"],
)
def test_crossref_venue_is_none_when_unstated(
    serve: Callable[..., list[str]], message_extra: dict[str, Any]
) -> None:
    """Never invent a venue — an unstated one stays None so the UI shows "—"."""
    serve(_crossref({"title": ["T"], "author": [{"given": "A", "family": "B"}], **message_extra}))
    meta = enrich_by_doi("10.1234/x")
    assert meta is not None
    assert meta.venue is None


def test_crossref_empty_title_list_does_not_crash(serve: Callable[..., list[str]]) -> None:
    """``title`` is an array CrossRef sometimes ships empty; ``[0]`` would raise."""
    serve(_crossref({"title": [], "author": [{"given": "Ada", "family": "Lovelace"}]}))
    meta = enrich_by_doi("10.1234/x")
    assert meta is not None
    assert meta.title is None
    assert meta.authors == ("Ada Lovelace",)


def test_crossref_record_with_neither_title_nor_authors_is_not_a_hit(
    serve: Callable[..., list[str]],
) -> None:
    """An all-empty record must not read as success, or merge() would let its
    blanks win over the PDF's real guesses."""
    serve(_crossref({"title": [], "author": [], "container-title": ["Some Journal"]}))
    assert enrich_by_doi("10.1234/x") is None


@pytest.mark.parametrize(
    ("date_parts", "expected"),
    [
        ([[2016, 6, 27]], 2016),
        ([[2016]], 2016),
        ([["2016"]], 2016),  # CrossRef occasionally stringifies the year
        ([[None]], None),  # null year — the nasty real-world case
        ([[]], None),
        ([], None),
        ([[0]], None),  # CrossRef really does contain 0
        ([[3999]], None),  # ... and far-future values
        ([[True]], None),  # bool is an int subclass; never a year
    ],
    ids=[
        "full-date",
        "year-only",
        "stringified",
        "null-year",
        "empty-inner",
        "empty-outer",
        "zero",
        "far-future",
        "boolean",
    ],
)
def test_crossref_year_handles_nullable_date_parts(
    serve: Callable[..., list[str]], date_parts: list[Any], expected: int | None
) -> None:
    serve(
        _crossref(
            {
                "title": ["T"],
                "author": [{"given": "A", "family": "B"}],
                "issued": {"date-parts": date_parts},
            }
        )
    )
    meta = enrich_by_doi("10.1234/x")
    assert meta is not None
    assert meta.year == expected


def test_crossref_organisation_author(serve: Callable[..., list[str]]) -> None:
    """Corporate authors carry only ``name``; person entries may lack ``given``."""
    serve(
        _crossref(
            {
                "title": ["Observation of a New Particle"],
                "author": [
                    {"name": "The ATLAS Collaboration"},
                    {"family": "Aad"},  # family only
                    {"given": "Georges"},  # given only
                    {"suffix": "Jr."},  # nothing renderable — skipped
                    "not-a-dict",  # malformed entry — skipped
                ],
            }
        )
    )
    meta = enrich_by_doi("10.1234/x")
    assert meta is not None
    assert meta.authors == ("The ATLAS Collaboration", "Aad", "Georges")


def test_crossref_strips_jats_markup(serve: Callable[..., list[str]]) -> None:
    serve(
        _crossref(
            {
                "title": ["T"],
                "author": [{"given": "A", "family": "B"}],
                "abstract": (
                    "<jats:title>Abstract</jats:title>"
                    "<jats:p>Deeper networks are harder to train.</jats:p>"
                    "<jats:p>We present a residual framework &amp; evaluate it.</jats:p>"
                ),
            }
        )
    )
    meta = enrich_by_doi("10.1234/x")
    assert meta is not None
    assert meta.abstract == (
        "Deeper networks are harder to train. We present a residual framework & evaluate it."
    )
    assert "<" not in meta.abstract


def test_crossref_jats_paragraphs_do_not_fuse(serve: Callable[..., list[str]]) -> None:
    """Tags become spaces, not deletions: ``a</jats:p><jats:p>b`` is two words."""
    serve(
        _crossref(
            {
                "title": ["T"],
                "author": [{"given": "A", "family": "B"}],
                "abstract": "<jats:p>alpha</jats:p><jats:p>beta</jats:p>",
            }
        )
    )
    meta = enrich_by_doi("10.1234/x")
    assert meta is not None
    assert meta.abstract == "alpha beta"


def test_crossref_structured_abstract_keeps_section_titles(
    serve: Callable[..., list[str]],
) -> None:
    """Only a *leading* "Abstract" label is a label; Methods/Results are content."""
    serve(
        _crossref(
            {
                "title": ["T"],
                "author": [{"given": "A", "family": "B"}],
                "abstract": (
                    "<jats:sec><jats:title>Methods</jats:title><jats:p>We ran a trial.</jats:p>"
                    "</jats:sec><jats:sec><jats:title>Results</jats:title>"
                    "<jats:p>It worked.</jats:p></jats:sec>"
                ),
            }
        )
    )
    meta = enrich_by_doi("10.1234/x")
    assert meta is not None
    assert meta.abstract == "Methods We ran a trial. Results It worked."


def test_crossref_drops_abstract_label_inside_a_wrapper_element(
    serve: Callable[..., list[str]],
) -> None:
    serve(
        _crossref(
            {
                "title": ["T"],
                "author": [{"given": "A", "family": "B"}],
                "abstract": (
                    "<jats:abstract><jats:title>Abstract</jats:title>"
                    "<jats:p>The real content.</jats:p></jats:abstract>"
                ),
            }
        )
    )
    meta = enrich_by_doi("10.1234/x")
    assert meta is not None
    assert meta.abstract == "The real content."


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (None, "network failure / 404"),
        (b"{not json", "malformed JSON"),
        (b'{"message": "a string, not an object"}', "unexpected shape"),
        (b"{}", "no message key"),
        (b"", "truncated body"),
    ],
)
def test_crossref_failures_return_none(
    serve: Callable[..., list[str]], body: bytes | None, reason: str
) -> None:
    serve(body)
    assert enrich_by_doi("10.1234/x") is None, reason


@pytest.mark.parametrize("doi", ["", "not-a-doi", "11.1234/x", "https://example.com/x"])
def test_crossref_rejects_malformed_doi_without_fetching(
    monkeypatch: pytest.MonkeyPatch, doi: str
) -> None:
    def _never(url: str, timeout: float) -> bytes | None:
        raise AssertionError("a malformed DOI must not reach the network")

    monkeypatch.setattr(enrich, "_fetch", _never)
    assert enrich_by_doi(doi) is None


# --------------------------------------------------------------------------- #
# arXiv parsing
# --------------------------------------------------------------------------- #

_ARXIV_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <title type="html">ArXiv Query</title>
  <entry>
    <id>http://arxiv.org/abs/1706.03762v7</id>
    <updated>2023-08-02T00:41:18Z</updated>
    <published>2017-06-12T17:57:34Z</published>
    <title>Attention Is All You Need</title>
    <summary>  The dominant sequence transduction models are based on complex
recurrent or convolutional neural networks.
</summary>
    <author><name>Ashish Vaswani</name></author>
    <author><name>Noam Shazeer</name></author>
    <author><name>Niki Parmar</name></author>
    <arxiv:journal_ref>Advances in Neural Information Processing Systems 30</arxiv:journal_ref>
    <arxiv:primary_category term="cs.CL"/>
    <category term="cs.CL" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
</feed>
"""

# arXiv answers an unknown id with HTTP 200 and this decoy entry.
_ARXIV_ERROR_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/api/errors#incorrect_id_format_for_9999.99999</id>
    <title>Error</title>
    <summary>incorrect id format for 9999.99999</summary>
  </entry>
</feed>
"""


def test_arxiv_full_entry(serve: Callable[..., list[str]]) -> None:
    urls = serve(_ARXIV_FEED)
    meta = enrich_by_arxiv("1706.03762")

    assert meta is not None
    assert meta.title == "Attention Is All You Need"
    assert meta.authors == ("Ashish Vaswani", "Noam Shazeer", "Niki Parmar")
    assert meta.year == 2017
    assert meta.venue == "Advances in Neural Information Processing Systems 30"
    assert meta.abstract == (
        "The dominant sequence transduction models are based on complex "
        "recurrent or convolutional neural networks."
    )
    # The canonical id is stored without its version suffix.
    assert meta.arxiv_id == "1706.03762"
    assert urls and "1706.03762" in urls[0]


def test_arxiv_unpublished_preprint_venue_is_arxiv(serve: Callable[..., list[str]]) -> None:
    """Until journal_ref appears, the preprint server is the only honest venue."""
    serve(
        _ARXIV_FEED.replace(
            "<arxiv:journal_ref>Advances in Neural Information Processing Systems 30"
            "</arxiv:journal_ref>",
            "",
        )
    )
    meta = enrich_by_arxiv("1706.03762")
    assert meta is not None
    assert meta.venue == "arXiv"


def test_arxiv_error_entry_is_not_a_hit(serve: Callable[..., list[str]]) -> None:
    """Unhandled, a bad id would masquerade as a paper titled "Error"."""
    serve(_ARXIV_ERROR_FEED)
    assert enrich_by_arxiv("9999.99999") is None


def test_arxiv_versioned_input_is_accepted(serve: Callable[..., list[str]]) -> None:
    serve(_ARXIV_FEED)
    meta = enrich_by_arxiv("arXiv:1706.03762v7")
    assert meta is not None
    assert meta.arxiv_id == "1706.03762"


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (None, "network failure"),
        ("<<< not xml", "malformed XML"),
        ('<feed xmlns="http://www.w3.org/2005/Atom"><title>x</title></feed>', "no entry"),
        (b"", "truncated body"),
    ],
)
def test_arxiv_failures_return_none(
    serve: Callable[..., list[str]], body: bytes | str | None, reason: str
) -> None:
    serve(body)
    assert enrich_by_arxiv("1706.03762") is None, reason


@pytest.mark.parametrize("arxiv_id", ["", "hello", "12.34", "https://example.com/abs/1"])
def test_arxiv_rejects_malformed_id_without_fetching(
    monkeypatch: pytest.MonkeyPatch, arxiv_id: str
) -> None:
    def _never(url: str, timeout: float) -> bytes | None:
        raise AssertionError("a malformed arXiv id must not reach the network")

    monkeypatch.setattr(enrich, "_fetch", _never)
    assert enrich_by_arxiv(arxiv_id) is None


def test_arxiv_legacy_id_shape(serve: Callable[..., list[str]]) -> None:
    serve(
        _ARXIV_FEED.replace(
            "http://arxiv.org/abs/1706.03762v7", "http://arxiv.org/abs/math.GT/0309136v1"
        )
    )
    meta = enrich_by_arxiv("math.GT/0309136")
    assert meta is not None
    assert meta.arxiv_id == "math.GT/0309136"


# --------------------------------------------------------------------------- #
# merge
# --------------------------------------------------------------------------- #

_BASE = ExtractedMeta(
    title="Attentin Is All You Need",  # the PDF's typo-ridden guess
    authors=("Ashish Vaswani",),
    year=None,
    venue=None,
    doi="10.1234/pdf",
    abstract="The abstract only the PDF has.",
    arxiv_id="1706.03762",
)


def test_merge_better_wins_per_field() -> None:
    better = ExtractedMeta(
        title="Attention Is All You Need",
        authors=("Ashish Vaswani", "Noam Shazeer"),
        year=2017,
        venue="NeurIPS",
    )
    merged = merge(_BASE, better)

    assert merged.title == "Attention Is All You Need"
    assert merged.authors == ("Ashish Vaswani", "Noam Shazeer")
    assert merged.year == 2017
    assert merged.venue == "NeurIPS"


def test_merge_base_survives_where_better_is_empty() -> None:
    """Coverage is complementary: CrossRef routinely lacks the PDF's abstract."""
    merged = merge(_BASE, ExtractedMeta(title="Attention Is All You Need", year=2017))

    assert merged.abstract == "The abstract only the PDF has."
    assert merged.doi == "10.1234/pdf"
    assert merged.arxiv_id == "1706.03762"
    assert merged.authors == ("Ashish Vaswani",)


@pytest.mark.parametrize(
    "better",
    [
        ExtractedMeta(),
        ExtractedMeta(title="", authors=(), venue=""),
        ExtractedMeta(title="   ", venue="\t\n"),  # whitespace-only counts as absent
    ],
    ids=["all-defaults", "empty-strings-and-tuple", "whitespace-only"],
)
def test_merge_empty_values_never_overwrite(better: ExtractedMeta) -> None:
    assert merge(_BASE, better) == _BASE


def test_merge_none_returns_base_unchanged() -> None:
    """The lookup failing is the common case; base must pass straight through."""
    assert merge(_BASE, None) is _BASE


def test_merge_fills_fields_the_base_lacks() -> None:
    merged = merge(ExtractedMeta(), ExtractedMeta(title="T", authors=("A",), year=2020))
    assert merged.title == "T"
    assert merged.authors == ("A",)
    assert merged.year == 2020


def test_merge_mutates_neither_input() -> None:
    base = dataclasses.replace(_BASE)
    better = ExtractedMeta(title="New Title", year=2017)
    before_base = dataclasses.asdict(base)
    before_better = dataclasses.asdict(better)

    merge(base, better)

    assert dataclasses.asdict(base) == before_base
    assert dataclasses.asdict(better) == before_better


def test_merge_returns_an_extracted_meta_not_a_dict() -> None:
    merged = merge(_BASE, ExtractedMeta(year=2017))
    assert isinstance(merged, ExtractedMeta)
    assert merged.year == 2017


def test_merge_covers_every_field_of_the_dataclass() -> None:
    """Iterating ``dataclasses.fields`` means a new column needs no edit here;
    this pins that property so a hand-rolled rewrite cannot silently drop one."""
    filled = ExtractedMeta(
        title="T",
        authors=("A",),
        year=2020,
        venue="V",
        doi="10.1/x",
        abstract="Abs",
        arxiv_id="2001.00001",
    )
    merged = merge(ExtractedMeta(), filled)
    for field in dataclasses.fields(ExtractedMeta):
        assert getattr(merged, field.name) == getattr(filled, field.name), field.name


# --------------------------------------------------------------------------- #
# regression: adversarial review
# --------------------------------------------------------------------------- #


def test_fetch_honours_a_wall_clock_deadline_against_a_trickling_server() -> None:
    """``urlopen(timeout=)`` bounds each socket operation, not the whole call.

    A server dribbling the body out just inside the socket timeout kept a 1s
    lookup alive for 24s, blowing straight through the upload's enrichment
    budget. The read loop must abandon the response instead.
    """
    import socket
    import threading
    import time

    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def _trickle() -> None:
        conn, _ = server.accept()
        conn.recv(65536)
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 20\r\n\r\n")
        for _ in range(20):
            time.sleep(0.3)  # under the socket timeout, so each recv succeeds
            try:
                conn.sendall(b"x")
            except OSError:
                break
        conn.close()

    threading.Thread(target=_trickle, daemon=True).start()

    started = time.monotonic()
    body = enrich._fetch(f"http://127.0.0.1:{port}/", 0.5)
    elapsed = time.monotonic() - started
    server.close()

    assert body is None
    assert elapsed < 2.0, f"deadline not enforced: {elapsed:.2f}s for a 0.5s timeout"


def test_crossref_abstract_is_capped(serve: Callable[..., list[str]]) -> None:
    """Nothing upstream bounds this, so a huge record must not land in the row."""
    serve(_crossref({"title": ["T"], "abstract": "<jats:p>" + "word " * 40000 + "</jats:p>"}))
    meta = enrich_by_doi("10.1234/x")
    assert meta is not None
    assert meta.abstract is not None
    assert len(meta.abstract) <= 4000


@pytest.mark.parametrize(
    "spelling",
    ["math.GT/0309136", "math.gt/0309136", "MATH.GT/0309136", "cs/0701001"],
)
def test_arxiv_accepts_legacy_ids_in_any_case(
    serve: Callable[..., list[str]], spelling: str
) -> None:
    """``find_arxiv_id`` and this normaliser have to agree on legacy id spelling.

    They did not: extraction lower-cased the subject class and the normaliser
    demanded it upper-case, so every legacy preprint silently skipped lookup.
    """
    serve(
        "<feed xmlns='http://www.w3.org/2005/Atom'><entry>"
        "<id>http://arxiv.org/abs/math.GT/0309136v1</id><title>A Legacy Paper</title>"
        "<published>2003-09-08T00:00:00Z</published></entry></feed>"
    )
    meta = enrich_by_arxiv(spelling)
    assert meta is not None
    assert meta.title == "A Legacy Paper"
