"""Library service — importing and listing papers."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from xuanzang.db.models import Paper
from xuanzang.storage.blobs import BlobStore


def _pdf_metadata(path: Path, fallback_name: str) -> tuple[int | None, str]:
    """Return (page_count, title) for a PDF, tolerating malformed files."""
    title = Path(fallback_name).stem
    page_count: int | None = None
    try:
        import pymupdf

        with pymupdf.open(path) as doc:
            page_count = doc.page_count
            meta_title = (doc.metadata or {}).get("title")
            if meta_title and meta_title.strip():
                title = meta_title.strip()
    except Exception:
        pass
    return page_count, title


class LibraryService:
    def __init__(self, blobs: BlobStore) -> None:
        self.blobs = blobs

    def add_upload(self, session: Session, filename: str, data: bytes) -> Paper:
        """Store an uploaded PDF and create (or reuse) its Paper row.

        Content addressing means re-uploading the same file returns the existing
        paper instead of duplicating it.
        """
        sha256, path = self.blobs.store_original(data)
        existing = session.scalar(select(Paper).where(Paper.orig_sha256 == sha256))
        if existing is not None:
            return existing
        page_count, title = _pdf_metadata(path, filename)
        paper = Paper(
            title=title,
            orig_sha256=sha256,
            orig_filename=filename,
            page_count=page_count,
        )
        session.add(paper)
        session.flush()  # populate paper.id
        return paper

    def list_papers(self, session: Session) -> list[Paper]:
        return list(session.scalars(select(Paper).order_by(Paper.added_at.desc())))

    def get_paper(self, session: Session, paper_id: str) -> Paper | None:
        return session.get(Paper, paper_id)
