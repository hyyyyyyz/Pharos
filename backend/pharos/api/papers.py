"""Papers API: upload, list, detail, metadata editing, trash, and serving the PDFs."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from pharos.api.deps import current_user, get_blobs, get_library, get_session
from pharos.api.schemas import PaperOut, as_utc, paper_out
from pharos.db.models import Paper, User
from pharos.services.library import LibraryService
from pharos.storage.blobs import BlobStore

router = APIRouter(prefix="/api", tags=["papers"])

_KINDS = {"original", "mono", "dual"}

#: Prefixes users paste along with a DOI. The column stores the bare identifier.
_DOI_PREFIX = re.compile(r"^\s*(?:https?://(?:dx\.)?doi\.org/|doi:)\s*", re.IGNORECASE)

#: Accepted lengths for manual edits, matching the ``Paper`` columns. ``doi`` is
#: allowed extra room on input purely for a URL prefix that ``_clean_doi``
#: removes; what actually reaches the column is re-checked after normalisation.
_MAX_TITLE = 512
_MAX_VENUE = 256
_MAX_DOI = 128
_MAX_DOI_INPUT = 256
_MAX_ABSTRACT = 20000
_MAX_AUTHOR_COUNT = 200
_MAX_AUTHORS_JOINED = 8000


class MetadataPatch(BaseModel):
    """Manual corrections to a paper's bibliographic record.

    Omitted fields are left alone; an explicit ``null`` clears the stored value,
    which is how a user deletes a wrong guess rather than replacing it. Unknown
    fields are rejected so a typo'd key fails loudly instead of doing nothing.

    Every string is length-capped. SQLite does not enforce ``VARCHAR`` width, so
    without these an over-long title is accepted, written, and then returned in
    every subsequent library listing — and only becomes a hard error if the
    database is ever migrated to one that does enforce it. The caps sit slightly
    above the columns' declared widths where a value is normalised on the way in
    (a DOI arrives with a ``https://doi.org/`` prefix that ``_clean_doi`` strips),
    and the post-normalisation length is re-checked below.
    """

    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, Field(max_length=_MAX_TITLE)] | None = None
    authors: Annotated[list[str], Field(max_length=_MAX_AUTHOR_COUNT)] | None = None
    year: Annotated[int, Field(ge=1500, le=2100)] | None = None
    venue: Annotated[str, Field(max_length=_MAX_VENUE)] | None = None
    doi: Annotated[str, Field(max_length=_MAX_DOI_INPUT)] | None = None
    abstract: Annotated[str, Field(max_length=_MAX_ABSTRACT)] | None = None


class DeleteResult(BaseModel):
    """Outcome of a delete. ``purged`` is what distinguishes the recoverable case."""

    id: str
    purged: bool
    deleted_at: datetime | None = None


def _clean(value: str | None) -> str | None:
    """Trim a user-supplied string; blank means "no value", not an empty value."""
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _clean_doi(value: str | None) -> str | None:
    if value is None:
        return None
    return _clean(_DOI_PREFIX.sub("", value))


def _join_authors(names: list[str] | None) -> str | None:
    """Pack an author list into the column's ``"; "``-joined form.

    Semicolons are stripped from individual names because they are the delimiter:
    one inside a name would silently split it into two people on the way out.
    """
    if not names:
        return None
    cleaned = [" ".join(n.replace(";", " ").split()) for n in names]
    return "; ".join(n for n in cleaned if n) or None


def _require_paper(
    library: LibraryService, session: Session, paper_id: str, user: User
) -> Paper:
    """Resolve one of ``user``'s papers, or raise the 404 that hides the rest.

    A paper owned by somebody else raises 404, never 403. The distinction is the
    whole point: 403 means "this exists and you may not have it", which confirms
    the id is real and turns this endpoint into an oracle an attacker can walk to
    enumerate other people's libraries. 404 says nothing at all. The scoping is
    done by the query rather than by a check here, so there is no branch that can
    be reordered into leaking the row it was meant to withhold.
    """
    paper = library.get_paper(session, paper_id, user_id=user.id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    return paper


@router.post("/papers", response_model=PaperOut)
def upload_paper(
    file: UploadFile = File(...),
    library: LibraryService = Depends(get_library),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> PaperOut:
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only .pdf files are accepted")
    data = file.file.read()
    if not data[:5].startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="File does not look like a PDF")
    paper = library.add_upload(
        session, user_id=user.id, filename=file.filename or "upload.pdf", data=data
    )
    return paper_out(paper)


@router.get("/papers", response_model=list[PaperOut])
def list_papers(
    trash: bool = Query(False, description="List the recycle bin instead of the library."),
    library: LibraryService = Depends(get_library),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> list[PaperOut]:
    return [paper_out(p) for p in library.list_papers(session, user_id=user.id, trash=trash)]


@router.get("/papers/{paper_id}", response_model=PaperOut)
def get_paper(
    paper_id: str,
    library: LibraryService = Depends(get_library),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> PaperOut:
    return paper_out(_require_paper(library, session, paper_id, user))


@router.post("/papers/{paper_id}/metadata/refresh", response_model=PaperOut)
def refresh_paper_metadata(
    paper_id: str,
    library: LibraryService = Depends(get_library),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> PaperOut:
    """Re-parse the stored original and re-check the registries.

    This is the user's remedy for a bad first parse, so it may take a few seconds
    of network time — unlike upload, which never waits on a registry.
    """
    paper = _require_paper(library, session, paper_id, user)
    if not library.refresh_metadata(session, paper):
        raise HTTPException(status_code=404, detail="Original PDF is no longer available")
    return paper_out(paper)


@router.patch("/papers/{paper_id}/metadata", response_model=PaperOut)
def patch_paper_metadata(
    paper_id: str,
    patch: MetadataPatch,
    library: LibraryService = Depends(get_library),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> PaperOut:
    paper = _require_paper(library, session, paper_id, user)

    # exclude_unset is what separates "leave this alone" from "clear this".
    provided = patch.model_dump(exclude_unset=True)
    if not provided:
        raise HTTPException(status_code=400, detail="No metadata fields provided")

    if "title" in provided:
        title = _clean(provided["title"])
        if title is None:
            # Every paper needs a name; clearing the title would leave the row
            # unrenderable, and the column is NOT NULL besides.
            raise HTTPException(status_code=400, detail="title cannot be empty")
        paper.title = title
    if "authors" in provided:
        authors = _join_authors(provided["authors"])
        if authors is not None and len(authors) > _MAX_AUTHORS_JOINED:
            raise HTTPException(status_code=400, detail="authors list is too long")
        paper.authors = authors
    if "year" in provided:
        paper.year = provided["year"]
    if "venue" in provided:
        paper.venue = _clean(provided["venue"])
    if "doi" in provided:
        doi = _clean_doi(provided["doi"])
        if doi is not None and len(doi) > _MAX_DOI:
            raise HTTPException(status_code=400, detail="doi is too long")
        paper.doi = doi
    if "abstract" in provided:
        paper.abstract = _clean(provided["abstract"])

    paper.meta_source = "manual"
    paper.meta_extracted_at = datetime.now(timezone.utc)
    return paper_out(paper)


@router.delete("/papers/{paper_id}", response_model=DeleteResult)
def delete_paper(
    paper_id: str,
    purge: bool = Query(
        False,
        description=(
            "Permanently destroy the paper and its files. Only accepted for a paper "
            "already in the recycle bin."
        ),
    ),
    library: LibraryService = Depends(get_library),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> DeleteResult:
    """Move a paper to the recycle bin, or — opt-in — destroy it for good.

    The default is always recoverable: the PDF and any translated outputs survive
    a plain DELETE untouched. Purging additionally requires the paper to be in the
    bin already, so permanent loss takes two deliberate calls and can never be the
    consequence of a single mis-sent request.

    Purging destroys only the caller's own row. The shared blob survives as long
    as any other user's paper still references it — see ``LibraryService.purge``.
    """
    paper = _require_paper(library, session, paper_id, user)

    if purge:
        if paper.deleted_at is None:
            raise HTTPException(
                status_code=409,
                detail="Move the paper to the recycle bin before deleting it permanently",
            )
        library.purge(session, paper)
        return DeleteResult(id=paper_id, purged=True)

    library.soft_delete(session, paper)
    return DeleteResult(id=paper_id, purged=False, deleted_at=as_utc(paper.deleted_at))


@router.post("/papers/{paper_id}/restore", response_model=PaperOut)
def restore_paper(
    paper_id: str,
    library: LibraryService = Depends(get_library),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> PaperOut:
    paper = _require_paper(library, session, paper_id, user)
    library.restore(session, paper)
    return paper_out(paper)


@router.get("/papers/{paper_id}/pdf/{kind}")
def get_paper_pdf(
    paper_id: str,
    kind: str,
    library: LibraryService = Depends(get_library),
    blobs: BlobStore = Depends(get_blobs),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> FileResponse:
    """Stream one of a paper's PDFs. The most damaging endpoint to get wrong.

    Everything else here leaks metadata; this one leaks the document itself. Two
    properties make it safe, and both must survive any future edit:

    1. The path is derived from ``paper.orig_sha256`` — a column read off a row
       that ``_require_paper`` already proved belongs to the caller. The client
       never names a blob. If this ever takes a sha256 (or a path) from the
       request instead, ownership stops being checked at all and any user can
       read any document by hash.
    2. A paper that is not the caller's 404s before a path is even computed, so
       an unauthorised request cannot be distinguished from one for an id that
       was never issued.

    Content addressing means the *bytes* on disk may legitimately be shared with
    another user who uploaded the same file. That is not a leak: identical input
    documents produce one blob, and reaching it still requires owning a row that
    points at it.
    """
    if kind not in _KINDS:
        raise HTTPException(status_code=400, detail=f"kind must be one of {sorted(_KINDS)}")
    paper = _require_paper(library, session, paper_id, user)
    path = blobs.path(paper.orig_sha256, kind)  # type: ignore[arg-type]
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{kind} PDF not available yet")
    filename = f"{paper.title[:60]}.{kind}.pdf"
    return FileResponse(path, media_type="application/pdf", filename=filename)
