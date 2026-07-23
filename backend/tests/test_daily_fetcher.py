"""Tests for the daily arXiv sweep (:mod:`pharos.daily.fetcher`).

**No test here touches the network.** An autouse fixture replaces
``urllib.request.urlopen`` with a bomb, and every case either monkeypatches
``fetcher._http_get`` (the single choke point to the outside world) or installs
its own ``urlopen`` stub. That is a hard requirement rather than a nicety: this
suite has to pass behind the GFW and in a sandboxed CI runner, and a test that
quietly succeeds only when export.arxiv.org happens to answer is worse than no
test at all. A second autouse fixture neutralises ``_pause`` so the suite does
not actually observe arXiv's three-second politeness delay.

Two things get disproportionate attention below.

The first is :data:`DIRECTIONS`. It is not code — it is hand-tuned coverage the
user accumulated over months of daily runs, ported verbatim from ``arxiv_ws``.
A silent edit would not break anything loudly; it would just quietly change
what the user sees every morning. So the integrity tests assert exact list
equality, including the whitespace-sensitive entries ``"wam "`` and ``" dit "``
whose surrounding spaces are load-bearing.

The second is the error decoy. arXiv answers certain malformed queries with
HTTP 200 and a well-formed feed whose single entry is a pointer at its own
error documentation. A naive client stores a "paper" titled "Error"; the tests
below pin that it is dropped.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Sequence
from typing import Any
from xml.sax.saxutils import escape, quoteattr

import pytest
from pharos.daily import fetcher
from pharos.daily.directions import (
    ARXIV_CATEGORIES,
    DIRECTIONS,
    MAX_PAPERS_PER_DAY,
    direction_rank,
    match_directions,
)
from pharos.daily.fetcher import FetchedPaper, fetch_for_date

# --------------------------------------------------------------------------- #
# isolation
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if a test reaches the real ``urlopen``.

    Guards against a future edit adding a path that bypasses ``_http_get``:
    such a test would pass on a developer laptop and hang in CI.
    """

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a test attempted a live network call")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record instead of serving the inter-page and retry delays."""
    slept: list[float] = []
    monkeypatch.setattr(fetcher, "_pause", slept.append)
    return slept


# --------------------------------------------------------------------------- #
# Atom fixtures
# --------------------------------------------------------------------------- #

_TODAY = dt.date(2026, 7, 17)


def _entry(
    *,
    arxiv_id: str = "2607.08448v1",
    title: str = "A Vision-Language-Action Model",
    summary: str = "We train a vision-language-action policy.",
    published: str = "2026-07-17T17:59:59Z",
    authors: Sequence[str] = ("Ada Lovelace",),
    categories: Sequence[str] = ("cs.RO",),
    id_url: str | None = None,
    links: bool = True,
) -> str:
    """Render one ``<entry>`` shaped exactly like arXiv's Atom feed."""
    href = id_url if id_url is not None else f"http://arxiv.org/abs/{arxiv_id}"
    parts = [
        "  <entry>",
        f"    <id>{escape(href)}</id>",
        f"    <published>{escape(published)}</published>",
        f"    <updated>{escape(published)}</updated>",
        f"    <title>{escape(title)}</title>",
        f"    <summary>{escape(summary)}</summary>",
    ]
    parts += [f"    <author><name>{escape(name)}</name></author>" for name in authors]
    if links:
        parts += [
            f'    <link href={quoteattr(f"http://arxiv.org/abs/{arxiv_id}")}'
            ' rel="alternate" type="text/html"/>',
            f'    <link title="pdf" href={quoteattr(f"http://arxiv.org/pdf/{arxiv_id}")}'
            ' rel="related" type="application/pdf"/>',
        ]
    parts += [
        f'    <category term={quoteattr(term)} scheme="http://arxiv.org/schemas/atom"/>'
        for term in categories
    ]
    parts.append("  </entry>")
    return "\n".join(parts)


