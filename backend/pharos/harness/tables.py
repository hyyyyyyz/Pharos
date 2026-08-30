"""SQLAlchemy Core tables for the Harness, on their own metadata.

These tables are deliberately NOT on ``pharos.db.models.Base``: they are
created exclusively by the versioned migration runner, and ``create_all``
must never touch them. The repository layer reads and writes through these
Core tables (plus ORM-friendly helpers), while the DDL in
:mod:`pharos.db.migrations` remains the source of truth -- a test pins the
two together so they cannot drift.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()

#: Referenced by harness foreign keys. Declared in this metadata so SQLAlchemy
#: Core can resolve ``users.id`` without mixing the legacy Base metadata in;
#: the REAL table is created and owned by the legacy bootstrap. This twin is
#: never created (the migration runner owns all harness DDL) -- it exists only
#: for FK resolution and schema-consistency checks.
users = Table("users", metadata, Column("id", Text, primary_key=True))

#: State vocabularies shared by the tables (mirrors contracts.py).
RUN_STATES = (
    "queued",
    "running",
    "waiting_for_approval",
    "waiting_for_input",
    "paused",
    "succeeded",
    "failed",
    "cancelled",
    "indeterminate",
)
STEP_STATES = (
    "pending",
    "ready",
    "leased",
    "running",
    "waiting_for_approval",
    "waiting_for_input",
    "retry_scheduled",
    "succeeded",
    "failed",
    "cancelled",
    "skipped",
    "indeterminate",
)
ATTEMPT_STATES = (
    "leased",
    "running",
    "succeeded",
    "failed",
    "timed_out",
    "cancelled",
    "abandoned",
    "blocked",
    "indeterminate",
)
# Runtime delivery is deliberately separate from Attempt state: an Attempt
# may be indeterminate even when the parent process cannot prove whether the
# provider accepted the request.  NULL means this pre-runtime Attempt has no
# runtime delivery record; non-NULL values are the complete audit vocabulary.
RUNTIME_DELIVERY_STATES = (
    "not_started",
    "sent",
    "acknowledged",
    "unknown",
    "reconciled",
)

workflow_versions = Table(
    "harness_workflow_versions",
    metadata,
    Column("id", Text, primary_key=True),
    Column("workflow_key", Text, nullable=False),
    Column("version", Integer, nullable=False),
    Column("definition_json", Text, nullable=False),
    Column("definition_sha256", Text, nullable=False),
    Column("input_schema", Text, nullable=False),
    Column("output_schema", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    UniqueConstraint("workflow_key", "version", name="uq_harness_workflow_versions_key_version"),
    UniqueConstraint(
        "workflow_key", "definition_sha256", name="uq_harness_workflow_versions_key_hash"
    ),
)


def _sha256_check(column: str, name: str) -> CheckConstraint:
    """Return the shared nullable/non-nullable lower-hex SHA-256 check."""
    return CheckConstraint(
        f"length({column}) = 64 AND {column} NOT GLOB '*[^0-9a-f]*'",
        name=name,
    )


def _nullable_sha256_check(column: str, name: str) -> CheckConstraint:
    return CheckConstraint(
        f"{column} IS NULL OR (length({column}) = 64 AND "
        f"{column} NOT GLOB '*[^0-9a-f]*')",
        name=name,
    )


model_profile_versions = Table(
    "harness_model_profile_versions",
    metadata,
    Column("id", Text, primary_key=True),
    Column("profile_key", Text, nullable=False),
    Column("version", Integer, nullable=False),
    Column("definition_json", Text, nullable=False),
    Column("definition_sha256", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    CheckConstraint("length(profile_key) BETWEEN 1 AND 64", name="ck_harness_model_profiles_key"),
    CheckConstraint("version > 0", name="ck_harness_model_profiles_version_positive"),
    _sha256_check("definition_sha256", "ck_harness_model_profiles_definition_sha256"),
    UniqueConstraint("profile_key", "version", name="uq_harness_model_profiles_key_version"),
    UniqueConstraint(
        "profile_key", "definition_sha256", name="uq_harness_model_profiles_key_hash"
    ),
    UniqueConstraint(
        "profile_key",
        "version",
        "definition_sha256",
        name="uq_harness_model_profiles_key_version_hash",
    ),
    UniqueConstraint("definition_sha256", name="uq_harness_model_profiles_hash"),
)

capability_versions = Table(
    "harness_capability_versions",
    metadata,
    Column("id", Text, primary_key=True),
    Column("capability_key", Text, nullable=False),
    Column("version", Integer, nullable=False),
    Column("definition_json", Text, nullable=False),
    Column("definition_sha256", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    CheckConstraint("length(capability_key) BETWEEN 1 AND 64", name="ck_harness_capabilities_key"),
    CheckConstraint("version > 0", name="ck_harness_capabilities_version_positive"),
    _sha256_check("definition_sha256", "ck_harness_capabilities_definition_sha256"),
    UniqueConstraint("capability_key", "version", name="uq_harness_capabilities_key_version"),
    UniqueConstraint(
        "capability_key", "definition_sha256", name="uq_harness_capabilities_key_hash"
    ),
    UniqueConstraint(
        "capability_key",
        "version",
        "definition_sha256",
        name="uq_harness_capabilities_key_version_hash",
    ),
    UniqueConstraint("definition_sha256", name="uq_harness_capabilities_hash"),
)

role_versions = Table(
    "harness_role_versions",
    metadata,
    Column("id", Text, primary_key=True),
    Column("role_key", Text, nullable=False),
    Column("version", Integer, nullable=False),
    Column("definition_json", Text, nullable=False),
    Column("definition_sha256", Text, nullable=False),
    Column("runtime_kind", Text, nullable=False),
    Column("model_profile_key", Text, nullable=False),
    Column("model_profile_version", Integer, nullable=False),
    Column("model_profile_sha256", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    CheckConstraint("length(role_key) BETWEEN 1 AND 64", name="ck_harness_roles_key"),
    CheckConstraint("version > 0", name="ck_harness_roles_version_positive"),
    _sha256_check("definition_sha256", "ck_harness_roles_definition_sha256"),
    CheckConstraint(
        "runtime_kind IN ('in_process_fake','dsh')", name="ck_harness_roles_runtime_kind"
    ),
    CheckConstraint(
        "length(model_profile_key) BETWEEN 1 AND 64",
        name="ck_harness_roles_model_profile_key",
    ),
    CheckConstraint(
        "model_profile_version > 0", name="ck_harness_roles_profile_version_positive"
    ),
    _sha256_check("model_profile_sha256", "ck_harness_roles_profile_sha256"),
    UniqueConstraint("role_key", "version", name="uq_harness_roles_key_version"),
    UniqueConstraint("role_key", "definition_sha256", name="uq_harness_roles_key_hash"),
    UniqueConstraint(
        "role_key", "version", "definition_sha256", name="uq_harness_roles_key_version_hash"
    ),
    UniqueConstraint("definition_sha256", name="uq_harness_roles_hash"),
    ForeignKeyConstraint(
        ["model_profile_key", "model_profile_version", "model_profile_sha256"],
        [
            "harness_model_profile_versions.profile_key",
            "harness_model_profile_versions.version",
            "harness_model_profile_versions.definition_sha256",
        ],
        name="fk_harness_roles_model_profile",
    ),
)

workflow_definition_bindings = Table(
    "harness_workflow_definition_bindings",
    metadata,
    Column("binding_sha256", Text, primary_key=True),
    Column("schema_version", Integer, nullable=False),
    Column("workflow_key", Text, nullable=False),
    Column("workflow_version", Integer, nullable=False),
    Column("workflow_definition_sha256", Text, nullable=False),
    Column("binding_json", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    _sha256_check("binding_sha256", "ck_harness_bindings_sha256"),
    CheckConstraint(
        "length(workflow_key) BETWEEN 1 AND 64", name="ck_harness_bindings_workflow_key"
    ),
    CheckConstraint("schema_version = 1", name="ck_harness_bindings_schema_version"),
    CheckConstraint("workflow_version > 0", name="ck_harness_bindings_workflow_version_positive"),
    _sha256_check(
        "workflow_definition_sha256", "ck_harness_bindings_workflow_definition_sha256"
    ),
    UniqueConstraint(
        "workflow_key", "workflow_version", name="uq_harness_bindings_workflow_version"
    ),
    ForeignKeyConstraint(
        ["workflow_key", "workflow_version", "workflow_definition_sha256"],
        [
            "harness_workflow_versions.workflow_key",
            "harness_workflow_versions.version",
            "harness_workflow_versions.definition_sha256",
        ],
        name="fk_harness_bindings_workflow_definition",
    ),
)

run_definition_snapshots = Table(
    "harness_run_definition_snapshots",
    metadata,
    Column("run_id", Text, primary_key=True),
    Column("scope_type", Text, nullable=False),
    Column("scope_id", Text, nullable=False),
    Column("workflow_key", Text, nullable=False),
    Column("workflow_version", Integer, nullable=False),
    Column("workflow_definition_sha256", Text, nullable=False),
    Column("definition_binding_sha256", Text, nullable=False),
    Column("policy_snapshot_schema_version", Integer, nullable=False),
    Column("policy_snapshot_sha256", Text, nullable=False),
    Column("policy_snapshot_json", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    CheckConstraint("workflow_version > 0", name="ck_harness_run_snapshots_workflow_version"),
    CheckConstraint(
        "policy_snapshot_schema_version = 1",
        name="ck_harness_run_snapshots_policy_schema",
    ),
    _sha256_check(
        "workflow_definition_sha256", "ck_harness_run_snapshots_workflow_definition_sha256"
    ),
    _sha256_check("definition_binding_sha256", "ck_harness_run_snapshots_binding_sha256"),
    _sha256_check("policy_snapshot_sha256", "ck_harness_run_snapshots_policy_sha256"),
    UniqueConstraint(
        "run_id", "scope_type", "scope_id", "workflow_key", "workflow_version",
        "workflow_definition_sha256", "definition_binding_sha256",
        "policy_snapshot_schema_version", "policy_snapshot_sha256",
        name="uq_harness_run_snapshots_identity",
    ),
    ForeignKeyConstraint(
        [
            "run_id", "scope_type", "scope_id", "workflow_key", "workflow_version",
            "workflow_definition_sha256",
        ],
        [
            "harness_runs.id", "harness_runs.scope_type", "harness_runs.scope_id",
            "harness_runs.workflow_key", "harness_runs.workflow_version",
            "harness_runs.definition_sha256",
        ],
        name="fk_harness_run_snapshots_run_identity",
    ),
    ForeignKeyConstraint(
        ["workflow_key", "workflow_version", "workflow_definition_sha256"],
        [
            "harness_workflow_versions.workflow_key",
            "harness_workflow_versions.version",
            "harness_workflow_versions.definition_sha256",
        ],
        name="fk_harness_run_snapshots_workflow",
    ),
    ForeignKeyConstraint(
        [
            "definition_binding_sha256",
            "workflow_key",
            "workflow_version",
            "workflow_definition_sha256",
        ],
        [
            "harness_workflow_definition_bindings.binding_sha256",
            "harness_workflow_definition_bindings.workflow_key",
            "harness_workflow_definition_bindings.workflow_version",
            "harness_workflow_definition_bindings.workflow_definition_sha256",
        ],
        name="fk_harness_run_snapshots_binding",
    ),
)

attempt_definition_snapshots = Table(
    "harness_attempt_definition_snapshots",
    metadata,
    Column("attempt_id", Text, primary_key=True),
    Column("run_id", Text, nullable=False),
    Column("scope_type", Text, nullable=False),
    Column("scope_id", Text, nullable=False),
    Column("step_id", Text, nullable=False),
    Column("attempt_no", Integer, nullable=False),
    Column("definition_binding_sha256", Text, nullable=False),
    Column("run_policy_sha256", Text, nullable=False),
    Column("executor_kind", Text, nullable=False),
    Column("executor_identity", Text, nullable=False),
    Column("executor_role_key", Text, nullable=True),
    Column("executor_role_version", Integer, nullable=True),
    Column("executor_role_definition_sha256", Text, nullable=True),
    Column("executor_capability_key", Text, nullable=True),
    Column("executor_capability_version", Integer, nullable=True),
    Column("executor_capability_definition_sha256", Text, nullable=True),
    Column("model_profile_identity", Text, nullable=True),
    Column("model_profile_key", Text, nullable=True),
    Column("model_profile_version", Integer, nullable=True),
    Column("model_profile_sha256", Text, nullable=True),
    Column("model_route_key", Text, nullable=True),
    Column("model_route_sha256", Text, nullable=True),
    Column("provider", Text, nullable=True),
    Column("model", Text, nullable=True),
    Column("usage_source", Text, nullable=True),
    Column("created_at", Text, nullable=False),
    CheckConstraint(
        "executor_kind IN ('role','capability')", name="ck_harness_attempt_snapshots_executor_kind"
    ),
    CheckConstraint("attempt_no > 0", name="ck_harness_attempt_snapshots_attempt_no"),
    CheckConstraint(
        "length(executor_identity) BETWEEN 1 AND 128",
        name="ck_harness_attempt_snapshots_executor_identity",
    ),
    CheckConstraint(
        "((executor_kind = 'role' AND "
        "executor_identity = executor_role_key || '@' || executor_role_version AND "
        "executor_role_key IS NOT NULL AND "
        "executor_role_version IS NOT NULL AND executor_role_definition_sha256 IS NOT NULL "
        "AND executor_capability_key IS NULL AND executor_capability_version IS NULL AND "
        "executor_capability_definition_sha256 IS NULL AND model_profile_identity IS NOT NULL "
        "AND model_profile_key IS NOT NULL AND model_profile_version IS NOT NULL AND "
        "model_profile_sha256 IS NOT NULL AND model_route_key IS NOT NULL AND "
        "model_route_sha256 IS NOT NULL AND provider IS NOT NULL AND model IS NOT NULL AND "
        "usage_source IS NOT NULL) OR (executor_kind = 'capability' AND "
        "executor_role_key IS NULL AND executor_role_version IS NULL AND "
        "executor_role_definition_sha256 IS NULL AND "
        "executor_identity = executor_capability_key || '@' || "
        "executor_capability_version AND executor_capability_key IS NOT NULL AND "
        "executor_capability_version IS NOT NULL AND "
        "executor_capability_definition_sha256 IS NOT NULL "
        "AND model_profile_identity IS NULL AND model_profile_key IS NULL AND "
        "model_profile_version IS NULL AND model_profile_sha256 IS NULL AND "
        "model_route_key IS NULL "
        "AND model_route_sha256 IS NULL AND provider IS NULL AND model IS NULL AND "
        "usage_source IS NULL))",
        name="ck_harness_attempt_snapshots_executor_exactly_one",
    ),
    CheckConstraint(
        "usage_source IS NULL OR usage_source IN ('official','byok','system_shared')",
        name="ck_harness_attempt_snapshots_usage_source",
    ),
    CheckConstraint(
        "model_profile_version IS NULL OR model_profile_version > 0",
        name="ck_harness_attempt_snapshots_profile_version"
    ),
    CheckConstraint(
        "model_profile_identity IS NULL OR length(model_profile_identity) BETWEEN 1 AND 128",
        name="ck_harness_attempt_snapshots_profile_identity_length",
    ),
    CheckConstraint(
        "model_profile_key IS NULL OR length(model_profile_key) BETWEEN 1 AND 64",
        name="ck_harness_attempt_snapshots_profile_key",
    ),
    CheckConstraint(
        "model_route_key IS NULL OR length(model_route_key) BETWEEN 1 AND 64",
        name="ck_harness_attempt_snapshots_route_key",
    ),
    CheckConstraint(
        "provider IS NULL OR length(provider) BETWEEN 1 AND 64",
        name="ck_harness_attempt_snapshots_provider",
    ),
    CheckConstraint(
        "model IS NULL OR length(model) BETWEEN 1 AND 128",
        name="ck_harness_attempt_snapshots_model",
    ),
    CheckConstraint(
        "model_profile_identity IS NULL OR model_profile_identity = "
        "model_profile_key || '@' || model_profile_version",
        name="ck_harness_attempt_snapshots_profile_identity",
    ),
    _sha256_check("definition_binding_sha256", "ck_harness_attempt_snapshots_binding_sha256"),
    _sha256_check("run_policy_sha256", "ck_harness_attempt_snapshots_run_policy_sha256"),
    CheckConstraint(
        "model_profile_sha256 IS NULL OR (length(model_profile_sha256) = 64 AND "
        "model_profile_sha256 NOT GLOB '*[^0-9a-f]*')",
        name="ck_harness_attempt_snapshots_profile_sha256",
    ),
    CheckConstraint(
        "model_route_sha256 IS NULL OR (length(model_route_sha256) = 64 AND "
        "model_route_sha256 NOT GLOB '*[^0-9a-f]*')",
        name="ck_harness_attempt_snapshots_route_sha256",
    ),
    CheckConstraint(
        "executor_role_key IS NULL OR length(executor_role_key) BETWEEN 1 AND 64",
        name="ck_harness_attempt_snapshots_role_key",
    ),
    CheckConstraint(
        "executor_capability_key IS NULL OR length(executor_capability_key) BETWEEN 1 AND 64",
        name="ck_harness_attempt_snapshots_capability_key",
    ),
    CheckConstraint(
        "executor_role_version IS NULL OR executor_role_version > 0",
        name="ck_harness_attempt_snapshots_role_version",
    ),
    CheckConstraint(
        "executor_capability_version IS NULL OR executor_capability_version > 0",
        name="ck_harness_attempt_snapshots_capability_version",
    ),
    _nullable_sha256_check(
        "executor_role_definition_sha256", "ck_harness_attempt_snapshots_role_sha256"
    ),
    _nullable_sha256_check(
        "executor_capability_definition_sha256", "ck_harness_attempt_snapshots_capability_sha256"
    ),
    UniqueConstraint(
        "attempt_id", "run_id", "scope_type", "scope_id", "step_id", "attempt_no",
        name="uq_harness_attempt_snapshots_scope",
    ),
    ForeignKeyConstraint(
        ["attempt_id", "run_id", "scope_type", "scope_id", "step_id", "attempt_no"],
        [
            "harness_attempts.id",
            "harness_attempts.run_id",
            "harness_attempts.scope_type",
            "harness_attempts.scope_id",
            "harness_attempts.step_id",
            "harness_attempts.attempt_no",
        ],
        name="fk_harness_attempt_snapshots_attempt_scope",
    ),
    ForeignKeyConstraint(
        [
            "run_id", "scope_type", "scope_id", "definition_binding_sha256", "run_policy_sha256"
        ],
        [
            "harness_run_definition_snapshots.run_id",
            "harness_run_definition_snapshots.scope_type",
            "harness_run_definition_snapshots.scope_id",
            "harness_run_definition_snapshots.definition_binding_sha256",
            "harness_run_definition_snapshots.policy_snapshot_sha256",
        ],
        name="fk_harness_attempt_snapshots_run_binding",
    ),
    ForeignKeyConstraint(
        ["model_profile_key", "model_profile_version", "model_profile_sha256"],
        [
            "harness_model_profile_versions.profile_key",
            "harness_model_profile_versions.version",
            "harness_model_profile_versions.definition_sha256",
        ],
        name="fk_harness_attempt_snapshots_model_profile",
    ),
    ForeignKeyConstraint(
        [
            "executor_role_key", "executor_role_version", "executor_role_definition_sha256",
            "model_profile_key", "model_profile_version", "model_profile_sha256",
        ],
        [
            "harness_role_versions.role_key", "harness_role_versions.version",
            "harness_role_versions.definition_sha256",
            "harness_role_versions.model_profile_key",
            "harness_role_versions.model_profile_version",
            "harness_role_versions.model_profile_sha256",
        ],
        name="fk_harness_attempt_snapshots_executor_role",
    ),
    ForeignKeyConstraint(
        [
            "executor_capability_key",
            "executor_capability_version",
            "executor_capability_definition_sha256",
        ],
        [
            "harness_capability_versions.capability_key", "harness_capability_versions.version",
            "harness_capability_versions.definition_sha256",
        ],
        name="fk_harness_attempt_snapshots_executor_capability",
    ),
)

config_revisions = Table(
    "harness_config_revisions",
    metadata,
    Column("id", Text, primary_key=True),
    Column("parent_revision_id", Text, nullable=True),
    Column("snapshot_json", Text, nullable=False),
    Column("snapshot_sha256", Text, nullable=False),
    Column("gates_json", Text, nullable=False),
    Column("actor", Text, nullable=False, default=""),
    Column("reason", Text, nullable=False, default=""),
    Column("created_at", Text, nullable=False),
)

config_workflow_routes = Table(
    "harness_config_workflow_routes",
    metadata,
    Column("revision_id", Text, ForeignKey("harness_config_revisions.id"), nullable=False),
    Column("workflow_key", Text, nullable=False),
    Column("active_version", Integer, nullable=True),
    Column("activation_state", Text, nullable=False, default="disabled"),
    Column("execution_mode", Text, nullable=True),
    CheckConstraint(
        "activation_state IN ('active','deprecated','disabled')",
        name="ck_harness_config_routes_state",
    ),
    CheckConstraint(
        "execution_mode IN ('legacy','shadow','harness')",
        name="ck_harness_config_routes_mode",
    ),
    UniqueConstraint("revision_id", "workflow_key", name="uq_harness_config_routes"),
)

config_head = Table(
    "harness_config_head",
    metadata,
    Column("head_key", Text, primary_key=True),
    Column("current_revision_id", Text, ForeignKey("harness_config_revisions.id")),
    Column("updated_at", Text, nullable=False),
    CheckConstraint("head_key = 'singleton'", name="ck_harness_config_head_singleton"),
)

runs = Table(
    "harness_runs",
    metadata,
    Column("id", Text, primary_key=True),
    Column("scope_type", Text, nullable=False),
    Column("scope_id", Text, nullable=False),
    Column("user_id", Text, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    Column("workflow_key", Text, nullable=False),
    Column("workflow_version", Integer, nullable=False),
    Column("definition_sha256", Text, nullable=False),
    Column("config_revision_id", Text, ForeignKey("harness_config_revisions.id"), nullable=False),
    Column("state", Text, nullable=False),
    Column("outcome", Text, nullable=True),
    Column("input_json", Text, nullable=False),
    Column("input_sha256", Text, nullable=False),
    Column("policy_snapshot_json", Text, nullable=True),
    Column("budget_json", Text, nullable=True),
    Column("usage_json", Text, nullable=False, default="{}"),
    Column("initiator", Text, nullable=False, default="user"),
    Column("idempotency_key", Text, nullable=False),
    Column("parent_run_id", Text, nullable=True),
    Column("project_id", Text, nullable=True),
    Column("priority", Integer, nullable=False, default=0),
    Column("cancel_requested_at", Integer, nullable=True),
    Column("pause_requested_at", Integer, nullable=True),
    Column("created_at", Integer, nullable=False),
    Column("started_at", Integer, nullable=True),
    Column("updated_at", Integer, nullable=False),
    Column("finished_at", Integer, nullable=True),
    Column("error_code", Text, nullable=True),
    Column("error_message", Text, nullable=True),
    CheckConstraint(
        "state IN ('queued','running','waiting_for_approval','waiting_for_input',"
        "'paused','succeeded','failed','cancelled','indeterminate')",
        name="ck_harness_runs_state",
    ),
    CheckConstraint(
        "outcome IS NULL OR outcome IN ('complete','partial','incomplete')",
        name="ck_harness_runs_outcome",
    ),
    CheckConstraint(
        "initiator IN ('user','schedule','operator','child_run')",
        name="ck_harness_runs_initiator",
    ),
    UniqueConstraint(
        "scope_type",
        "scope_id",
        "workflow_key",
        "idempotency_key",
        name="uq_harness_runs_idempotency",
    ),
    UniqueConstraint("id", "scope_type", "scope_id", name="uq_harness_runs_scope"),
)

steps = Table(
    "harness_steps",
    metadata,
    Column("id", Text, primary_key=True),
    Column("run_id", Text, nullable=False),
    Column("scope_type", Text, nullable=False),
    Column("scope_id", Text, nullable=False),
    Column("definition_step_key", Text, nullable=False),
    Column("instance_key", Text, nullable=False),
    Column("step_kind", Text, nullable=False),
    Column("definition_json", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("depends_on_json", Text, nullable=False, default="[]"),
    Column("fan_in", Text, nullable=True),
    Column("min_success_count", Integer, nullable=True),
    Column("input_artifact_ids_json", Text, nullable=False, default="[]"),
    Column("output_artifact_id", Text, nullable=True),
    Column("attempt_count", Integer, nullable=False, default=0),
    Column("max_attempts", Integer, nullable=False, default=3),
    Column("ready_at", Integer, nullable=True),
    Column("timeout_seconds", Text, nullable=True),
    Column("retry_policy_json", Text, nullable=True),
    Column("lease_owner", Text, nullable=True),
    Column("lease_expires_at", Integer, nullable=True),
    Column("heartbeat_at", Integer, nullable=True),
    Column("waiting_reason", Text, nullable=True),
    Column("error_code", Text, nullable=True),
    Column("error_message", Text, nullable=True),
    Column("skip_reason", Text, nullable=True),
    Column("created_at", Integer, nullable=False),
    Column("updated_at", Integer, nullable=False),
    Column("finished_at", Integer, nullable=True),
    CheckConstraint(
        "step_kind IN ('deterministic','agent','mapped','mapped_agent')",
        name="ck_harness_steps_kind",
    ),
    CheckConstraint(
        "state IN ('pending','ready','leased','running','waiting_for_approval',"
        "'waiting_for_input','retry_scheduled','succeeded','failed','cancelled',"
        "'skipped','indeterminate')",
        name="ck_harness_steps_state",
    ),
    CheckConstraint(
        "waiting_reason IS NULL OR waiting_reason IN ('budget','configuration',"
        "'device_offline','user_input','credential')",
        name="ck_harness_steps_waiting",
    ),
    UniqueConstraint(
        "run_id", "definition_step_key", "instance_key", name="uq_harness_steps_identity"
    ),
    UniqueConstraint("id", "scope_type", "scope_id", name="uq_harness_steps_scope"),
    ForeignKeyConstraint(
        ["run_id", "scope_type", "scope_id"],
        ["harness_runs.id", "harness_runs.scope_type", "harness_runs.scope_id"],
        ondelete="CASCADE",
        name="fk_harness_steps_run_scope",
    ),
)

attempts = Table(
    "harness_attempts",
    metadata,
    Column("id", Text, primary_key=True),
    Column("step_id", Text, ForeignKey("harness_steps.id", ondelete="CASCADE"), nullable=False),
    Column("run_id", Text, nullable=False),
    Column("scope_type", Text, nullable=False),
    Column("scope_id", Text, nullable=False),
    Column("attempt_no", Integer, nullable=False),
    Column("worker_id", Text, nullable=True),
    Column("state", Text, nullable=False),
    Column("role_or_capability", Text, nullable=True),
    Column("model_prompt_version", Text, nullable=True),
    Column("input_sha256", Text, nullable=True),
    Column("output_sha256", Text, nullable=True),
    Column("input_tokens", Integer, nullable=False, default=0),
    Column("output_tokens", Integer, nullable=False, default=0),
    Column("cost_micros", Integer, nullable=False, default=0),
    Column("duration_us", Integer, nullable=True),
    Column("request_count", Integer, nullable=False, default=0),
    Column("retryable", Integer, nullable=False, default=0),
    Column("external_outcome", Text, nullable=True),
    Column("provider_request_id", Text, nullable=True),
    Column("error_class", Text, nullable=True),
    Column("error_code", Text, nullable=True),
    Column("error_message", Text, nullable=True),
    Column("lease_owner", Text, nullable=True),
    Column("started_at", Integer, nullable=True),
    Column("heartbeat_at", Integer, nullable=True),
    Column("finished_at", Integer, nullable=True),
    # H1.5 runtime provenance.  These are audit metadata only: no credential,
    # prompt, response, or other secret-bearing payload is stored here.
    Column("runtime_session_id", Text, nullable=True),
    Column("runtime_message_id", Text, nullable=True),
    Column("child_pid", Integer, nullable=True),
    Column("deadline_at", Integer, nullable=True),
    Column("upstream_commit", Text, nullable=True),
    Column("runtime_hash", Text, nullable=True),
    Column("profile_hash", Text, nullable=True),
    Column("policy_hash", Text, nullable=True),
    Column("protocol_version", Text, nullable=True),
    Column("delivery_state", Text, nullable=True),
    CheckConstraint(
        "state IN ('leased','running','succeeded','failed','timed_out','cancelled',"
        "'abandoned','blocked','indeterminate')",
        name="ck_harness_attempts_state",
    ),
    CheckConstraint(
        "delivery_state IN ('not_started','sent','acknowledged','unknown','reconciled')",
        name="ck_harness_attempts_delivery_state",
    ),
    UniqueConstraint("step_id", "attempt_no", name="uq_harness_attempts_identity"),
    ForeignKeyConstraint(
        ["run_id", "scope_type", "scope_id"],
        ["harness_runs.id", "harness_runs.scope_type", "harness_runs.scope_id"],
        ondelete="CASCADE",
        name="fk_harness_attempts_run_scope",
    ),
)

events = Table(
    "harness_events",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Text, nullable=False),
    Column("scope_type", Text, nullable=False),
    Column("scope_id", Text, nullable=False),
    Column("step_id", Text, nullable=True),
    Column("attempt_id", Text, nullable=True),
    Column("event_type", Text, nullable=False),
    Column("payload_json", Text, nullable=False, default="{}"),
    Column("created_at", Integer, nullable=False),
)

artifacts = Table(
    "harness_artifacts",
    metadata,
    Column("id", Text, primary_key=True),
    Column("scope_type", Text, nullable=False),
    Column("scope_id", Text, nullable=False),
    Column("user_id", Text, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    Column("run_id", Text, nullable=False),
    Column("step_id", Text, nullable=True),
    Column("producer_attempt_id", Text, ForeignKey("harness_attempts.id"), nullable=True),
    Column("artifact_type", Text, nullable=False),
    Column("schema_name", Text, nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("mime", Text, nullable=False, default="application/json"),
    Column("content_json", Text, nullable=True),
    Column("blob_sha256", Text, nullable=True),
    Column("content_sha256", Text, nullable=False),
    Column("size_bytes", Integer, nullable=False, default=0),
    Column("sensitivity", Text, nullable=False, default="private"),
    Column("producer_kind", Text, nullable=False, default="deterministic"),
    Column("workflow_key", Text, nullable=True),
    Column("workflow_version", Integer, nullable=True),
    Column("role_prompt_version", Text, nullable=True),
    Column("provider", Text, nullable=True),
    Column("model", Text, nullable=True),
    Column("upstream_commit", Text, nullable=True),
    Column("runtime_session_id", Text, nullable=True),
    Column("runtime_hash", Text, nullable=True),
    Column("profile_hash", Text, nullable=True),
    Column("policy_hash", Text, nullable=True),
    Column("protocol_version", Text, nullable=True),
    Column("route_key", Text, nullable=True),
    Column("route_sha256", Text, nullable=True),
    Column("definition_binding_sha256", Text, nullable=True),
    Column("run_policy_sha256", Text, nullable=True),
    Column("provenance_sha256", Text, nullable=True),
    Column("input_artifact_ids_json", Text, nullable=False, default="[]"),
    Column("input_sha256", Text, nullable=True),
    Column("source_refs_json", Text, nullable=False, default="[]"),
    Column("quality_status", Text, nullable=True),
    Column("evidence_level", Text, nullable=True),
    Column("deleted_at", Integer, nullable=True),
    Column("deletion_reason", Text, nullable=True),
    Column("created_at", Integer, nullable=False),
    CheckConstraint(
        "sensitivity IN ('public','private','local_only','secret')",
        name="ck_harness_artifacts_sensitivity",
    ),
    CheckConstraint(
        "producer_kind IN ('rule_summary','model_inference','human_note','quote',"
        "'deterministic')",
        name="ck_harness_artifacts_producer",
    ),
    _nullable_sha256_check("runtime_hash", "ck_harness_artifacts_runtime_hash"),
    _nullable_sha256_check("profile_hash", "ck_harness_artifacts_profile_hash"),
    _nullable_sha256_check("policy_hash", "ck_harness_artifacts_policy_hash"),
    _nullable_sha256_check("route_sha256", "ck_harness_artifacts_route_sha256"),
    _nullable_sha256_check(
        "definition_binding_sha256",
        "ck_harness_artifacts_definition_binding_sha256",
    ),
    _nullable_sha256_check("run_policy_sha256", "ck_harness_artifacts_run_policy_sha256"),
    _nullable_sha256_check("provenance_sha256", "ck_harness_artifacts_provenance_sha256"),
    CheckConstraint(
        "upstream_commit IS NULL OR (length(upstream_commit) = 40 AND "
        "upstream_commit NOT GLOB '*[^0-9a-f]*')",
        name="ck_harness_artifacts_upstream_commit",
    ),
    CheckConstraint(
        "runtime_session_id IS NULL OR length(runtime_session_id) BETWEEN 1 AND 256",
        name="ck_harness_artifacts_runtime_session_id",
    ),
    CheckConstraint(
        "producer_attempt_id IS NOT NULL OR (upstream_commit IS NULL "
        "AND runtime_session_id IS NULL AND runtime_hash IS NULL AND profile_hash IS NULL "
        "AND policy_hash IS NULL AND protocol_version IS NULL AND route_key IS NULL "
        "AND route_sha256 IS NULL AND definition_binding_sha256 IS NULL "
        "AND run_policy_sha256 IS NULL AND provenance_sha256 IS NULL)",
        name="ck_harness_artifacts_provenance_detached",
    ),
    CheckConstraint(
        "producer_attempt_id IS NULL OR (step_id IS NOT NULL AND upstream_commit IS NOT NULL "
        "AND runtime_session_id IS NOT NULL AND runtime_hash IS NOT NULL "
        "AND profile_hash IS NOT NULL AND policy_hash IS NOT NULL "
        "AND protocol_version IS NOT NULL AND route_key IS NOT NULL "
        "AND route_sha256 IS NOT NULL AND provider IS NOT NULL AND model IS NOT NULL "
        "AND definition_binding_sha256 IS NOT NULL AND run_policy_sha256 IS NOT NULL "
        "AND provenance_sha256 IS NOT NULL)",
        name="ck_harness_artifacts_provenance_complete",
    ),
    UniqueConstraint("id", "scope_type", "scope_id", name="uq_harness_artifacts_scope"),
    ForeignKeyConstraint(
        ["run_id", "scope_type", "scope_id"],
        ["harness_runs.id", "harness_runs.scope_type", "harness_runs.scope_id"],
        ondelete="CASCADE",
        name="fk_harness_artifacts_run_scope",
    ),
)

artifact_links = Table(
    "harness_artifact_links",
    metadata,
    Column("id", Text, primary_key=True),
    Column("scope_type", Text, nullable=False),
    Column("scope_id", Text, nullable=False),
    Column("run_id", Text, nullable=False),
    Column("from_artifact_id", Text, nullable=False),
    Column("to_artifact_id", Text, nullable=False),
    Column("link_kind", Text, nullable=False),
    Column("created_at", Integer, nullable=False),
    CheckConstraint(
        "link_kind IN ('derived_from','supports','contradicts','critiques','supersedes',"
        "'published_as')",
        name="ck_harness_artifact_links_kind",
    ),
    ForeignKeyConstraint(
        ["from_artifact_id", "scope_type", "scope_id"],
        ["harness_artifacts.id", "harness_artifacts.scope_type", "harness_artifacts.scope_id"],
        ondelete="CASCADE",
        name="fk_harness_links_from_scope",
    ),
    ForeignKeyConstraint(
        ["to_artifact_id", "scope_type", "scope_id"],
        ["harness_artifacts.id", "harness_artifacts.scope_type", "harness_artifacts.scope_id"],
        ondelete="CASCADE",
        name="fk_harness_links_to_scope",
    ),
)

public_artifact_releases = Table(
    "harness_public_artifact_releases",
    metadata,
    Column("id", Text, primary_key=True),
    Column("source_artifact_id", Text, ForeignKey("harness_artifacts.id"), nullable=False),
    Column("source_schema_name", Text, nullable=False),
    Column("source_schema_version", Integer, nullable=False),
    Column("source_content_sha256", Text, nullable=False),
    Column("public_manifest_sha256", Text, nullable=False),
    Column("release_policy_version", Text, nullable=False),
    Column("release_sha256", Text, nullable=False, unique=True),
    Column("revoked_at", Integer, nullable=True),
    Column("created_at", Integer, nullable=False),
)

public_artifact_projections = Table(
    "harness_public_artifact_projections",
    metadata,
    Column("id", Text, primary_key=True),
    Column("release_id", Text, ForeignKey("harness_public_artifact_releases.id"), nullable=False),
    Column("user_id", Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("projection_artifact_id", Text, ForeignKey("harness_artifacts.id"), nullable=False),
    Column("release_sha256", Text, nullable=False),
    Column("projection_schema_name", Text, nullable=False),
    Column("projection_schema_version", Integer, nullable=False),
    Column("projection_sha256", Text, nullable=False),
    Column("created_at", Integer, nullable=False),
    UniqueConstraint(
        "release_id",
        "user_id",
        "projection_schema_name",
        "projection_schema_version",
        name="uq_harness_projections",
    ),
)

approvals = Table(
    "harness_approvals",
    metadata,
    Column("id", Text, primary_key=True),
    Column("run_id", Text, nullable=False),
    Column("scope_type", Text, nullable=False),
    Column("scope_id", Text, nullable=False),
    Column("step_id", Text, nullable=True),
    Column("requesting_attempt_id", Text, nullable=True),
    Column("consumed_by_attempt_id", Text, nullable=True),
    Column("action", Text, nullable=False),
    Column("resource_json", Text, nullable=False),
    Column("risk", Text, nullable=False, default="write_private"),
    Column("effect_summary_json", Text, nullable=False, default="{}"),
    Column("request_hash", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("request_json", Text, nullable=False, default="{}"),
    Column("decision_json", Text, nullable=False, default="{}"),
    Column("resolver_user_id", Text, nullable=True),
    Column("resolver_reason", Text, nullable=True),
    Column("requested_at", Integer, nullable=False),
    Column("expires_at", Integer, nullable=False),
    Column("resolved_at", Integer, nullable=True),
    CheckConstraint(
        "state IN ('pending','approved','rejected','expired','cancelled')",
        name="ck_harness_approvals_state",
    ),
    ForeignKeyConstraint(
        ["run_id", "scope_type", "scope_id"],
        ["harness_runs.id", "harness_runs.scope_type", "harness_runs.scope_id"],
        ondelete="CASCADE",
        name="fk_harness_approvals_run_scope",
    ),
)

schedules = Table(
    "harness_schedules",
    metadata,
    Column("id", Text, primary_key=True),
    Column("scope_type", Text, nullable=False),
    Column("scope_id", Text, nullable=False),
    Column("workflow_key", Text, nullable=False),
    Column("spec_json", Text, nullable=False),
    Column("input_json", Text, nullable=False, default="{}"),
    Column("next_due_at", Integer, nullable=False),
    Column("last_evaluated_at", Integer, nullable=True),
    Column("last_run_id", Text, nullable=True),
    Column("enabled", Integer, nullable=False, default=1),
    Column("created_at", Integer, nullable=False),
    Column("updated_at", Integer, nullable=False),
    UniqueConstraint("scope_type", "scope_id", "workflow_key", name="uq_harness_schedules"),
)

usage_events = Table(
    "harness_usage_events",
    metadata,
    Column("id", Text, primary_key=True),
    Column("run_id", Text, nullable=False),
    Column("step_id", Text, nullable=True),
    Column("attempt_id", Text, nullable=True),
    Column("scope_type", Text, nullable=False),
    Column("scope_id", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("reservation_id", Text, nullable=True),
    Column("op", Text, nullable=False),
    Column("amount", Integer, nullable=False),
    Column("cost_micros", Integer, nullable=False, default=0),
    Column("provider", Text, nullable=True),
    Column("model", Text, nullable=True),
    Column("created_at", Integer, nullable=False),
    CheckConstraint(
        "source IN ('official','byok','system_shared')", name="ck_harness_usage_source"
    ),
    CheckConstraint(
        "kind IN ('model_tokens','search_request','download_bytes','translation_pages',"
        "'compute_ms')",
        name="ck_harness_usage_kind",
    ),
    CheckConstraint("op IN ('reserve','settle','release')", name="ck_harness_usage_op"),
)

Index("ix_harness_events_run_seq", events.c.run_id, events.c.seq)
Index("ix_harness_events_scope_seq", events.c.scope_type, events.c.scope_id, events.c.seq)
Index("ix_harness_artifacts_run", artifacts.c.run_id)
Index("ix_harness_approvals_state", approvals.c.state, approvals.c.expires_at)
Index("ix_harness_usage_run", usage_events.c.run_id)
Index(
    "ux_harness_usage_reservation_reserve",
    usage_events.c.reservation_id,
    unique=True,
    sqlite_where=(
        usage_events.c.reservation_id.is_not(None) & (usage_events.c.op == "reserve")
    ),
)
Index(
    "ux_harness_usage_reservation_outcome",
    usage_events.c.reservation_id,
    unique=True,
    sqlite_where=(
        usage_events.c.reservation_id.is_not(None)
        & usage_events.c.op.in_(["settle", "release"])
    ),
)
Index("ix_harness_steps_ready", steps.c.state, steps.c.ready_at)
Index(
    "ux_harness_workflow_versions_key_version_hash",
    workflow_versions.c.workflow_key,
    workflow_versions.c.version,
    workflow_versions.c.definition_sha256,
    unique=True,
)
Index(
    "ux_harness_bindings_identity",
    workflow_definition_bindings.c.binding_sha256,
    workflow_definition_bindings.c.workflow_key,
    workflow_definition_bindings.c.workflow_version,
    workflow_definition_bindings.c.workflow_definition_sha256,
    unique=True,
)
Index(
    "ux_harness_runs_scope_workflow_definition",
    runs.c.id,
    runs.c.scope_type,
    runs.c.scope_id,
    runs.c.workflow_key,
    runs.c.workflow_version,
    runs.c.definition_sha256,
    unique=True,
)
Index(
    "ux_harness_attempts_scope_run",
    attempts.c.id,
    attempts.c.run_id,
    attempts.c.scope_type,
    attempts.c.scope_id,
    attempts.c.step_id,
    attempts.c.attempt_no,
    unique=True,
)
Index(
    "ux_harness_roles_executor_profile",
    role_versions.c.role_key,
    role_versions.c.version,
    role_versions.c.definition_sha256,
    role_versions.c.model_profile_key,
    role_versions.c.model_profile_version,
    role_versions.c.model_profile_sha256,
    unique=True,
)
Index(
    "ux_harness_attempts_runtime_session_active",
    attempts.c.runtime_session_id,
    unique=True,
    sqlite_where=(
        attempts.c.runtime_session_id.is_not(None)
        & attempts.c.state.in_(["leased", "running"])
    ),
)
# 0009 identity fences.  Runtime sessions are globally unique whenever
# recorded, including terminal/indeterminate Attempts; PIDs are unique only
# while their owning Attempt is leased or running because the OS may reuse a
# PID after a process exits.
Index(
    "ux_harness_attempts_runtime_session",
    attempts.c.runtime_session_id,
    unique=True,
    sqlite_where=attempts.c.runtime_session_id.is_not(None),
)
Index("ix_harness_attempts_child_pid", attempts.c.child_pid)
Index(
    "ux_harness_attempts_child_pid_active",
    attempts.c.child_pid,
    unique=True,
    sqlite_where=(
        attempts.c.child_pid.is_not(None) & attempts.c.state.in_(["leased", "running"])
    ),
)
Index("ix_harness_attempts_deadline", attempts.c.state, attempts.c.deadline_at)
Index("ix_harness_attempts_delivery", attempts.c.delivery_state)
Index(
    "ux_harness_run_snapshots_scope_binding_policy",
    run_definition_snapshots.c.run_id,
    run_definition_snapshots.c.scope_type,
    run_definition_snapshots.c.scope_id,
    run_definition_snapshots.c.definition_binding_sha256,
    run_definition_snapshots.c.policy_snapshot_sha256,
    unique=True,
)
Index(
    "ix_harness_run_definition_snapshots_binding",
    run_definition_snapshots.c.definition_binding_sha256,
)
Index("ix_harness_attempt_definition_snapshots_run", attempt_definition_snapshots.c.run_id)
Index(
    "ix_harness_attempt_definition_snapshots_profile",
    attempt_definition_snapshots.c.model_profile_key,
    attempt_definition_snapshots.c.model_profile_version,
    attempt_definition_snapshots.c.model_profile_sha256,
)
