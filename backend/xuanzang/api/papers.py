"""Papers API: upload, list, detail, and serving the PDFs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from xuanzang.api.deps import get_blobs, get_library, get_session
from xuanzang.api.schemas import PaperOut, paper_out
from xuanzang.services.library import LibraryService
from xuanzang.storage.blobs import BlobStore

router = APIRouter(prefix="/api", tags=["papers"])

_KINDS = {"original", "mono", "dual"}


@router.post("/papers", response_model=PaperOut)
def upload_paper(
    file: UploadFile = File(...),
    library: LibraryService = Depends(get_library),
    session: Session = Depends(get_session),
) -> PaperOut:
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only .pdf files are accepted")
    data = file.file.read()
    if not data[:5].startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="File does not look like a PDF")
    paper = library.add_upload(session, file.filename or "upload.pdf", data)
    return paper_out(paper)


@router.get("/papers", response_model=list[PaperOut])
def list_papers(
    library: LibraryService = Depends(get_library),
    session: Session = Depends(get_session),
) -> list[PaperOut]:
    return [paper_out(p) for p in library.list_papers(session)]


@router.get("/papers/{paper_id}", response_model=PaperOut)
def get_paper(
    paper_id: str,
    library: LibraryService = Depends(get_library),
    session: Session = Depends(get_session),
) -> PaperOut:
    paper = library.get_paper(session, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    return paper_out(paper)


@router.get("/papers/{paper_id}/pdf/{kind}")
def get_paper_pdf(
    paper_id: str,
    kind: str,
    library: LibraryService = Depends(get_library),
    blobs: BlobStore = Depends(get_blobs),
    session: Session = Depends(get_session),
) -> FileResponse:
    if kind not in _KINDS:
        raise HTTPException(status_code=400, detail=f"kind must be one of {sorted(_KINDS)}")
    paper = library.get_paper(session, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    path = blobs.path(paper.orig_sha256, kind)  # type: ignore[arg-type]
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{kind} PDF not available yet")
    filename = f"{paper.title[:60]}.{kind}.pdf"
    return FileResponse(path, media_type="application/pdf", filename=filename)