def _feed(*entries: str) -> bytes:
    """Wrap entries in the surrounding Atom document arXiv actually sends."""
    body = "\n".join(entries)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom">\n'
        "  <title>ArXiv Query</title>\n"
        f"{body}\n"
        "</feed>\n"
    ).encode()


#: The decoy: HTTP 200, valid Atom, and not a paper at all.
_ERROR_ENTRY = _entry(
    id_url="http://arxiv.org/api/errors#incorrect_id_format_for_2607.0844",
    title="Error",
    summary="incorrect id format for 2607.0844",
    authors=(),
    categories=(),
    links=False,
)


@pytest.fixture
def serve(monkeypatch: pytest.MonkeyPatch) -> Callable[..., list[str]]:
    """Serve canned pages from ``_http_get``; return the list of URLs asked for.

    Pages are consumed in order; once exhausted, an empty feed is returned,
    which is how the sweep learns to stop.
    """

    def _install(*pages: bytes | None) -> list[str]:
        requested: list[str] = []
        remaining = list(pages)

        def _fake_get(url: str, **kwargs: Any) -> bytes | None:
            requested.append(url)
            if remaining:
                return remaining.pop(0)
            return _feed()

        monkeypatch.setattr(fetcher, "_http_get", _fake_get)
        return requested

    return _install


def _ids(papers: Iterable[FetchedPaper]) -> list[str]:
    return [p.arxiv_id for p in papers]


# --------------------------------------------------------------------------- #
# DIRECTIONS integrity — this is the user's tuned coverage, not our config
# --------------------------------------------------------------------------- #


def test_all_seven_directions_survived_the_port() -> None:
    """The digest is organised by these seven names; losing one loses a topic."""
    assert list(DIRECTIONS) == [
        "VLA",
        "World Model",
        "WAM",
        "VGGT",
        "Agent",
        "Diffusion",
        "Multi-modal",
    ]


def test_categories_and_cap_survived_the_port() -> None:
    assert ARXIV_CATEGORIES == ["cs.RO", "cs.CV", "cs.LG", "cs.AI", "cs.CL", "cs.MM"]
    assert MAX_PAPERS_PER_DAY == 60


def test_keyword_count_is_unchanged() -> None:
    """A blunt canary: any prune or addition anywhere moves this number."""
    assert sum(len(kws) for kws in DIRECTIONS.values()) == 97


@pytest.mark.parametrize(
    ("direction", "keywords"),
    [
        # Spot-checks of lists small enough to pin exactly. The two acronym
        # entries are the whole reason this is here — see
        # test_short_acronyms_match_as_whole_words for why they are quoted.
        (
            "WAM",
            [
                "world action model",
                '"wam"',
                "action world model",
                "joint action prediction",
                "unified action model",
            ],
        ),
        (
            "World Model",
            [
                "world model",
                "world models",
                "neural simulator",
                "latent dynamics",
                "dynamics model",
                "video prediction",
                "video generation for robotics",
                "video world model",
                "genie",
                "navworld",
                "dreamerv3",
                "policy world model",
            ],
        ),
    ],
)
def test_keyword_lists_are_verbatim(direction: str, keywords: list[str]) -> None:
    assert DIRECTIONS[direction] == keywords


@pytest.mark.parametrize(
    ("direction", "keyword"),
    [
        ("VLA", "vision-language-action"),
        ("VLA", "openvla"),
        ("VLA", "rt-2"),
        ("VLA", "pi0"),
        ("WAM", '"wam"'),  # quoted: whole-word, so never inside "swam"
        ("VGGT", "dust3r"),
        ("VGGT", "gaussian splatting"),
        ("Agent", "agentic workflow"),
        ("Diffusion", '"dit"'),  # quoted: whole-word, so never inside "audit"
        ("Diffusion", "classifier-free guidance"),
        ("Multi-modal", "mllm"),
        ("Multi-modal", "embodied chain-of-thought"),
    ],
)
def test_specific_keywords_survived_verbatim(direction: str, keyword: str) -> None:
    assert keyword in DIRECTIONS[direction]


