"""Live academic discovery across arXiv and OpenAlex.

This module has no database dependency. It fetches provider records, normalises
them into one honest shape, deduplicates the overlap, and extracts a small
*rule-based* reading from the abstract. The extraction is deliberately marked
as heuristic and leaves unknown fields blank: an unavailable LLM is not a
license to invent a result or limitation the authors never reported.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass

ARXIV = "arxiv"
OPENALEX = "openalex"
SOURCES = frozenset({ARXIV, OPENALEX})

_ARXIV_API = "https://export.arxiv.org/api/query"
_OPENALEX_API = "https://api.openalex.org/works"
_USER_AGENT = "Pharos/0.1 (research discovery; +https://github.com/hyyyyyyz/Pharos)"
_TIMEOUT_SECONDS = 20.0
_MAX_RESPONSE_BYTES = 12 * 1024 * 1024

_ATOM = "http://www.w3.org/2005/Atom"
_ARXIV = "http://arxiv.org/schemas/atom"
_WS = re.compile(r"\s+")
_TITLE_TOKEN = re.compile(r"[^\w]+", re.UNICODE)
_SENTENCE = re.compile(r"(?<=[.!?。！？)])\s+(?=[A-Z0-9\u4e00-\u9fff])")
_MISSING_SENTENCE_SPACE = re.compile(r"(?<=[.!?)])(?=[A-Z])")
_ARXIV_VERSION = re.compile(r"v\d+$", re.IGNORECASE)
_QUERY_PART = re.compile(r'"([^"]+)"|(\S+)')
_QUERY_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "of",
        "on",
        "or",
        "the",
        "to",
        "using",
        "with",
    }
)
_MAX_ARXIV_TERMS = 8

_CORE_CUES = (
    "we then propose",
    "in this work, we propose",
    "in this work we propose",
    "we propose",
    "we introduce",
    "we present",
    "we develop",
    "this paper proposes",
    "is the first",
    "we are the first",
    "novel ",
    "本文提出",
)
_METHOD_CUES = (
    "we then propose",
    "in this work, we propose",
    "in this work we propose",
    "we propose",
    "we introduce",
    "we present",
    "we develop",
    "we train",
    "we use",
    "using ",
    "based on",
    "consists of",
    "via ",
    "方法",
    "框架",
)
_RESULT_CUES = (
    "outperform",
    "achieve",
    "improve",
    "experiments show",
    "results show",
    "state-of-the-art",
    "demonstrate",
    "实验表明",
    "结果显示",
)
_LIMIT_CUES = (
    "limitation",
    "however",
    "fails to",
    "restricted to",
    "future work",
    "remains challenging",
    "局限",
    "然而",
)


class SourceUnavailable(Exception):
    """A provider could not return a usable response."""


@dataclass(frozen=True)
class DiscoveredPaper:
    source: str
    external_id: str
    title: str
    authors: tuple[str, ...] = ()
    abstract: str = ""
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    url: str | None = None
    pdf_url: str | None = None
    citation_count: int | None = None
    sources: tuple[str, ...] = ()
    source_ids: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class DiscoveryBatch:
    papers: tuple[DiscoveredPaper, ...]
    errors: dict[str, str]


def _clean(value: object, limit: int = 20_000) -> str:
    if not isinstance(value, str):
        return ""
    return _WS.sub(" ", value).strip()[:limit]


def _doi(value: object) -> str | None:
    cleaned = _clean(value, 256)
    if not cleaned:
        return None
    cleaned = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^doi:\s*", "", cleaned, flags=re.IGNORECASE)
    return cleaned.lower() or None


def _http_get(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json, application/atom+xml", "User-Agent": _USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
            data = response.read(_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise SourceUnavailable(f"HTTP {exc.code}") from exc
    except Exception as exc:
        # The concrete socket/TLS error is useful to an operator, but bounded so
        # a provider cannot inject a giant error page into our persisted state.
        raise SourceUnavailable(_clean(str(exc), 500) or type(exc).__name__) from exc
    if len(data) > _MAX_RESPONSE_BYTES:
        raise SourceUnavailable("response exceeded the 12 MiB safety limit")
    return data


def _content_terms(query: str) -> list[str]:
    """Bounded, de-duplicated content terms shared by both providers."""
    terms: list[str] = []
    seen: set[str] = set()
    for match in _QUERY_PART.finditer(query):
        raw = match.group(1) or match.group(2) or ""
        phrase = _clean(raw, 100).strip(".,;:!?()[]{}")
        key = phrase.casefold()
        if not phrase or key in _QUERY_STOPWORDS or key in seen:
            continue
        seen.add(key)
        # arXiv's query parser uses quotes structurally; discard embedded ones
        # left by malformed input rather than allowing them to rewrite syntax.
        terms.append(phrase.replace('"', ""))
        if len(terms) == _MAX_ARXIV_TERMS:
            break
    if not terms:
        terms = [_clean(query, 100).replace('"', "") or "research"]
    return terms


def _arxiv_search_expression(query: str) -> str:
    """Turn a natural research brief into arXiv's term-level AND query.

    Wrapping the entire input in one pair of quotes asks arXiv for an exact
    phrase, so a useful brief such as “KV cache compression video generation”
    can return zero despite dozens of papers containing all four concepts. User
    quotes are retained as phrases; ordinary text is split into bounded,
    de-duplicated content terms.
    """
    terms = _content_terms(query)
    return " AND ".join(f'all:"{term}"' for term in terms)


def fetch_arxiv(query: str, limit: int) -> list[DiscoveredPaper]:
    params = urllib.parse.urlencode(
        {
            "search_query": _arxiv_search_expression(query),
            "start": 0,
            "max_results": limit,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
    )
    data = _http_get(f"{_ARXIV_API}?{params}")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise SourceUnavailable("malformed Atom response") from exc

    papers: list[DiscoveredPaper] = []
    ns = {"atom": _ATOM, "arxiv": _ARXIV}
    for entry in root.findall("atom:entry", ns):
        title = _clean(entry.findtext("atom:title", default="", namespaces=ns), 2_000)
        id_url = _clean(entry.findtext("atom:id", default="", namespaces=ns), 1_024)
        if not title or not id_url or "/api/errors" in id_url:
            continue
        external_id = id_url.partition("/abs/")[2] or id_url.rsplit("/", 1)[-1]
        external_id = _ARXIV_VERSION.sub("", external_id)
        abstract = _clean(entry.findtext("atom:summary", default="", namespaces=ns))
        authors = tuple(
            name
            for node in entry.findall("atom:author", ns)
            if (name := _clean(node.findtext("atom:name", default="", namespaces=ns), 300))
        )
        categories = [
            _clean(node.attrib.get("term"), 128) for node in entry.findall("atom:category", ns)
        ]
        venue = _clean(entry.findtext("arxiv:journal_ref", default="", namespaces=ns), 512)
        doi = _doi(entry.findtext("arxiv:doi", default="", namespaces=ns))
        published = _clean(entry.findtext("atom:published", default="", namespaces=ns), 64)
        year = int(published[:4]) if published[:4].isdigit() else None
        pdf_url = next(
            (
                _clean(link.attrib.get("href"), 1_024)
                for link in entry.findall("atom:link", ns)
                if link.attrib.get("title") == "pdf"
                or link.attrib.get("type") == "application/pdf"
            ),
            None,
        )
        papers.append(
            DiscoveredPaper(
                source=ARXIV,
                external_id=external_id,
                title=title,
                authors=authors,
                abstract=abstract,
                year=year,
                venue=venue or (categories[0] if categories else "arXiv"),
                doi=doi,
                url=id_url,
                pdf_url=pdf_url or f"https://arxiv.org/pdf/{external_id}",
                sources=(ARXIV,),
                source_ids=((ARXIV, external_id),),
            )
        )
    return papers


def _openalex_abstract(inverted: object) -> str:
    if not isinstance(inverted, dict):
        return ""
    placed: list[tuple[int, str]] = []
    for word, positions in inverted.items():
        if not isinstance(word, str) or not isinstance(positions, list):
            continue
        for position in positions:
            if isinstance(position, int) and not isinstance(position, bool) and position >= 0:
                placed.append((position, word))
    placed.sort(key=lambda item: item[0])
    return _clean(" ".join(word for _, word in placed))


def fetch_openalex(query: str, limit: int) -> list[DiscoveredPaper]:
    # ``search=`` ranges beyond title/abstract and ranks generic cache eviction
    # and on-chip-memory papers for a KV-cache/video brief. Restricting the
    # filter to the fields that describe the paper keeps the second provider a
    # corroborating literature source instead of a source of topical noise.
    search_text = " ".join(_content_terms(query))
    params = urllib.parse.urlencode(
        {
            "filter": f"title_and_abstract.search:{search_text}",
            "per-page": limit,
            "select": (
                "id,doi,title,publication_year,authorships,"
                "abstract_inverted_index,primary_location,cited_by_count"
            ),
        }
    )
    data = _http_get(f"{_OPENALEX_API}?{params}")
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceUnavailable("malformed JSON response") from exc
    rows = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise SourceUnavailable("JSON response has no results array")

    papers: list[DiscoveredPaper] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = _clean(row.get("display_name") or row.get("title"), 2_000)
        id_url = _clean(row.get("id"), 1_024)
        if not title or not id_url:
            continue
        external_id = id_url.rsplit("/", 1)[-1]
        authors: list[str] = []
        for authorship in row.get("authorships") or []:
            if not isinstance(authorship, dict):
                continue
            author = authorship.get("author")
            if isinstance(author, dict):
                name = _clean(author.get("display_name"), 300)
                if name:
                    authors.append(name)
        location = row.get("primary_location")
        source = location.get("source") if isinstance(location, dict) else None
        venue = _clean(source.get("display_name") if isinstance(source, dict) else None, 512)
        pdf_url = _clean(location.get("pdf_url") if isinstance(location, dict) else None, 1_024)
        landing = _clean(
            location.get("landing_page_url") if isinstance(location, dict) else None, 1_024
        )
        cited = row.get("cited_by_count")
        citation_count = cited if isinstance(cited, int) and not isinstance(cited, bool) else None
        year_value = row.get("publication_year")
        year = (
            year_value
            if isinstance(year_value, int) and not isinstance(year_value, bool)
            else None
        )
        papers.append(
            DiscoveredPaper(
                source=OPENALEX,
                external_id=external_id,
                title=title,
                authors=tuple(authors),
                abstract=_openalex_abstract(row.get("abstract_inverted_index")),
                year=year,
                venue=venue or None,
                doi=_doi(row.get("doi")),
                url=landing or id_url,
                pdf_url=pdf_url or None,
                citation_count=citation_count,
                sources=(OPENALEX,),
                source_ids=((OPENALEX, external_id),),
            )
        )
    return papers


def dedup_key(paper: DiscoveredPaper) -> str:
    if paper.doi:
        return f"doi:{paper.doi.lower()}"
    normalised = _title_key(paper)
    if normalised:
        return f"title:{normalised[:750]}"
    return f"source:{paper.source}:{paper.external_id}"


def _title_key(paper: DiscoveredPaper) -> str:
    return _TITLE_TOKEN.sub("", paper.title.casefold())


def _merge(first: DiscoveredPaper, later: DiscoveredPaper) -> DiscoveredPaper:
    authors = tuple(dict.fromkeys((*first.authors, *later.authors)))
    sources = tuple(dict.fromkeys((*first.sources, *later.sources)))
    source_ids = tuple(dict((*first.source_ids, *later.source_ids)).items())
    # Prefer the richer abstract and otherwise preserve the first provider's
    # relevance ordering. Citation count is safe to take from OpenAlex even when
    # arXiv supplied the canonical metadata.
    return dataclasses.replace(
        first,
        authors=authors,
        abstract=max((first.abstract, later.abstract), key=len),
        year=first.year or later.year,
        venue=first.venue or later.venue,
        doi=first.doi or later.doi,
        url=first.url or later.url,
        pdf_url=first.pdf_url or later.pdf_url,
        citation_count=max(
            (value for value in (first.citation_count, later.citation_count) if value is not None),
            default=None,
        ),
        sources=sources,
        source_ids=source_ids,
    )


def deduplicate(papers: list[DiscoveredPaper], limit: int) -> list[DiscoveredPaper]:
    # Use both aliases at comparison time. A very common cross-source shape is
    # OpenAlex returning the DOI while arXiv has the identical title but no DOI;
    # choosing one primary key up front makes those two records impossible to
    # meet. The search is bounded at 100 raw rows, so an O(n²) scan is simpler
    # and safer than maintaining alias groups that must themselves be merged.
    canonical: list[DiscoveredPaper] = []
    for paper in papers:
        title_key = _title_key(paper)
        match = next(
            (
                index
                for index, existing in enumerate(canonical)
                if (title_key and title_key == _title_key(existing))
                or (paper.doi and existing.doi and paper.doi.lower() == existing.doi.lower())
            ),
            None,
        )
        if match is None:
            canonical.append(paper)
        else:
            canonical[match] = _merge(canonical[match], paper)
    return canonical[:limit]


def discover(query: str, sources: list[str], limit: int) -> DiscoveryBatch:
    """Fetch requested providers in parallel and return partial success honestly."""
    fetchers = {ARXIV: fetch_arxiv, OPENALEX: fetch_openalex}
    by_source: dict[str, list[DiscoveredPaper]] = {}
    errors: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(sources)) as executor:
        pending = {executor.submit(fetchers[source], query, limit): source for source in sources}
        for future in concurrent.futures.as_completed(pending):
            source = pending[future]
            try:
                by_source[source] = future.result()
            except Exception as exc:
                errors[source] = _clean(str(exc), 500) or type(exc).__name__

    # Interleave providers rather than concatenating them. If arXiv returns a
    # full ``limit`` and is flattened first, truncation makes a nominally
    # multi-source search indistinguishable from an arXiv-only one. Round-robin
    # preserves each provider's own relevance order while ensuring coverage.
    flattened: list[DiscoveredPaper] = []
    longest = max((len(by_source.get(source, [])) for source in sources), default=0)
    for index in range(longest):
        for source in sources:
            rows = by_source.get(source, [])
            if index < len(rows):
                flattened.append(rows[index])
    return DiscoveryBatch(tuple(deduplicate(flattened, limit)), errors)


def _sentences(abstract: str) -> list[str]:
    text = _clean(abstract)
    if not text:
        return []
    # OpenAlex reconstructs an abstract from an inverted index. Some records
    # contain tokens such as ``TTFT).To`` or ``systems.Experimental`` where the
    # source omitted inter-sentence whitespace. Repair only punctuation→capital
    # boundaries before splitting; the text itself remains extractive.
    text = _MISSING_SENTENCE_SPACE.sub(" ", text)
    parts = [_clean(piece, 1_000) for piece in _SENTENCE.split(text)]
    return [part for part in parts if part]


def _matching_sentence(sentences: list[str], cues: tuple[str, ...]) -> str:
    # Cue order expresses specificity. Searching all sentences for the strong
    # cue before considering the weak one avoids an early “our approach shows
    # potential” sentence stealing the slot from a later concrete method/result.
    for cue in cues:
        for sentence in sentences:
            if cue in sentence.casefold():
                return sentence
    return ""


def _result_sentence(sentences: list[str]) -> str:
    best = ""
    best_score = 0
    for sentence in sentences:
        lower = sentence.casefold()
        score = 0
        if re.search(r"\b\d+(?:\.\d+)?\s*(?:%|percent|points?)\b", lower):
            score += 8
        elif re.search(r"\b\d+(?:\.\d+)?k?\b", lower):
            score += 4
        if any(cue in lower for cue in ("outperform", "achieve", "improve", "surpass")):
            score += 6
        if any(
            cue in lower
            for cue in (
                "experiment",
                "evaluation",
                "trial",
                "results show",
                "state-of-the-art",
                "benchmark",
            )
        ):
            score += 4
        if "demonstrate" in lower or "show" in lower:
            score += 1
        if "to achieve" in lower or "in order to achieve" in lower:
            # “We introduce a recipe to achieve X” describes intent/method, not
            # an observed outcome. Without this penalty it beats a later
            # evaluation sentence merely because it contains “achieve”.
            score -= 8
        if score > best_score:
            best = sentence
            best_score = score
    return best


def rule_summary(title: str, abstract: str) -> dict[str, str | None]:
    """Extract only sentences actually present in the abstract.

    The marker and warning are wire-visible so a rules result can never be
    mistaken for a model reading. Missing evidence stays an empty string.
    """
    sentences = _sentences(abstract)
    core = _matching_sentence(sentences, _CORE_CUES)
    if not core and sentences:
        core = sentences[0]
    return {
        "analysis_mode": "rules",
        "analysis_model": None,
        "analysis_warning": (
            "Heuristic extraction from title/abstract only; no LLM analysis or "
            "experiment execution was performed. Empty fields were not stated clearly."
        ),
        "summary_zh": "",
        "contribution": "",
        "core_trick": core or _clean(title, 1_000),
        "method": _matching_sentence(sentences, _METHOD_CUES),
        "results": _result_sentence(sentences),
        "limitations": _matching_sentence(sentences, _LIMIT_CUES),
    }


def source_ids_dict(paper: DiscoveredPaper) -> dict[str, str]:
    return dict(paper.source_ids)
