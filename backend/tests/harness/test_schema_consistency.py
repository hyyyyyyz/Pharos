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
from sqlalchemy.dialects.sqlite import dialect as sqlite_dialect
from sqlalchemy.schema import CreateIndex, CreateTable


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
        usage_indexes = {
            row[1] for row in conn.execute("PRAGMA index_list(harness_usage_events)")
        }
        assert {
            "ux_harness_usage_reservation_reserve",
            "ux_harness_usage_reservation_outcome",
        } <= usage_indexes
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
        attempt_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(harness_attempts)")
        }
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(harness_attempts)")}
        artifact_indexes = {
            row[1] for row in conn.execute("PRAGMA index_list(harness_artifacts)")
        }
        artifact_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(harness_artifacts)")
        }
        launch_trigger_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
            "AND name = 'ck_harness_attempt_launch_identity_immutable'"
        ).fetchone()[0]
        artifact_source_trigger_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
            "AND name = 'ck_harness_artifacts_provenance_source_insert'"
        ).fetchone()[0]
        artifact_immutable_trigger_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
            "AND name = 'ck_harness_artifacts_provenance_immutable'"
        ).fetchone()[0]
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
    assert "runtime_message_id" in attempt_columns
    assert {
        "producer_attempt_id",
        "upstream_commit",
        "runtime_session_id",
        "runtime_hash",
        "profile_hash",
        "policy_hash",
        "protocol_version",
        "route_key",
        "route_sha256",
        "definition_binding_sha256",
        "run_policy_sha256",
        "provenance_sha256",
    } <= artifact_columns
    assert {
        "ux_harness_attempts_runtime_session_active",
        "ux_harness_attempts_runtime_session",
        "ix_harness_attempts_child_pid",
        "ux_harness_attempts_child_pid_active",
        "ix_harness_attempts_deadline",
        "ix_harness_attempts_delivery",
    } <= indexes
    assert {
        "ix_harness_artifacts_producer_attempt",
        "ux_harness_artifacts_producer_attempt",
    } <= artifact_indexes
    for column in (
        "upstream_commit",
        "runtime_hash",
        "profile_hash",
        "policy_hash",
        "protocol_version",
        "runtime_session_id",
        "deadline_at",
    ):
        assert f"OLD.{column}" in launch_trigger_sql
    for mutable_column in ("child_pid", "delivery_state", "runtime_message_id"):
        assert mutable_column not in launch_trigger_sql
    assert "s.executor_kind = 'role'" in artifact_source_trigger_sql
    assert "s.executor_kind = 'capability'" in artifact_source_trigger_sql
    assert "NEW.producer_kind = 'model_inference'" in artifact_source_trigger_sql
    assert "NEW.producer_kind = 'deterministic'" in artifact_source_trigger_sql
    assert "JOIN harness_run_definition_snapshots r" in artifact_source_trigger_sql
    assert "a.state = 'succeeded'" in artifact_source_trigger_sql
    assert "a.input_sha256 = NEW.input_sha256" in artifact_source_trigger_sql
    assert "a.output_sha256 = NEW.content_sha256" in artifact_source_trigger_sql
    assert "r.workflow_key = NEW.workflow_key" in artifact_source_trigger_sql
    assert "r.workflow_version = NEW.workflow_version" in artifact_source_trigger_sql
    assert "json_extract(d.definition_json, '$.output_schema')" in artifact_source_trigger_sql
    assert (
        "json_extract(d.definition_json, '$.observation_schema')"
        in artifact_source_trigger_sql
    )
    for forbidden_capability_runtime in (
        "NEW.upstream_commit IS NULL",
        "NEW.runtime_session_id IS NULL",
        "NEW.provider IS NULL",
        "NEW.model IS NULL",
    ):
        assert forbidden_capability_runtime in artifact_source_trigger_sql
    for immutable_column in (
        "id",
        "scope_type",
        "scope_id",
        "user_id",
        "run_id",
        "step_id",
        "producer_attempt_id",
        "artifact_type",
        "content_json",
        "blob_sha256",
        "content_sha256",
        "schema_name",
        "schema_version",
        "mime",
        "size_bytes",
        "sensitivity",
        "producer_kind",
        "workflow_key",
        "workflow_version",
        "role_prompt_version",
        "provider",
        "model",
        "input_artifact_ids_json",
        "input_sha256",
        "source_refs_json",
        "quality_status",
        "evidence_level",
        "created_at",
    ):
        assert f"OLD.{immutable_column}" in artifact_immutable_trigger_sql
    assert "NEW.deleted_at IS NOT NULL" in artifact_immutable_trigger_sql
    assert "NEW.content_json IS NULL" in artifact_immutable_trigger_sql

    artifact_metadata_sql = str(
        CreateTable(metadata.tables["harness_artifacts"]).compile(dialect=sqlite_dialect())
    )
    for mirrored_requirement in (
        "workflow_key IS NOT NULL",
        "workflow_version IS NOT NULL",
        "input_sha256 IS NOT NULL",
        "producer_kind = 'model_inference'",
        "producer_kind = 'deterministic'",
        "role_prompt_version IS NOT NULL",
        "role_prompt_version IS NULL",
    ):
        assert mirrored_requirement in artifact_metadata_sql

    metadata_indexes = metadata.tables["harness_attempts"].indexes
    runtime_session_index = next(
        index for index in metadata_indexes if index.name == "ux_harness_attempts_runtime_session"
    )
    child_pid_index = next(
        index for index in metadata_indexes if index.name == "ux_harness_attempts_child_pid_active"
    )
    assert runtime_session_index.unique is True
    assert child_pid_index.unique is True
    producer_attempt_index = next(
        index
        for index in metadata.tables["harness_artifacts"].indexes
        if index.name == "ux_harness_artifacts_producer_attempt"
    )
    assert producer_attempt_index.unique is True
    runtime_sql = str(CreateIndex(runtime_session_index).compile(dialect=sqlite_dialect()))
    child_pid_sql = str(CreateIndex(child_pid_index).compile(dialect=sqlite_dialect()))
    producer_attempt_sql = str(
        CreateIndex(producer_attempt_index).compile(dialect=sqlite_dialect())
    )
    assert (
        "ON harness_attempts (runtime_session_id) WHERE runtime_session_id IS NOT NULL"
        in runtime_sql
    )
    assert (
        "ON harness_attempts (child_pid) WHERE child_pid IS NOT NULL "
        "AND state IN ('leased', 'running')"
        in child_pid_sql
    )
    assert (
        "ON harness_artifacts (producer_attempt_id) "
        "WHERE producer_attempt_id IS NOT NULL"
        in producer_attempt_sql
    )


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