def test_short_acronyms_match_as_whole_words() -> None:
    """Pins the semantics of the two acronym keywords, ``"wam"`` and ``"dit"``.

    These two were originally written as ``"wam "`` and ``" dit "`` — padded
    with hand-placed spaces so the acronym would not match inside a longer
    word. That worked partially and failed in three ways, all asserted below:

    * ``"wam "`` is padded only on the right, so it fired on the *tail* of an
      unrelated word — "the fish **swam** upstream" was a WAM paper.
    * Padding requires the neighbour to be a space, so "WAM: world action
      model" and "we present wam." were both missed, punctuation being the
      normal thing to find next to an acronym in a title.
    * The padding is invisible in a text box and does not survive a
      round-trip through one. Once directions became user-editable, the first
      person to edit this direction would silently get bare ``dit``, which
      matches *credit*, *audit*, *edit*, *condition* — a flooded feed with no
      visible cause.

    So the intent is now written explicitly rather than encoded in whitespace:
    a keyword wrapped in double quotes matches as a whole word. This is a
    deliberate change to the user's tuned data, not a drift — it is strictly
    more accurate than what it replaces, and it is the only form that can be
    edited in a UI.
    """
    # Whole-word: fires on the word regardless of surrounding punctuation.
    assert match_directions("wam is our unified formulation.")[0] == "WAM"
    assert match_directions("WAM: world action model")[0] == "WAM"
    assert match_directions("we present wam.")[0] == "WAM"
    # ...and never inside a longer word, in either direction.
    assert match_directions("The fish swam upstream.") == (None, ())
    assert match_directions("wams are a new family.") == (None, ())
    # The same guarantee for the other acronym, which was the flooding risk.
    assert match_directions("use dit blocks for denoising")[0] == "Diffusion"
    for benign in ("a credit score", "we audit the model", "addition and condition"):
        assert match_directions(benign) == (None, ()), benign


def test_unquoted_keywords_still_match_as_substrings() -> None:
    """The quoting is opt-in; everything else keeps substring semantics.

    Most terms want it: "diffusion policy" should hit inside a longer phrase.
    Only the short acronyms, where a substring is actively wrong, are quoted.
    """
    assert match_directions("a diffusion policy for robot control")[0] == "Diffusion"
    assert match_directions("vision-language-action models for manipulation")[0] == "VLA"

    # " dit " is padded on both sides, so "audit" is correctly declined.
    assert match_directions("the audit found nothing") == (None, ())
    assert match_directions("We adopt a dit backbone for generation.")[0] == "Diffusion"


# --------------------------------------------------------------------------- #
# direction matching
# --------------------------------------------------------------------------- #


def test_single_keyword_selects_its_direction() -> None:
    domain, hits = match_directions("A vision-language-action model for tabletop tasks.")
    assert domain == "VLA"
    assert hits == ("vision-language-action",)


def test_most_distinct_hits_wins() -> None:
    """A paper that is *about* world models must not land under VLA.

    The text mentions ``robot policy`` once (one VLA hit) while carrying three
    distinct World Model keywords, which is exactly the case the rule exists
    for: incidental vocabulary must not outrank the subject matter.
    """
    text = (
        "Learning a world model for control. "
        "We fit latent dynamics from video prediction and distil it into a robot policy."
    )
    domain, hits = match_directions(text)
    assert domain == "World Model"
    assert set(hits) == {"world model", "latent dynamics", "video prediction"}


def test_no_match_returns_none_and_empty_hits() -> None:
    """The sweep is opt-in by topic, so an unmatched paper is dropped."""
    assert match_directions("A new proof of the four colour theorem.") == (None, ())


