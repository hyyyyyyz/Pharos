"""Best-effort bibliographic metadata extraction from a PDF's text layer.

This runs synchronously inside the upload request, so it reads at most the
first two pages and never raises: a scanned, encrypted or malformed paper
yields an empty :class:`ExtractedMeta` and the UI renders "—".

The guiding rule throughout is *precision over recall*. A wrong author list is
worse than a missing one, because the user cannot tell a bad guess from a real
value. Every heuristic below therefore discards its result the moment anything
looks off, rather than shipping a partially-parsed value.
"""

from __future__ import annotations

import contextlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

try:  # PyMuPDF renamed its import in 1.24; "fitz" remains as a legacy alias.
    import pymupdf
except ImportError:  # pragma: no cover - depends on the installed wheel
    import fitz as pymupdf  # type: ignore[no-redef]


# How many leading pages we are willing to parse. Front matter never lives
# beyond page 2, and the caller is a blocking HTTP request.
_MAX_PAGES = 2

_ABSTRACT_MAX_CHARS = 4000
_VENUE_MAX_CHARS = 120
_TITLE_MIN_CHARS = 8
_TITLE_MAX_CHARS = 300

# An author list longer than this is certainly a mis-parse (physics papers with
# 3000 authors exist, but their front page is not parseable by these rules).
_MAX_AUTHORS = 60


@dataclass(frozen=True)
class ExtractedMeta:
    """Whatever could be determined confidently. Absent fields stay falsy."""

    title: str | None = None
    authors: tuple[str, ...] = ()
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    abstract: str | None = None
    arxiv_id: str | None = None


# ---------------------------------------------------------------------------
# text normalisation
# ---------------------------------------------------------------------------


# Ligatures ("ﬁ"), full-width punctuation and non-breaking spaces are pervasive
# in PDF text layers; NFKC folds them into the plain ASCII a database and a
# CrossRef query can actually match on.
def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text).replace(" ", " ")


def _squash(text: str) -> str:
    """Collapse the arbitrary line breaks a PDF text layer introduces."""
    return re.sub(r"\s+", " ", text).strip()


_HYPHEN_BREAK = re.compile(r"(?<![-­‐‑])\b(\w+)[-­‐‑]\s*\n\s*([a-z]+)\b(?!-)")
_HYPHEN_COMPOUND = re.compile(r"(\w)[-­‐‑]\s*\n\s*(\w)")


def dehyphenate(text: str) -> str:
    """Rejoin words a PDF broke across lines ("repre-\\nsentation").

    The hyphen is only *dropped* for a genuine syllable break: the fragments
    on both sides must be unhyphenated and the continuation lowercase. When
    either side carries another hyphen the break fell inside a compound, so
    the halves are merely closed up and the hyphen kept — "English-\\nto-German"
    and "state-of-the-\\nart" survive intact instead of losing a hyphen.
    """
    return _HYPHEN_COMPOUND.sub(r"\1-\2", _HYPHEN_BREAK.sub(r"\1\2", text))


# ---------------------------------------------------------------------------
# DOI
# ---------------------------------------------------------------------------

_DOI_RE = re.compile(r"10\.\d{4,9}/\S+")
_CLOSERS = {")": "(", "]": "[", "}": "{", ">": "<"}
# A DOI printed as part of a URL carries the query string or fragment the
# publisher's site appends ("...?casa_token=...", "...#sec-4"), which is not part
# of the identifier and makes it resolve nowhere. Registries avoid "?" and "#" in
# suffixes for exactly this reason, and quotes never appear in one.
# Angle brackets are deliberately NOT cut: legacy Wiley SICI DOIs contain them
# ("...15:4<361::AID-SIM168>3.0.CO;2-4").
_DOI_URL_TAIL = re.compile(r"[?#\"']")