def test_definition_binding_schema_contract_is_bidirectionally_pinned(tmp_path: Path) -> None:
    db = tmp_path / "definition-binding-schema.sqlite"
    migrations.run_migrations(db)
    expected_indexes = {
        "ux_harness_workflow_versions_key_version_hash",
        "ux_harness_bindings_identity",
        "ux_harness_runs_scope_workflow_definition",
        "ux_harness_attempts_scope_run",
        "ux_harness_roles_executor_profile",
        "ux_harness_run_snapshots_scope_binding_policy",
        "ix_harness_run_definition_snapshots_binding",
        "ix_harness_attempt_definition_snapshots_run",
        "ix_harness_attempt_definition_snapshots_profile",
        "ux_harness_usage_reservation_reserve",
        "ux_harness_usage_reservation_outcome",
    }
    conn = sqlite3.connect(db)
    try:
        indexes = {
            row[1]
            for table in (
                "harness_workflow_versions",
                "harness_runs",
                "harness_attempts",
                "harness_workflow_definition_bindings",
                "harness_role_versions",
                "harness_run_definition_snapshots",
                "harness_attempt_definition_snapshots",
                "harness_usage_events",
            )
            for row in conn.execute(f"PRAGMA index_list({table})")
        }
        assert expected_indexes <= indexes
        trigger_names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        expected_triggers = {
            "ck_harness_workflows_immutable_update",
            "ck_harness_workflows_immutable_delete",
            "ck_harness_model_profiles_immutable_update",
            "ck_harness_model_profiles_immutable_delete",
            "ck_harness_capabilities_immutable_update",
            "ck_harness_capabilities_immutable_delete",
            "ck_harness_roles_immutable_update",
            "ck_harness_roles_immutable_delete",
            "ck_harness_bindings_immutable_update",
            "ck_harness_bindings_immutable_delete",
            "ck_harness_run_snapshot_parent_policy",
            "ck_harness_run_snapshot_immutable_update",
            "ck_harness_run_snapshot_immutable_delete",
            "ck_harness_attempt_snapshot_immutable_update",
            "ck_harness_attempt_snapshot_immutable_delete",
            "ck_harness_run_parent_snapshot_update",
            "ck_harness_run_parent_snapshot_delete",
            "ck_harness_attempt_parent_snapshot_update",
            "ck_harness_attempt_parent_snapshot_delete",
            "ck_harness_run_snapshot_creation_parent",
            "ck_harness_run_snapshot_execution_identity_update",
            "ck_harness_step_attempt_snapshot_update",
            "ck_harness_attempt_launch_identity_immutable",
            "ck_harness_artifacts_provenance_scope_insert",
            "ck_harness_artifacts_provenance_required_insert",
            "ck_harness_artifacts_provenance_source_insert",
            "ck_harness_artifacts_provenance_required_update",
            "ck_harness_artifacts_provenance_scope_update",
            "ck_harness_artifacts_provenance_immutable",
        }
        assert trigger_names == expected_triggers
        for table in (
            "harness_model_profile_versions",
            "harness_capability_versions",
            "harness_role_versions",
            "harness_workflow_definition_bindings",
            "harness_run_definition_snapshots",
            "harness_attempt_definition_snapshots",
        ):
            assert {
                row[1]
                for row in conn.execute(f"PRAGMA index_list({table})")
                if row[2] == 1
            }, f"{table} must expose identity/hash unique constraints"

        binding_fk = conn.execute(
            "PRAGMA foreign_key_list(harness_workflow_definition_bindings)"
        ).fetchall()
        assert {
            (row[2], row[3], row[4]) for row in binding_fk
        } >= {
            (
                "harness_workflow_versions",
                "workflow_key",
                "workflow_key",
            ),
            (
                "harness_workflow_versions",
                "workflow_version",
                "version",
            ),
            (
                "harness_workflow_versions",
                "workflow_definition_sha256",
                "definition_sha256",
            ),
        }
        role_fk = conn.execute("PRAGMA foreign_key_list(harness_role_versions)").fetchall()
        assert {
            (row[2], row[3], row[4]) for row in role_fk
        } >= {
            (
                "harness_model_profile_versions",
                "model_profile_key",
                "profile_key",
            ),
            (
                "harness_model_profile_versions",
                "model_profile_version",
                "version",
            ),
            (
                "harness_model_profile_versions",
                "model_profile_sha256",
                "definition_sha256",
            ),
        }
        run_snapshot_fk = conn.execute(
            "PRAGMA foreign_key_list(harness_run_definition_snapshots)"
        ).fetchall()
        assert {
            (row[2], row[3], row[4]) for row in run_snapshot_fk
        } >= {
            (
                "harness_runs",
                "run_id",
                "id",
            ),
            (
                "harness_workflow_definition_bindings",
                "definition_binding_sha256",
                "binding_sha256",
            ),
            (
                "harness_workflow_definition_bindings",
                "workflow_definition_sha256",
                "workflow_definition_sha256",
            ),
        }
        attempt_snapshot_fk = conn.execute(
            "PRAGMA foreign_key_list(harness_attempt_definition_snapshots)"
        ).fetchall()
        assert {
            (row[2], row[3], row[4]) for row in attempt_snapshot_fk
        } >= {
            (
                "harness_run_definition_snapshots",
                "run_id",
                "run_id",
            ),
            (
                "harness_run_definition_snapshots",
                "definition_binding_sha256",
                "definition_binding_sha256",
            ),
            (
                "harness_run_definition_snapshots",
                "run_policy_sha256",
                "policy_snapshot_sha256",
            ),
            (
                "harness_model_profile_versions",
                "model_profile_key",
                "profile_key",
            ),
        }
    finally:
        conn.close()