def test_ties_break_by_declaration_order_not_text_order() -> None:
    """One hit each: VLA is declared first, so it wins regardless of position.

    ``gaussian splatting`` appears *before* ``openvla`` in the text, so a
    text-order tie-break would answer VGGT.
    """
    domain, _ = match_directions("Gaussian splatting meets openvla for scene-level control.")
    assert domain == "VLA"
    assert direction_rank("VLA") < direction_rank("VGGT")


def test_matching_is_case_insensitive() -> None:
    assert match_directions("A VISION-LANGUAGE-ACTION Model")[0] == "VLA"


def test_direction_rank_orders_by_declaration_and_sinks_unknowns() -> None:
    assert direction_rank("VLA") == 0
    assert direction_rank("Multi-modal") == len(DIRECTIONS) - 1
    # Unknown and None both sort past every real direction.
    assert direction_rank("Astrology") == len(DIRECTIONS)
    assert direction_rank(None) == len(DIRECTIONS)


# --------------------------------------------------------------------------- #
# Atom parsing
# --------------------------------------------------------------------------- #


def test_parses_a_full_entry(serve: Callable[..., list[str]]) -> None:
    serve(
        _feed(
            _entry(
                arxiv_id="2607.08448v1",
                title="OpenVLA-2:  A Vision-Language-Action\n    Model",
                summary="We present a vision-language-action policy trained on 970k episodes.",
                authors=("Ada Lovelace", "Grace Hopper", "Alan Turing"),
                categories=("cs.RO", "cs.CV", "cs.LG"),
            )
        )
    )
    (paper,) = fetch_for_date(_TODAY)

    # The version marker is stripped: the dedup key is the paper, not v1.
    assert paper.arxiv_id == "2607.08448"
    # arXiv hard-wraps titles across source lines; the whitespace is collapsed.
    assert paper.title == "OpenVLA-2: A Vision-Language-Action Model"
    assert paper.authors == ("Ada Lovelace", "Grace Hopper", "Alan Turing")
    assert paper.categories == ("cs.RO", "cs.CV", "cs.LG")
    assert paper.published_at == dt.datetime(2026, 7, 17, 17, 59, 59, tzinfo=dt.UTC)
    assert paper.arxiv_url == "http://arxiv.org/abs/2607.08448v1"
    assert paper.pdf_url == "http://arxiv.org/pdf/2607.08448v1"
    assert paper.matched_domain == "VLA"
    assert "vision-language-action" in paper.matched_keywords


def test_matched_keywords_are_capped(serve: Callable[..., list[str]]) -> None:
    """At most six keywords are stored; the column is a hint, not an index."""
    serve(
        _feed(
            _entry(
                summary=(
                    "world model world models neural simulator latent dynamics "
                    "dynamics model video prediction genie dreamerv3"
                )
            )
        )
    )
    (paper,) = fetch_for_date(_TODAY)
    assert len(paper.matched_keywords) <= 6


def test_missing_links_fall_back_to_constructed_urls(serve: Callable[..., list[str]]) -> None:
    serve(_feed(_entry(arxiv_id="2607.00042v2", links=False)))
    (paper,) = fetch_for_date(_TODAY)
    assert paper.arxiv_url == "https://arxiv.org/abs/2607.00042"
    assert paper.pdf_url == "https://arxiv.org/pdf/2607.00042"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2607.08448v1", "2607.08448"),
        ("2607.08448v12", "2607.08448"),
        ("2607.08448", "2607.08448"),
        # Legacy ids embed a slash and a "v"; naive splitting mauls both.
        ("hep-ph/0701001v1", "hep-ph/0701001"),
        ("math.GT/0309136", "math.GT/0309136"),
    ],
)
def test_version_stripping_and_legacy_ids(
    serve: Callable[..., list[str]], raw: str, expected: str
) -> None:
    serve(_feed(_entry(arxiv_id=raw)))
    (paper,) = fetch_for_date(_TODAY)
    assert paper.arxiv_id == expected


