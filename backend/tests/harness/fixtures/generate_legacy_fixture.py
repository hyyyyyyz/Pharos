"""Deterministically generate the checked-in legacy-schema fixture.

Run from ``backend/``::

    python tests/harness/fixtures/generate_legacy_fixture.py

Historical contract
-------------------

The fixture represents the last published Pharos schema *before* the Research
Harness: commit ``9afc1fb`` ("Add the staged Harness implementation handoff")
introduced the Harness documents but no ``harness_*`` tables, so the database
that commit produced is the upgrade target the Harness migration runner must
handle.

The generator therefore uses **only the legacy bootstrap code path** --
``Base.metadata.create_all``, the additive column/index compatibility passes
and the FTS setup, all of which are the exact code the published release ran.
It never consults the Harness models, never opens the real data directory, and
writes only structure plus deterministic synthetic rows (no user content, no
secrets, no production paths).

The committed artifact is ``legacy-schema-v0.sqlite``. If the legacy schema
contract ever changes deliberately, regenerate and commit the new file in the
same change as the revision that consumes it.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from pharos.db.models import Base, Paper, User
from pharos.db.session import (
    _add_missing_columns,
    _add_missing_indexes,
    _configure_sqlite,
    _init_fts,
)
from sqlalchemy import create_engine

OUT = Path(__file__).parent / "legacy-schema-v0.sqlite"


def main() -> None:
    if OUT.exists():
        OUT.unlink()
    engine = create_engine(f"sqlite:///{OUT}", future=True)
    # The same connect event the real application installs, so the fixture's
    # pragmas and py_lower registration match production byte for byte.
    from sqlalchemy import event

    event.listen(engine, "connect", _configure_sqlite)
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
    _add_missing_indexes(engine)
    _init_fts(engine)

    # Synthetic, deterministic rows: fixed ids, no real content. Enough for
    # upgrade tests to assert rows survive, and enough for FTS to have one
    # document (so the rebuild probe sees a populated index).
    with engine.begin() as conn:
        conn.execute(
            User.__table__.insert(),
            [
                {
                    "id": "fixtureuser0000000000000000001",
                    "email": "fixture@example.invalid",
                    "password_hash": "argon2-synthetic-not-a-secret",
                    "display_name": "Fixture User",
                    "is_admin": False,
                    "token_epoch": 0,
                }
            ],
        )
        conn.execute(
            Paper.__table__.insert(),
            [
                {
                    "id": "fixturepaper0000000000000000001",
                    "user_id": "fixtureuser0000000000000000001",
                    "title": "A Deterministic Fixture Paper",
                    "authors": "A. Author, B. Author",
                    "abstract": "The quick brown fox jumps over the lazy dog.",
                    "source": "upload",
                    "source_lang": "en",
                    "orig_sha256": "0" * 64,
                    "orig_filename": "fixture-paper.pdf",
                    "full_text": "Section one.\nSection two.",
                    "page_count": 2,
                }
            ],
        )
    engine.dispose()

    # Make the artifact reproducible: WAL checkpointed back into the main file,
    # no sidecar files shipped, timestamps deterministic.
    shutil.rmtree(f"{OUT}-wal", ignore_errors=True)
    shutil.rmtree(f"{OUT}-shm", ignore_errors=True)
    with sqlite3.connect(OUT) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")

    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