def test_definition_snapshot_pk_nullability_fk_actions_and_indexes_are_exact(
    tmp_path: Path,
) -> None:
    db = tmp_path / "definition-binding-exact-schema.sqlite"
    migrations.run_migrations(db)
    conn = sqlite3.connect(db)
    try:
        definition_primary_keys = {
            "harness_model_profile_versions": "id",
            "harness_capability_versions": "id",
            "harness_role_versions": "id",
            "harness_workflow_definition_bindings": "binding_sha256",
        }
        for table_name, primary_key in definition_primary_keys.items():
            info = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            actual = next(row for row in info if row[1] == primary_key)
            assert actual[3] == 1, f"{table_name}.{primary_key} must be NOT NULL"
            assert actual[5] == 1, f"{table_name}.{primary_key} must be the sole primary key"
            table = metadata.tables[table_name]
            assert [column.name for column in table.primary_key.columns] == [primary_key]
            assert table.c[primary_key].nullable is False

        for table_name in (
            "harness_run_definition_snapshots",
            "harness_attempt_definition_snapshots",
        ):
            info = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            assert sum(row[5] > 0 for row in info) == 1
            assert info[0][5] == 1
            table = metadata.tables[table_name]
            assert {row[1] for row in info if row[5] > 0} == {
                column.name for column in table.primary_key.columns
            }
            assert {row[1]: row[3] == 0 for row in info} == {
                column.name: column.nullable for column in table.columns
            }
        run_info = conn.execute("PRAGMA table_info(harness_run_definition_snapshots)").fetchall()
        assert all(row[3] == 1 for row in run_info)
        assert {
            row[1]
            for row in conn.execute("PRAGMA table_info(harness_run_definition_snapshots)")
            if row[3] == 0
        } == set()
        assert {
            row[1]
            for row in conn.execute("PRAGMA table_info(harness_attempt_definition_snapshots)")
            if row[3] == 0
        } == {
            "executor_role_key",
            "executor_role_version",
            "executor_role_definition_sha256",
            "executor_capability_key",
            "executor_capability_version",
            "executor_capability_definition_sha256",
            "model_profile_identity",
            "model_profile_key",
            "model_profile_version",
            "model_profile_sha256",
            "model_route_key",
            "model_route_sha256",
            "provider",
            "model",
            "usage_source",
        }

        FkGroup = tuple[tuple[str, str, str, str, str], ...]

        def fk_groups(table_name: str) -> set[FkGroup]:
            rows = conn.execute(f"PRAGMA foreign_key_list({table_name})").fetchall()
            grouped: dict[int, list[tuple[str, str, str, str, str]]] = {}
            for row in rows:
                grouped.setdefault(row[0], []).append(
                    (row[2], row[3], row[4], row[5], row[6])
                )
            return {tuple(grouped[key]) for key in grouped}

        expected_run_fks: set[FkGroup] = {
            (
                (
                    "harness_workflow_definition_bindings",
                    "definition_binding_sha256",
                    "binding_sha256",
                    "NO ACTION",
                    "NO ACTION",
                ),
                (
                    "harness_workflow_definition_bindings",
                    "workflow_key",
                    "workflow_key",
                    "NO ACTION",
                    "NO ACTION",
                ),
                (
                    "harness_workflow_definition_bindings",
                    "workflow_version",
                    "workflow_version",
                    "NO ACTION",
                    "NO ACTION",
                ),
                (
                    "harness_workflow_definition_bindings",
                    "workflow_definition_sha256",
                    "workflow_definition_sha256",
                    "NO ACTION",
                    "NO ACTION",
                ),
            ),
            (
                (
                    "harness_workflow_versions",
                    "workflow_key",
                    "workflow_key",
                    "NO ACTION",
                    "NO ACTION",
                ),
                (
                    "harness_workflow_versions",
                    "workflow_version",
                    "version",
                    "NO ACTION",
                    "NO ACTION",
                ),
                (
                    "harness_workflow_versions",
                    "workflow_definition_sha256",
                    "definition_sha256",
                    "NO ACTION",
                    "NO ACTION",
                ),
            ),
            (
                ("harness_runs", "run_id", "id", "NO ACTION", "NO ACTION"),
                (
                    "harness_runs",
                    "scope_type",
                    "scope_type",
                    "NO ACTION",
                    "NO ACTION",
                ),
                ("harness_runs", "scope_id", "scope_id", "NO ACTION", "NO ACTION"),
                (
                    "harness_runs",
                    "workflow_key",
                    "workflow_key",
                    "NO ACTION",
                    "NO ACTION",
                ),
                (
                    "harness_runs",
                    "workflow_version",
                    "workflow_version",
                    "NO ACTION",
                    "NO ACTION",
                ),
                (
                    "harness_runs",
                    "workflow_definition_sha256",
                    "definition_sha256",
                    "NO ACTION",
                    "NO ACTION",
                ),
            ),
        }
        expected_attempt_fks: set[FkGroup] = {
            (
                (
                    "harness_model_profile_versions",
                    "model_profile_key",
                    "profile_key",
                    "NO ACTION",
                    "NO ACTION",
                ),
                (
                    "harness_model_profile_versions",
                    "model_profile_version",
                    "version",
                    "NO ACTION",
                    "NO ACTION",
                ),
                (
                    "harness_model_profile_versions",
                    "model_profile_sha256",
                    "definition_sha256",
                    "NO ACTION",
                    "NO ACTION",
                ),
            ),
            (
                (
                    "harness_capability_versions",
                    "executor_capability_key",
                    "capability_key",
                    "NO ACTION",
                    "NO ACTION",
                ),
                (
                    "harness_capability_versions",
                    "executor_capability_version",
                    "version",
                    "NO ACTION",
                    "NO ACTION",
                ),
                (
                    "harness_capability_versions",
                    "executor_capability_definition_sha256",
                    "definition_sha256",
                    "NO ACTION",
                    "NO ACTION",
                ),
            ),
            (
                (
                    "harness_role_versions",
                    "executor_role_key",
                    "role_key",
                    "NO ACTION",
                    "NO ACTION",
                ),
                (
                    "harness_role_versions",
                    "executor_role_version",
                    "version",
                    "NO ACTION",
                    "NO ACTION",
                ),
                (
                    "harness_role_versions",
                    "executor_role_definition_sha256",
                    "definition_sha256",
                    "NO ACTION",
                    "NO ACTION",
                ),
                (
                    "harness_role_versions",
                    "model_profile_key",
                    "model_profile_key",
                    "NO ACTION",
                    "NO ACTION",
                ),
                (
                    "harness_role_versions",
                    "model_profile_version",
                    "model_profile_version",
                    "NO ACTION",
                    "NO ACTION",
                ),
                (
                    "harness_role_versions",
                    "model_profile_sha256",
                    "model_profile_sha256",
                    "NO ACTION",
                    "NO ACTION",
                ),
            ),
            (
                (
                    "harness_run_definition_snapshots",
                    "run_id",
                    "run_id",
                    "NO ACTION",
                    "NO ACTION",
                ),
                (
                    "harness_run_definition_snapshots",
                    "scope_type",
                    "scope_type",
                    "NO ACTION",
                    "NO ACTION",
                ),
                (
                    "harness_run_definition_snapshots",
                    "scope_id",
                    "scope_id",
                    "NO ACTION",
                    "NO ACTION",
                ),
                (
                    "harness_run_definition_snapshots",
                    "definition_binding_sha256",
                    "definition_binding_sha256",
                    "NO ACTION",
                    "NO ACTION",
                ),
                (
                    "harness_run_definition_snapshots",
                    "run_policy_sha256",
                    "policy_snapshot_sha256",
                    "NO ACTION",
                    "NO ACTION",
                ),
            ),
            (
                ("harness_attempts", "attempt_id", "id", "NO ACTION", "NO ACTION"),
                (
                    "harness_attempts",
                    "run_id",
                    "run_id",
                    "NO ACTION",
                    "NO ACTION",
                ),
                (
                    "harness_attempts",
                    "scope_type",
                    "scope_type",
                    "NO ACTION",
                    "NO ACTION",
                ),
                (
                    "harness_attempts",
                    "scope_id",
                    "scope_id",
                    "NO ACTION",
                    "NO ACTION",
                ),
                (
                    "harness_attempts",
                    "step_id",
                    "step_id",
                    "NO ACTION",
                    "NO ACTION",
                ),
                (
                    "harness_attempts",
                    "attempt_no",
                    "attempt_no",
                    "NO ACTION",
                    "NO ACTION",
                ),
            ),
        }
        assert fk_groups("harness_run_definition_snapshots") == expected_run_fks
        assert fk_groups("harness_attempt_definition_snapshots") == expected_attempt_fks

        for table_name in (
            "harness_run_definition_snapshots",
            "harness_attempt_definition_snapshots",
        ):
            table = metadata.tables[table_name]
            metadata_fks: set[FkGroup] = set()
            for constraint in table.foreign_key_constraints:
                metadata_fks.add(
                    tuple(
                        (
                            element.column.table.name,
                            element.parent.name,
                            element.column.name,
                            constraint.onupdate or "NO ACTION",
                            constraint.ondelete or "NO ACTION",
                        )
                        for element in constraint.elements
                    )
                )
            assert fk_groups(table_name) == metadata_fks

        exact_indexes = {
            "ux_harness_workflow_versions_key_version_hash": (
                "workflow_key", "version", "definition_sha256"
            ),
            "ux_harness_bindings_identity": (
                "binding_sha256", "workflow_key", "workflow_version", "workflow_definition_sha256"
            ),
            "ux_harness_runs_scope_workflow_definition": (
                "id", "scope_type", "scope_id", "workflow_key", "workflow_version",
                "definition_sha256",
            ),
            "ux_harness_attempts_scope_run": (
                "id", "run_id", "scope_type", "scope_id", "step_id", "attempt_no"
            ),
            "ux_harness_roles_executor_profile": (
                "role_key", "version", "definition_sha256", "model_profile_key",
                "model_profile_version", "model_profile_sha256",
            ),
            "ux_harness_run_snapshots_scope_binding_policy": (
                "run_id", "scope_type", "scope_id", "definition_binding_sha256",
                "policy_snapshot_sha256",
            ),
        }
        for name, expected in exact_indexes.items():
            table = conn.execute(
                "SELECT tbl_name FROM sqlite_master WHERE type='index' AND name=?", (name,)
            ).fetchone()[0]
            actual = tuple(
                row[2]
                for row in conn.execute(f"PRAGMA index_info({name})").fetchall()
            )
            assert actual == expected, (name, table, actual)
    finally:
        conn.close()


