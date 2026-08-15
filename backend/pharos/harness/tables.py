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
    CheckConstraint(
        "state IN ('leased','running','succeeded','failed','timed_out','cancelled',"
        "'abandoned','blocked','indeterminate')",
        name="ck_harness_attempts_state",
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
Index("ix_harness_steps_ready", steps.c.state, steps.c.ready_at)
