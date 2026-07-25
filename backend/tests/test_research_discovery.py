"""Provider parsing, cross-source coverage, deduplication and honest extraction."""

from __future__ import annotations

import json
import urllib.parse

from pharos.services import discovery
from pharos.services.discovery import DiscoveredPaper


def _paper(
    source: str,
    external_id: str,
    title: str,
    *,
    doi: str | None = None,
    abstract: str = "",
) -> DiscoveredPaper:
    return DiscoveredPaper(
        source=source,
        external_id=external_id,
        title=title,
        doi=doi,
        abstract=abstract,
        sources=(source,),
        source_ids=((source, external_id),),
    )


def test_dedup_merges_same_title_when_only_one_provider_has_a_doi() -> None:
    arxiv = _paper("arxiv", "2607.00001", "A Unified Vision-Language-Action Model")
    openalex = _paper(
        "openalex",
        "W123",
        "A Unified Vision Language Action Model",
        doi="10.1000/vla.1",
        abstract="A longer registry abstract.",
    )

    (merged,) = discovery.deduplicate([arxiv, openalex], 20)

    assert merged.doi == "10.1000/vla.1"
    assert merged.abstract == "A longer registry abstract."
    assert merged.sources == ("arxiv", "openalex")
    assert dict(merged.source_ids) == {"arxiv": "2607.00001", "openalex": "W123"}
    # Persistence uses the strongest alias discovered after merging.
    assert discovery.dedup_key(merged) == "doi:10.1000/vla.1"


def test_arxiv_natural_query_uses_term_level_and_not_one_exact_phrase() -> None:
    expression = discovery._arxiv_search_expression(  # noqa: SLF001 - query contract
        "KV cache compression for video generation KV"
    )
    assert expression == (
        'all:"KV" AND all:"cache" AND all:"compression" '
        'AND all:"video" AND all:"generation"'
    )


def test_arxiv_fetch_url_preserves_an_explicit_phrase(monkeypatch) -> None:
    captured = ""

    def fake_get(url: str) -> bytes:
        nonlocal captured
        captured = url
        return b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'

    monkeypatch.setattr(discovery, "_http_get", fake_get)
    discovery.fetch_arxiv('"vision language action" robot learning', 5)
    query = urllib.parse.parse_qs(urllib.parse.urlparse(captured).query)["search_query"][0]
    assert query == (
        'all:"vision language action" AND all:"robot" AND all:"learning"'
    )


def test_discovery_interleaves_sources_before_applying_limit(monkeypatch) -> None:
    monkeypatch.setattr(
        discovery,
        "fetch_arxiv",
        lambda query, limit: [_paper("arxiv", f"a{i}", f"arXiv {i}") for i in range(limit)],
    )
    monkeypatch.setattr(
        discovery,
        "fetch_openalex",
        lambda query, limit: [
            _paper("openalex", f"o{i}", f"OpenAlex {i}") for i in range(limit)
        ],
    )

    batch = discovery.discover("robot learning", ["arxiv", "openalex"], 4)

    assert [paper.source for paper in batch.papers] == [
        "arxiv",
        "openalex",
        "arxiv",
        "openalex",
    ]


def test_one_provider_failure_keeps_real_results_and_names_the_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        discovery,
        "fetch_arxiv",
        lambda query, limit: [_paper("arxiv", "a1", "A real paper")],
    )

    def fail(query: str, limit: int):
        raise discovery.SourceUnavailable("timed out")

    monkeypatch.setattr(discovery, "fetch_openalex", fail)

    batch = discovery.discover("robot learning", ["arxiv", "openalex"], 10)

    assert [paper.title for paper in batch.papers] == ["A real paper"]
    assert batch.errors == {"openalex": "timed out"}


def test_openalex_abstract_positions_are_reconstructed() -> None:
    assert discovery._openalex_abstract(  # noqa: SLF001 - parser contract
        {"world": [1], "Hello": [0], "again": [2]}
    ) == "Hello world again"