def test_error_decoy_entry_is_rejected(serve: Callable[..., list[str]]) -> None:
    """HTTP 200 + valid Atom + an error pointer must not become a "paper".

    A client that trusts the envelope stores a paper titled "Error" and, with
    the reading layer wired up, pays an LLM to summarise it.
    """
    serve(_feed(_ERROR_ENTRY))
    assert fetch_for_date(_TODAY) == []


def test_error_decoy_does_not_suppress_real_papers(serve: Callable[..., list[str]]) -> None:
    serve(_feed(_ERROR_ENTRY, _entry(arxiv_id="2607.00001v1")))
    assert _ids(fetch_for_date(_TODAY)) == ["2607.00001"]


@pytest.mark.parametrize(
    "id_url",
    [
        "http://arxiv.org/abs/not-an-arxiv-id",
        "http://arxiv.org/abs/",
        "https://example.com/paper/1",
        "http://arxiv.org/abs/2607.084",  # too few digits to be real
    ],
)
def test_implausibly_shaped_ids_are_dropped(serve: Callable[..., list[str]], id_url: str) -> None:
    """The guard is structural, so it also catches decoys we have not seen yet."""
    serve(_feed(_entry(id_url=id_url)))
    assert fetch_for_date(_TODAY) == []


def test_entry_without_a_title_is_dropped(serve: Callable[..., list[str]]) -> None:
    serve(_feed(_entry(title="", summary="a vision-language-action policy")))
    assert fetch_for_date(_TODAY) == []


def test_entry_without_a_timestamp_is_dropped(serve: Callable[..., list[str]]) -> None:
    """No ``published`` means we cannot place it in the window, so we decline."""
    serve(_feed(_entry(published="")))
    assert fetch_for_date(_TODAY) == []


def test_unparseable_timestamp_is_dropped(serve: Callable[..., list[str]]) -> None:
    serve(_feed(_entry(published="last Thursday")))
    assert fetch_for_date(_TODAY) == []


def test_unmatched_paper_is_dropped(serve: Callable[..., list[str]]) -> None:
    serve(
        _feed(
            _entry(
                title="Sheaf Cohomology of Toric Varieties",
                summary="We compute cohomology groups for a class of toric varieties.",
            )
        )
    )
    assert fetch_for_date(_TODAY) == []


# --------------------------------------------------------------------------- #
# date windowing
# --------------------------------------------------------------------------- #


def test_paper_outside_the_window_is_dropped(serve: Callable[..., list[str]]) -> None:
    """arXiv's date filter is server-side and coarse; we re-check locally."""
    serve(_feed(_entry(arxiv_id="2607.00009v1", published="2026-07-15T09:00:00Z")))
    assert fetch_for_date(_TODAY, days=1) == []


def test_days_widens_the_window(serve: Callable[..., list[str]]) -> None:
    """days=3 means [date-2 .. date], so the 15th is now in range."""
    serve(_feed(_entry(arxiv_id="2607.00009v1", published="2026-07-15T09:00:00Z")))
    assert _ids(fetch_for_date(_TODAY, days=3)) == ["2607.00009"]


def test_window_boundaries_are_inclusive(serve: Callable[..., list[str]]) -> None:
    """Both endpoints belong to the window; a day must never fall through."""
    serve(
        _feed(
            _entry(arxiv_id="2607.00001v1", published="2026-07-17T23:59:00Z"),
            _entry(arxiv_id="2607.00002v1", published="2026-07-15T00:00:00Z"),
        )
    )
    assert set(_ids(fetch_for_date(_TODAY, days=3))) == {"2607.00001", "2607.00002"}


def test_paper_newer_than_the_window_is_skipped(serve: Callable[..., list[str]]) -> None:
    """Submissions landing after the window are not ours to report yet."""
    serve(
        _feed(
            _entry(arxiv_id="2607.00010v1", published="2026-07-19T09:00:00Z"),
            _entry(arxiv_id="2607.00011v1", published="2026-07-17T09:00:00Z"),
        )
    )
    assert _ids(fetch_for_date(_TODAY)) == ["2607.00011"]