def _strip_doi_tail(candidate: str) -> str:
    """Drop the sentence punctuation a DOI printed in running text picks up.

    Closing brackets are only stripped when unbalanced, because some legacy
    Wiley DOIs genuinely contain "(SICI)".
    """
    # Cut at the first bracket that closes without ever having been opened.
    # Inspecting only the *trailing* character leaves the tail of a bracketed
    # citation attached when text follows it: "[10.1234/abc]more" yielded the
    # identifier "10.1234/abc]more". A depth scan leaves balanced legacy SICI
    # DOIs untouched, since their brackets never close below zero.
    depth = dict.fromkeys(_CLOSERS.values(), 0)
    for index, char in enumerate(candidate):
        if char in depth:
            depth[char] += 1
        elif char in _CLOSERS:
            opener = _CLOSERS[char]
            if depth[opener] == 0:
                candidate = candidate[:index]
                break
            depth[opener] -= 1

    while candidate:
        last = candidate[-1]
        if last in ".,;:'\"’”":
            candidate = candidate[:-1]
            continue
        opener = _CLOSERS.get(last)
        if opener is not None and candidate.count(last) > candidate.count(opener):
            candidate = candidate[:-1]
            continue
        break
    return candidate


def find_doi(text: str) -> str | None:
    """Return the first plausible DOI in ``text``, lower-cased, or None.

    Lower-casing is safe (DOIs are case-insensitive) and is what CrossRef's
    API expects for a lookup key.
    """
    if not text:
        return None
    candidates = [text]
    # DOIs are frequently wrapped right after the prefix slash; rejoining only
    # across that slash cannot glue two unrelated words together.
    rejoined = re.sub(r"/\s*\n\s*", "/", text)
    if rejoined != text:
        candidates.append(rejoined)

    for haystack in candidates:
        for match in _DOI_RE.finditer(haystack):
            raw = match.group(0)
            cut = _DOI_URL_TAIL.search(raw)
            if cut is not None:
                raw = raw[: cut.start()]
            doi = _strip_doi_tail(raw)
            if "://" in doi or len(doi) > 100 or len(doi) < 8:
                continue
            # Stripping the tail can eat the whole suffix ("10.1234/..."), so
            # re-check that a real identifier survived.
            if not _DOI_RE.fullmatch(doi):
                continue
            return doi.lower()
    return None


# ---------------------------------------------------------------------------
# arXiv id
# ---------------------------------------------------------------------------

# Modern (2007-04 onwards) ids are YYMM.NNNNN; legacy ids are archive/YYMMNNN.
# We require the literal "arXiv:" prefix: a bare "2301.01234" is indis-
# tinguishable from a number in the body text.
_ARXIV_MODERN = re.compile(r"arxiv\s*:\s*(\d{2})(\d{2})\.(\d{4,5})(v\d+)?", re.I)
_ARXIV_LEGACY = re.compile(r"arxiv\s*:\s*([a-z][a-z-]{1,20}(?:\.[a-zA-Z]{2})?/\d{7})(v\d+)?", re.I)


def find_arxiv_id(text: str) -> str | None:
    """Return a bare arXiv id (no version suffix) from ``text``, or None.

    The version is dropped so the id works as a stable lookup key; arXiv's API
    resolves an unversioned id to the latest version.
    """
    if not text:
        return None
    match = _ARXIV_MODERN.search(text)
    if match is not None:
        yy, mm, seq = match.group(1), match.group(2), match.group(3)
        if 1 <= int(mm) <= 12:  # guards against a random "1234.5678"
            return f"{yy}{mm}.{seq}"
    match = _ARXIV_LEGACY.search(text)
    if match is not None:
        return _canonical_legacy_arxiv(match.group(1))
    return None


def _canonical_legacy_arxiv(identifier: str) -> str:
    """Case-normalise a legacy id to arXiv's own spelling (``math.GT/0309136``).

    The archive is lower-case but the two-letter subject class is upper-case,
    and arXiv's ``id_list`` endpoint honours that: blanket-lowercasing produced
    ``math.gt/0309136``, which every downstream lookup rejected.
    """
    archive, _, number = identifier.partition("/")
    stem, dot, subclass = archive.partition(".")
    if dot:
        return f"{stem.lower()}.{subclass.upper()}/{number}"
    return f"{stem.lower()}/{number}"