def test_openalex_parser_keeps_only_returned_facts(monkeypatch) -> None:
    payload = {
        "results": [
            {
                "id": "https://openalex.org/W42",
                "doi": "https://doi.org/10.1000/test",
                "display_name": "Mechanistic Robot Learning",
                "publication_year": 2026,
                "cited_by_count": 9,
                "authorships": [{"author": {"display_name": "Ada Lovelace"}}],
                "abstract_inverted_index": {"We": [0], "propose": [1], "it.": [2]},
                "primary_location": {
                    "landing_page_url": "https://example.test/work",
                    "pdf_url": "https://example.test/work.pdf",
                    "source": {"display_name": "Robotics Letters"},
                },
            }
        ]
    }
    monkeypatch.setattr(discovery, "_http_get", lambda url: json.dumps(payload).encode())

    (paper,) = discovery.fetch_openalex("robot", 5)

    assert paper.external_id == "W42"
    assert paper.authors == ("Ada Lovelace",)
    assert paper.abstract == "We propose it."
    assert paper.doi == "10.1000/test"
    assert paper.citation_count == 9


def test_openalex_url_restricts_search_to_title_and_abstract(monkeypatch) -> None:
    captured = ""

    def fake_get(url: str) -> bytes:
        nonlocal captured
        captured = url
        return b'{"results":[]}'

    monkeypatch.setattr(discovery, "_http_get", fake_get)
    discovery.fetch_openalex("KV cache compression for video generation KV", 7)

    params = urllib.parse.parse_qs(urllib.parse.urlparse(captured).query)
    assert "search" not in params
    assert params["filter"] == [
        "title_and_abstract.search:KV cache compression video generation"
    ]
    assert params["per-page"] == ["7"]
    assert "abstract_inverted_index" in params["select"][0]


def test_rule_summary_prefers_concrete_numeric_results_over_early_hype() -> None:
    abstract = (
        "Vision-language-action models have demonstrated strong potential. "
        "We introduce a token routing method using sparse action experts. "
        "Experiments show 44.6% success, outperforming the baseline by 11.7 points."
    )

    summary = discovery.rule_summary("Sparse VLA", abstract)

    assert summary["method"] == "We introduce a token routing method using sparse action experts."
    assert summary["results"].startswith("Experiments show 44.6%")
    assert summary["analysis_mode"] == "rules"
    assert "no LLM analysis" in str(summary["analysis_warning"])


def test_rule_summary_does_not_invent_an_unstated_limitation() -> None:
    summary = discovery.rule_summary(
        "A Method", "We propose a calibrated controller. It improves success by 5%."
    )
    assert summary["limitations"] == ""
    assert summary["summary_zh"] == ""
    assert summary["contribution"] == ""


def test_rule_summary_does_not_confuse_an_achievement_goal_with_results() -> None:
    abstract = (
        "We present a training recipe to achieve general-purpose robotic control. "
        "Our extensive evaluation across 6k trials shows strong generalization to novel tasks."
    )
    summary = discovery.rule_summary("RT-2", abstract)
    assert summary["results"].startswith("Our extensive evaluation across 6k trials")


def test_rule_summary_recognises_then_propose_and_first_claims_as_core_tricks() -> None:
    then_propose = discovery.rule_summary(
        "Cache Method",
        "KV caches are expensive. We then propose a layer-aware compression controller.",
    )
    first_claim = discovery.rule_summary(
        "Video Cache",
        (
            "Video generation remains costly. "
            "MuKV is the first multi-grained cache for streaming video."
        ),
    )
    assert then_propose["core_trick"].startswith("We then propose")
    assert first_claim["core_trick"].startswith("MuKV is the first")


def test_rule_summary_repairs_missing_openalex_sentence_spaces() -> None:
    summary = discovery.rule_summary(
        "Fast Serving",
        (
            "Serving latency is measured by TTFT).To reduce it, we then propose sparse caching."
            "Experimental evaluation across 6k trials shows 18% lower latency."
        ),
    )
    assert summary["core_trick"].startswith("To reduce it, we then propose")
    assert summary["results"].startswith("Experimental evaluation across 6k trials")
