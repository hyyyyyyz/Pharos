"""Content-addressed blob store for original and translated PDFs.

Layout on disk::

    <files_dir>/<sha256>/original.pdf
    <files_dir>/<sha256>/jobs/<job_id>/mono.pdf   (Chinese-only, layout-preserving)
    <files_dir>/<sha256>/jobs/<job_id>/dual.pdf   (bilingual)
    <files_dir>/<sha256>/jobs/<job_id>/work/      (engine scratch output)

Addressing originals by SHA-256 deduplicates re-uploads of the same paper for free.
That sharing is deliberate and applies to the *original* only. A translation is the
product of one user's request (their translator, their page range), so it lives
under the job that produced it and is served only to the owner of that job's paper.
Keying it by the content hash alone made two users who uploaded the same PDF read
and overwrite each other's output.

Legacy layout (before per-job isolation)::

    <files_dir>/<sha256>/mono.pdf, dual.pdf, work/

Existing ``TranslationJob`` rows still point at those files through ``mono_path``
/ ``dual_path``, and keep being served to the owner of the job's paper. Nothing is
moved. No *new* job ever writes there.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from typing import Literal

PdfKind = Literal["original", "mono", "dual"]

_SAFE_ID = re.compile(r"[0-9a-fA-F]{1,64}")


class BlobStore:
    def __init__(self, files_dir: Path) -> None:
        self.files_dir = Path(files_dir)
        self.files_dir.mkdir(parents=True, exist_ok=True)

    def paper_dir(self, sha256: str) -> Path:
        return self.files_dir / sha256

    def job_dir(self, sha256: str, job_id: str) -> Path:
        if not _SAFE_ID.fullmatch(job_id) or not _SAFE_ID.fullmatch(sha256):
            raise ValueError("unsafe blob identifier")
        return self.paper_dir(sha256) / "jobs" / job_id

    def work_dir(self, sha256: str, job_id: str) -> Path:
        """Engine scratch space, private to one job."""
        d = self.job_dir(sha256, job_id) / "work"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def job_path(self, sha256: str, job_id: str, kind: PdfKind) -> Path:
        return self.job_dir(sha256, job_id) / f"{kind}.pdf"

    def remove_job(self, sha256: str, job_id: str) -> None:
        """Delete one job's output directory (best effort)."""
        try:
            target = self.job_dir(sha256, job_id)
        except ValueError:
            return
        shutil.rmtree(target, ignore_errors=True)

    def is_inside(self, candidate: Path) -> bool:
        """True when ``candidate`` resolves to a path under the blob root."""
        try:
            candidate.resolve().relative_to(self.files_dir.resolve())
        except (ValueError, OSError):
            return False
        return True

    def path(self, sha256: str, kind: PdfKind) -> Path:
        return self.paper_dir(sha256) / f"{kind}.pdf"

    def store_original(self, data: bytes) -> tuple[str, Path]:
        """Store the uploaded PDF by content hash. Returns (sha256, path)."""
        sha256 = hashlib.sha256(data).hexdigest()
        target = self.path(sha256, "original")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(data)
        return sha256, target

    def adopt_output(self, sha256: str, job_id: str, kind: PdfKind, produced: Path) -> Path:
        """Copy an engine-produced PDF to the job's canonical ``<kind>.pdf`` name."""
        dest = self.job_path(sha256, job_id, kind)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(produced, dest)
        return dest

    def exists(self, sha256: str, kind: PdfKind) -> bool:
        return self.path(sha256, kind).exists()
