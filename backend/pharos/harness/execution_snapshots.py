"""Immutable, owner-scoped definition snapshots for Run and Attempt execution.

The 0011 migration intentionally leaves snapshot creation out of the generic
repository: creating a snapshot is a boundary operation and must validate the
entire definition closure before a worker can use it.  This module is a small
transaction participant.  It never commits (or rolls back) the caller's
session, so callers can include it in the same short transaction as Run or
Attempt creation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from pharos.harness.configrev import decode_snapshot_payload
from pharos.harness.contracts import (
    ConfigIntegrityError,
    DefinitionError,
    IdempotencyConflictError,
    NotFoundError,
    ScopeType,
)
from pharos.harness.definitions import (
    CapabilityDefinition,
    ModelProfileDefinition,
    RoleDefinition,
    WorkflowDefinition,
    canonical_json,
)
from pharos.harness.policy_snapshot import POLICY_SNAPSHOT_SCHEMA_VERSION, RunPolicySnapshot
from pharos.harness.repository import Scope, now_iso, sha256_text
from pharos.harness.tables import (
    attempt_definition_snapshots,
    attempts,
    capability_versions,
    config_revisions,
    model_profile_versions,
    role_versions,
    run_definition_snapshots,
    runs,
    steps,
    workflow_definition_bindings,
    workflow_versions,
)


class SnapshotIntegrityError(ConfigIntegrityError):
    """A persisted or requested snapshot is not internally authenticated."""


class SnapshotConflictError(IdempotencyConflictError):
    """A write-once snapshot identity already has a different value."""


class MissingExecutionSnapshotError(SnapshotIntegrityError):
    """A legacy Run/Attempt has no snapshot and therefore cannot execute."""


@dataclass(frozen=True)
class RunDefinitionSnapshot:
    run_id: str
    scope_type: str
    scope_id: str
    workflow_key: str
    workflow_version: int
    workflow_definition_sha256: str
    definition_binding_sha256: str
    policy_snapshot_schema_version: int
    policy_snapshot_sha256: str
    policy_snapshot_json: str
    policy_snapshot: RunPolicySnapshot

    @property
    def config_revision_id(self) -> str:
        return self.policy_snapshot.config_revision_id

    @property
    def config_revision_sha256(self) -> str:
        return self.policy_snapshot.config_revision_sha256


@dataclass(frozen=True)
class AttemptDefinitionSnapshot:
    attempt_id: str
    run_id: str
    scope_type: str
    scope_id: str
    step_id: str
    attempt_no: int
    definition_binding_sha256: str
    run_policy_sha256: str
    executor_kind: str
    executor_identity: str
    executor_role_key: str | None
    executor_role_version: int | None
    executor_role_definition_sha256: str | None
    executor_capability_key: str | None
    executor_capability_version: int | None
    executor_capability_definition_sha256: str | None
    model_profile_identity: str | None
    model_profile_key: str | None
    model_profile_version: int | None
    model_profile_sha256: str | None
    model_route_key: str | None
    model_route_sha256: str | None
    provider: str | None
    model: str | None
    usage_source: str | None
    runtime_kind: str
    policy_snapshot: RunPolicySnapshot


def _scope(scope: Scope | ScopeType | str, scope_id: str | None = None) -> tuple[str, str]:
    if isinstance(scope, Scope):
        return scope.scope_type.value, scope.scope_id
    if isinstance(scope, ScopeType):
        if not isinstance(scope_id, str) or not scope_id:
            raise SnapshotIntegrityError("scope_id is required")
        return scope.value, scope_id
    if isinstance(scope, str) and scope in {item.value for item in ScopeType}:
        if not isinstance(scope_id, str) or not scope_id:
            raise SnapshotIntegrityError("scope_id is required")
        return scope, scope_id
    raise SnapshotIntegrityError("scope must be a Scope or ScopeType")


def _parse_json(raw: object, *, label: str) -> Any:
    """Parse one JSON value while rejecting duplicate keys and JSON constants."""
    if not isinstance(raw, str):
        raise SnapshotIntegrityError(f"{label} must be a JSON string")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise SnapshotIntegrityError(f"{label} contains duplicate key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw,
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda token: (_ for _ in ()).throw(
                SnapshotIntegrityError(f"{label} contains non-finite JSON constant {token}")
            ),
        )
    except SnapshotIntegrityError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SnapshotIntegrityError(f"{label} is not valid JSON") from exc
    return value


def _canonical_payload(raw: object, *, label: str) -> tuple[Any, str]:
    value = _parse_json(raw, label=label)
    try:
        canonical = canonical_json(value)
    except (TypeError, ValueError) as exc:
        raise SnapshotIntegrityError(f"{label} contains unsupported/non-finite values") from exc
    if canonical != raw:
        raise SnapshotIntegrityError(f"{label} is not canonical JSON")
    return value, canonical


def _stored_definition(row: dict[str, Any], *, label: str, definition_type: Any) -> Any:
    value, raw = _canonical_payload(row.get("definition_json"), label=label)
    digest = row.get("definition_sha256")
    if not isinstance(digest, str) or sha256_text(raw) != digest:
        raise SnapshotIntegrityError(f"{label} hash mismatch")
    try:
        definition = definition_type.model_validate(value)
    except (TypeError, ValueError) as exc:
        raise SnapshotIntegrityError(f"{label} has invalid shape") from exc
    expected = (
        definition.canonical()
        if hasattr(definition, "canonical")
        else definition.model_dump(mode="json")
    )
    if canonical_json(expected) != raw or definition.definition_hash() != digest:
        raise SnapshotIntegrityError(f"{label} definition/hash mismatch")
    return definition


def _require_row(session: Session, table: Any, *, where: Any, label: str) -> dict[str, Any]:
    row = session.execute(select(table).where(where)).mappings().first()
    if row is None:
        raise SnapshotIntegrityError(f"{label} is missing")
    return dict(row)


def _policy(value: RunPolicySnapshot | dict[str, Any] | str) -> tuple[RunPolicySnapshot, str, str]:
    try:
        if isinstance(value, RunPolicySnapshot):
            parsed = value
            raw = parsed.canonical_json()
        elif isinstance(value, str):
            parsed = RunPolicySnapshot.from_canonical_json(value)
            raw = value
        else:
            parsed = RunPolicySnapshot.from_canonical(value)
            raw = parsed.canonical_json()
    except (TypeError, ValueError) as exc:
        raise SnapshotIntegrityError("invalid RunPolicySnapshot") from exc
    if raw != parsed.canonical_json():
        raise SnapshotIntegrityError("policy snapshot is not canonical")
    return parsed, raw, sha256_text(raw)


def _verify_workflow_and_binding(
    session: Session,
    *,
    workflow_key: str,
    workflow_version: int,
    workflow_hash: str,
    binding_hash: str,
) -> dict[str, Any]:
    workflow_row = _require_row(
        session,
        workflow_versions,
        where=(workflow_versions.c.workflow_key == workflow_key)
        & (workflow_versions.c.version == workflow_version),
        label=f"workflow {workflow_key}@{workflow_version}",
    )
    workflow = _stored_definition(
        workflow_row,
        label=f"workflow {workflow_key}@{workflow_version}",
        definition_type=WorkflowDefinition,
    )
    if (
        workflow.definition_hash() != workflow_hash
        or workflow_row["definition_sha256"] != workflow_hash
    ):
        raise SnapshotIntegrityError("workflow definition hash/metadata mismatch")
    binding_row = _require_row(
        session,
        workflow_definition_bindings,
        where=workflow_definition_bindings.c.binding_sha256 == binding_hash,
        label=f"workflow binding {binding_hash}",
    )
    # Reuse the definition repository's transitive-closure verifier.  Checking
    # only the binding row's four denormalized columns would allow a forged
    # nested role/capability hash to pass the snapshot boundary.
    from pharos.harness.repository import HarnessDefinitionRepository

    try:
        stored_binding = HarnessDefinitionRepository().get_binding(session, binding_hash)
    except SnapshotIntegrityError:
        raise
    except (ConfigIntegrityError, DefinitionError, TypeError, ValueError) as exc:
        raise SnapshotIntegrityError("workflow binding failed authentication") from exc
    if stored_binding is None:
        raise SnapshotIntegrityError("workflow binding is missing")
    value, raw = _canonical_payload(binding_row.get("binding_json"), label="workflow binding")
    if sha256_text(raw) != binding_hash:
        raise SnapshotIntegrityError("workflow binding hash mismatch")
    workflow_value = value.get("workflow") if isinstance(value, dict) else None
    if not isinstance(workflow_value, dict):
        raise SnapshotIntegrityError("workflow binding has invalid workflow metadata")
    if (
        binding_row["workflow_key"],
        binding_row["workflow_version"],
        binding_row["workflow_definition_sha256"],
    ) != (workflow_key, workflow_version, workflow_hash):
        raise SnapshotIntegrityError("workflow binding row metadata mismatch")
    if (
        workflow_value.get("workflow_key"),
        workflow_value.get("version"),
        workflow_value.get("definition_sha256"),
    ) != (workflow_key, workflow_version, workflow_hash):
        raise SnapshotIntegrityError("workflow binding payload identity mismatch")
    return value


def _verify_policy_binding(
    policy: RunPolicySnapshot, binding: dict[str, Any]
) -> None:
    """Prove that policy roles/profiles are the exact authenticated closure."""
    workflow = binding.get("workflow")
    if not isinstance(workflow, dict) or (
        workflow.get("identity"),
        workflow.get("definition_sha256"),
    ) != (policy.workflow_identity, policy.workflow_definition_sha256):
        raise SnapshotIntegrityError("policy workflow does not match definition binding")

    records = binding.get("roles")
    if not isinstance(records, list):
        raise SnapshotIntegrityError("definition binding roles are invalid")
    role_records = {
        record.get("identity"): record for record in records if isinstance(record, dict)
    }
    policy_roles = {item.role_identity: item for item in policy.role_bindings}
    if len(role_records) != len(records) or set(role_records) != set(policy_roles):
        raise SnapshotIntegrityError("policy role set does not match definition binding")

    for identity, role_binding in policy_roles.items():
        record = role_records[identity]
        profile = record.get("model_profile")
        if not isinstance(profile, dict):
            raise SnapshotIntegrityError(f"binding role {identity} has no model profile")
        if (
            record.get("definition_sha256") != role_binding.role_definition_sha256
            or canonical_json(record.get("definition"))
            != canonical_json(role_binding.role_definition.canonical())
            or profile.get("identity") != role_binding.model_profile_identity
            or profile.get("definition_sha256") != role_binding.model_profile_sha256
            or canonical_json(profile.get("definition"))
            != canonical_json(role_binding.model_profile_definition.canonical())
        ):
            raise SnapshotIntegrityError(
                f"policy role {identity} does not match definition binding"
            )


def _verify_config_revision(revision: dict[str, Any], expected_hash: str) -> None:
    value, raw = _canonical_payload(revision.get("snapshot_json"), label="config revision")
    if revision.get("snapshot_sha256") != expected_hash or sha256_text(raw) != expected_hash:
        raise SnapshotIntegrityError("policy config revision hash mismatch")
    try:
        decode_snapshot_payload(value)
    except (TypeError, ValueError) as exc:
        raise SnapshotIntegrityError("policy config revision has invalid shape") from exc


def _verify_run_input(parent: dict[str, Any]) -> None:
    value, raw = _canonical_payload(parent.get("input_json"), label="run input")
    if not isinstance(value, dict) or sha256_text(raw) != parent.get("input_sha256"):
        raise SnapshotIntegrityError("run input hash/shape mismatch")


def _require_pristine_run_creation(session: Session, parent: dict[str, Any]) -> None:
    """Reject snapshot backfill after a Run has entered its execution graph."""
    if (
        parent.get("state") != "queued"
        or parent.get("started_at") is not None
        or parent.get("finished_at") is not None
        or parent.get("cancel_requested_at") is not None
        or parent.get("pause_requested_at") is not None
        or parent.get("created_at") != parent.get("updated_at")
        or parent.get("policy_snapshot_json") is not None
    ):
        raise SnapshotIntegrityError("run is no longer in its snapshot creation phase")
    existing_step = session.execute(
        select(steps.c.id).where(steps.c.run_id == parent["id"]).limit(1)
    ).first()
    if existing_step is not None:
        raise SnapshotIntegrityError("legacy or activated Run snapshots cannot be backfilled")


def _verify_step_definition(
    step: dict[str, Any], binding: dict[str, Any]
) -> dict[str, Any]:
    """Authenticate a physical Step against the Run's frozen workflow.

    Step rows mix immutable expansion inputs with mutable state-machine fields.
    They therefore cannot be trusted merely because the selected executor is
    present somewhere in the binding closure: the exact logical step and all
    duplicated execution controls must still agree with the frozen definition.
    """
    workflow_record = binding.get("workflow")
    definition_value = (
        workflow_record.get("definition") if isinstance(workflow_record, dict) else None
    )
    try:
        workflow = WorkflowDefinition.model_validate(definition_value)
        trusted_step = workflow.step(step["definition_step_key"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SnapshotIntegrityError("attempt step is absent from the Run binding") from exc

    step_definition, _ = _canonical_payload(
        step.get("definition_json"), label="attempt step definition"
    )
    if not isinstance(step_definition, dict):
        raise SnapshotIntegrityError("attempt step definition must be an object")
    expected = trusted_step.model_dump(mode="json")
    if step.get("step_kind") != trusted_step.kind:
        raise SnapshotIntegrityError("attempt step kind does not match the Run binding")
    dynamic_keys = set(step_definition) - set(expected)
    if trusted_step.kind in ("mapped", "mapped_agent"):
        if dynamic_keys not in (set(), {"expand_items"}):
            raise SnapshotIntegrityError("mapped step contains unknown expansion metadata")
        if "expand_items" in step_definition:
            items = step_definition["expand_items"]
            if (
                not isinstance(items, list)
                or trusted_step.max_fanout is None
                or len(items) > trusted_step.max_fanout
            ):
                raise SnapshotIntegrityError("mapped step expansion exceeds its frozen bound")
    elif dynamic_keys:
        raise SnapshotIntegrityError("step contains unknown expansion metadata")
    if set(expected) - set(step_definition) or any(
        step_definition.get(key) != value for key, value in expected.items()
    ):
        raise SnapshotIntegrityError("attempt step definition does not match the Run binding")

    depends_on, _ = _canonical_payload(
        step.get("depends_on_json"), label="attempt step dependencies"
    )
    retry_raw = step.get("retry_policy_json")
    if retry_raw is None:
        retry_policy = None
    else:
        retry_policy, _ = _canonical_payload(retry_raw, label="attempt step retry policy")
    expected_retry = trusted_step.retry.model_dump(mode="json") if trusted_step.retry else None
    timeout_raw = step.get("timeout_seconds")
    if trusted_step.timeout_seconds is None:
        timeout_matches = timeout_raw is None
    else:
        try:
            timeout_matches = (
                not isinstance(timeout_raw, bool)
                and timeout_raw is not None
                and float(timeout_raw) == trusted_step.timeout_seconds
            )
        except (TypeError, ValueError, OverflowError):
            timeout_matches = False
    controls_match = {
        "dependencies": depends_on == list(trusted_step.depends_on),
        "fan-in": step.get("fan_in") == trusted_step.fan_in,
        "minimum success count": (
            step.get("min_success_count") == trusted_step.min_success_count
        ),
        "attempt limit": step.get("max_attempts")
        == (trusted_step.retry.max_attempts if trusted_step.retry else 3),
        "timeout": timeout_matches,
        "retry policy": retry_policy == expected_retry,
    }
    for control, matches in controls_match.items():
        if not matches:
            raise SnapshotIntegrityError(
                f"attempt step {control} does not match the Run binding"
            )
    return step_definition


def _snapshot_from_row(row: dict[str, Any]) -> RunDefinitionSnapshot:
    _, raw = _canonical_payload(row["policy_snapshot_json"], label="run policy snapshot")
    policy, _, digest = _policy(raw)
    if digest != row["policy_snapshot_sha256"]:
        raise SnapshotIntegrityError("run policy snapshot hash mismatch")
    if row["policy_snapshot_schema_version"] != POLICY_SNAPSHOT_SCHEMA_VERSION:
        raise SnapshotIntegrityError("unknown run policy snapshot schema")
    if (
        policy.workflow_definition_sha256 != row["workflow_definition_sha256"]
        or policy.definition_binding_sha256 != row["definition_binding_sha256"]
        or policy.workflow_identity != f"{row['workflow_key']}@{row['workflow_version']}"
    ):
        raise SnapshotIntegrityError("run snapshot redundant metadata mismatch")
    return RunDefinitionSnapshot(
        run_id=row["run_id"],
        scope_type=row["scope_type"],
        scope_id=row["scope_id"],
        workflow_key=row["workflow_key"],
        workflow_version=row["workflow_version"],
        workflow_definition_sha256=row["workflow_definition_sha256"],
        definition_binding_sha256=row["definition_binding_sha256"],
        policy_snapshot_schema_version=row["policy_snapshot_schema_version"],
        policy_snapshot_sha256=row["policy_snapshot_sha256"],
        policy_snapshot_json=raw,
        policy_snapshot=policy,
    )


def _run_identity(
    session: Session,
    *,
    run_id: str,
    scope_type: str,
    scope_id: str,
) -> dict[str, Any]:
    row = (
        session.execute(
            select(runs).where(
                runs.c.id == run_id,
                runs.c.scope_type == scope_type,
                runs.c.scope_id == scope_id,
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        # Do not reveal whether a foreign owner's ID exists.
        raise NotFoundError("run not found")
    return dict(row)


class ExecutionSnapshotStore:
    """Read/write-once persistence for the 0011 execution snapshots."""

    def resolve_executor_fields(
        self,
        session: Session,
        *,
        run_snapshot: RunDefinitionSnapshot,
        step: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve an executor solely from an authenticated Run snapshot.

        Dispatch must not consult the live Registry to decide what a queued
        Run executes.  This helper projects the authenticated binding and
        policy into the exact Attempt snapshot columns; ``write_attempt``
        performs the same checks again before persisting the projection.
        """
        binding = _verify_workflow_and_binding(
            session,
            workflow_key=run_snapshot.workflow_key,
            workflow_version=run_snapshot.workflow_version,
            workflow_hash=run_snapshot.workflow_definition_sha256,
            binding_hash=run_snapshot.definition_binding_sha256,
        )
        definition = _verify_step_definition(step, binding)
        step_kind = step.get("step_kind")
        if step_kind in ("deterministic", "mapped"):
            identity = definition.get("capability")
            if not isinstance(identity, str):
                raise SnapshotIntegrityError("deterministic step has no capability identity")
            key, version = _split_identity(identity, "capability")
            records = {
                item.get("identity"): item
                for item in binding.get("capabilities", [])
                if isinstance(item, dict)
            }
            record = records.get(identity)
            if not isinstance(record, dict):
                raise SnapshotIntegrityError("capability is absent from the Run binding")
            digest = record.get("definition_sha256")
            if not isinstance(digest, str):
                raise SnapshotIntegrityError("capability definition hash is missing")
            return {
                "executor_kind": "capability",
                "executor_identity": identity,
                "executor_role_key": None,
                "executor_role_version": None,
                "executor_role_definition_sha256": None,
                "executor_capability_key": key,
                "executor_capability_version": version,
                "executor_capability_definition_sha256": digest,
                "model_profile_identity": None,
                "model_profile_key": None,
                "model_profile_version": None,
                "model_profile_sha256": None,
                "model_route_key": None,
                "model_route_sha256": None,
                "provider": None,
                "model": None,
                "usage_source": None,
            }

        if step_kind not in ("agent", "mapped_agent"):
            raise SnapshotIntegrityError("step has an unsupported executor kind")
        identity = definition.get("role")
        if not isinstance(identity, str):
            raise SnapshotIntegrityError("agent step has no role identity")
        key, version = _split_identity(identity, "role")
        role_records = {
            item.get("identity"): item
            for item in binding.get("roles", [])
            if isinstance(item, dict)
        }
        role_record = role_records.get(identity)
        if not isinstance(role_record, dict):
            raise SnapshotIntegrityError("role is absent from the Run binding")
        role_digest = role_record.get("definition_sha256")
        profile = role_record.get("model_profile")
        if not isinstance(role_digest, str) or not isinstance(profile, dict):
            raise SnapshotIntegrityError("role binding is incomplete")
        profile_identity = profile.get("identity")
        profile_digest = profile.get("definition_sha256")
        if not isinstance(profile_identity, str) or not isinstance(profile_digest, str):
            raise SnapshotIntegrityError("role model profile binding is incomplete")
        profile_key, profile_version = _split_identity(profile_identity, "model profile")
        frozen = next(
            (
                item
                for item in run_snapshot.policy_snapshot.role_bindings
                if item.role_identity == identity
            ),
            None,
        )
        if frozen is None:
            raise SnapshotIntegrityError("role is absent from the frozen Run policy")
        if (
            frozen.role_definition_sha256,
            frozen.model_profile_identity,
            frozen.model_profile_sha256,
        ) != (role_digest, profile_identity, profile_digest):
            raise SnapshotIntegrityError("role binding does not match the frozen Run policy")
        route_key = frozen.model_route_identity
        route_digest = frozen.model_route_sha256
        if not isinstance(route_key, str) or not isinstance(route_digest, str):
            raise SnapshotIntegrityError("role model route binding is incomplete")
        return {
            "executor_kind": "role",
            "executor_identity": identity,
            "executor_role_key": key,
            "executor_role_version": version,
            "executor_role_definition_sha256": role_digest,
            "executor_capability_key": None,
            "executor_capability_version": None,
            "executor_capability_definition_sha256": None,
            "model_profile_identity": profile_identity,
            "model_profile_key": profile_key,
            "model_profile_version": profile_version,
            "model_profile_sha256": profile_digest,
            "model_route_key": route_key,
            "model_route_sha256": route_digest,
            "provider": frozen.provider,
            "model": frozen.model,
            "usage_source": frozen.usage_source.value,
        }

    def write_run(
        self,
        session: Session,
        *,
        scope: Scope | ScopeType | str,
        scope_id: str | None = None,
        run_id: str,
        workflow_key: str,
        workflow_version: int,
        workflow_definition_sha256: str,
        definition_binding_sha256: str,
        policy_snapshot: RunPolicySnapshot | dict[str, Any] | str,
        policy_snapshot_sha256: str | None = None,
        created_at: str | None = None,
    ) -> RunDefinitionSnapshot:
        scope_type, scope_value = _scope(scope, scope_id)
        parent = _run_identity(session, run_id=run_id, scope_type=scope_type, scope_id=scope_value)
        _verify_run_input(parent)
        if (
            parent["workflow_key"],
            parent["workflow_version"],
            parent["definition_sha256"],
        ) != (workflow_key, workflow_version, workflow_definition_sha256):
            raise SnapshotIntegrityError("run identity does not match snapshot metadata")
        policy, raw, digest = _policy(policy_snapshot)
        if policy_snapshot_sha256 is not None and policy_snapshot_sha256 != digest:
            raise SnapshotIntegrityError("forged policy snapshot hash")
        if (
            policy.workflow_identity,
            policy.workflow_definition_sha256,
            policy.definition_binding_sha256,
        ) != (
            f"{workflow_key}@{workflow_version}",
            workflow_definition_sha256,
            definition_binding_sha256,
        ):
            raise SnapshotIntegrityError("policy snapshot identity does not match run")
        if policy.config_revision_id != parent["config_revision_id"]:
            raise SnapshotIntegrityError("policy config revision does not match run")
        revision = _require_row(
            session,
            config_revisions,
            where=config_revisions.c.id == policy.config_revision_id,
            label="config revision",
        )
        _verify_config_revision(revision, policy.config_revision_sha256)
        binding = _verify_workflow_and_binding(
            session,
            workflow_key=workflow_key,
            workflow_version=workflow_version,
            workflow_hash=workflow_definition_sha256,
            binding_hash=definition_binding_sha256,
        )
        _verify_policy_binding(policy, binding)
        values = {
            "run_id": run_id,
            "scope_type": scope_type,
            "scope_id": scope_value,
            "workflow_key": workflow_key,
            "workflow_version": workflow_version,
            "workflow_definition_sha256": workflow_definition_sha256,
            "definition_binding_sha256": definition_binding_sha256,
            "policy_snapshot_schema_version": POLICY_SNAPSHOT_SCHEMA_VERSION,
            "policy_snapshot_sha256": digest,
            "policy_snapshot_json": raw,
            "created_at": created_at or now_iso(),
        }
        existing = session.execute(
            select(run_definition_snapshots).where(
                run_definition_snapshots.c.run_id == run_id
            )
        ).first()
        if existing is None:
            _require_pristine_run_creation(session, parent)
        session.execute(
            sqlite_insert(run_definition_snapshots)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["run_id"])
        )
        row = _require_row(
            session,
            run_definition_snapshots,
            where=run_definition_snapshots.c.run_id == run_id,
            label="run definition snapshot",
        )
        result = _snapshot_from_row(row)
        if any(row.get(key) != values[key] for key in values if key != "created_at"):
            raise SnapshotConflictError("run snapshot is already bound to a different value")
        return result

    def read_run(
        self,
        session: Session,
        *,
        scope: Scope | ScopeType | str,
        scope_id: str | None = None,
        run_id: str,
        require_for_execution: bool = False,
    ) -> RunDefinitionSnapshot | None:
        scope_type, scope_value = _scope(scope, scope_id)
        _run_identity(session, run_id=run_id, scope_type=scope_type, scope_id=scope_value)
        row = (
            session.execute(
                select(run_definition_snapshots).where(
                    run_definition_snapshots.c.run_id == run_id,
                    run_definition_snapshots.c.scope_type == scope_type,
                    run_definition_snapshots.c.scope_id == scope_value,
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            if require_for_execution:
                raise MissingExecutionSnapshotError("run has no execution snapshot")
            return None
        row_dict = dict(row)
        binding = _verify_workflow_and_binding(
            session,
            workflow_key=row_dict["workflow_key"],
            workflow_version=row_dict["workflow_version"],
            workflow_hash=row_dict["workflow_definition_sha256"],
            binding_hash=row_dict["definition_binding_sha256"],
        )
        run = _run_identity(session, run_id=run_id, scope_type=scope_type, scope_id=scope_value)
        _verify_run_input(run)
        if (
            run["workflow_key"],
            run["workflow_version"],
            run["definition_sha256"],
        ) != (
            row_dict["workflow_key"],
            row_dict["workflow_version"],
            row_dict["workflow_definition_sha256"],
        ):
            raise SnapshotIntegrityError("stored run snapshot parent identity mismatch")
        result = _snapshot_from_row(row_dict)
        if run["config_revision_id"] != result.config_revision_id:
            raise SnapshotIntegrityError("stored Run config revision no longer matches policy")
        _verify_policy_binding(result.policy_snapshot, binding)
        revision = _require_row(
            session,
            config_revisions,
            where=config_revisions.c.id == result.config_revision_id,
            label="config revision",
        )
        _verify_config_revision(revision, result.config_revision_sha256)
        return result

    def write_attempt(
        self,
        session: Session,
        *,
        scope: Scope | ScopeType | str,
        scope_id: str | None = None,
        attempt_id: str,
        run_id: str,
        step_id: str,
        attempt_no: int,
        lease_owner: str,
        definition_binding_sha256: str,
        run_policy_sha256: str,
        executor_kind: str,
        executor_identity: str,
        executor_role_key: str | None = None,
        executor_role_version: int | None = None,
        executor_role_definition_sha256: str | None = None,
        executor_capability_key: str | None = None,
        executor_capability_version: int | None = None,
        executor_capability_definition_sha256: str | None = None,
        model_profile_identity: str | None = None,
        model_profile_key: str | None = None,
        model_profile_version: int | None = None,
        model_profile_sha256: str | None = None,
        model_route_key: str | None = None,
        model_route_sha256: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        usage_source: str | None = None,
        policy_snapshot: RunPolicySnapshot | dict[str, Any] | str | None = None,
        created_at: str | None = None,
    ) -> AttemptDefinitionSnapshot:
        scope_type, scope_value = _scope(scope, scope_id)
        attempt = (
            session.execute(
                select(attempts).where(
                    attempts.c.id == attempt_id,
                    attempts.c.run_id == run_id,
                    attempts.c.scope_type == scope_type,
                    attempts.c.scope_id == scope_value,
                    attempts.c.step_id == step_id,
                    attempts.c.attempt_no == attempt_no,
                )
            )
            .mappings()
            .first()
        )
        if attempt is None:
            raise NotFoundError("attempt not found")
        if not isinstance(lease_owner, str) or not lease_owner:
            raise SnapshotIntegrityError("lease_owner is required for Attempt snapshot creation")
        step = _require_row(session, steps, where=steps.c.id == step_id, label="step")
        if (step["run_id"], step["scope_type"], step["scope_id"]) != (
            run_id,
            scope_type,
            scope_value,
        ):
            raise SnapshotIntegrityError("attempt step identity mismatch")
        existing_snapshot = session.execute(
            select(attempt_definition_snapshots.c.attempt_id).where(
                attempt_definition_snapshots.c.attempt_id == attempt_id
            )
        ).first()
        if existing_snapshot is None and (
            attempt["state"] != "leased"
            or attempt["lease_owner"] != lease_owner
            or attempt["worker_id"] != lease_owner
            or step["state"] != "leased"
            or step["lease_owner"] != lease_owner
        ):
            raise SnapshotIntegrityError(
                "Attempt snapshot must be created by the active claim owner"
            )
        run_snapshot = self.read_run(
            session,
            scope=scope_type,
            scope_id=scope_value,
            run_id=run_id,
            require_for_execution=True,
        )
        assert run_snapshot is not None
        if (definition_binding_sha256, run_policy_sha256) != (
            run_snapshot.definition_binding_sha256,
            run_snapshot.policy_snapshot_sha256,
        ):
            raise SnapshotIntegrityError("attempt binding/policy is not the Run snapshot")
        if policy_snapshot is not None:
            parsed_policy, _, policy_digest = _policy(policy_snapshot)
            if policy_digest != run_policy_sha256 or parsed_policy != run_snapshot.policy_snapshot:
                raise SnapshotIntegrityError("attempt policy snapshot does not match Run")

        fields = {
            "attempt_id": attempt_id,
            "run_id": run_id,
            "scope_type": scope_type,
            "scope_id": scope_value,
            "step_id": step_id,
            "attempt_no": attempt_no,
            "definition_binding_sha256": definition_binding_sha256,
            "run_policy_sha256": run_policy_sha256,
            "executor_kind": executor_kind,
            "executor_identity": executor_identity,
            "executor_role_key": executor_role_key,
            "executor_role_version": executor_role_version,
            "executor_role_definition_sha256": executor_role_definition_sha256,
            "executor_capability_key": executor_capability_key,
            "executor_capability_version": executor_capability_version,
            "executor_capability_definition_sha256": executor_capability_definition_sha256,
            "model_profile_identity": model_profile_identity,
            "model_profile_key": model_profile_key,
            "model_profile_version": model_profile_version,
            "model_profile_sha256": model_profile_sha256,
            "model_route_key": model_route_key,
            "model_route_sha256": model_route_sha256,
            "provider": provider,
            "model": model,
            "usage_source": usage_source,
            "created_at": created_at or now_iso(),
        }
        runtime_kind = self._validate_executor(session, fields, run_snapshot)
        session.execute(
            sqlite_insert(attempt_definition_snapshots)
            .values(**fields)
            .on_conflict_do_nothing(index_elements=["attempt_id"])
        )
        row = _require_row(
            session,
            attempt_definition_snapshots,
            where=attempt_definition_snapshots.c.attempt_id == attempt_id,
            label="attempt definition snapshot",
        )
        result = self._attempt_from_row(session, dict(row), run_snapshot)
        if any(row.get(key) != fields[key] for key in fields if key != "created_at"):
            raise SnapshotConflictError("attempt snapshot is already bound to a different value")
        if result.runtime_kind != runtime_kind:
            raise SnapshotIntegrityError("attempt runtime identity mismatch")
        return result

    @staticmethod
    def _validate_executor(
        session: Session, fields: dict[str, Any], run_snapshot: RunDefinitionSnapshot
    ) -> str:
        kind = fields["executor_kind"]
        identity = fields["executor_identity"]
        binding = _verify_workflow_and_binding(
            session,
            workflow_key=run_snapshot.workflow_key,
            workflow_version=run_snapshot.workflow_version,
            workflow_hash=run_snapshot.workflow_definition_sha256,
            binding_hash=run_snapshot.definition_binding_sha256,
        )
        step = _require_row(
            session,
            steps,
            where=(steps.c.id == fields["step_id"])
            & (steps.c.run_id == fields["run_id"])
            & (steps.c.scope_type == fields["scope_type"])
            & (steps.c.scope_id == fields["scope_id"]),
            label="attempt step",
        )
        step_definition = _verify_step_definition(step, binding)
        if kind == "capability":
            key, version = _split_identity(identity, "capability")
            expected = (key, version, fields["executor_capability_definition_sha256"])
            if expected != (
                fields["executor_capability_key"],
                fields["executor_capability_version"],
                fields["executor_capability_definition_sha256"],
            ) or any(
                fields[name] is not None
                for name in (
                    "executor_role_key",
                    "executor_role_version",
                    "executor_role_definition_sha256",
                    "model_profile_identity",
                    "model_profile_key",
                    "model_profile_version",
                    "model_profile_sha256",
                    "model_route_key",
                    "model_route_sha256",
                    "provider",
                    "model",
                    "usage_source",
                )
            ):
                raise SnapshotIntegrityError(
                    "capability snapshot contains redundant agent metadata"
                )
            if step.get("step_kind") not in ("deterministic", "mapped") or (
                step_definition.get("capability") != identity
            ):
                raise SnapshotIntegrityError("capability executor does not match its step")
            capability_records = {
                item.get("identity"): item
                for item in binding.get("capabilities", [])
                if isinstance(item, dict)
            }
            bound_capability = capability_records.get(identity)
            if not isinstance(bound_capability, dict) or bound_capability.get(
                "definition_sha256"
            ) != fields["executor_capability_definition_sha256"]:
                raise SnapshotIntegrityError("capability executor is not in the Run binding")
            row = _require_row(
                session,
                capability_versions,
                where=(capability_versions.c.capability_key == key)
                & (capability_versions.c.version == version),
                label=f"capability {identity}",
            )
            definition = _stored_definition(
                row,
                label=f"capability {identity}",
                definition_type=CapabilityDefinition,
            )
            if definition.definition_hash() != fields["executor_capability_definition_sha256"]:
                raise SnapshotIntegrityError("capability definition hash mismatch")
            return "deterministic"
        if kind != "role":
            raise SnapshotIntegrityError("executor_kind must be capability or role")
        key, version = _split_identity(identity, "role")
        expected_role = (key, version, fields["executor_role_definition_sha256"])
        if expected_role != (
            fields["executor_role_key"],
            fields["executor_role_version"],
            fields["executor_role_definition_sha256"],
        ) or any(
            fields[name] is not None
            for name in (
                "executor_capability_key",
                "executor_capability_version",
                "executor_capability_definition_sha256",
            )
        ):
            raise SnapshotIntegrityError("role snapshot contains redundant capability metadata")
        if step.get("step_kind") not in ("agent", "mapped_agent") or (
            step_definition.get("role") != identity
        ):
            raise SnapshotIntegrityError("role executor does not match its step")
        role_records = {
            item.get("identity"): item
            for item in binding.get("roles", [])
            if isinstance(item, dict)
        }
        bound_role = role_records.get(identity)
        if not isinstance(bound_role, dict) or bound_role.get(
            "definition_sha256"
        ) != fields["executor_role_definition_sha256"]:
            raise SnapshotIntegrityError("role executor is not in the Run binding")
        frozen_bindings = {
            item.role_identity: item for item in run_snapshot.policy_snapshot.role_bindings
        }
        frozen = frozen_bindings.get(identity)
        if frozen is None or (
            frozen.role_definition_sha256,
            frozen.model_profile_identity,
            frozen.model_profile_sha256,
            frozen.model_route_identity,
            frozen.model_route_sha256,
            frozen.provider,
            frozen.model,
            frozen.usage_source.value,
        ) != (
            fields["executor_role_definition_sha256"],
            fields["model_profile_identity"],
            fields["model_profile_sha256"],
            fields["model_route_key"],
            fields["model_route_sha256"],
            fields["provider"],
            fields["model"],
            fields["usage_source"],
        ):
            raise SnapshotIntegrityError("role executor does not match the frozen Run policy")
        role_row = _require_row(
            session,
            role_versions,
            where=(role_versions.c.role_key == key) & (role_versions.c.version == version),
            label=f"role {identity}",
        )
        role = _stored_definition(
            role_row, label=f"role {identity}", definition_type=RoleDefinition
        )
        if role.definition_hash() != fields["executor_role_definition_sha256"]:
            raise SnapshotIntegrityError("role definition hash mismatch")
        bound_profile = bound_role.get("model_profile")
        if not isinstance(bound_profile, dict):
            raise SnapshotIntegrityError("bound role has no model profile")
        profile_identity = bound_profile.get("identity")
        profile_key, profile_version = _split_identity(profile_identity, "model profile")
        if (
            fields["model_profile_identity"],
            fields["model_profile_key"],
            fields["model_profile_version"],
        ) != (profile_identity, profile_key, profile_version):
            raise SnapshotIntegrityError("role model profile identity mismatch")
        profile_row = _require_row(
            session,
            model_profile_versions,
            where=(model_profile_versions.c.profile_key == profile_key)
            & (model_profile_versions.c.version == profile_version),
            label=f"model profile {profile_key}@{profile_version}",
        )
        profile = _stored_definition(
            profile_row, label="model profile", definition_type=ModelProfileDefinition
        )
        from pharos.harness.registry import validate_role_model_profile

        try:
            validate_role_model_profile(role, profile)
        except DefinitionError as exc:
            raise SnapshotIntegrityError("role/model profile policy mismatch") from exc
        if (
            profile.definition_hash() != fields["model_profile_sha256"]
            or bound_profile.get("definition_sha256") != fields["model_profile_sha256"]
        ):
            raise SnapshotIntegrityError("model profile hash mismatch")
        if fields["model_route_key"] is None or fields["model_route_sha256"] != profile.route_hash(
            fields["model_route_key"]
        ):
            raise SnapshotIntegrityError("model route hash mismatch")
        route = next(
            (item for item in profile.routes if item.route_key == fields["model_route_key"]),
            None,
        )
        if route is None or (fields["provider"], fields["model"], fields["usage_source"]) != (
            route.provider,
            route.model,
            route.usage_source,
        ):
            raise SnapshotIntegrityError("model route metadata mismatch")
        if (
            role_row["model_profile_key"],
            role_row["model_profile_version"],
            role_row["model_profile_sha256"],
        ) != (profile_key, profile_version, fields["model_profile_sha256"]):
            raise SnapshotIntegrityError("stored role/profile foreign identity mismatch")
        return role.runtime_kind

    @staticmethod
    def _attempt_from_row(
        session: Session, row: dict[str, Any], run_snapshot: RunDefinitionSnapshot
    ) -> AttemptDefinitionSnapshot:
        # Re-run all executor checks on read: a forged hash/metadata row must
        # never become executable merely because SQLite accepted its shape.
        runtime_kind = ExecutionSnapshotStore._validate_executor(session, row, run_snapshot)
        if (
            row["run_policy_sha256"] != run_snapshot.policy_snapshot_sha256
            or row["definition_binding_sha256"] != run_snapshot.definition_binding_sha256
        ):
            raise SnapshotIntegrityError("attempt stored policy/binding mismatch")
        return AttemptDefinitionSnapshot(
            **{
                key: row[key]
                for key in (
                    "attempt_id",
                    "run_id",
                    "scope_type",
                    "scope_id",
                    "step_id",
                    "attempt_no",
                    "definition_binding_sha256",
                    "run_policy_sha256",
                    "executor_kind",
                    "executor_identity",
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
                )
            },
            runtime_kind=runtime_kind,
            policy_snapshot=run_snapshot.policy_snapshot,
        )

    def read_attempt(
        self,
        session: Session,
        *,
        scope: Scope | ScopeType | str,
        scope_id: str | None = None,
        attempt_id: str,
        require_for_execution: bool = False,
    ) -> AttemptDefinitionSnapshot | None:
        scope_type, scope_value = _scope(scope, scope_id)
        parent = (
            session.execute(
                select(attempts).where(
                    attempts.c.id == attempt_id,
                    attempts.c.scope_type == scope_type,
                    attempts.c.scope_id == scope_value,
                )
            )
            .mappings()
            .first()
        )
        if parent is None:
            raise NotFoundError("attempt not found")
        row = (
            session.execute(
                select(attempt_definition_snapshots).where(
                    attempt_definition_snapshots.c.attempt_id == attempt_id,
                    attempt_definition_snapshots.c.scope_type == scope_type,
                    attempt_definition_snapshots.c.scope_id == scope_value,
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            if require_for_execution:
                raise MissingExecutionSnapshotError("attempt has no execution snapshot")
            return None
        row_dict = dict(row)
        run_snapshot = self.read_run(
            session,
            scope=scope_type,
            scope_id=scope_value,
            run_id=row_dict["run_id"],
            require_for_execution=True,
        )
        assert run_snapshot is not None
        if (
            parent["run_id"],
            parent["step_id"],
            parent["attempt_no"],
        ) != (row_dict["run_id"], row_dict["step_id"], row_dict["attempt_no"]):
            raise SnapshotIntegrityError("stored attempt parent identity mismatch")
        return self._attempt_from_row(session, row_dict, run_snapshot)


def _split_identity(identity: object, label: str) -> tuple[str, int]:
    if not isinstance(identity, str) or identity.count("@") != 1:
        raise SnapshotIntegrityError(f"invalid {label} identity")
    key, version_text = identity.rsplit("@", 1)
    if not key or not version_text.isdigit() or int(version_text) < 1:
        raise SnapshotIntegrityError(f"invalid {label} identity")
    return key, int(version_text)


__all__ = [
    "AttemptDefinitionSnapshot",
    "ExecutionSnapshotStore",
    "MissingExecutionSnapshotError",
    "RunDefinitionSnapshot",
    "SnapshotConflictError",
    "SnapshotIntegrityError",
]
