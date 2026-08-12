"""Safe, direct arXiv PDF import helpers.

The browser is allowed to submit an arXiv identifier, but it is never allowed
to choose an outbound URL.  We parse the identifier first, turn it into one of
our own canonical HTTPS URLs, and then use the same bounded PDF downloader as
the daily digest.  Keeping this boundary in a service makes it difficult for a
new endpoint to accidentally turn a pasted value into a general SSRF proxy.
"""

from __future__ import annotations

import hashlib
import re
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from pharos.daily import service as daily_service
from pharos.db.models import Paper
from pharos.services import enrich
from pharos.services.library import LibraryService
from pharos.services.metadata import ExtractedMeta

__all__ = [
    "ArxivInputError",
    "ArxivReference",
    "import_paper",
    "normalize_input",
]


class ArxivInputError(ValueError):
    """The submitted value is not an arXiv identifier or canonical URL."""


@dataclass(frozen=True)
class ArxivReference:
    """A version-normalised arXiv paper and the URLs derived from it."""

    arxiv_id: str
    abs_url: str
    pdf_url: str


_HOSTS = frozenset({"arxiv.org", "www.arxiv.org", "export.arxiv.org"})
_MODERN_RE = re.compile(r"\A(\d{4}\.\d{4,5})(?:v\d+)?\Z", re.IGNORECASE)
_LEGACY_RE = re.compile(
    r"\A([a-z][a-z0-9-]{1,30}(?:\.[a-z]{2})?)/(\d{7})(?:v\d+)?\Z",
    re.IGNORECASE,
)
_VERSION_RE = re.compile(r"v\d+\Z", re.IGNORECASE)
_MAX_INPUT_CHARS = 512
_METADATA_TIMEOUT_SECONDS = 6.0


def _canonical_id(value: str) -> str | None:
    """Return a versionless canonical id, or ``None`` for a malformed one."""
    candidate = value.strip()
    if not candidate:
        return None
    candidate = _VERSION_RE.sub("", candidate)
    modern = _MODERN_RE.fullmatch(candidate)
    if modern is not None:
        return modern.group(1)
    legacy = _LEGACY_RE.fullmatch(candidate)
    if legacy is None:
        return None
    archive, number = legacy.groups()
    stem, dot, subclass = archive.partition(".")
    archive = stem.lower() + (f".{subclass.upper()}" if dot else "")
    return f"{archive}/{number}"


def normalize_input(value: str) -> ArxivReference:
    """Normalise a bare id, ``arXiv:`` id, or arXiv abs/pdf URL.

    HTTP links are accepted for compatibility with old arXiv citations but are
    upgraded to HTTPS before any network request.  URLs with credentials,
    ports, query strings, fragments, or a non-arXiv host are rejected; those
    details are not part of an arXiv paper identifier and accepting them would
    make the endpoint a surprising network primitive.
    """
    if not isinstance(value, str):
        raise ArxivInputError("请输入有效的 arXiv 链接或编号")
    raw = value.strip()
    if not raw or len(raw) > _MAX_INPUT_CHARS:
        raise ArxivInputError("请输入有效的 arXiv 链接或编号")

    candidate = raw
    if "://" in raw:
        try:
            parsed = urllib.parse.urlsplit(raw)
            host = parsed.hostname.lower() if parsed.hostname else None
            if parsed.scheme.lower() not in {"http", "https"}:
                raise ArxivInputError("arXiv 链接必须使用 HTTP(S)")
            if host not in _HOSTS or parsed.username or parsed.password or parsed.port is not None:
                raise ArxivInputError("请输入 arXiv 官方链接")
            if parsed.query or parsed.fragment:
                raise ArxivInputError("arXiv 链接不能包含查询参数或片段")
            path = urllib.parse.unquote(parsed.path).strip()
            match = re.fullmatch(r"/(?:abs|pdf)/(.+?)/?", path, re.IGNORECASE)
            if match is None:
                raise ArxivInputError("请输入 arXiv 的 abs 或 pdf 链接")
            candidate = match.group(1)
            if candidate.lower().endswith(".pdf"):
                candidate = candidate[:-4]
        except ArxivInputError:
            raise
        except ValueError as exc:
            # Accessing ``SplitResult.port`` raises for malformed spellings
            # such as ``:not-a-port``.  It is still bad caller input, not an
            # internal failure that should surface as a 500.
            raise ArxivInputError("请输入有效的 arXiv 链接或编号") from exc
        except Exception as exc:
            raise ArxivInputError("请输入有效的 arXiv 链接或编号") from exc
    else:
        candidate = re.sub(r"^arxiv\s*:\s*", "", candidate, flags=re.IGNORECASE)

    # A URL-encoded slash is decoded above; a bare value containing a scheme or
    # control character is never treated as an id.
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in candidate):
        raise ArxivInputError("请输入有效的 arXiv 链接或编号")
    arxiv_id = _canonical_id(candidate)
    if arxiv_id is None:
        raise ArxivInputError("请输入有效的 arXiv 链接或编号")
    return ArxivReference(
        arxiv_id=arxiv_id,
        abs_url=f"https://arxiv.org/abs/{arxiv_id}",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
    )


def _copy_metadata(paper: Paper, meta: ExtractedMeta, *, replace: bool) -> None:
    """Apply registry metadata without allowing an existing manual row to lose data."""
    fields = ("title", "authors", "year", "venue", "doi", "abstract")
    for field in fields:
        value = getattr(meta, field)
        if field == "authors":
            value = "; ".join(value) if value else None
        if value is None or value == "":
            continue
        if replace or not getattr(paper, field):
            setattr(paper, field, value)
    if meta.arxiv_id and (replace or not paper.arxiv_id):
        paper.arxiv_id = meta.arxiv_id
    if any(getattr(meta, f) for f in fields) or meta.arxiv_id:
        paper.meta_source = "arxiv"
        paper.meta_extracted_at = datetime.now(UTC)


def import_paper(
    library: LibraryService,
    session: Session,
    *,
    user_id: str,
    value: str,
) -> Paper:
    """Download and ingest one arXiv paper into the caller's library."""
    reference = normalize_input(value)
    # The downloader validates the host again (including the final URL after a
    # redirect), while the reference guarantees that this request starts at the
    # arXiv HTTPS origin rather than at caller-provided bytes.
    data = daily_service.download_pdf(reference.pdf_url, https_only=True)

    # Detect a duplicate before ``add_upload`` so a direct import can stamp a
    # fresh row as arXiv while preserving the provenance of an earlier upload.
    sha256 = hashlib.sha256(data).hexdigest()
    existing = session.scalar(
        select(Paper).where(Paper.orig_sha256 == sha256, Paper.user_id == user_id)
    )
    filename = f"{reference.arxiv_id.replace('/', '-')}.pdf"
    paper = library.add_upload(session, user_id=user_id, filename=filename, data=data)
    is_new = existing is None
    if is_new:
        paper.source = "arxiv"
    if not paper.arxiv_id:
        paper.arxiv_id = reference.arxiv_id

    # Metadata enrichment is deliberately best effort. The PDF is already safely
    # stored, so an unavailable arXiv API must not turn a usable import into an
    # error. Registry data is authoritative for a fresh direct import, and only
    # fills gaps on an existing row so manual corrections remain intact.
    try:
        meta = enrich.enrich_by_arxiv(reference.arxiv_id, timeout=_METADATA_TIMEOUT_SECONDS)
    except Exception:
        meta = None
    if meta is not None:
        _copy_metadata(
            paper,
            meta,
            replace=is_new,
        )
    return paper
