"""Explicit, versioned, checksummed schema migrations.

The existing schema bootstrap is ``create_all`` plus an additive
``_add_missing_columns`` pass (see :mod:`pharos.db.session`). That is exactly
right for a schema that only ever grows by nullable columns on legacy tables,
and exactly wrong for the Research Harness, whose tables carry CHECK
constraints, composite foreign keys and unique contracts that
``ALTER TABLE ADD COLUMN`` cannot express and ``create_all`` silently skips on
an upgraded database.

This module introduces the missing layer: a numbered revision ledger where
every migration has a stable identity and a checksum over its immutable
content, applied as one atomic batch per startup.

Design rules (they are the gate, not decoration):

* Every migration is a frozen :class:`Migration`: a stable ``revision``, a
  ``description`` and a tuple of DDL statements. The checksum covers all three,
  so a change to any historical migration is a loud startup failure rather
  than a silent divergence between databases.
* One startup upgrade batch runs inside a single connection-level
  ``BEGIN IMMEDIATE`` transaction: ledger bootstrap, verification of already
  applied revisions, all pending revisions, and the ledger rows recording
  them. Any failure rolls the whole batch back -- no new revision, no partial
  DDL -- and the application fails to start.
* Migrations never commit, never switch connections, never read configuration
  secrets, never touch the network and never call business services.
* There is no automatic "drop the table" rollback. Rollback means: stop the
  feature gate, restore a backup, or ship a forward-fix migration.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

LEDGER_TABLE = "pharos_schema_migrations"

#: SQLite's default busy timeout for the migration connection. Upgrades on a
#: busy production database must wait for writers rather than fail instantly;
#: the single BEGIN IMMEDIATE batch also serialises concurrent bootstraps.
_BUSY_TIMEOUT_MS = 15000


class MigrationError(RuntimeError):
    """A migration batch failed or the ledger no longer matches the code."""


@dataclass(frozen=True)
class Migration:
    """One immutable schema revision.

    ``statements`` is the whole revision: the DDL, in order, executed against
    the single migration connection. Nothing here may be computed at runtime
    from ORM metadata -- the checksum must mean "this revision will always run
    exactly this SQL", so the DDL is written out explicitly.
    """

    revision: str
    description: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        return _checksum(self.revision, self.description, self.statements)


def _checksum(revision: str, description: str, statements: tuple[str, ...]) -> str:
    canonical = json.dumps(
        {"revision": revision, "description": description, "statements": list(statements)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _ledger_ddl() -> str:
    return f"""
    CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} (
        revision TEXT PRIMARY KEY,
        description TEXT NOT NULL,
        checksum TEXT NOT NULL,
        applied_at TEXT NOT NULL
    )
    """


# --------------------------------------------------------------------------
# Revisions. Append only. Harness business tables arrive in their own
# revisions; the first revision establishes nothing but the ledger itself.
# --------------------------------------------------------------------------

MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        revision="0001_schema_ledger",
        description="Establish the versioned migration ledger",
        statements=(_ledger_ddl(),),
    ),
)


def _connect(db_path: Path) -> sqlite3.Connection:
    # isolation_level=None: the driver never opens an implicit transaction, so
    # the explicit BEGIN IMMEDIATE below owns transaction boundaries and DDL
    # inside it is transactional (SQLite rolls back schema changes with the
    # transaction, which is exactly what the interrupted-migration gate
    # depends on).
    conn = sqlite3.connect(str(db_path), timeout=_BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    return conn


def _applied(conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    try:
        rows = conn.execute(
            f"SELECT revision, description, checksum FROM {LEDGER_TABLE} ORDER BY revision"
        ).fetchall()
    except sqlite3.OperationalError:
        # Ledger absent: nothing applied yet. The first migration creates it
        # inside the same batch.
        return {}
    return {revision: (description, checksum) for revision, description, checksum in rows}


def _verify_applied(applied: dict[str, tuple[str, str]]) -> None:
    by_revision = {migration.revision: migration for migration in MIGRATIONS}
    seen_unknown: list[str] = []
    for revision, (description, checksum) in applied.items():
        migration = by_revision.get(revision)
        if migration is None:
            seen_unknown.append(revision)
            continue
        if migration.checksum != checksum:
            raise MigrationError(
                f"Migration {revision} checksum mismatch: database records "
                f"{checksum[:12]} but the code computes {migration.checksum[:12]}. "
                "A committed migration must never change; write a new revision."
            )
        if migration.description != description:
            raise MigrationError(
                f"Migration {revision} description mismatch: "
                f"{description!r} in the database, {migration.description!r} in the code."
            )
    if seen_unknown:
        raise MigrationError(
            f"Database has migrations not present in this build: {sorted(seen_unknown)}. "
            "Refusing to run against a database migrated by newer code."
        )


def _apply_pending(conn: sqlite3.Connection, applied: dict[str, tuple[str, str]]) -> list[str]:
    new_revisions: list[str] = []
    for migration in MIGRATIONS:
        if migration.revision in applied:
            continue
        for statement in migration.statements:
            conn.execute(statement)
        conn.execute(
            f"INSERT INTO {LEDGER_TABLE} (revision, description, checksum, applied_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (migration.revision, migration.description, migration.checksum),
        )
        new_revisions.append(migration.revision)
        log.info("applied migration %s: %s", migration.revision, migration.description)
    return new_revisions


def run_migrations(db_path: Path | str) -> list[str]:
    """Apply all pending migrations atomically; return the new revisions.

    The whole batch -- ledger bootstrap, checksum verification, pending DDL,
    ledger rows -- is one ``BEGIN IMMEDIATE`` transaction. A failure anywhere
    rolls back everything, leaves the database exactly as it was, and raises.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(db_path)
    result: list[str]
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # The ledger itself must exist before _applied can read it. The
            # first migration creates it, but a database written by a build
            # that predates this module has no ledger; bootstrap it now, in the
            # same transaction, without recording anything yet -- recording
            # happens when revision 0001 actually applies.
            conn.execute(_ledger_ddl())
            applied = _applied(conn)
            _verify_applied(applied)
            result = _apply_pending(conn, applied)
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
    finally:
        conn.close()
    return result


def migration_status(db_path: Path | str) -> list[dict[str, str]]:
    """Read-only list of applied revisions, oldest first."""
    conn = _connect(Path(db_path))
    try:
        return [
            {"revision": row[0], "description": row[1], "checksum": row[2], "applied_at": row[3]}
            for row in conn.execute(
                f"SELECT revision, description, checksum, applied_at "
                f"FROM {LEDGER_TABLE} ORDER BY revision"
            )
        ]
    finally:
        conn.close()


def verify_migrations(db_path: Path | str) -> list[dict[str, str]]:
    """Check that every applied revision matches this build's checksums."""
    conn = _connect(Path(db_path))
    try:
        applied = _applied(conn)
    finally:
        conn.close()
    _verify_applied(applied)
    return [
        {"revision": revision, "checksum": checksum} for revision, (_, checksum) in applied.items()
    ]


def _cli() -> None:
    import sys

    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} status|upgrade|verify DB_PATH", file=sys.stderr)
        raise SystemExit(2)
    command, db_path = sys.argv[1], sys.argv[2]
    if command == "status":
        for row in migration_status(db_path):
            print(f"{row['revision']}  {row['checksum'][:12]}  {row['description']}")
    elif command == "upgrade":
        print("applied:", ", ".join(run_migrations(db_path)) or "nothing pending")
    elif command == "verify":
        verify_migrations(db_path)
        print("ok")
    else:
        print(f"unknown command {command}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    _cli()
