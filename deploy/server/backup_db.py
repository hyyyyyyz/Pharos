#!/usr/bin/env python3
"""Create an online SQLite backup without stopping the Pharos API."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: backup_db.py SOURCE DESTINATION", file=sys.stderr)
        return 2

    source = Path(sys.argv[1])
    destination = Path(sys.argv[2])
    if not source.exists():
        return 0

    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src:
        with sqlite3.connect(destination) as dst:
            src.backup(dst)
            integrity = dst.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise RuntimeError(f"backup integrity check failed: {integrity!r}")
    destination.chmod(0o600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
