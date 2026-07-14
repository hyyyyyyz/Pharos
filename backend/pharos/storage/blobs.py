"""Content-addressed blob store for original and translated PDFs.

Layout on disk::

    <files_dir>/<sha256>/original.pdf
    <files_dir>/<sha256>/mono.pdf      (Chinese-only, layout-preserving)
    <files_dir>/<sha256>/dual.pdf      (bilingual)
    <files_dir>/<sha256>/work/         (engine scratch output)

Addressing originals by SHA-256 deduplicates re-uploads of the same paper for free.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Literal

PdfKind = Literal["original", "mono", "dual"]


class BlobStore:
    def __init__(self, files_dir: Path) -> None:
        self.files_dir = Path(files_dir)
        self.files_dir.mkdir(parents=True, exist_ok=True)

    def paper_dir(self, sha256: str) -> Path:
        return self.files_dir / sha256

    def work_dir(self, sha256: str) -> Path:
        d = self.paper_dir(sha256) / "work"
        d.mkdir(parents=True, exist_ok=True)
        return d

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

    def adopt_output(self, sha256: str, kind: PdfKind, produced: Path) -> Path:
        """Copy an engine-produced PDF to its canonical ``<kind>.pdf`` name."""
        dest = self.path(sha256, kind)
        shutil.copyfile(produced, dest)
        return dest

    def exists(self, sha256: str, kind: PdfKind) -> bool:
        return self.path(sha256, kind).exists()
