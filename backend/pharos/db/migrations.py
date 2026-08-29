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


def _harness_ddl() -> tuple[str, ...]:
    """The H1 Harness schema, split into revisions.

    Written as literal SQL rather than generated from ORM metadata so that a
    revision's checksum really means "this revision will always run exactly
    this SQL". The ORM mirror in :mod:`pharos.harness.tables` must match this
    DDL; a test pins the two together.

    All lease/heartbeat/deadline times are UTC Unix epoch microseconds, never
    SQLite datetimes. Owner scope is repeated on every user-visible table and
    bound by composite foreign keys so a row can never silently change scope.
    """

    return (
        # 0002: definitions and the single configuration authority.
        """
        CREATE TABLE harness_workflow_versions (
            id TEXT PRIMARY KEY,
            workflow_key TEXT NOT NULL,
            version INTEGER NOT NULL,
            definition_json TEXT NOT NULL,
            definition_sha256 TEXT NOT NULL,
            input_schema TEXT NOT NULL,
            output_schema TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (workflow_key, version),
            UNIQUE (workflow_key, definition_sha256)
        )
        """,
        """
        CREATE TABLE harness_config_revisions (
            id TEXT PRIMARY KEY,
            parent_revision_id TEXT,
            snapshot_json TEXT NOT NULL,
            snapshot_sha256 TEXT NOT NULL,
            gates_json TEXT NOT NULL,
            actor TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE harness_config_workflow_routes (
            revision_id TEXT NOT NULL REFERENCES harness_config_revisions(id),
            workflow_key TEXT NOT NULL,
            active_version INTEGER,
            activation_state TEXT NOT NULL DEFAULT 'disabled'
                CHECK (activation_state IN ('active','deprecated','disabled')),
            execution_mode TEXT
                CHECK (execution_mode IN ('legacy','shadow','harness')),
            FOREIGN KEY (workflow_key, active_version)
                REFERENCES harness_workflow_versions(workflow_key, version),
            UNIQUE (revision_id, workflow_key)
        )
        """,
        """
        CREATE TABLE harness_config_head (
            head_key TEXT PRIMARY KEY CHECK (head_key = 'singleton'),
            current_revision_id TEXT REFERENCES harness_config_revisions(id),
            updated_at TEXT NOT NULL
        )
        """,
        # 0003: runs.
        """
        CREATE TABLE harness_runs (
            id TEXT PRIMARY KEY,
            scope_type TEXT NOT NULL CHECK (scope_type IN ('user','system')),
            scope_id TEXT NOT NULL,
            user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
            workflow_key TEXT NOT NULL,
            workflow_version INTEGER NOT NULL,
            definition_sha256 TEXT NOT NULL,
            config_revision_id TEXT NOT NULL REFERENCES harness_config_revisions(id),
            state TEXT NOT NULL
                CHECK (state IN ('queued','running','waiting_for_approval',
                    'waiting_for_input','paused','succeeded','failed',
                    'cancelled','indeterminate')),
            outcome TEXT CHECK (outcome IN ('complete','partial','incomplete')),
            input_json TEXT NOT NULL,
            input_sha256 TEXT NOT NULL,
            policy_snapshot_json TEXT,
            budget_json TEXT,
            usage_json TEXT NOT NULL DEFAULT '{}',
            initiator TEXT NOT NULL DEFAULT 'user'
                CHECK (initiator IN ('user','schedule','operator','child_run')),
            idempotency_key TEXT NOT NULL,
            parent_run_id TEXT,
            project_id TEXT,
            priority INTEGER NOT NULL DEFAULT 0,
            cancel_requested_at INTEGER,
            pause_requested_at INTEGER,
            created_at INTEGER NOT NULL,
            started_at INTEGER,
            updated_at INTEGER NOT NULL,
            finished_at INTEGER,
            error_code TEXT,
            error_message TEXT,
            UNIQUE (scope_type, scope_id, workflow_key, idempotency_key),
            UNIQUE (id, scope_type, scope_id)
        )
        """,
        # 0004: steps and attempts.
        """
        CREATE TABLE harness_steps (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            definition_step_key TEXT NOT NULL,
            instance_key TEXT NOT NULL,
            step_kind TEXT NOT NULL
                CHECK (step_kind IN ('deterministic','agent','mapped','mapped_agent')),
            definition_json TEXT NOT NULL,
            state TEXT NOT NULL
                CHECK (state IN ('pending','ready','leased','running',
                    'waiting_for_approval','waiting_for_input',
                    'retry_scheduled','succeeded','failed','cancelled',
                    'skipped','indeterminate')),
            depends_on_json TEXT NOT NULL DEFAULT '[]',
            fan_in TEXT CHECK (fan_in IN ('all_success','all_terminal',
                'min_success','allow_partial')),
            min_success_count INTEGER,
            input_artifact_ids_json TEXT NOT NULL DEFAULT '[]',
            output_artifact_id TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            ready_at INTEGER,
            timeout_seconds REAL,
            retry_policy_json TEXT,
            lease_owner TEXT,
            lease_expires_at INTEGER,
            heartbeat_at INTEGER,
            waiting_reason TEXT CHECK (waiting_reason IN ('budget','configuration',
                'device_offline','user_input','credential')),
            error_code TEXT,
            error_message TEXT,
            skip_reason TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            finished_at INTEGER,
            UNIQUE (run_id, definition_step_key, instance_key),
            UNIQUE (id, scope_type, scope_id),
            FOREIGN KEY (run_id, scope_type, scope_id)
                REFERENCES harness_runs(id, scope_type, scope_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE harness_attempts (
            id TEXT PRIMARY KEY,
            step_id TEXT NOT NULL REFERENCES harness_steps(id) ON DELETE CASCADE,
            run_id TEXT NOT NULL,
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            attempt_no INTEGER NOT NULL,
            worker_id TEXT,
            state TEXT NOT NULL
                CHECK (state IN ('leased','running','succeeded','failed',
                    'timed_out','cancelled','abandoned','blocked',
                    'indeterminate')),
            role_or_capability TEXT,
            model_prompt_version TEXT,
            input_sha256 TEXT,
            output_sha256 TEXT,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cost_micros INTEGER NOT NULL DEFAULT 0,
            duration_us INTEGER,
            request_count INTEGER NOT NULL DEFAULT 0,
            retryable INTEGER NOT NULL DEFAULT 0,
            external_outcome TEXT,
            provider_request_id TEXT,
            error_class TEXT,
            error_code TEXT,
            error_message TEXT,
            lease_owner TEXT,
            started_at INTEGER,
            heartbeat_at INTEGER,
            finished_at INTEGER,
            UNIQUE (step_id, attempt_no),
            FOREIGN KEY (run_id, scope_type, scope_id)
                REFERENCES harness_runs(id, scope_type, scope_id) ON DELETE CASCADE
        )
        """,
        # 0005: the append-only event log.
        """
        CREATE TABLE harness_events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            step_id TEXT,
            attempt_id TEXT,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL
        )
        """,
        "CREATE INDEX ix_harness_events_run_seq ON harness_events (run_id, seq)",
        "CREATE INDEX ix_harness_events_scope_seq ON harness_events (scope_type, scope_id, seq)",
        # 0006: artifacts, links, public releases and projections.
        """
        CREATE TABLE harness_artifacts (
            id TEXT PRIMARY KEY,
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
            run_id TEXT NOT NULL,
            step_id TEXT,
            artifact_type TEXT NOT NULL,
            schema_name TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            mime TEXT NOT NULL DEFAULT 'application/json',
            content_json TEXT,
            blob_sha256 TEXT,
            content_sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            sensitivity TEXT NOT NULL DEFAULT 'private'
                CHECK (sensitivity IN ('public','private','local_only','secret')),
            producer_kind TEXT NOT NULL DEFAULT 'deterministic'
                CHECK (producer_kind IN ('rule_summary','model_inference',
                    'human_note','quote','deterministic')),
            workflow_key TEXT,
            workflow_version INTEGER,
            role_prompt_version TEXT,
            provider TEXT,
            model TEXT,
            input_artifact_ids_json TEXT NOT NULL DEFAULT '[]',
            input_sha256 TEXT,
            source_refs_json TEXT NOT NULL DEFAULT '[]',
            quality_status TEXT CHECK (quality_status IN ('valid','partial',
                'insufficient_evidence','invalid')),
            evidence_level TEXT CHECK (evidence_level IN ('metadata_only',
                'abstract_only','unlocated','page')),
            deleted_at INTEGER,
            deletion_reason TEXT,
            created_at INTEGER NOT NULL,
            UNIQUE (id, scope_type, scope_id),
            FOREIGN KEY (run_id, scope_type, scope_id)
                REFERENCES harness_runs(id, scope_type, scope_id) ON DELETE CASCADE
        )
        """,
        "CREATE INDEX ix_harness_artifacts_run ON harness_artifacts (run_id)",
        """
        CREATE TABLE harness_artifact_links (
            id TEXT PRIMARY KEY,
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            from_artifact_id TEXT NOT NULL,
            to_artifact_id TEXT NOT NULL,
            link_kind TEXT NOT NULL CHECK (link_kind IN ('derived_from','supports',
                'contradicts','critiques','supersedes','published_as')),
            created_at INTEGER NOT NULL,
            FOREIGN KEY (from_artifact_id, scope_type, scope_id)
                REFERENCES harness_artifacts(id, scope_type, scope_id) ON DELETE CASCADE,
            FOREIGN KEY (to_artifact_id, scope_type, scope_id)
                REFERENCES harness_artifacts(id, scope_type, scope_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE harness_public_artifact_releases (
            id TEXT PRIMARY KEY,
            source_artifact_id TEXT NOT NULL REFERENCES harness_artifacts(id),
            source_schema_name TEXT NOT NULL,
            source_schema_version INTEGER NOT NULL,
            source_content_sha256 TEXT NOT NULL,
            public_manifest_sha256 TEXT NOT NULL,
            release_policy_version TEXT NOT NULL,
            release_sha256 TEXT NOT NULL UNIQUE,
            revoked_at INTEGER,
            created_at INTEGER NOT NULL
        )
        """,
        """
        CREATE TABLE harness_public_artifact_projections (
            id TEXT PRIMARY KEY,
            release_id TEXT NOT NULL
                REFERENCES harness_public_artifact_releases(id),
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            projection_artifact_id TEXT NOT NULL REFERENCES harness_artifacts(id),
            release_sha256 TEXT NOT NULL,
            projection_schema_name TEXT NOT NULL,
            projection_schema_version INTEGER NOT NULL,
            projection_sha256 TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            UNIQUE (release_id, user_id, projection_schema_name,
                projection_schema_version)
        )
        """,
        # 0007: approvals, schedules, usage ledger.
        """
        CREATE TABLE harness_approvals (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            step_id TEXT,
            requesting_attempt_id TEXT,
            consumed_by_attempt_id TEXT,
            action TEXT NOT NULL,
            resource_json TEXT NOT NULL,
            risk TEXT NOT NULL DEFAULT 'write_private',
            effect_summary_json TEXT NOT NULL DEFAULT '{}',
            request_hash TEXT NOT NULL,
            state TEXT NOT NULL
                CHECK (state IN ('pending','approved','rejected','expired','cancelled')),
            request_json TEXT NOT NULL DEFAULT '{}',
            decision_json TEXT NOT NULL DEFAULT '{}',
            resolver_user_id TEXT,
            resolver_reason TEXT,
            requested_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            resolved_at INTEGER,
            FOREIGN KEY (run_id, scope_type, scope_id)
                REFERENCES harness_runs(id, scope_type, scope_id) ON DELETE CASCADE
        )
        """,
        "CREATE INDEX ix_harness_approvals_state ON harness_approvals (state, expires_at)",
        """
        CREATE TABLE harness_schedules (
            id TEXT PRIMARY KEY,
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            workflow_key TEXT NOT NULL,
            spec_json TEXT NOT NULL,
            input_json TEXT NOT NULL DEFAULT '{}',
            next_due_at INTEGER NOT NULL,
            last_evaluated_at INTEGER,
            last_run_id TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            UNIQUE (scope_type, scope_id, workflow_key)
        )
        """,
        """
        CREATE TABLE harness_usage_events (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            step_id TEXT,
            attempt_id TEXT,
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            source TEXT NOT NULL
                CHECK (source IN ('official','byok','system_shared')),
            kind TEXT NOT NULL CHECK (kind IN ('model_tokens','search_request',
                'download_bytes','translation_pages','compute_ms')),
            reservation_id TEXT,
            op TEXT NOT NULL CHECK (op IN ('reserve','settle','release')),
            amount INTEGER NOT NULL,
            cost_micros INTEGER NOT NULL DEFAULT 0,
            provider TEXT,
            model TEXT,
            created_at INTEGER NOT NULL
        )
        """,
        "CREATE INDEX ix_harness_usage_run ON harness_usage_events (run_id)",
    )


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        revision="0001_schema_ledger",
        description="Establish the versioned migration ledger",
        statements=(_ledger_ddl(),),
    ),
    Migration(
        revision="0002_harness_definitions_config",
        description="Harness workflow definitions and the configuration revision authority",
        statements=_harness_ddl()[0:4],
    ),
    Migration(
        revision="0003_harness_runs",
        description="Durable Harness runs with owner scope and idempotency",
        statements=_harness_ddl()[4:5],
    ),
    Migration(
        revision="0004_harness_steps_attempts",
        description="Harness steps and frozen attempt rows",
        statements=_harness_ddl()[5:7],
    ),
    Migration(
        revision="0005_harness_events",
        description="Append-only Harness event log with monotonic cursor",
        statements=_harness_ddl()[7:10],
    ),
    Migration(
        revision="0006_harness_artifacts_links_releases",
        description="Immutable Harness artifacts, links and public releases",
        statements=_harness_ddl()[10:16],
    ),
    Migration(
        revision="0007_harness_approvals_schedules_usage",
        description="Harness approvals, schedules and the usage ledger",
        statements=_harness_ddl()[16:21],
    ),
    Migration(
        revision="0008_harness_attempt_runtime_provenance",
        description="Add auditable, secret-free runtime provenance to Harness attempts",
        statements=(
            # These columns are intentionally nullable: existing attempts were
            # created before the runtime adapter existed and must not be
            # backfilled with invented process/session evidence.
            "ALTER TABLE harness_attempts ADD COLUMN runtime_session_id TEXT",
            "ALTER TABLE harness_attempts ADD COLUMN child_pid INTEGER",
            "ALTER TABLE harness_attempts ADD COLUMN deadline_at INTEGER",
            "ALTER TABLE harness_attempts ADD COLUMN upstream_commit TEXT",
            "ALTER TABLE harness_attempts ADD COLUMN runtime_hash TEXT",
            "ALTER TABLE harness_attempts ADD COLUMN profile_hash TEXT",
            "ALTER TABLE harness_attempts ADD COLUMN policy_hash TEXT",
            "ALTER TABLE harness_attempts ADD COLUMN protocol_version TEXT",
            "ALTER TABLE harness_attempts ADD COLUMN delivery_state TEXT CHECK "
            "(delivery_state IN ('not_started','sent','acknowledged','unknown','reconciled'))",
            # Recovery/reconciliation scans these independently of the step
            # queue, so each lookup key has a narrow index.
            "CREATE UNIQUE INDEX ux_harness_attempts_runtime_session_active "
            "ON harness_attempts (runtime_session_id) "
            "WHERE runtime_session_id IS NOT NULL AND state IN ('leased','running')",
            "CREATE INDEX ix_harness_attempts_child_pid ON harness_attempts (child_pid)",
            "CREATE INDEX ix_harness_attempts_deadline ON harness_attempts (state, deadline_at)",
            "CREATE INDEX ix_harness_attempts_delivery ON harness_attempts (delivery_state)",
        ),
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