@pytest.mark.parametrize("days", [0, -1])
def test_nonsensical_window_is_a_programming_error(days: int) -> None:
    """Unlike a network failure, this is our bug, so it raises rather than degrades."""
    with pytest.raises(ValueError, match="days must be >= 1"):
        fetch_for_date(_TODAY, days=days)


def test_nonsensical_cap_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="max_papers must be >= 1"):
        fetch_for_date(_TODAY, max_papers=0)


# --------------------------------------------------------------------------- #
# dedup
# --------------------------------------------------------------------------- #


def test_exclude_ids_removes_already_seen_papers(serve: Callable[..., list[str]]) -> None:
    serve(
        _feed(
            _entry(arxiv_id="2607.00001v1"),
            _entry(arxiv_id="2607.00002v1"),
        )
    )
    assert _ids(fetch_for_date(_TODAY, exclude_ids=["2607.00001"])) == ["2607.00002"]


def test_exclude_ids_are_version_normalised(serve: Callable[..., list[str]]) -> None:
    """A caller passing a versioned id still suppresses the paper.

    The stored column is version-stripped, but nothing stops a caller handing
    back a raw id, and a dedup key that depends on the caller's spelling is not
    a dedup key.
    """
    serve(_feed(_entry(arxiv_id="2607.00001v1")))
    assert fetch_for_date(_TODAY, exclude_ids=["2607.00001v9"]) == []


def test_blank_exclude_ids_are_ignored(serve: Callable[..., list[str]]) -> None:
    serve(_feed(_entry(arxiv_id="2607.00001v1")))
    assert _ids(fetch_for_date(_TODAY, exclude_ids=["", "  "])) == ["2607.00001"]


def test_duplicates_within_one_sweep_are_collapsed(serve: Callable[..., list[str]]) -> None:
    """arXiv paging repeats entries when submissions land mid-sweep."""
    serve(
        _feed(_entry(arxiv_id="2607.00001v1")),
        _feed(_entry(arxiv_id="2607.00001v1"), _entry(arxiv_id="2607.00002v1")),
    )
    assert sorted(_ids(fetch_for_date(_TODAY))) == ["2607.00001", "2607.00002"]


# --------------------------------------------------------------------------- #
# capping and ordering
# --------------------------------------------------------------------------- #


def test_cap_keeps_the_most_recent(serve: Callable[..., list[str]]) -> None:
    """Over the cap, recency decides — a digest is a "what's new" list."""
    serve(
        _feed(
            _entry(arxiv_id="2607.00001v1", published="2026-07-14T09:00:00Z"),
            _entry(arxiv_id="2607.00002v1", published="2026-07-15T09:00:00Z"),
            _entry(arxiv_id="2607.00003v1", published="2026-07-16T09:00:00Z"),
            _entry(arxiv_id="2607.00004v1", published="2026-07-17T09:00:00Z"),
        )
    )
    papers = fetch_for_date(_TODAY, days=5, max_papers=2)
    assert _ids(papers) == ["2607.00004", "2607.00003"]


def test_results_are_ordered_by_declared_direction_then_recency(
    serve: Callable[..., list[str]],
) -> None:
    """The user's declared interest order is the display order, not recency.

    The Diffusion paper is newer, but VLA is declared first, so it leads.
    """
    serve(
        _feed(
            _entry(
                arxiv_id="2607.00050v1",
                title="Guided Diffusion for Image Synthesis",
                summary="We study classifier-free guidance in a diffusion model.",
                published="2026-07-17T20:00:00Z",
            ),
            _entry(
                arxiv_id="2607.00051v1",
                title="OpenVLA at Scale",
                summary="A vision-language-action policy for tabletop rearrangement.",
                published="2026-07-17T01:00:00Z",
            ),
            _entry(
                arxiv_id="2607.00052v1",
                title="Instruction-Following Manipulation",
                summary="An instruction-following manipulation system.",
                published="2026-07-17T18:00:00Z",
            ),
        )
    )
    papers = fetch_for_date(_TODAY)
    assert [p.matched_domain for p in papers] == ["VLA", "VLA", "Diffusion"]
    # Newest first inside a direction.
    assert _ids(papers) == ["2607.00052", "2607.00051", "2607.00050"]


