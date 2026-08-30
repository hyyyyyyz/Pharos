"""Owner-scoped Harness repositories: scope, runs, steps, config, events.

Every read and write here takes a :class:`Scope` and filters by it; a foreign
id and a nonexistent id are indistinguishable (both return None / raise
NotFound). The database constraints mirror this in composite foreign keys, so
a service-layer mistake cannot silently broaden visibility.

Two special shapes live here too:

* :class:`HarnessConfigService` -- the only writer of configuration revisions
  and the head CAS. Activation, writer mode and gates have no other update
  path.
* Run creation with idempotency: the same (scope, workflow, key) returns the
  existing run; the same key with different input raises a typed conflict.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import case, delete, exists, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from pharos.harness.configrev import (
    HarnessConfigSnapshot,
    WorkflowRoute,
    decode_snapshot_payload,
    validate_snapshot,
)
from pharos.harness.contracts import (
    AttemptState,
    ConfigIntegrityError,
    DefinitionError,
    IdempotencyConflictError,
    NotFoundError,
    ScopeType,
    StaleConfigError,
)
from pharos.harness.definitions import (
    CapabilityDefinition,
    ModelProfileDefinition,
    RoleDefinition,
    WorkflowDefinition,
    canonical_json,
)
from pharos.harness.registry import (
    CompiledWorkflowBinding,
    Registry,
    _validate_binding_payload,
    validate_role_model_profile,
)
from pharos.harness.tables import (
    attempts,
    capability_versions,
    config_head,
    config_revisions,
    config_workflow_routes,
    model_profile_versions,
    role_versions,
    runs,
    steps,
    workflow_definition_bindings,
    workflow_versions,
)

HEAD_KEY = "singleton"
MAX_RUN_INPUT_CHARS = 200_000


@dataclass(frozen=True)
class Scope:
    scope_type: ScopeType
    scope_id: str

    @classmethod
    def user(cls, user_id: str) -> Scope:
        return cls(ScopeType.user, user_id)

    @classmethod
    def system(cls, system_id: str) -> Scope:
        return cls(ScopeType.system, system_id)

    def where(self, table) -> Any:  # noqa: ANN001
        return (table.c.scope_type == self.scope_type.value) & (table.c.scope_id == self.scope_id)


def new_id() -> str:
    return uuid.uuid4().hex


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def json_load(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    return json.loads(raw)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _split_identity(identity: str, label: str) -> tuple[str, int]:
    if not isinstance(identity, str) or identity.count("@") != 1:
        raise DefinitionError(f"invalid {label} identity")
    key, raw_version = identity.rsplit("@", 1)
    try:
        version = int(raw_version)
    except (TypeError, ValueError) as exc:
        raise DefinitionError(f"invalid {label} identity") from exc
    if not key or version < 1:
        raise DefinitionError(f"invalid {label} identity")
    return key, version


class HarnessWorkflowStore:
    """Immutable workflow version rows."""

    def upsert(self, session: Session, workflow: WorkflowDefinition, now: str) -> str:
        row = (
            session.execute(
                select(workflow_versions).where(
                    workflow_versions.c.workflow_key == workflow.workflow_key,
                    workflow_versions.c.version == workflow.version,
                )
            )
            .mappings()
            .first()
        )
        if row is not None:
            if row["definition_sha256"] != workflow.definition_hash():
                raise StaleConfigError(
                    f"{workflow.identity()} already stored with a different hash"
                )
            return row["id"]
        row_id = new_id()
        session.execute(
            workflow_versions.insert().values(
                id=row_id,
                workflow_key=workflow.workflow_key,
                version=workflow.version,
                definition_json=json_dump(workflow.model_dump(mode="json")),
                definition_sha256=workflow.definition_hash(),
                input_schema=workflow.input_schema,
                output_schema=workflow.output_schema,
                created_at=now,
            )
        )
        return row_id

    def get(self, session: Session, workflow_key: str, version: int) -> dict | None:
        row = (
            session.execute(
                select(workflow_versions).where(
                    workflow_versions.c.workflow_key == workflow_key,
                    workflow_versions.c.version == version,
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row is not None else None


class HarnessDefinitionRepository:
    """Persist and read immutable definition closures.

    Definitions are content addressed by their exact key/version/hash tuple.
    A second write of the same value is idempotent; a write that attempts to
    reuse a key/version with different content is rejected.  The repository
    does not update or delete definition rows -- SQLite immutability triggers
    provide the final database-level fence.
    """

    @classmethod
    def _check_existing(
        cls,
        row: dict,
        *,
        raw: str,
        digest: str,
        identity: str,
        definition_type: Any,
    ) -> str:
        row = cls._checked_row(row, identity=identity, definition_type=definition_type)
        if row["definition_sha256"] != digest or row["definition_json"] != raw:
            raise StaleConfigError(f"{identity} is already stored with a different definition")
        return row["id"]

    @staticmethod
    def _checked_row(row: dict, *, identity: str, definition_type: Any = None) -> dict:
        """Verify canonical JSON, typed shape and denormalized metadata."""
        raw = row.get("definition_json")
        digest = row.get("definition_sha256")
        try:
            payload = json.loads(raw) if isinstance(raw, str) else None
            valid = isinstance(raw, str) and canonical_json(payload) == raw
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ConfigIntegrityError(f"{identity} stored definition is not valid JSON") from exc
        if (
            not valid
            or not isinstance(raw, str)
            or not isinstance(digest, str)
            or sha256_text(raw) != digest
        ):
            raise ConfigIntegrityError(f"{identity} stored definition hash mismatch")
        if definition_type is not None:
            try:
                parsed = definition_type.model_validate(payload)
            except (TypeError, ValueError) as exc:
                raise ConfigIntegrityError(
                    f"{identity} stored definition has invalid shape"
                ) from exc
            expected = (
                parsed.canonical()
                if hasattr(parsed, "canonical")
                else parsed.model_dump(mode="json")
            )
            if canonical_json(expected) != raw or parsed.definition_hash() != digest:
                raise ConfigIntegrityError(f"{identity} stored definition does not match its hash")
            metadata = {
                "workflow_key": getattr(parsed, "workflow_key", None),
                "profile_key": getattr(parsed, "profile_key", None),
                "capability_key": getattr(parsed, "capability_key", None),
                "role_key": getattr(parsed, "role_key", None),
                "version": getattr(parsed, "version", None),
            }
            for key, expected_value in metadata.items():
                if expected_value is not None and row.get(key) != expected_value:
                    raise ConfigIntegrityError(f"{identity} stored metadata is inconsistent")
            if isinstance(parsed, WorkflowDefinition) and (
                row.get("input_schema"), row.get("output_schema")
            ) != (parsed.input_schema, parsed.output_schema):
                raise ConfigIntegrityError(f"{identity} stored schema metadata is inconsistent")
            if isinstance(parsed, RoleDefinition):
                if row.get("runtime_kind") != parsed.runtime_kind:
                    raise ConfigIntegrityError(
                        f"{identity} stored runtime metadata is inconsistent"
                    )
                if "@" not in parsed.model_profile:
                    # Frozen legacy canary is resolved to canary@1 by the
                    # repository.  Keep the full identity exact: accepting a
                    # later canary version here would silently rebind the
                    # already-hashed legacy role.
                    if (
                        row.get("model_profile_key"),
                        row.get("model_profile_version"),
                    ) != ("canary", 1):
                        raise ConfigIntegrityError(
                            f"{identity} stored legacy profile is inconsistent"
                        )
                else:
                    profile_key, profile_version = _split_identity(
                        parsed.model_profile, "model profile"
                    )
                    if (row.get("model_profile_key"), row.get("model_profile_version")) != (
                        profile_key,
                        profile_version,
                    ):
                        raise ConfigIntegrityError(
                            f"{identity} stored profile metadata is inconsistent"
                        )
        return row

    @staticmethod
    def _insert_ignore(
        session: Session, table, values: dict[str, Any], conflict_columns: list[str]
    ) -> None:  # noqa: ANN001
        """Insert once; only the declared uniqueness race is ignored.

        SQLite's conflict target is intentionally explicit.  Foreign-key,
        CHECK and NOT NULL failures still propagate to the caller instead of
        being mistaken for a concurrent idempotent write.
        """
        if session.get_bind().dialect.name != "sqlite":
            raise RuntimeError("Harness definition persistence currently requires SQLite")
        session.execute(
            sqlite_insert(table)
            .values(**values)
            .on_conflict_do_nothing(index_elements=conflict_columns)
        )

    def upsert_workflow(self, session: Session, workflow: WorkflowDefinition, now: str) -> str:
        raw = canonical_json(workflow.model_dump(mode="json"))
        digest = workflow.definition_hash()
        row_id = new_id()
        self._insert_ignore(
            session,
            workflow_versions,
            dict(
                id=row_id,
                workflow_key=workflow.workflow_key,
                version=workflow.version,
                definition_json=raw,
                definition_sha256=digest,
                input_schema=workflow.input_schema,
                output_schema=workflow.output_schema,
                created_at=now,
            ),
            ["workflow_key", "version"],
        )
        row = (
            session.execute(
                select(workflow_versions).where(
                    workflow_versions.c.workflow_key == workflow.workflow_key,
                    workflow_versions.c.version == workflow.version,
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ConfigIntegrityError(f"failed to persist {workflow.identity()}")
        return self._check_existing(
            dict(row),
            raw=raw,
            digest=digest,
            identity=workflow.identity(),
            definition_type=WorkflowDefinition,
        )

    def upsert_model_profile(
        self, session: Session, profile: ModelProfileDefinition, now: str
    ) -> str:
        raw = canonical_json(profile.canonical())
        digest = profile.definition_hash()
        row_id = new_id()
        self._insert_ignore(
            session,
            model_profile_versions,
            dict(
                id=row_id,
                profile_key=profile.profile_key,
                version=profile.version,
                definition_json=raw,
                definition_sha256=digest,
                created_at=now,
            ),
            ["profile_key", "version"],
        )
        row = (
            session.execute(
                select(model_profile_versions).where(
                    model_profile_versions.c.profile_key == profile.profile_key,
                    model_profile_versions.c.version == profile.version,
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ConfigIntegrityError(f"failed to persist {profile.identity()}")
        return self._check_existing(
            dict(row),
            raw=raw,
            digest=digest,
            identity=profile.identity(),
            definition_type=ModelProfileDefinition,
        )

    def upsert_capability(
        self, session: Session, capability: CapabilityDefinition, now: str
    ) -> str:
        raw = canonical_json(capability.canonical())
        digest = capability.definition_hash()
        row_id = new_id()
        self._insert_ignore(
            session,
            capability_versions,
            dict(
                id=row_id,
                capability_key=capability.capability_key,
                version=capability.version,
                definition_json=raw,
                definition_sha256=digest,
                created_at=now,
            ),
            ["capability_key", "version"],
        )
        row = (
            session.execute(
                select(capability_versions).where(
                    capability_versions.c.capability_key == capability.capability_key,
                    capability_versions.c.version == capability.version,
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ConfigIntegrityError(f"failed to persist {capability.identity()}")
        return self._check_existing(
            dict(row),
            raw=raw,
            digest=digest,
            identity=capability.identity(),
            definition_type=CapabilityDefinition,
        )

    def upsert_role(
        self,
        session: Session,
        role: RoleDefinition,
        now: str,
        *,
        resolved_profile: ModelProfileDefinition | None = None,
    ) -> str:
        if resolved_profile is None:
            if "@" not in role.model_profile:
                raise DefinitionError(f"{role.identity()}: role model profile must be versioned")
            profile_key, profile_version = _split_identity(role.model_profile, "model profile")
        else:
            profile_key = resolved_profile.profile_key
            profile_version = resolved_profile.version
        profile_row = (
            session.execute(
                select(model_profile_versions).where(
                    model_profile_versions.c.profile_key == profile_key,
                    model_profile_versions.c.version == profile_version,
                )
            )
            .mappings()
            .first()
        )
        if profile_row is None:
            raise DefinitionError(
                f"{role.identity()}: model profile {role.model_profile} not stored"
            )
        profile = self._checked_row(
            dict(profile_row),
            identity=f"{profile_key}@{profile_version}",
            definition_type=ModelProfileDefinition,
        )
        if resolved_profile is not None and profile["definition_sha256"] != (
            resolved_profile.definition_hash()
        ):
            raise ConfigIntegrityError(
                f"{role.identity()}: stored model profile hash does not match the registry"
            )
        stored_profile = ModelProfileDefinition.model_validate(
            json.loads(profile["definition_json"])
        )
        validate_role_model_profile(role, stored_profile)
        raw = canonical_json(role.canonical())
        digest = role.definition_hash()
        values = dict(
            id=new_id(),
            role_key=role.role_key,
            version=role.version,
            definition_json=raw,
            definition_sha256=digest,
            runtime_kind=role.runtime_kind,
            model_profile_key=profile_key,
            model_profile_version=profile_version,
            model_profile_sha256=profile["definition_sha256"],
            created_at=now,
        )
        self._insert_ignore(session, role_versions, values, ["role_key", "version"])
        row = (
            session.execute(
                select(role_versions).where(
                    role_versions.c.role_key == role.role_key,
                    role_versions.c.version == role.version,
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ConfigIntegrityError(f"failed to persist {role.identity()}")
        existing = self._checked_row(
            dict(row), identity=role.identity(), definition_type=RoleDefinition
        )
        if (
            existing["model_profile_key"],
            existing["model_profile_version"],
            existing["model_profile_sha256"],
        ) != (profile_key, profile_version, profile["definition_sha256"]):
            raise StaleConfigError(f"{role.identity()} model profile binding changed")
        return existing["id"]

    def upsert_binding(self, session: Session, binding: CompiledWorkflowBinding, now: str) -> str:
        raw = binding.canonical_json()
        value = json.loads(raw)
        if sha256_text(raw) != binding.binding_sha256:
            raise ConfigIntegrityError("compiled workflow binding hash mismatch")
        _validate_binding_payload(value)
        workflow = value["workflow"]
        self._verify_binding_rows(session, value)
        values = dict(
            binding_sha256=binding.binding_sha256,
            schema_version=value.get("schema_version"),
            workflow_key=workflow.get("workflow_key"),
            workflow_version=workflow.get("version"),
            workflow_definition_sha256=workflow.get("definition_sha256"),
            binding_json=raw,
            created_at=now,
        )
        self._insert_ignore(
            session,
            workflow_definition_bindings,
            values,
            ["workflow_key", "workflow_version"],
        )
        row = (
            session.execute(
                select(workflow_definition_bindings).where(
                    workflow_definition_bindings.c.workflow_key == workflow["workflow_key"],
                    workflow_definition_bindings.c.workflow_version == workflow["version"],
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ConfigIntegrityError("failed to persist workflow binding")
        existing = dict(row)
        if existing["binding_sha256"] != binding.binding_sha256:
            raise StaleConfigError(
                f"{workflow['identity']} is already stored with a different binding"
            )
        if existing["binding_json"] != raw:
            raise ConfigIntegrityError("stored workflow binding content does not match its hash")
        if existing["workflow_definition_sha256"] != workflow["definition_sha256"]:
            raise ConfigIntegrityError("stored workflow binding metadata is inconsistent")
        return existing["binding_sha256"]

    def _verify_binding_rows(self, session: Session, value: dict[str, Any]) -> None:
        """Cross-check every persisted row in a binding's transitive closure."""
        workflow_record = value["workflow"]
        workflow_key = workflow_record["workflow_key"]
        workflow_version = workflow_record["version"]
        workflow_row = (
            session.execute(
                select(workflow_versions).where(
                    workflow_versions.c.workflow_key == workflow_key,
                    workflow_versions.c.version == workflow_version,
                )
            )
            .mappings()
            .first()
        )
        if workflow_row is None:
            raise DefinitionError(
                f"binding references an unstored workflow {workflow_record['identity']}"
            )
        checked_workflow = self._checked_row(
            dict(workflow_row),
            identity=workflow_record["identity"],
            definition_type=WorkflowDefinition,
        )
        if checked_workflow["definition_sha256"] != workflow_record["definition_sha256"]:
            raise ConfigIntegrityError("binding workflow hash does not match stored definition")

        for record in value["capabilities"]:
            key, version = _split_identity(record["identity"], "capability")
            row = (
                session.execute(
                    select(capability_versions).where(
                        capability_versions.c.capability_key == key,
                        capability_versions.c.version == version,
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise DefinitionError(
                    f"binding references an unstored capability {record['identity']}"
                )
            checked = self._checked_row(
                dict(row), identity=record["identity"], definition_type=CapabilityDefinition
            )
            if checked["definition_sha256"] != record["definition_sha256"]:
                raise ConfigIntegrityError(f"binding capability {record['identity']} hash mismatch")

        for record in value["roles"]:
            key, version = _split_identity(record["identity"], "role")
            row = (
                session.execute(
                    select(role_versions).where(
                        role_versions.c.role_key == key,
                        role_versions.c.version == version,
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise DefinitionError(f"binding references an unstored role {record['identity']}")
            checked_role = self._checked_row(
                dict(row), identity=record["identity"], definition_type=RoleDefinition
            )
            if checked_role["definition_sha256"] != record["definition_sha256"]:
                raise ConfigIntegrityError(f"binding role {record['identity']} hash mismatch")
            profile_record = record["model_profile"]
            profile_key, profile_version = _split_identity(
                profile_record["identity"], "model profile"
            )
            if (
                checked_role["model_profile_key"],
                checked_role["model_profile_version"],
                checked_role["model_profile_sha256"],
            ) != (
                profile_key,
                profile_version,
                profile_record["definition_sha256"],
            ):
                raise ConfigIntegrityError(
                    f"binding role {record['identity']} profile binding mismatch"
                )
            profile_row = (
                session.execute(
                    select(model_profile_versions).where(
                        model_profile_versions.c.profile_key == profile_key,
                        model_profile_versions.c.version == profile_version,
                    )
                )
                .mappings()
                .first()
            )
            if profile_row is None:
                raise DefinitionError(
                    f"binding references an unstored model profile {profile_record['identity']}"
                )
            checked_profile = self._checked_row(
                dict(profile_row),
                identity=profile_record["identity"],
                definition_type=ModelProfileDefinition,
            )
            if checked_profile["definition_sha256"] != profile_record["definition_sha256"]:
                raise ConfigIntegrityError(
                    f"binding model profile {profile_record['identity']} hash mismatch"
                )

    def persist_workflow_binding(
        self, session: Session, *, registry: Registry, workflow: WorkflowDefinition, now: str
    ) -> CompiledWorkflowBinding:
        """Compile and atomically persist one complete transitive closure."""
        binding = registry.compile_workflow_binding(workflow.identity())
        self.upsert_workflow(session, workflow, now)
        profiles: dict[str, ModelProfileDefinition] = {}
        for role_record in binding.roles:
            profile_record = role_record["model_profile"]
            profile_payload = profile_record["definition"]
            profile = ModelProfileDefinition.model_validate(profile_payload)
            profiles[profile.identity()] = profile
            self.upsert_model_profile(session, profile, now)
        for capability_record in binding.capabilities:
            capability = CapabilityDefinition.model_validate(capability_record["definition"])
            self.upsert_capability(session, capability, now)
        for role_record in binding.roles:
            role = RoleDefinition.model_validate(role_record["definition"])
            profile_identity = role_record["model_profile"]["identity"]
            if profile_identity not in profiles:
                raise DefinitionError(f"{role.identity()}: binding profile closure is incomplete")
            self.upsert_role(session, role, now, resolved_profile=profiles[profile_identity])
        self.upsert_binding(session, binding, now)
        return binding

    # Exact read APIs intentionally return copies, not mutable SQLAlchemy Row
    # objects.  Callers can independently verify the stored canonical JSON.
    def get_binding(self, session: Session, binding_sha256: str) -> dict | None:
        row = (
            session.execute(
                select(workflow_definition_bindings).where(
                    workflow_definition_bindings.c.binding_sha256 == binding_sha256
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        result = dict(row)
        raw = result["binding_json"]
        try:
            value = json.loads(raw)
            if canonical_json(value) != raw or sha256_text(raw) != binding_sha256:
                raise ConfigIntegrityError("stored workflow binding is not canonical or hash-valid")
            _validate_binding_payload(value)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ConfigIntegrityError("stored workflow binding is not valid JSON") from exc
        workflow = value["workflow"]
        if (
            result["schema_version"] != value["schema_version"]
            or result["workflow_key"] != workflow["workflow_key"]
            or result["workflow_version"] != workflow["version"]
            or result["workflow_definition_sha256"] != workflow["definition_sha256"]
        ):
            raise ConfigIntegrityError("stored workflow binding metadata is inconsistent")
        self._verify_binding_rows(session, value)
        return result

    def get_workflow(self, session: Session, workflow_key: str, version: int) -> dict | None:
        row = (
            session.execute(
                select(workflow_versions).where(
                    workflow_versions.c.workflow_key == workflow_key,
                    workflow_versions.c.version == version,
                )
            )
            .mappings()
            .first()
        )
        return (
            self._checked_row(
                dict(row), identity=f"{workflow_key}@{version}", definition_type=WorkflowDefinition
            )
            if row
            else None
        )

    def get_role(self, session: Session, role_key: str, version: int) -> dict | None:
        row = (
            session.execute(
                select(role_versions).where(
                    role_versions.c.role_key == role_key,
                    role_versions.c.version == version,
                )
            )
            .mappings()
            .first()
        )
        return (
            self._checked_row(
                dict(row), identity=f"{role_key}@{version}", definition_type=RoleDefinition
            )
            if row
            else None
        )

    def get_capability(self, session: Session, capability_key: str, version: int) -> dict | None:
        row = (
            session.execute(
                select(capability_versions).where(
                    capability_versions.c.capability_key == capability_key,
                    capability_versions.c.version == version,
                )
            )
            .mappings()
            .first()
        )
        return (
            self._checked_row(
                dict(row),
                identity=f"{capability_key}@{version}",
                definition_type=CapabilityDefinition,
            )
            if row
            else None
        )

    def get_model_profile(self, session: Session, profile_key: str, version: int) -> dict | None:
        row = (
            session.execute(
                select(model_profile_versions).where(
                    model_profile_versions.c.profile_key == profile_key,
                    model_profile_versions.c.version == version,
                )
            )
            .mappings()
            .first()
        )
        return (
            self._checked_row(
                dict(row),
                identity=f"{profile_key}@{version}",
                definition_type=ModelProfileDefinition,
            )
            if row
            else None
        )


# Keep the shorter name available to integration code while the repository
# name remains explicit in stack traces and documentation.
HarnessDefinitionStore = HarnessDefinitionRepository


@dataclass(frozen=True)
class CurrentConfig:
    """One validated view of the persisted config head and its revision."""

    head: dict
    revision: dict
    snapshot: HarnessConfigSnapshot

    @property
    def revision_id(self) -> str:
        return self.head["current_revision_id"]

    @property
    def snapshot_sha256(self) -> str:
        """The immutable hash stored with this exact revision payload."""
        return self.revision["snapshot_sha256"]


class HarnessConfigService:
    """The only authority for activation, writer mode and gates."""

    def __init__(self, registry: Registry) -> None:
        self.registry = registry

    def current(self, session: Session) -> dict | None:
        """The head row, or None on a fresh database."""
        row = (
            session.execute(config_head.select().where(config_head.c.head_key == HEAD_KEY))
            .mappings()
            .first()
        )
        return dict(row) if row is not None else None

    def current_validated(self, session: Session) -> CurrentConfig | None:
        """Read and validate the complete current configuration atomically.

        A head without a revision, a missing revision row, a tampered snapshot,
        and a snapshot rejected by the registry are all
        integrity failures.  Callers must fail closed rather than treating
        any of those states as the safe default.  Only an entirely absent
        head means that the database has not been bootstrapped yet.
        """
        head = self.current(session)
        if head is None:
            return None
        revision_id = head["current_revision_id"]
        if not revision_id:
            raise ConfigIntegrityError("config head has no current revision")
        revision = self.get_revision(session, revision_id)
        if revision is None:
            raise ConfigIntegrityError(f"config revision {revision_id!r} is missing")
        raw = revision.get("snapshot_json")
        stored_hash = revision.get("snapshot_sha256")
        if not isinstance(raw, str) or not isinstance(stored_hash, str):
            raise ConfigIntegrityError(f"config revision {revision_id!r} has invalid snapshot data")
        if sha256_text(raw) != stored_hash:
            raise ConfigIntegrityError(f"config revision {revision_id!r} snapshot hash mismatch")
        try:
            parsed = json.loads(raw)
            # The raw immutable payload is authenticated above.  Decoding may
            # only add safe in-memory defaults for explicitly supported
            # additive schema upgrades; it never rewrites the revision/hash.
            snapshot = decode_snapshot_payload(parsed)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ConfigIntegrityError(
                f"config revision {revision_id!r} snapshot is not valid JSON/schema: {exc}"
            ) from exc
        errors = validate_snapshot(snapshot, self.registry)
        if errors:
            raise ConfigIntegrityError(
                f"config revision {revision_id!r} is invalid: {'; '.join(errors)}"
            )
        return CurrentConfig(head=head, revision=revision, snapshot=snapshot)

    def current_snapshot(self, session: Session) -> HarnessConfigSnapshot | None:
        current = self.current_validated(session)
        return current.snapshot if current is not None else None

    def fence_current(self, session: Session, *, revision_id: str) -> None:
        """Acquire the head write fence and verify it did not cut over.

        This deliberately is a conditional UPDATE, even though it writes the
        same revision id.  On SQLite the UPDATE serializes this transaction
        with an operator apply; the row-count check makes a committed cutover
        fail closed before any run rows are inserted.
        """
        try:
            result: Any = session.execute(
                update(config_head)
                .where(
                    config_head.c.head_key == HEAD_KEY,
                    config_head.c.current_revision_id == revision_id,
                )
                .values(current_revision_id=revision_id)
            )
        except OperationalError as exc:
            # SQLite reports SQLITE_BUSY_SNAPSHOT when an apply acquired the
            # writer lock after this transaction read the old head.  It is a
            # cutover race from the caller's perspective, so fail closed with
            # the same typed result as a row-count CAS miss.
            sqlite_code = getattr(exc.orig, "sqlite_errorcode", None)
            lock_codes = {
                sqlite3.SQLITE_BUSY,
                sqlite3.SQLITE_LOCKED,
                getattr(sqlite3, "SQLITE_BUSY_SNAPSHOT", sqlite3.SQLITE_BUSY),
            }
            if sqlite_code in lock_codes:
                raise StaleConfigError(
                    f"config head changed while creating run; expected {revision_id!r}"
                ) from exc
            raise
        if result.rowcount != 1:
            raise StaleConfigError(
                f"config head changed while creating run; expected {revision_id!r}"
            )

    def get_revision(self, session: Session, revision_id: str) -> dict | None:
        row = (
            session.execute(select(config_revisions).where(config_revisions.c.id == revision_id))
            .mappings()
            .first()
        )
        return dict(row) if row is not None else None

    def routes(self, session: Session, revision_id: str) -> dict[str, WorkflowRoute]:
        rows = session.execute(
            select(config_workflow_routes).where(
                config_workflow_routes.c.revision_id == revision_id
            )
        ).mappings()
        return {row["workflow_key"]: _route_from_row(row) for row in rows}

    def apply(
        self,
        session: Session,
        *,
        snapshot: HarnessConfigSnapshot,
        expected_head_revision: str | None,
        actor: str,
        reason: str,
        now: str,
    ) -> str:
        """Persist a complete snapshot and CAS the head.

        The whole operation is one short transaction (callers own the
        session and commit). Any validation error, CAS failure or constraint
        violation raises and the caller must roll back.
        """
        errors = validate_snapshot(snapshot, self.registry)
        if errors:
            raise StaleConfigError("; ".join(errors))
        current = self.current_validated(session)
        current_id = current.revision_id if current is not None else None
        if current_id != expected_head_revision:
            raise StaleConfigError(
                f"config head is {current_id!r}, expected {expected_head_revision!r}"
            )

        revision_id = new_id()
        snapshot_json = json_dump(snapshot.canonical())
        # Keep the candidate revision, its route projection, and the head CAS
        # inside one savepoint.  A caller that handles a stale CAS and keeps its
        # outer transaction alive still cannot accidentally commit an orphaned
        # candidate revision.
        with session.begin_nested():
            session.execute(
                config_revisions.insert().values(
                    id=revision_id,
                    parent_revision_id=current_id,
                    snapshot_json=snapshot_json,
                    snapshot_sha256=sha256_text(snapshot_json),
                    gates_json=json_dump(snapshot.gates),
                    actor=actor,
                    reason=reason,
                    created_at=now,
                )
            )
            for route in snapshot.routes:
                session.execute(
                    config_workflow_routes.insert().values(
                        revision_id=revision_id,
                        workflow_key=route.workflow_key,
                        active_version=route.active_version,
                        activation_state=route.activation_state.value,
                        execution_mode=(
                            route.execution_mode.value if route.execution_mode else None
                        ),
                    )
                )
            # The head transition is the actual concurrency control.  A prior
            # read is useful for diagnostics but cannot authorize an unconditional
            # overwrite: another operator may have committed after that read.
            if current is None:
                try:
                    session.execute(
                        config_head.insert().values(
                            head_key=HEAD_KEY, current_revision_id=revision_id, updated_at=now
                        )
                    )
                except IntegrityError as exc:
                    raise StaleConfigError("config head was created concurrently") from exc
            else:
                result: Any = session.execute(
                    update(config_head)
                    .where(
                        config_head.c.head_key == HEAD_KEY,
                        config_head.c.current_revision_id == expected_head_revision,
                    )
                    .values(current_revision_id=revision_id, updated_at=now)
                )
                if result.rowcount != 1:
                    raise StaleConfigError(
                        f"config head changed while applying; expected {expected_head_revision!r}"
                    )
        return revision_id

    def rollback(self, session: Session, *, reason: str, actor: str, now: str) -> str | None:
        """Persist a revision that returns everything to the safe default.

        Used by operators (and emergency rollback runs) to turn the Harness
        off with one atomic revision instead of N partial edits.
        """
        head = self.current(session)
        if head is None or head["current_revision_id"] is None:
            return None
        from pharos.harness.configrev import bootstrap_snapshot

        snapshot = bootstrap_snapshot(self.registry, actor=actor, reason=reason)
        return self.apply(
            session,
            snapshot=snapshot,
            expected_head_revision=head["current_revision_id"],
            actor=actor,
            reason=reason,
            now=now,
        )


def _route_from_row(row) -> WorkflowRoute:  # noqa: ANN001
    mode = row["execution_mode"]
    return WorkflowRoute(
        workflow_key=row["workflow_key"],
        active_version=row["active_version"],
        activation_state=row["activation_state"],
        execution_mode=mode,
    )


class HarnessRunRepository:
    """Owner-scoped run creation, lookup and listing."""

    def create(
        self,
        session: Session,
        *,
        scope: Scope,
        workflow: WorkflowDefinition,
        config_revision_id: str,
        input: dict,
        idempotency_key: str,
        initiator: str,
        now_us: int,
        parent_run_id: str | None = None,
        project_id: str | None = None,
    ) -> dict:
        input_json = json_dump(input)
        if len(input_json) > MAX_RUN_INPUT_CHARS:
            raise ValueError("run input too large")
        existing = self.find_by_key(
            session, scope=scope, workflow_key=workflow.workflow_key, key=idempotency_key
        )
        if existing is not None:
            if existing["input_sha256"] != sha256_text(input_json):
                raise IdempotencyConflictError(
                    "the same idempotency key was already used with different input"
                )
            return existing
        run_id = new_id()
        session.execute(
            runs.insert().values(
                id=run_id,
                scope_type=scope.scope_type.value,
                scope_id=scope.scope_id,
                user_id=scope.scope_id if scope.scope_type == ScopeType.user else None,
                workflow_key=workflow.workflow_key,
                workflow_version=workflow.version,
                definition_sha256=workflow.definition_hash(),
                config_revision_id=config_revision_id,
                state="queued",
                outcome=None,
                input_json=input_json,
                input_sha256=sha256_text(input_json),
                budget_json=json_dump(workflow.default_budget.model_dump(mode="json")),
                initiator=initiator,
                idempotency_key=idempotency_key,
                parent_run_id=parent_run_id,
                project_id=project_id,
                created_at=now_us,
                updated_at=now_us,
            )
        )
        created = self.get(session, scope=scope, run_id=run_id)
        assert created is not None
        return created

    def find_by_key(
        self, session: Session, *, scope: Scope, workflow_key: str, key: str
    ) -> dict | None:
        row = (
            session.execute(
                select(runs).where(
                    scope.where(runs),
                    runs.c.workflow_key == workflow_key,
                    runs.c.idempotency_key == key,
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row is not None else None

    def get(self, session: Session, *, scope: Scope, run_id: str) -> dict | None:
        row = (
            session.execute(select(runs).where(scope.where(runs), runs.c.id == run_id))
            .mappings()
            .first()
        )
        return dict(row) if row is not None else None

    def require(self, session: Session, *, scope: Scope, run_id: str) -> dict:
        run = self.get(session, scope=scope, run_id=run_id)
        if run is None:
            raise NotFoundError("run not found")
        return run

    def list(
        self,
        session: Session,
        *,
        scope: Scope,
        limit: int,
        after_seq: int | None = None,
    ) -> list[dict]:
        """Runs newest-first, keyed by created_at for stable pagination."""
        query = select(runs).where(scope.where(runs))
        if after_seq is not None:
            query = query.where(runs.c.created_at < after_seq)
        rows = session.execute(
            query.order_by(runs.c.created_at.desc(), runs.c.id.desc()).limit(limit)
        ).mappings()
        return [dict(row) for row in rows]

    def update_fields(self, session: Session, *, scope: Scope, run_id: str, **values) -> None:
        result: Any = session.execute(
            runs.update().where(scope.where(runs), runs.c.id == run_id).values(**values)
        )
        if result.rowcount != 1:
            raise NotFoundError("run not found")


class HarnessStepRepository:
    """Owner-scoped step rows."""

    def expand(
        self,
        session: Session,
        *,
        scope: Scope,
        run_id: str,
        definition_step_key: str,
        instance_key: str,
        step_kind: str,
        definition_json: str,
        depends_on_json: str,
        fan_in: str | None,
        min_success_count: int | None,
        max_attempts: int,
        timeout_seconds: float | None,
        retry_policy_json: str | None,
        now_us: int,
    ) -> dict:
        from pharos.harness.tables import steps as steps_table

        existing = (
            session.execute(
                select(steps_table).where(
                    scope.where(steps_table),
                    steps_table.c.run_id == run_id,
                    steps_table.c.definition_step_key == definition_step_key,
                    steps_table.c.instance_key == instance_key,
                )
            )
            .mappings()
            .first()
        )
        if existing is not None:
            return dict(existing)
        step_id = new_id()
        session.execute(
            steps_table.insert().values(
                id=step_id,
                run_id=run_id,
                scope_type=scope.scope_type.value,
                scope_id=scope.scope_id,
                definition_step_key=definition_step_key,
                instance_key=instance_key,
                step_kind=step_kind,
                definition_json=definition_json,
                state="pending",
                depends_on_json=depends_on_json,
                fan_in=fan_in,
                min_success_count=min_success_count,
                max_attempts=max_attempts,
                timeout_seconds=str(timeout_seconds) if timeout_seconds is not None else None,
                retry_policy_json=retry_policy_json,
                created_at=now_us,
                updated_at=now_us,
            )
        )
        row = (
            session.execute(select(steps_table).where(steps_table.c.id == step_id))
            .mappings()
            .first()
        )
        assert row is not None
        return dict(row)

    def get(self, session: Session, *, scope: Scope, step_id: str) -> dict | None:
        from pharos.harness.tables import steps as steps_table

        row = (
            session.execute(
                select(steps_table).where(scope.where(steps_table), steps_table.c.id == step_id)
            )
            .mappings()
            .first()
        )
        return dict(row) if row is not None else None

    def for_run(self, session: Session, *, scope: Scope, run_id: str) -> list[dict]:
        from pharos.harness.tables import steps as steps_table

        rows = session.execute(
            select(steps_table)
            .where(scope.where(steps_table), steps_table.c.run_id == run_id)
            .order_by(steps_table.c.definition_step_key, steps_table.c.instance_key)
        ).mappings()
        return [dict(row) for row in rows]

    def delete_run_steps(self, session: Session, *, run_id: str) -> None:
        from pharos.harness.tables import steps as steps_table

        session.execute(delete(steps_table).where(steps_table.c.run_id == run_id))


# ---------------------------------------------------------------------------
# Runtime provenance and delivery


class AttemptRuntimeError(ValueError):
    """A runtime binding or delivery update violates its immutable contract."""


_ATTEMPT_ACTIVE_STATES = frozenset({AttemptState.leased.value, AttemptState.running.value})
_DELIVERY_STATES = frozenset({"not_started", "sent", "acknowledged", "unknown", "reconciled"})
_DELIVERY_TRANSITIONS: dict[str, frozenset[str]] = {
    # Delivery evidence is strictly monotonic.  The observer records unknown
    # before the first byte, sent after a complete frame, and acknowledged only
    # after its receipt; a later observation cannot erase stronger evidence.
    "not_started": frozenset({"unknown"}),
    "sent": frozenset({"acknowledged"}),
    # ``unknown -> reconciled`` is a restart/reconciliation operation and is
    # deliberately not available to the leased worker delivery writer.
    "unknown": frozenset({"sent"}),
    "acknowledged": frozenset(),
    "reconciled": frozenset(),
}


def _attempt_state_value(state: AttemptState | str | None) -> str | None:
    if state is None:
        return None
    try:
        value = state.value if isinstance(state, AttemptState) else str(state)
        AttemptState(value)
    except (TypeError, ValueError) as exc:
        raise AttemptRuntimeError(f"unknown Attempt state {state!r}") from exc
    if value not in _ATTEMPT_ACTIVE_STATES:
        raise AttemptRuntimeError(f"Attempt state {value!r} is not active")
    return value


def _nonempty_identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise AttemptRuntimeError(f"{field} must be a non-empty string")
    return value


def _positive_integer(value: object, field: str) -> int:
    # bool is an int subclass but is never a valid process/deadline value.
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise AttemptRuntimeError(f"{field} must be a positive integer")
    return value


# ---------------------------------------------------------------------------
# H1.5 launch reservation API


@dataclass(frozen=True, slots=True)
class AttemptRuntimeLaunch:
    """Immutable provenance recorded before a runtime child is spawned."""

    runtime_session_id: str
    deadline_at: int
    upstream_commit: str
    runtime_hash: str
    profile_hash: str
    policy_hash: str
    protocol_version: str


_LAUNCH_COLUMNS = (
    "runtime_session_id",
    "deadline_at",
    "upstream_commit",
    "runtime_hash",
    "profile_hash",
    "policy_hash",
    "protocol_version",
)


def _canonical_launch(launch: AttemptRuntimeLaunch, *, now_us: int) -> AttemptRuntimeLaunch:
    session_id = _nonempty_identifier(launch.runtime_session_id, "runtime_session_id")
    if len(session_id) > 256 or any(character.isspace() for character in session_id):
        raise AttemptRuntimeError("runtime_session_id must be bounded and contain no whitespace")
    deadline = _positive_integer(launch.deadline_at, "deadline_at")
    now = _positive_integer(now_us, "now_us")
    if deadline <= now:
        raise AttemptRuntimeError("deadline_at must be later than now_us")
    if not isinstance(launch.upstream_commit, str) or not re.fullmatch(
        r"[0-9a-f]{40}", launch.upstream_commit
    ):
        raise AttemptRuntimeError("upstream_commit must be a lowercase full git SHA-1")
    for field in ("runtime_hash", "profile_hash", "policy_hash"):
        value = getattr(launch, field)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise AttemptRuntimeError(f"{field} must be a lowercase SHA-256 hex digest")
    if launch.protocol_version != "pharos.dsh.stdio@1":
        raise AttemptRuntimeError("unsupported runtime protocol version")
    return AttemptRuntimeLaunch(
        runtime_session_id=session_id,
        deadline_at=deadline,
        upstream_commit=launch.upstream_commit,
        runtime_hash=launch.runtime_hash,
        profile_hash=launch.profile_hash,
        policy_hash=launch.policy_hash,
        protocol_version=launch.protocol_version,
    )


class HarnessAttemptRepository:
    """Minimal owner/generation-scoped runtime persistence.

    A launch reservation is durable before ``spawn``.  PID attachment and
    delivery transitions are conditional writes owned by the caller's
    transaction.  No method commits, kills a process, or treats a PID as a
    restart identity; orphan termination remains a trusted supervisor/cgroup
    deployment gate.
    """

    @staticmethod
    def _lease_where(
        *,
        scope: Scope,
        run_id: str,
        step_id: str,
        attempt_id: str,
        attempt_no: int,
        lease_owner: str,
        expected_state: str,
        now_us: int,
    ) -> list[Any]:
        for value, field in (
            (run_id, "run_id"),
            (step_id, "step_id"),
            (attempt_id, "attempt_id"),
            (lease_owner, "lease_owner"),
        ):
            _nonempty_identifier(value, field)
        _positive_integer(attempt_no, "attempt_no")
        predicates: list[Any] = [
            scope.where(attempts),
            attempts.c.run_id == run_id,
            attempts.c.step_id == step_id,
            attempts.c.id == attempt_id,
            attempts.c.attempt_no == attempt_no,
            attempts.c.lease_owner == lease_owner,
            attempts.c.state == expected_state,
            attempts.c.state.in_(_ATTEMPT_ACTIVE_STATES),
        ]
        predicates.append(
            exists(
                select(1)
                .select_from(steps)
                .where(
                    steps.c.id == attempts.c.step_id,
                    steps.c.run_id == attempts.c.run_id,
                    steps.c.scope_type == scope.scope_type.value,
                    steps.c.scope_id == scope.scope_id,
                    steps.c.attempt_count == attempts.c.attempt_no,
                    steps.c.lease_owner == attempts.c.lease_owner,
                    steps.c.state == attempts.c.state,
                    steps.c.state.in_(("leased", "running")),
                    steps.c.lease_expires_at.is_not(None),
                    steps.c.lease_expires_at > now_us,
                )
            )
        )
        return predicates

    @staticmethod
    def _require_fence(
        *, expected_state: AttemptState | str | None, now_us: int | None
    ) -> tuple[str, int]:
        state = _attempt_state_value(expected_state)
        if state is None:
            raise AttemptRuntimeError("expected_state (leased or running) is required")
        if now_us is None:
            raise AttemptRuntimeError("now_us is required for a lease-scoped mutation")
        return state, _positive_integer(now_us, "now_us")

    @staticmethod
    def _launch_complete() -> list[Any]:
        return [getattr(attempts.c, field).is_not(None) for field in _LAUNCH_COLUMNS] + [
            attempts.c.delivery_state.is_not(None)
        ]

    @staticmethod
    def _active_launch_where(*, scope: Scope, now_us: int) -> list[Any]:
        """Return the invariant portion shared by active runtime queries."""
        now = _positive_integer(now_us, "now_us")
        return [
            scope.where(attempts),
            attempts.c.state.in_(_ATTEMPT_ACTIVE_STATES),
            *HarnessAttemptRepository._launch_complete(),
            exists(
                select(1)
                .select_from(steps)
                .where(
                    steps.c.id == attempts.c.step_id,
                    steps.c.run_id == attempts.c.run_id,
                    steps.c.scope_type == scope.scope_type.value,
                    steps.c.scope_id == scope.scope_id,
                    steps.c.attempt_count == attempts.c.attempt_no,
                    steps.c.lease_owner == attempts.c.lease_owner,
                    steps.c.state == attempts.c.state,
                    steps.c.state.in_(_ATTEMPT_ACTIVE_STATES),
                    steps.c.lease_expires_at.is_not(None),
                    steps.c.lease_expires_at > now,
                ),
            ),
        ]

    @staticmethod
    def _current_generation() -> Any:
        return exists(
            select(1)
            .select_from(steps)
            .where(
                steps.c.id == attempts.c.step_id,
                steps.c.run_id == attempts.c.run_id,
                steps.c.scope_type == attempts.c.scope_type,
                steps.c.scope_id == attempts.c.scope_id,
                steps.c.attempt_count == attempts.c.attempt_no,
            )
        )

    @staticmethod
    def _live_lease(*, now_us: int) -> Any:
        now = _positive_integer(now_us, "now_us")
        return exists(
            select(1)
            .select_from(steps)
            .where(
                steps.c.id == attempts.c.step_id,
                steps.c.run_id == attempts.c.run_id,
                steps.c.scope_type == attempts.c.scope_type,
                steps.c.scope_id == attempts.c.scope_id,
                steps.c.attempt_count == attempts.c.attempt_no,
                steps.c.lease_owner == attempts.c.lease_owner,
                steps.c.state == attempts.c.state,
                steps.c.state.in_(_ATTEMPT_ACTIVE_STATES),
                steps.c.lease_expires_at.is_not(None),
                steps.c.lease_expires_at > now,
            )
        )

    def reserve_runtime_launch(
        self,
        session: Session,
        *,
        scope: Scope,
        run_id: str,
        step_id: str,
        attempt_id: str,
        attempt_no: int,
        lease_owner: str,
        expected_state: AttemptState | str,
        now_us: int,
        launch: AttemptRuntimeLaunch,
    ) -> dict | None:
        """Persist complete launch provenance before spawning a child."""
        expected, now = self._require_fence(expected_state=expected_state, now_us=now_us)
        canonical = _canonical_launch(launch, now_us=now)
        predicates = self._lease_where(
            scope=scope,
            run_id=run_id,
            step_id=step_id,
            attempt_id=attempt_id,
            attempt_no=attempt_no,
            lease_owner=lease_owner,
            expected_state=expected,
            now_us=now,
        )
        current = session.execute(select(attempts).where(*predicates)).mappings().first()
        if current is None:
            return None
        if (
            any(current.get(field) is not None for field in _LAUNCH_COLUMNS)
            or current.get("child_pid") is not None
            or current.get("delivery_state") is not None
        ):
            raise AttemptRuntimeError("runtime launch is already bound or partially present")
        duplicate = session.execute(
            select(attempts.c.id)
            .where(
                attempts.c.runtime_session_id == canonical.runtime_session_id,
                attempts.c.id != attempt_id,
            )
            .limit(1)
        ).scalar_one_or_none()
        if duplicate is not None:
            raise AttemptRuntimeError("runtime_session_id is already bound")
        values = {field: getattr(canonical, field) for field in _LAUNCH_COLUMNS}
        values.update(child_pid=None, delivery_state="not_started")
        try:
            with session.begin_nested():
                result: Any = session.execute(
                    update(attempts)
                    .where(
                        *predicates,
                        *[getattr(attempts.c, field).is_(None) for field in _LAUNCH_COLUMNS],
                    )
                    .where(attempts.c.child_pid.is_(None), attempts.c.delivery_state.is_(None))
                    .values(**values)
                )
        except IntegrityError as exc:
            raise AttemptRuntimeError("runtime identity conflict") from exc
        if result.rowcount != 1:
            return None
        row = session.execute(select(attempts).where(*predicates)).mappings().first()
        return dict(row) if row is not None else None

    def attach_child_process(
        self,
        session: Session,
        *,
        scope: Scope,
        run_id: str,
        step_id: str,
        attempt_id: str,
        attempt_no: int,
        lease_owner: str,
        expected_state: AttemptState | str,
        now_us: int,
        child_pid: int,
    ) -> bool:
        """Attach one PID exactly once after spawn, under the same lease CAS."""
        expected, now = self._require_fence(expected_state=expected_state, now_us=now_us)
        pid = _positive_integer(child_pid, "child_pid")
        predicates = self._lease_where(
            scope=scope,
            run_id=run_id,
            step_id=step_id,
            attempt_id=attempt_id,
            attempt_no=attempt_no,
            lease_owner=lease_owner,
            expected_state=expected,
            now_us=now,
        )
        current = session.execute(select(attempts).where(*predicates)).mappings().first()
        if current is None:
            return False
        if (
            any(current.get(field) is None for field in _LAUNCH_COLUMNS)
            or current.get("delivery_state") is None
        ):
            raise AttemptRuntimeError("child attach requires a complete launch reservation")
        if current.get("child_pid") is not None:
            raise AttemptRuntimeError("child_pid is already attached")
        duplicate = session.execute(
            select(attempts.c.id)
            .where(
                attempts.c.child_pid == pid,
                attempts.c.state.in_(_ATTEMPT_ACTIVE_STATES),
                attempts.c.id != attempt_id,
            )
            .limit(1)
        ).scalar_one_or_none()
        if duplicate is not None:
            raise AttemptRuntimeError("child_pid is already attached to an active Attempt")
        try:
            with session.begin_nested():
                result: Any = session.execute(
                    update(attempts)
                    .where(
                        *predicates,
                        attempts.c.child_pid.is_(None),
                        *self._launch_complete(),
                    )
                    .values(child_pid=pid)
                )
        except IntegrityError as exc:
            # The preflight is advisory under concurrent callers.  A database
            # identity constraint may still win at UPDATE time; the savepoint
            # keeps that conflict from poisoning the caller-owned transaction.
            raise AttemptRuntimeError("runtime identity conflict") from exc
        return result.rowcount == 1

    def transition_delivery(
        self,
        session: Session,
        *,
        scope: Scope,
        run_id: str,
        step_id: str,
        attempt_id: str,
        attempt_no: int,
        lease_owner: str,
        expected_state: AttemptState | str,
        now_us: int,
        delivery_state: str,
    ) -> bool:
        """CAS a delivery transition only after child PID attachment."""
        if delivery_state not in _DELIVERY_STATES:
            raise AttemptRuntimeError(f"unknown delivery state {delivery_state!r}")
        expected, now = self._require_fence(expected_state=expected_state, now_us=now_us)
        predicates = self._lease_where(
            scope=scope,
            run_id=run_id,
            step_id=step_id,
            attempt_id=attempt_id,
            attempt_no=attempt_no,
            lease_owner=lease_owner,
            expected_state=expected,
            now_us=now,
        )
        current = session.execute(select(attempts).where(*predicates)).mappings().first()
        if current is None:
            return False
        if (
            any(current.get(field) is None for field in _LAUNCH_COLUMNS)
            or current.get("delivery_state") is None
            or current.get("child_pid") is None
        ):
            raise AttemptRuntimeError("delivery requires launch reservation and child attachment")
        current_delivery = current["delivery_state"]
        if current_delivery not in _DELIVERY_STATES:
            raise AttemptRuntimeError(f"stored delivery state is invalid: {current_delivery!r}")
        if delivery_state not in _DELIVERY_TRANSITIONS[current_delivery]:
            raise AttemptRuntimeError(
                f"illegal delivery transition {current_delivery} -> {delivery_state}"
            )
        result: Any = session.execute(
            update(attempts)
            .where(
                *predicates,
                attempts.c.child_pid.is_not(None),
                *self._launch_complete(),
                attempts.c.delivery_state == current_delivery,
            )
            .values(delivery_state=delivery_state)
        )
        return result.rowcount == 1

    def list_active_runtime(
        self,
        session: Session,
        *,
        scope: Scope,
        run_id: str | None = None,
        step_id: str | None = None,
        attempt_id: str | None = None,
        lease_owner: str | None = None,
        now_us: int,
    ) -> list[dict]:
        """List attached active runtime Attempts with generation validation."""
        predicates: list[Any] = HarnessAttemptRepository._active_launch_where(
            scope=scope, now_us=now_us
        )
        predicates.append(attempts.c.child_pid.is_not(None))
        if run_id is not None:
            predicates.append(attempts.c.run_id == _nonempty_identifier(run_id, "run_id"))
        if step_id is not None:
            predicates.append(attempts.c.step_id == _nonempty_identifier(step_id, "step_id"))
        if attempt_id is not None:
            predicates.append(attempts.c.id == _nonempty_identifier(attempt_id, "attempt_id"))
        if lease_owner is not None:
            predicates.append(
                attempts.c.lease_owner == _nonempty_identifier(lease_owner, "lease_owner")
            )
        rows = session.execute(
            select(attempts).where(*predicates).order_by(attempts.c.started_at, attempts.c.id)
        ).mappings()
        return [dict(row) for row in rows]

    def list_launch_recovery_candidates(
        self, session: Session, *, scope: Scope, now_us: int
    ) -> list[dict]:
        """Scan all active launch reservations after a process/API restart.

        This deliberately includes attached and unattached children, expired
        leases, and stale Attempt generations.  ``current_generation`` and
        ``live_lease`` are diagnostics for a coordinator; neither authorizes
        killing a PID.  PID identity does not survive a restart, so orphan
        termination still requires trusted supervisor/cgroup evidence.
        """
        current_generation = HarnessAttemptRepository._current_generation()
        live_lease = HarnessAttemptRepository._live_lease(now_us=now_us)
        rows = session.execute(
            select(
                attempts,
                case((current_generation, True), else_=False).label("current_generation"),
                case((live_lease, True), else_=False).label("live_lease"),
            )
            .where(
                scope.where(attempts),
                attempts.c.state.in_(_ATTEMPT_ACTIVE_STATES),
                *HarnessAttemptRepository._launch_complete(),
            )
            .order_by(attempts.c.started_at, attempts.c.id)
        ).mappings()
        return [dict(row) for row in rows]

    def list_reconciliation_candidates(self, session: Session, *, scope: Scope) -> list[dict]:
        """Read-only terminal ``indeterminate + unknown`` candidates.

        Reconciliation writes belong to a later service that records an
        append-only receipt, usage fact, actor/evidence/outcome and Event in
        one transaction.  This repository intentionally provides no terminal
        delivery mutation.
        """
        rows = session.execute(
            select(attempts)
            .where(
                scope.where(attempts),
                attempts.c.state == AttemptState.indeterminate.value,
                attempts.c.delivery_state == "unknown",
                *HarnessAttemptRepository._launch_complete(),
                HarnessAttemptRepository._current_generation(),
            )
            .order_by(attempts.c.started_at, attempts.c.id)
        ).mappings()
        return [dict(row) for row in rows]