def test_attempt_snapshot_check_constraints_are_mirrored(tmp_path: Path) -> None:
    db = tmp_path / "definition-binding-check-parity.sqlite"
    migrations.run_migrations(db)
    conn = sqlite3.connect(db)
    try:
        migrated_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='harness_attempt_definition_snapshots'"
        ).fetchone()[0]
    finally:
        conn.close()
    metadata_sql = str(
        CreateTable(metadata.tables["harness_attempt_definition_snapshots"]).compile(
            dialect=sqlite_dialect()
        )
    )
    migrated_sql = " ".join(migrated_sql.split())
    metadata_sql = " ".join(metadata_sql.split())
    critical_checks = (
        "model_profile_identity IS NULL OR length(model_profile_identity) BETWEEN 1 AND 128",
        "model_profile_key IS NULL OR length(model_profile_key) BETWEEN 1 AND 64",
        "model_route_key IS NULL OR length(model_route_key) BETWEEN 1 AND 64",
        "provider IS NULL OR length(provider) BETWEEN 1 AND 64",
        "model IS NULL OR length(model) BETWEEN 1 AND 128",
        "model_profile_sha256 IS NULL OR (length(model_profile_sha256) = 64",
        "model_route_sha256 IS NULL OR (length(model_route_sha256) = 64",
        "usage_source IS NULL OR usage_source IN ('official','byok','system_shared')",
    )
    for expression in critical_checks:
        assert expression in migrated_sql
        assert expression in metadata_sql