# --------------------------------------------------------------------------- #
# query construction
# --------------------------------------------------------------------------- #


def test_query_asks_for_every_category_newest_first(serve: Callable[..., list[str]]) -> None:
    urls = serve(_feed(_entry()))
    fetch_for_date(_TODAY)

    url = urls[0]
    for category in ARXIV_CATEGORIES:
        assert f"cat:{category}" in url
    assert "sortBy=submittedDate" in url
    assert "sortOrder=descending" in url
    assert "start=0" in url
    # The window is expressed server-side; without it a backfill would have to
    # page down through the whole archive.
    assert "submittedDate:[202607170000+TO+202607172359]" in url


def test_query_window_widens_with_days(serve: Callable[..., list[str]]) -> None:
    urls = serve(_feed(_entry()))
    fetch_for_date(_TODAY, days=3)
    assert "submittedDate:[202607150000+TO+202607172359]" in urls[0]


def test_pages_are_spaced_by_arxivs_requested_delay(
    serve: Callable[..., list[str]], _no_sleeping: list[float]
) -> None:
    """arXiv asks for one request per three seconds; we are a background job."""
    serve(
        _feed(_entry(arxiv_id="2607.00001v1")),
        _feed(_entry(arxiv_id="2607.00002v1")),
    )
    fetch_for_date(_TODAY)
    assert _no_sleeping and all(delay >= 3.0 for delay in _no_sleeping)


def test_paging_stops_on_an_empty_page(serve: Callable[..., list[str]]) -> None:
    urls = serve(_feed(_entry(arxiv_id="2607.00001v1")), _feed())
    fetch_for_date(_TODAY)
    assert len(urls) == 2


# --------------------------------------------------------------------------- #
# failure is not exceptional
# --------------------------------------------------------------------------- #


def test_unreachable_arxiv_returns_empty_rather_than_raising(
    serve: Callable[..., list[str]],
) -> None:
    """A scheduler that dies on a bad sweep is worse than an empty digest."""
    serve(None)
    assert fetch_for_date(_TODAY) == []


def test_malformed_xml_returns_empty(serve: Callable[..., list[str]]) -> None:
    serve(b"<feed><entry>truncated mid-document")
    assert fetch_for_date(_TODAY) == []


def test_html_error_page_returns_empty(serve: Callable[..., list[str]]) -> None:
    """A proxy or captive portal answering HTML must not crash the sweep."""
    serve(b"<html><body>503 Service Unavailable</body></html>")
    assert fetch_for_date(_TODAY) == []


def test_empty_day_returns_empty(serve: Callable[..., list[str]]) -> None:
    """Weekends and announcement gaps are ordinary, not errors."""
    serve(_feed())
    assert fetch_for_date(_TODAY) == []


def test_a_failed_page_keeps_what_earlier_pages_produced(
    serve: Callable[..., list[str]],
) -> None:
    """Partial results beat none: tomorrow's sweep will pick up the remainder."""
    serve(_feed(_entry(arxiv_id="2607.00001v1")), None)
    assert _ids(fetch_for_date(_TODAY)) == ["2607.00001"]


# --------------------------------------------------------------------------- #
# the HTTP layer itself
# --------------------------------------------------------------------------- #


