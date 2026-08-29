"""Schema consistency: the ORM mirror and the migration DDL cannot drift.

The migration runner executes literal SQL (so its checksums mean something);
the repository layer reads through Core tables. This test creates a fresh
database through the real startup path and then compares the tables, columns,
unique constraints and check constraints that SQLite actually has against the
metadata, so a column added to one side but not the other fails loudly.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pharos.db import migrations
from pharos.harness.tables import metadata


def _ddl_columns(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    return {row[1]: row[2].upper() for row in conn.execute(f"PRAGMA table_info({table})")}


def test_orm_tables_match_migration_ddl(tmp_path: Path) -> None:
    db = tmp_path / "consistency.sqlite"
    migrations.run_migrations(db)
    conn = sqlite3.connect(db)
    try:
        for table in metadata.sorted_tables:
            if table.name == "users":
                continue  # the FK-resolution twin, owned by legacy bootstrap
            ddl = _ddl_columns(conn, table.name)
            assert ddl, f"{table.name} missing from the migrated schema"
            for column in table.columns:
                assert column.name in ddl, f"{table.name}.{column.name} missing in DDL"
    finally:
        conn.close()


def test_migration_ddl_has_no_columns_the_orm_does_not_know(tmp_path: Path) -> None:
    db = tmp_path / "consistency-reverse.sqlite"
    migrations.run_migrations(db)
    conn = sqlite3.connect(db)
    try:
        for table in metadata.sorted_tables:
            ddl = _ddl_columns(conn, table.name)
            orm = {column.name for column in table.columns}
            extra = set(ddl) - orm
            assert not extra, f"{table.name}: DDL has columns unknown to the ORM: {extra}"
    finally:
        conn.close()


def test_harness_tables_are_not_on_the_legacy_base(tmp_path: Path) -> None:
    """create_all must never create harness_* tables: they belong to the
    versioned runner alone."""
    from pharos.db.models import Base
    from pharos.db.session import _add_missing_columns, _configure_sqlite
    from sqlalchemy import create_engine, event

    db = tmp_path / "legacy-only.sqlite"
    engine = create_engine(f"sqlite:///{db}", future=True)
    event.listen(engine, "connect", _configure_sqlite)
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
    engine.dispose()
    conn = sqlite3.connect(db)
    try:
        legacy_names = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()
    for table in metadata.sorted_tables:
        if table.name == "users":
            continue  # the FK-resolution twin, owned by legacy bootstrap
        assert (
            table.name not in legacy_names
        ), f"{table.name} was created by legacy bootstrap; the migration runner owns it"


def test_key_constraints_present(tmp_path: Path) -> None:
    db = tmp_path / "constraints.sqlite"
    migrations.run_migrations(db)
    conn = sqlite3.connect(db)
    try:
        unique_indexes = conn.execute("PRAGMA index_list(harness_runs)").fetchall()
        runs_unique = {row[1] for row in unique_indexes if row[2] == 1}
        # The idempotency unique and the scope unique are both unique indexes.
        assert len(runs_unique) >= 2
        head_check = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='harness_config_head'"
        ).fetchone()[0]
        assert "singleton" in head_check
        events_seq = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='harness_events'"
        ).fetchone()[0]
        assert "AUTOINCREMENT" in events_seq
    finally:
        conn.close()


def test_runtime_provenance_constraints_and_indexes_present(tmp_path: Path) -> None:
    db = tmp_path / "runtime-schema.sqlite"
    migrations.run_migrations(db)
    conn = sqlite3.connect(db)
    try:
        table_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='harness_attempts'"
        ).fetchone()[0]
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(harness_attempts)")}
    finally:
        conn.close()
    assert (
        "delivery_state IN ('not_started','sent','acknowledged','unknown','reconciled')"
        in table_sql
    )
    assert "not_started" in table_sql
    assert "acknowledged" in table_sql
    assert "unknown" in table_sql
    assert "reconciled" in table_sql
    assert {
        "ux_harness_attempts_runtime_session_active",
        "ix_harness_attempts_child_pid",
        "ix_harness_attempts_deadline",
        "ix_harness_attempts_delivery",
    } <= indexes


def test_lease_columns_are_integers(tmp_path: Path) -> None:
    """Lease math is epoch microseconds, never SQLite datetimes."""
    db = tmp_path / "epoch.sqlite"
    migrations.run_migrations(db)
    conn = sqlite3.connect(db)
    try:
        for column in ("lease_expires_at", "heartbeat_at", "ready_at", "created_at"):
            info = conn.execute("PRAGMA table_info(harness_steps)").fetchall()
            found = {row[1]: row[2] for row in info}
            assert found[column].upper() in (
                "INTEGER",
                "INT",
            ), f"harness_steps.{column} must be an epoch integer, got {found[column]}"
    finally:
        conn.close()