def year_from_arxiv_id(arxiv_id: str | None) -> int | None:
    """Derive the submission year from a *modern* arXiv id (2301 -> 2023).

    Legacy ids (``cs/0701001``) encode the year too, but their archive prefix
    makes the shape ambiguous enough that we simply decline.
    """
    if not arxiv_id:
        return None
    match = re.fullmatch(r"(\d{2})(\d{2})\.\d{4,5}", arxiv_id)
    if match is None:
        return None
    year = 2000 + int(match.group(1))
    return year if _plausible_year(year) else None


def _plausible_year(year: int) -> bool:
    return 1900 <= year <= datetime.now(UTC).year + 1


# ---------------------------------------------------------------------------
# title
# ---------------------------------------------------------------------------

_BAD_METADATA_TITLE = re.compile(
    r"""
      ^untitled\b
    | ^microsoft\ word\ -
    | ^(?:pdf)?tex\b
    | \.(?:dvi|pdf|tex|doc|docx|ps|eps|indd|qxd|rtf|odt)$
    | ^(?:paper|manuscript|main|template|preprint|draft|output|document|article|ms)$
    | ^[\d\s.\-_]+$
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Banner text that is often set in the largest font on page 1 but is not the
# title: venue stamps, arXiv margin stamps, running heads.
_TITLE_NOISE = re.compile(
    r"""
      ^arxiv\s*:
    | ^(?:published|accepted|to\ appear|under\ review|preprint|submitted)\b
    | ^proceedings\b
    | ^(?:abstract|introduction|keywords|contents)$
    | ^(?:draft|confidential|do\ not\ distribute)\b
    | ^doi\b
    | ^https?://
    | ^\d{1,2}(?:st|nd|rd|th)\ (?:international\ )?(?:conference|workshop|symposium)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def looks_like_title(candidate: str | None) -> bool:
    """Reject the garbage PDF producers routinely write into /Title.

    Also used to vet the font-size heuristic's output, which can latch onto a
    journal banner instead of the real title.
    """
    if not candidate:
        return False
    text = _squash(_norm(candidate))
    if not (_TITLE_MIN_CHARS <= len(text) <= _TITLE_MAX_CHARS):
        return False
    if not re.search(r"[A-Za-z]{2}", text):
        return False
    if _BAD_METADATA_TITLE.search(text) or _TITLE_NOISE.search(text):
        return False
    if find_doi(text) or find_arxiv_id(text):
        return False
    # An "@" or a spaceless path is an email or a filename, never a title.
    return not ("@" in text or ("/" in text and " " not in text))


class _Line(NamedTuple):
    """One rendered line of page 1, with the geometry the heuristics need."""

    y0: float
    y1: float
    x0: float
    size: float
    text: str


def _page_lines(page: object) -> list[_Line]:
    """Flatten ``get_text("dict")`` into reading-ordered lines with font sizes."""
    lines: list[_Line] = []
    try:
        data = page.get_text("dict")  # type: ignore[attr-defined]
    except Exception:
        return lines
    for block in data.get("blocks", ()):
        if block.get("type") != 0:  # images carry no text
            continue
        for line in block.get("lines", ()):
            spans = line.get("spans", ())
            text = _squash(_norm("".join(span.get("text", "") for span in spans)))
            if not text:
                continue
            size = max((float(span.get("size", 0.0)) for span in spans), default=0.0)
            bbox = line.get("bbox") or (0.0, 0.0, 0.0, 0.0)
            lines.append(
                _Line(y0=float(bbox[1]), y1=float(bbox[3]), x0=float(bbox[0]), size=size, text=text)
            )
    lines.sort(key=lambda ln: (round(ln.y0, 1), ln.x0))
    return lines


class _TitleBlock(NamedTuple):
    text: str
    size: float
    bottom: float


def _title_from_lines(lines: list[_Line], page_height: float) -> _TitleBlock | None:
    """Pick the largest-font contiguous group in the top region of page 1.

    Tries successively smaller font sizes because the biggest text is just as
    often a publisher banner as it is the title.
    """
    cutoff = page_height * 0.45 if page_height > 0 else float("inf")
    head = [ln for ln in lines if ln.y0 < cutoff and ln.size > 0]
    if not head:
        return None

    sizes = sorted({round(ln.size, 1) for ln in head}, reverse=True)
    for size in sizes[:4]:
        group: list[_Line] = []
        for line in head:
            if abs(round(line.size, 1) - size) > 0.11:
                continue
            # A gap larger than ~2 line heights means we left the title block.
            if group and line.y0 - group[-1].y1 > size * 1.8:
                break
            group.append(line)
        if not group:
            continue
        candidate = _squash(" ".join(ln.text for ln in group))
        if looks_like_title(candidate):
            return _TitleBlock(text=candidate, size=size, bottom=group[-1].y1)
    return None


# ---------------------------------------------------------------------------
# authors
# ---------------------------------------------------------------------------

# Words that prove a token is an affiliation, a footnote or a venue rather than
# a person. Their presence voids the *entire* list: a list that mixes people
# and institutions has been mis-segmented, and no subset of it is trustworthy.
_NON_NAME_WORDS = frozenset(
    # institutions
    ("university", "universite", "universitat", "universidad", "universita")
    + ("institute", "institut", "instituto", "department", "dept", "school", "college")
    + ("center", "centre", "centro", "laboratory", "laboratories", "lab", "labs", "research")
    + ("inc", "ltd", "llc", "gmbh", "corp", "corporation", "company", "academy", "hospital")
    + ("faculty", "division", "group", "team", "clinic", "foundation", "trust", "society")
    + ("association", "ministry", "agency")
    # front-matter headings and footnote boilerplate
    + ("abstract", "introduction", "keywords", "keyword", "index", "terms", "contents")
    + ("equal", "contribution", "contributions", "corresponding", "correspondence")
    + ("author", "authors", "email")
    # publication metadata that sits right next to the author block
    + ("arxiv", "preprint", "proceedings", "conference", "workshop", "journal")
    + ("volume", "issue", "pages", "copyright", "reserved", "rights")
    + ("received", "accepted", "published", "revised", "submitted", "editor", "reviewer")
    # frequent corporate affiliations, which read as plausible two-word names
    + ("google", "microsoft", "facebook", "meta", "amazon", "apple", "ibm")
    + ("nvidia", "openai", "deepmind", "anthropic")
)

# The local and domain parts stop at a separator: a greedy ``\S+@\S+`` ate the
# comma after an inline per-author address, fusing "Jane Doe jane@ex.edu, John
# Roe" into the single invented person "Jane Doe John Roe".
_EMAIL_RE = re.compile(r"[^\s,;|]+@[^\s,;|]+")
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_BRACKET_MARKER = re.compile(r"[\[\(\{][^\]\)\}]{0,24}[\]\)\}]")
# Affiliation markers: digits (incl. superscript), stars, daggers, footnote glyphs.
_MARKER_CHARS = re.compile(r"[\d¹²³⁰-₟*∗⋆★☆†‡§¶#^~◦♦♣]+")
_INITIALS_ONLY = re.compile(r"(?:[A-Z]\.?\s*){1,4}")
_ET_AL = re.compile(r"\bet\s+al\b\.?", re.I)
_AUTHOR_SPLIT = re.compile(r"\s*(?:,|;|\||·|•|&|\band\b|\bAND\b)\s*")


def _is_person_name(token: str) -> bool:
    if not token or len(token) > 80:
        return False
    if "@" in token or "/" in token or re.search(r"\d", token):
        return False
    if not re.match(r"[^\W\d_]", token, re.UNICODE):  # must start with a letter
        return False
    words = token.split()
    if not (1 <= len(words) <= 6):  # "more than ~6 words" is never one name
        return False
    if any(len(word) > 25 for word in words):
        return False
    stripped = {re.sub(r"[^\w]", "", word).lower() for word in words}
    return not (stripped & _NON_NAME_WORDS)


def _author_tokens(raw: str) -> list[str]:
    """Strip the decoration off an author line and split it into name tokens.

    Shared with the row classifier below so that "what counts as a token" cannot
    drift between deciding a row is unparseable and parsing it.
    """
    text = _norm(raw)
    text = _EMAIL_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    text = _BRACKET_MARKER.sub(" ", text)
    text = _MARKER_CHARS.sub(" ", text)
    tokens = [_squash(part).strip(" .,-–—") for part in _AUTHOR_SPLIT.split(text)]
    return [token for token in tokens if token]


# "MIT" and "IBM" match the initials shape too, but they are affiliations rather
# than evidence of "Surname, A." ordering. A genuine initial group is 1-2 letters.
_FLIPPED_INITIALS_MAX_LETTERS = 2


def _looks_like_initials(token: str) -> bool:
    if _INITIALS_ONLY.fullmatch(token) is None:
        return False
    return len(re.sub(r"[.\s]", "", token)) <= _FLIPPED_INITIALS_MAX_LETTERS


def _is_full_name(token: str) -> bool:
    """A token we would accept, on its own, as somebody's complete name."""
    return _is_person_name(token) and len(token.split()) >= 2 and not _looks_like_initials(token)


def parse_authors(raw: str) -> tuple[str, ...]:
    """Split an author line into names, or return () if anything looks wrong.

    Returning () is a first-class success mode: half a correct author list is
    indistinguishable to the user from a wrong one.
    """
    if not raw:
        return ()
    # "et al." is the line telling us outright that it is not the whole list.
    # Deleting it and shipping the visible prefix is precisely the silent
    # truncation this module exists to avoid, so the list is discarded instead.
    if _ET_AL.search(_BRACKET_MARKER.sub(" ", _norm(raw))):
        return ()

    names = _author_tokens(raw)
    if not names or len(names) > _MAX_AUTHORS:
        return ()
    # A bare "A." token means the line uses "Surname, Initials" ordering, which
    # comma-splitting shreds. Bail rather than emit half-names.
    if any(_INITIALS_ONLY.fullmatch(name) for name in names):
        return ()
    if not all(_is_person_name(name) for name in names):
        return ()
    # Every name must carry at least a forename and a surname. A bare single
    # word is far more likely a stray heading or an un-keyworded affiliation:
    # "Jane Doe, Stanford" used to ship "Stanford" as the second author, because
    # only mixed-case institutions the keyword list happens to name were caught.
    # Mononym authors are rare enough that discarding beats inventing a person.
    if any(len(name.split()) < 2 for name in names):
        return ()
    return tuple(names)


_AFFILIATION_STOP = re.compile(
    r"""
      \b(?:university|universit|institute|institut|department|dept\.|school|college
        |laborator|laborat[oó]rio|academy|hospital|faculty|research\ center
        |inc\.|ltd\.|llc|gmbh|corp\.|corporation)\b
    | @
    | https?://
    | ^\s*abstract\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _group_rows(lines: list[_Line]) -> list[list[_Line]]:
    """Merge lines that share a baseline into one visual row.

    Multi-column author grids emit one text line *per column*, so "Vaswani …
    Shazeer … Parmar … Uszkoreit" arrives as four separate lines at the same
    y. Treating them as one row is what makes the grid parseable.
    """
    rows: list[list[_Line]] = []
    for line in lines:
        if rows:
            head = rows[-1][0]
            if abs(line.y0 - head.y0) <= max(2.0, head.size * 0.3):
                rows[-1].append(line)
                continue
        rows.append([line])
    return rows


def _row_holds_unparseable_names(text: str) -> bool:
    """Whether a row ``parse_authors`` rejected nonetheless carries people.

    ``parse_authors`` returning () is overloaded. It means either "this row
    holds no people" — an affiliation line, correctly skipped — or "this row
    holds people I refuse to parse": an "et al.", a comma-flipped "Parmar, N.",
    a name sitting beside an un-keyworded institution. Skipping the second kind
    publishes the rows around it as if they were the complete list, which is
    exactly the silent truncation this module exists to prevent. Only a row that
    shows positive evidence of names voids the list, so a pure affiliation row
    ("Google Brain, Google Research") still merely gets skipped.
    """
    tokens = _author_tokens(text)
    return any(_is_full_name(token) or _looks_like_initials(token) for token in tokens)


def _row_is_all_names(text: str) -> bool:
    """Whether every token in a row is a complete personal name."""
    tokens = _author_tokens(text)
    return len(tokens) >= 2 and all(_is_full_name(token) for token in tokens)


def _authors_from_lines(
    lines: list[_Line], title: _TitleBlock, abstract_top: float | None
) -> tuple[str, ...]:
    """Collect every name-bearing row between the title and the abstract.

    Papers interleave rows of names with rows of affiliations and emails
    (name / affiliation / email, then the next batch of names). Classifying
    each row independently and unioning the name rows therefore recovers the
    whole list, where scanning until the first non-name row would silently
    truncate it — the worst possible outcome.
    """
    limit = abstract_top if abstract_top is not None else float("inf")
    region = [
        ln
        for ln in lines
        if ln.y0 >= title.bottom - 0.5 and ln.y0 < limit and ln.size < title.size - 0.3
    ]

    names: list[str] = []
    seen: set[str] = set()
    for row in _group_rows(region)[:14]:
        cells = [ln.text for ln in row]
        text = ", ".join(cells)
        if len(text.split()) >= 12:
            # Long rows are normally the body prose that follows the front
            # matter, so stopping here is right — unless the "prose" is in fact
            # a wide author row, in which case stopping would ship the rows
            # above it as though they were the whole list.
            if _row_is_all_names(text):
                return ()
            break
        if _AFFILIATION_STOP.search(text):
            continue
        # A grid's affiliation row repeats one string across its columns
        # ("Google Brain, Google Brain, Google Research"); people do not.
        if len(cells) > 1 and len(set(cells)) < len(cells):
            continue
        parsed = parse_authors(text)
        if not parsed:
            if _row_holds_unparseable_names(text):
                return ()
            continue
        for name in parsed:
            if name not in seen:
                seen.add(name)
                names.append(name)
        if len(names) > _MAX_AUTHORS:
            return ()
    return tuple(names)


# ---------------------------------------------------------------------------
# abstract
# ---------------------------------------------------------------------------

# Elsevier and IEEE templates letter-space the heading ("A B S T R A C T"), so
# a single optional space between letters is tolerated.
_ABSTRACT_HEAD = re.compile(
    r"^[ \t]*A\s?B\s?S\s?T\s?R\s?A\s?C\s?T\b[ \t]*[-–—:.]?[ \t]*",
    re.IGNORECASE | re.MULTILINE,
)
_ABSTRACT_STOP = re.compile(
    r"""
      \f                                    # a page break always ends it
    | ^[ \t]*
      (?:(?:\d{1,2}|[IVX]{1,4})[ \t]*[.)]?[ \t]*)?
      (?:Introduction
        |Keywords?
        |Key\ [Ww]ords?
        |Index\ Terms?
        |CCS\ Concepts?
        |General\ Terms?
        |Categories\ and\ Subject\ Descriptors
        |ACM\ Reference\ Format
        |Background
      )\b
    | ^[ \t]*[∗*†‡§¶]\s*\S                  # footnote block under the abstract
    | ^[ \t]*arXiv\s*:                      # the rotated margin stamp
    | ^[ \t]*\d{1,2}(?:st|nd|rd|th)\ (?:International\ )?
      (?:Conference|Workshop|Symposium|Annual\ Meeting)\b
    | ^[ \t]*(?:Preprint|Published\ as|Accepted\ (?:at|to))\b
    """,
    re.MULTILINE | re.VERBOSE,
)


def find_abstract(text: str) -> str | None:
    """Extract the abstract body between its heading and the next section."""
    if not text:
        return None
    head = _ABSTRACT_HEAD.search(text)
    if head is None:
        return None
    body = text[head.end() :]
    stop = _ABSTRACT_STOP.search(body)
    if stop is not None:
        body = body[: stop.start()]
    body = _squash(dehyphenate(_norm(body)))
    if len(body) < 40 or " " not in body:
        return None
    if len(body) > _ABSTRACT_MAX_CHARS:
        cut = body.rfind(" ", 0, _ABSTRACT_MAX_CHARS)
        body = body[: cut if cut > 0 else _ABSTRACT_MAX_CHARS].rstrip()
    return body


# ---------------------------------------------------------------------------
# venue
# ---------------------------------------------------------------------------

_ACRONYMS = (
    r"(?:ICLR|NeurIPS|NIPS|ICML|CVPR|ICCV|ECCV|ACL|EMNLP|NAACL|EACL|AAAI|IJCAI|KDD"
    r"|SIGIR|WWW|SIGGRAPH|INTERSPEECH|ICASSP|COLING|CoNLL|WACV|BMVC|MICCAI|UAI"
    r"|AISTATS|COLT|RSS|ICRA|IROS|CoRL|OSDI|SOSP|NSDI|EuroSys|ASPLOS|ISCA|MICRO"
    r"|PLDI|POPL|OOPSLA|ICSE|FSE|ASE|CCS|NDSS|MobiCom|SIGCOMM|INFOCOM|CHI|UIST"
    r"|TMLR|JMLR|VLDB|SIGMOD|ICDE|STOC|FOCS|SODA)"
)

_VENUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:Published|Accepted|To appear)\b[^\n]{0,40}?\bat\s+([^\n.]{3,60})",
        re.IGNORECASE,
    ),
    re.compile(rf"\(\s*({_ACRONYMS}\s*[’']?\s*(?:19|20)?\d{{2}})\s*\)"),
    re.compile(r"\b(Proceedings of[^\n]{3,90})", re.IGNORECASE),
    re.compile(
        r"\b(\d{1,2}(?:st|nd|rd|th)\s+(?:International\s+)?"
        r"(?:Conference|Workshop|Symposium|Annual Meeting)\s+on\s+[^\n,.]{3,70})",
        re.IGNORECASE,
    ),
    re.compile(rf"\b({_ACRONYMS}\s*[’']?\s*(?:19|20)\d{{2}})\b"),
)


def find_venue(text: str) -> str | None:
    """Spot a conference/journal stamp. Returns a short label, never a sentence."""
    if not text:
        return None
    for pattern in _VENUE_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        venue = _squash(_norm(match.group(1))).strip(" .,;:-–—")
        if not venue or len(venue) > _VENUE_MAX_CHARS or len(venue.split()) > 14:
            continue
        return venue
    if re.search(r"\barxiv\s*(?::|preprint)", text, re.I):
        return "arXiv"
    return None


_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_COPYRIGHT_YEAR = re.compile(r"(?:©|\(c\)|copyright)[^\n]{0,30}?\b((?:19|20)\d{2})\b", re.I)


def _year_in(text: str) -> int | None:
    """First plausible 4-digit year in a *venue-like* snippet, never body text."""
    for match in _YEAR_RE.finditer(text or ""):
        year = int(match.group(0))
        if _plausible_year(year):
            return year
    return None


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def _creation_year(doc: object) -> int | None:
    """Fall back to the PDF's own creation date (``D:20170612...``)."""
    try:
        raw = (doc.metadata or {}).get("creationDate") or ""  # type: ignore[attr-defined]
    except Exception:
        return None
    match = re.search(r"D:((?:19|20)\d{2})", raw)
    if match is None:
        return None
    year = int(match.group(1))
    return year if _plausible_year(year) else None


def extract_from_pdf(path: Path) -> ExtractedMeta:
    """Read the first pages of ``path`` and return whatever is certain.

    Never raises. Encrypted, image-only, zero-page and structurally broken
    files all return an empty :class:`ExtractedMeta`, which is a correct
    outcome rather than an error the caller must handle.
    """
    doc = None
    try:
        doc = pymupdf.open(path)
        if getattr(doc, "needs_pass", False):
            return ExtractedMeta()
        if doc.page_count <= 0:
            return ExtractedMeta()

        pages = []
        for index in range(min(_MAX_PAGES, doc.page_count)):
            try:
                pages.append(doc.load_page(index))
            except Exception:
                break  # a corrupt xref mid-document still leaves earlier pages
        if not pages:
            return ExtractedMeta()

        page_texts: list[str] = []
        for page in pages:
            try:
                page_texts.append(_norm(page.get_text("text")))
            except Exception:
                page_texts.append("")
        # Form feeds keep the abstract from bleeding onto the next page when
        # its terminating section heading is missing or mis-ordered.
        full_text = "\f".join(page_texts)
        first_text = page_texts[0] if page_texts else ""

        doi = find_doi(full_text)
        arxiv_id = find_arxiv_id(full_text)
        abstract = find_abstract(full_text)

        lines = _page_lines(pages[0])
        try:
            page_height = float(pages[0].rect.height)
        except Exception:
            page_height = 0.0

        title_block = _title_from_lines(lines, page_height)
        title = title_block.text if title_block is not None else None
        if title is None:
            try:
                fallback = (doc.metadata or {}).get("title")
            except Exception:
                fallback = None
            if looks_like_title(fallback):
                title = _squash(_norm(fallback or ""))

        # The abstract heading marks the bottom of the author block.
        abstract_top: float | None = None
        for line in lines:
            if _ABSTRACT_HEAD.match(line.text):
                abstract_top = line.y0
                break

        authors: tuple[str, ...] = ()
        if title_block is not None:
            authors = _authors_from_lines(lines, title_block, abstract_top)

        # Venue stamps live in the header/footer of page 1, not in the body.
        if lines and page_height > 0:
            margins = [
                ln.text for ln in lines if ln.y0 < page_height * 0.22 or ln.y1 > page_height * 0.80
            ]
            region_text = "\n".join(margins)
        else:
            region_text = first_text
        venue = find_venue(region_text)
        if venue is None and arxiv_id is not None:
            # The rotated margin stamp sometimes falls outside the header/footer
            # bands, but a resolved arXiv id says the same thing on its own.
            venue = "arXiv"

        year = year_from_arxiv_id(arxiv_id)
        if year is None and venue:
            year = _year_in(venue)
        if year is None:
            match = _COPYRIGHT_YEAR.search(region_text)
            if match is not None and _plausible_year(int(match.group(1))):
                year = int(match.group(1))
        if year is None:
            year = _creation_year(doc)

        return ExtractedMeta(
            title=title,
            authors=authors,
            year=year,
            venue=venue,
            doi=doi,
            abstract=abstract,
            arxiv_id=arxiv_id,
        )
    except Exception:
        # Surfacing a parse failure as an empty result keeps upload working;
        # the user can still correct the fields by hand.
        return ExtractedMeta()
    finally:
        # A leaked handle keeps the blob file open on Windows and starves the
        # process of file descriptors, so closing must survive any failure.
        if doc is not None:
            with contextlib.suppress(Exception):
                doc.close()