class _FakeResponse:
    """Minimal stand-in for the object ``urlopen`` yields."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self, size: int = -1) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://example.invalid", code, "nope", {}, None)  # type: ignore[arg-type]


def test_http_get_identifies_itself_to_arxiv(monkeypatch: pytest.MonkeyPatch) -> None:
    """arXiv asks clients to be contactable rather than blanket-blocking an IP."""
    seen: list[urllib.request.Request] = []

    def _fake_urlopen(request: urllib.request.Request, **kwargs: Any) -> _FakeResponse:
        seen.append(request)
        return _FakeResponse(b"ok")

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    assert fetcher._http_get("http://example.invalid/query") == b"ok"

    agent = seen[0].get_header("User-agent")
    assert agent is not None
    assert "Pharos" in agent
    assert "github.com/hyyyyyyz/Pharos" in agent


def test_http_get_retries_a_5xx_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, _no_sleeping: list[float]
) -> None:
    attempts: list[int] = []

    def _fake_urlopen(request: urllib.request.Request, **kwargs: Any) -> _FakeResponse:
        attempts.append(1)
        if len(attempts) < 3:
            raise _http_error(503)
        return _FakeResponse(b"finally")

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    assert fetcher._http_get("http://example.invalid/query") == b"finally"
    assert len(attempts) == 3
    # Exponential, so the second wait is longer than the first.
    assert _no_sleeping == [5.0, 10.0]


def test_http_get_gives_up_after_bounded_attempts(
    monkeypatch: pytest.MonkeyPatch, _no_sleeping: list[float]
) -> None:
    attempts: list[int] = []

    def _fake_urlopen(request: urllib.request.Request, **kwargs: Any) -> _FakeResponse:
        attempts.append(1)
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    assert fetcher._http_get("http://example.invalid/query") is None
    assert len(attempts) == 3  # bounded: if arXiv is down, tomorrow can have it


@pytest.mark.parametrize("code", [400, 403, 404, 429])
def test_http_get_never_retries_a_4xx(monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    """An identical retry of a request the server rejected cannot succeed.

    Note this includes 429: the source project retried rate limits, but for an
    unattended job it is better to yield what we have and wait for tomorrow
    than to keep pushing on a free service that just asked us to stop.
    """
    attempts: list[int] = []

    def _fake_urlopen(request: urllib.request.Request, **kwargs: Any) -> _FakeResponse:
        attempts.append(1)
        raise _http_error(code)

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    assert fetcher._http_get("http://example.invalid/query") is None
    assert len(attempts) == 1


def test_http_get_bounds_the_response_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hostile or broken endpoint must not be able to exhaust memory."""
    requested: list[int] = []

    class _Recorder(_FakeResponse):
        def read(self, size: int = -1) -> bytes:
            requested.append(size)
            return b"ok"

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Recorder(b"ok"))
    fetcher._http_get("http://example.invalid/query")
    assert requested == [fetcher._MAX_RESPONSE_BYTES]


# --------------------------------------------------------------------------- #
# the parsed record itself
# --------------------------------------------------------------------------- #


def test_fetched_paper_is_immutable(serve: Callable[..., list[str]]) -> None:
    """A record of what arXiv said is not for downstream code to edit."""
    serve(_feed(_entry()))
    (paper,) = fetch_for_date(_TODAY)
    with pytest.raises(dataclasses.FrozenInstanceError):
        paper.title = "something else"  # type: ignore[misc]


def test_parse_entry_is_usable_standalone() -> None:
    """The parser is exercised directly, without pretending to be a feed."""
    root = ET.fromstring(_feed(_entry(arxiv_id="2607.12345v3")))
    entry = root.find("{http://www.w3.org/2005/Atom}entry")
    assert entry is not None
    paper = fetcher._parse_entry(entry)
    assert paper is not None
    assert paper.arxiv_id == "2607.12345"
    # The match fields are filled in by the sweep, not the parser.
    assert paper.matched_domain is None
    assert paper.matched_keywords == ()
