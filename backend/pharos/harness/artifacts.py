"""Immutable Harness artifacts, links, and the public release/projection path.

Artifacts are append-only: revisions create a new row plus a ``supersedes``
link. Physical content may be tombstoned (retention, user deletion, release
revocation) but the row, hash and provenance survive and the API reports
``content_deleted`` instead of pretending the artifact never existed.

The release/projection tables are the only system -> user bridge. A public
release freezes an immutable ``release_sha256`` over the canonical envelope;
per-user projections copy the minimal public fields into a user-scoped
artifact bound by the same owner as its consumer.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from pharos.harness.contracts import (
    ApprovalConflictError,
    ArtifactSensitivity,
    NotFoundError,
    ProducerKind,
    ScopeType,
)
from pharos.harness.definitions import canonical_json, sha256_hex
from pharos.harness.execution_snapshots import (
    ExecutionSnapshotStore,
    SnapshotIntegrityError,
)
from pharos.harness.repository import Scope, json_dump, new_id
from pharos.harness.tables import (
    artifact_links,
    artifacts,
    attempt_definition_snapshots,
    attempts,
    public_artifact_projections,
    public_artifact_releases,
)

MAX_INLINE_CONTENT_CHARS = 1_000_000
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_GIT_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_CAPABILITY_PROVENANCE_SCHEMA = "pharos.harness.capability-artifact-provenance@1"


def artifact_provenance_hash(
    *,
    artifact_type: str,
    schema_name: str,
    schema_version: int,
    producer_kind: str,
    producer_attempt_id: str,
    run_id: str,
    step_id: str,
    scope_type: str,
    scope_id: str,
    workflow_key: str,
    workflow_version: int,
    workflow_definition_sha256: str,
    executor_kind: str,
    executor_identity: str,
    executor_role_definition_sha256: str | None,
    executor_capability_definition_sha256: str | None,
    role_prompt_version: str | None,
    model_profile_identity: str | None,
    model_profile_sha256: str | None,
    usage_source: str | None,
    upstream_commit: str,
    runtime_session_id: str,
    runtime_hash: str,
    profile_hash: str,
    policy_hash: str,
    protocol_version: str,
    route_key: str,
    route_sha256: str,
    definition_binding_sha256: str,
    run_policy_sha256: str,
    provider: str,
    model: str,
) -> str:
    """Hash the original DSH producer/runtime envelope without changing its bytes."""
    return sha256_hex(
        {
            "artifact_type": artifact_type,
            "schema_name": schema_name,
            "schema_version": schema_version,
            "producer_kind": producer_kind,
            "producer_attempt_id": producer_attempt_id,
            "run_id": run_id,
            "step_id": step_id,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "workflow_key": workflow_key,
            "workflow_version": workflow_version,
            "workflow_definition_sha256": workflow_definition_sha256,
            "executor_kind": executor_kind,
            "executor_identity": executor_identity,
            "executor_role_definition_sha256": executor_role_definition_sha256,
            "executor_capability_definition_sha256": executor_capability_definition_sha256,
            "role_prompt_version": role_prompt_version,
            "model_profile_identity": model_profile_identity,
            "model_profile_sha256": model_profile_sha256,
            "usage_source": usage_source,
            "upstream_commit": upstream_commit,
            "runtime_session_id": runtime_session_id,
            "runtime_hash": runtime_hash,
            "profile_hash": profile_hash,
            "policy_hash": policy_hash,
            "protocol_version": protocol_version,
            "route_key": route_key,
            "route_sha256": route_sha256,
            "definition_binding_sha256": definition_binding_sha256,
            "run_policy_sha256": run_policy_sha256,
            "provider": provider,
            "model": model,
        }
    )


def capability_artifact_provenance_hash(
    *,
    artifact_type: str,
    schema_name: str,
    schema_version: int,
    producer_kind: str,
    producer_attempt_id: str,
    run_id: str,
    step_id: str,
    scope_type: str,
    scope_id: str,
    workflow_key: str,
    workflow_version: int,
    workflow_definition_sha256: str,
    executor_identity: str,
    executor_capability_definition_sha256: str,
    definition_binding_sha256: str,
    run_policy_sha256: str,
) -> str:
    """Hash one deterministic Observation's frozen execution identity.

    The DSH envelope above predates an explicit schema discriminator and is
    deliberately unchanged: existing hashes are durable audit evidence.  A
    capability has no model route or child-runtime identity, so its envelope
    is a separate, versioned contract rather than a set of invented runtime
    values.
    """
    return sha256_hex(
        {
            "provenance_schema": _CAPABILITY_PROVENANCE_SCHEMA,
            "artifact_type": artifact_type,
            "schema_name": schema_name,
            "schema_version": schema_version,
            "producer_kind": producer_kind,
            "producer_attempt_id": producer_attempt_id,
            "run_id": run_id,
            "step_id": step_id,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "workflow_key": workflow_key,
            "workflow_version": workflow_version,
            "workflow_definition_sha256": workflow_definition_sha256,
            "executor_kind": "capability",
            "executor_identity": executor_identity,
            "executor_capability_definition_sha256": (
                executor_capability_definition_sha256
            ),
            "definition_binding_sha256": definition_binding_sha256,
            "run_policy_sha256": run_policy_sha256,
        }
    )


def content_hash(content: Any) -> str:
    return sha256_hex(content)


class ArtifactStore:
    """Owner-scoped immutable artifacts."""

    def create(
        self,
        session: Session,
        *,
        scope: Scope,
        run_id: str,
        artifact_type: str,
        schema_name: str,
        schema_version: int,
        content: Any,
        producer_kind: ProducerKind,
        now_us: int,
        step_id: str | None = None,
        sensitivity: ArtifactSensitivity = ArtifactSensitivity.private,
        workflow_key: str | None = None,
        workflow_version: int | None = None,
        role_prompt_version: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        input_artifact_ids: list[str] | None = None,
        input_sha256: str | None = None,
        source_refs: list[str] | None = None,
        quality_status: str | None = None,
        evidence_level: str | None = None,
        producer_attempt_id: str | None = None,
        upstream_commit: str | None = None,
        runtime_session_id: str | None = None,
        runtime_hash: str | None = None,
        profile_hash: str | None = None,
        policy_hash: str | None = None,
        protocol_version: str | None = None,
        route_key: str | None = None,
        route_sha256: str | None = None,
        definition_binding_sha256: str | None = None,
        run_policy_sha256: str | None = None,
        provenance_sha256: str | None = None,
    ) -> dict:
        content_json = json_dump(content)
        if len(content_json) > MAX_INLINE_CONTENT_CHARS:
            raise ValueError("artifact content exceeds the inline cap")
        content_sha256 = content_hash(content)
        provenance = self._prepare_provenance(
            session,
            scope=scope,
            run_id=run_id,
            step_id=step_id,
            producer_attempt_id=producer_attempt_id,
            artifact_type=artifact_type,
            schema_name=schema_name,
            schema_version=schema_version,
            producer_kind=producer_kind,
            workflow_key=workflow_key,
            workflow_version=workflow_version,
            role_prompt_version=role_prompt_version,
            provider=provider,
            model=model,
            artifact_input_sha256=input_sha256,
            artifact_content_sha256=content_sha256,
            caller_values={
                "upstream_commit": upstream_commit,
                "runtime_session_id": runtime_session_id,
                "runtime_hash": runtime_hash,
                "profile_hash": profile_hash,
                "policy_hash": policy_hash,
                "protocol_version": protocol_version,
                "route_key": route_key,
                "route_sha256": route_sha256,
                "definition_binding_sha256": definition_binding_sha256,
                "run_policy_sha256": run_policy_sha256,
                "provenance_sha256": provenance_sha256,
            },
        )
        artifact_id = new_id()
        session.execute(
            artifacts.insert().values(
                id=artifact_id,
                scope_type=scope.scope_type.value,
                scope_id=scope.scope_id,
                user_id=scope.scope_id if scope.scope_type == ScopeType.user else None,
                run_id=run_id,
                step_id=step_id,
                producer_attempt_id=producer_attempt_id,
                artifact_type=artifact_type,
                schema_name=schema_name,
                schema_version=schema_version,
                content_json=content_json,
                content_sha256=content_sha256,
                size_bytes=len(content_json.encode("utf-8")),
                sensitivity=sensitivity.value,
                producer_kind=producer_kind.value,
                workflow_key=provenance.get("workflow_key", workflow_key),
                workflow_version=provenance.get("workflow_version", workflow_version),
                role_prompt_version=provenance.get("role_prompt_version", role_prompt_version),
                provider=provenance.get("provider", provider),
                model=provenance.get("model", model),
                upstream_commit=provenance.get("upstream_commit"),
                runtime_session_id=provenance.get("runtime_session_id"),
                runtime_hash=provenance.get("runtime_hash"),
                profile_hash=provenance.get("profile_hash"),
                policy_hash=provenance.get("policy_hash"),
                protocol_version=provenance.get("protocol_version"),
                route_key=provenance.get("route_key"),
                route_sha256=provenance.get("route_sha256"),
                definition_binding_sha256=provenance.get("definition_binding_sha256"),
                run_policy_sha256=provenance.get("run_policy_sha256"),
                provenance_sha256=provenance.get("provenance_sha256"),
                input_artifact_ids_json=json_dump(input_artifact_ids or []),
                input_sha256=input_sha256,
                source_refs_json=json_dump(source_refs or []),
                quality_status=quality_status,
                evidence_level=evidence_level,
                created_at=now_us,
            )
        )
        row = self.get(session, scope=scope, artifact_id=artifact_id)
        assert row is not None
        return row

    @staticmethod
    def _prepare_provenance(
        session: Session,
        *,
        scope: Scope,
        run_id: str,
        step_id: str | None,
        producer_attempt_id: str | None,
        artifact_type: str,
        schema_name: str,
        schema_version: int,
        producer_kind: ProducerKind,
        workflow_key: str | None,
        workflow_version: int | None,
        role_prompt_version: str | None,
        provider: str | None,
        model: str | None,
        artifact_input_sha256: str | None,
        artifact_content_sha256: str,
        caller_values: dict[str, str | None],
    ) -> dict[str, Any]:
        if producer_attempt_id is None:
            if any(value is not None for value in caller_values.values()):
                raise ValueError("runtime provenance requires producer_attempt_id")
            return {}
        trusted = ArtifactStore._trusted_provenance(
            session,
            scope=scope,
            run_id=run_id,
            step_id=step_id,
            producer_attempt_id=producer_attempt_id,
        )
        if artifact_input_sha256 != trusted["attempt_input_sha256"]:
            raise ValueError("artifact input hash does not match producer Attempt")
        if artifact_content_sha256 != trusted["attempt_output_sha256"]:
            raise ValueError("artifact content hash does not match producer Attempt")
        assert step_id is not None
        for name, supplied in {
            "workflow_key": workflow_key,
            "workflow_version": workflow_version,
            "role_prompt_version": role_prompt_version,
        }.items():
            if supplied is not None and supplied != trusted[name]:
                raise ValueError(f"caller {name} does not match frozen Attempt identity")
        if trusted["frozen_schema_name"] is not None and (
            schema_name,
            schema_version,
        ) != (trusted["frozen_schema_name"], trusted["frozen_schema_version"]):
            raise ValueError("caller schema name/version does not match frozen Attempt identity")
        if producer_kind.value != trusted["producer_kind"]:
            raise ValueError("caller producer_kind does not match frozen Attempt executor")
        for name, supplied in {**caller_values, "provider": provider, "model": model}.items():
            if name == "provenance_sha256" and supplied is not None:
                raise ValueError("caller provenance_sha256 is not accepted")
            if supplied is not None and supplied != trusted[name]:
                raise ValueError(f"caller {name} does not match frozen Attempt provenance")
        expected = ArtifactStore._provenance_hash(
            artifact_type=artifact_type,
            schema_name=schema_name,
            schema_version=schema_version,
            producer_kind=producer_kind.value,
            producer_attempt_id=producer_attempt_id,
            run_id=run_id,
            step_id=step_id,
            scope_type=scope.scope_type.value,
            scope_id=scope.scope_id,
            workflow_key=trusted["workflow_key"],
            workflow_version=trusted["workflow_version"],
            workflow_definition_sha256=trusted["workflow_definition_sha256"],
            executor_kind=trusted["executor_kind"],
            executor_identity=trusted["executor_identity"],
            executor_role_definition_sha256=trusted["executor_role_definition_sha256"],
            executor_capability_definition_sha256=trusted[
                "executor_capability_definition_sha256"
            ],
            role_prompt_version=trusted["role_prompt_version"],
            model_profile_identity=trusted["model_profile_identity"],
            model_profile_sha256=trusted["model_profile_sha256"],
            usage_source=trusted["usage_source"],
            upstream_commit=trusted["upstream_commit"],
            runtime_session_id=trusted["runtime_session_id"],
            runtime_hash=trusted["runtime_hash"],
            profile_hash=trusted["profile_hash"],
            policy_hash=trusted["policy_hash"],
            protocol_version=trusted["protocol_version"],
            route_key=trusted["route_key"],
            route_sha256=trusted["route_sha256"],
            definition_binding_sha256=trusted["definition_binding_sha256"],
            run_policy_sha256=trusted["run_policy_sha256"],
            provider=trusted["provider"],
            model=trusted["model"],
        )
        return {**trusted, "provenance_sha256": expected}

    @staticmethod
    def _provenance_hash(
        *,
        artifact_type: str,
        schema_name: str,
        schema_version: int,
        producer_kind: str,
        producer_attempt_id: str,
        run_id: str,
        step_id: str,
        scope_type: str,
        scope_id: str,
        **trusted: Any,
    ) -> str:
        """Select the immutable envelope for the authenticated executor."""
        if trusted["executor_kind"] == "capability":
            capability_sha256 = trusted["executor_capability_definition_sha256"]
            assert isinstance(capability_sha256, str)
            return capability_artifact_provenance_hash(
                artifact_type=artifact_type,
                schema_name=schema_name,
                schema_version=schema_version,
                producer_kind=producer_kind,
                producer_attempt_id=producer_attempt_id,
                run_id=run_id,
                step_id=step_id,
                scope_type=scope_type,
                scope_id=scope_id,
                workflow_key=trusted["workflow_key"],
                workflow_version=trusted["workflow_version"],
                workflow_definition_sha256=trusted["workflow_definition_sha256"],
                executor_identity=trusted["executor_identity"],
                executor_capability_definition_sha256=capability_sha256,
                definition_binding_sha256=trusted["definition_binding_sha256"],
                run_policy_sha256=trusted["run_policy_sha256"],
            )
        return artifact_provenance_hash(
            artifact_type=artifact_type,
            schema_name=schema_name,
            schema_version=schema_version,
            producer_kind=producer_kind,
            producer_attempt_id=producer_attempt_id,
            run_id=run_id,
            step_id=step_id,
            scope_type=scope_type,
            scope_id=scope_id,
            workflow_key=trusted["workflow_key"],
            workflow_version=trusted["workflow_version"],
            workflow_definition_sha256=trusted["workflow_definition_sha256"],
            executor_kind=trusted["executor_kind"],
            executor_identity=trusted["executor_identity"],
            executor_role_definition_sha256=trusted["executor_role_definition_sha256"],
            executor_capability_definition_sha256=trusted[
                "executor_capability_definition_sha256"
            ],
            role_prompt_version=trusted["role_prompt_version"],
            model_profile_identity=trusted["model_profile_identity"],
            model_profile_sha256=trusted["model_profile_sha256"],
            usage_source=trusted["usage_source"],
            upstream_commit=trusted["upstream_commit"],
            runtime_session_id=trusted["runtime_session_id"],
            runtime_hash=trusted["runtime_hash"],
            profile_hash=trusted["profile_hash"],
            policy_hash=trusted["policy_hash"],
            protocol_version=trusted["protocol_version"],
            route_key=trusted["route_key"],
            route_sha256=trusted["route_sha256"],
            definition_binding_sha256=trusted["definition_binding_sha256"],
            run_policy_sha256=trusted["run_policy_sha256"],
            provider=trusted["provider"],
            model=trusted["model"],
        )

    @staticmethod
    def _trusted_provenance(
        session: Session,
        *,
        scope: Scope,
        run_id: str,
        step_id: str | None,
        producer_attempt_id: str,
    ) -> dict[str, Any]:
        """Read the only trusted provenance sources for an Attempt artifact."""
        attempt = (
            session.execute(
                select(attempts).where(
                    attempts.c.id == producer_attempt_id,
                    attempts.c.run_id == run_id,
                    attempts.c.scope_type == scope.scope_type.value,
                    attempts.c.scope_id == scope.scope_id,
                )
            )
            .mappings()
            .first()
        )
        snapshot = (
            session.execute(
                select(attempt_definition_snapshots).where(
                    attempt_definition_snapshots.c.attempt_id == producer_attempt_id,
                    attempt_definition_snapshots.c.run_id == run_id,
                    attempt_definition_snapshots.c.scope_type == scope.scope_type.value,
                    attempt_definition_snapshots.c.scope_id == scope.scope_id,
                )
            )
            .mappings()
            .first()
        )
        if (
            attempt is None
            or snapshot is None
            or attempt["step_id"] != step_id
            or snapshot["step_id"] != step_id
            or snapshot["attempt_no"] != attempt["attempt_no"]
        ):
            raise ValueError("artifact producer Attempt or frozen snapshot scope mismatch")
        try:
            frozen = ExecutionSnapshotStore().read_attempt(
                session,
                scope=scope,
                attempt_id=producer_attempt_id,
                require_for_execution=True,
            )
        except (NotFoundError, SnapshotIntegrityError) as exc:
            raise ValueError("artifact producer Attempt or frozen snapshot scope mismatch") from exc
        if frozen is None:
            raise ValueError("artifact producer Attempt or frozen snapshot scope mismatch")
        workflow_key, workflow_version_text = frozen.policy_snapshot.workflow_identity.rsplit(
            "@", 1
        )
        if not workflow_version_text.isdigit():
            raise ValueError("frozen Attempt workflow identity is invalid")
        workflow_version = int(workflow_version_text)
        role_prompt_version = (
            frozen.role_definition.prompt_template_version
            if frozen.role_definition is not None
            else None
        )
        frozen_schema_name: str | None = None
        frozen_schema_version: int | None = None
        schema_identity: str | None = None
        schema_label: str | None = None
        if frozen.role_definition is not None:
            schema_identity = frozen.role_definition.output_schema
            schema_label = "role output"
        elif frozen.capability_definition is not None:
            schema_identity = frozen.capability_definition.observation_schema
            schema_label = "capability observation"
        if schema_identity is not None:
            try:
                frozen_schema_name, frozen_schema_version_text = schema_identity.rsplit("@", 1)
                if not frozen_schema_name or not frozen_schema_version_text.isdigit():
                    raise ValueError
                frozen_schema_version = int(frozen_schema_version_text)
            except (ValueError, TypeError):
                raise ValueError(f"frozen Attempt {schema_label} schema is invalid") from None
        producer_kind = (
            ProducerKind.model_inference.value
            if frozen.executor_kind == "role"
            else ProducerKind.deterministic.value
        )
        values: dict[str, Any] = {
            "workflow_key": workflow_key,
            "workflow_version": workflow_version,
            "workflow_definition_sha256": frozen.policy_snapshot.workflow_definition_sha256,
            "executor_kind": frozen.executor_kind,
            "executor_identity": frozen.executor_identity,
            "executor_role_definition_sha256": frozen.executor_role_definition_sha256,
            "executor_capability_definition_sha256": frozen.executor_capability_definition_sha256,
            "role_prompt_version": role_prompt_version,
            "frozen_schema_name": frozen_schema_name,
            "frozen_schema_version": frozen_schema_version,
            "model_profile_identity": frozen.model_profile_identity,
            "model_profile_sha256": frozen.model_profile_sha256,
            "usage_source": frozen.usage_source,
            "producer_kind": producer_kind,
            "attempt_input_sha256": attempt["input_sha256"],
            "attempt_output_sha256": attempt["output_sha256"],
            "upstream_commit": attempt["upstream_commit"],
            "runtime_session_id": attempt["runtime_session_id"],
            "runtime_hash": attempt["runtime_hash"],
            "profile_hash": attempt["profile_hash"],
            "policy_hash": attempt["policy_hash"],
            "protocol_version": attempt["protocol_version"],
            "route_key": snapshot["model_route_key"],
            "route_sha256": snapshot["model_route_sha256"],
            "definition_binding_sha256": snapshot["definition_binding_sha256"],
            "run_policy_sha256": snapshot["run_policy_sha256"],
            "provider": snapshot["provider"],
            "model": snapshot["model"],
        }
        common_required_names = (
            "workflow_key",
            "workflow_version",
            "workflow_definition_sha256",
            "executor_kind",
            "executor_identity",
            "producer_kind",
            "definition_binding_sha256",
            "run_policy_sha256",
        )
        if any(values[name] is None for name in common_required_names):
            raise ValueError("frozen Attempt provenance is incomplete")
        if attempt["state"] != "succeeded":
            raise ValueError("artifact producer Attempt is not succeeded")
        for name in ("attempt_input_sha256", "attempt_output_sha256"):
            value = values[name]
            if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
                raise ValueError(f"artifact producer {name} is not a lowercase SHA-256")
        if frozen.executor_kind == "role":
            role_required_names = (
                "executor_role_definition_sha256",
                "role_prompt_version",
                "model_profile_identity",
                "model_profile_sha256",
                "usage_source",
                "upstream_commit",
                "runtime_session_id",
                "runtime_hash",
                "profile_hash",
                "policy_hash",
                "protocol_version",
                "route_key",
                "route_sha256",
                "provider",
                "model",
            )
            if any(values[name] is None for name in role_required_names):
                raise ValueError("frozen Attempt role identity is incomplete")
        elif frozen.executor_kind == "capability":
            if values["executor_capability_definition_sha256"] is None:
                raise ValueError("frozen Attempt capability identity is incomplete")
            capability_null_names = (
                "executor_role_definition_sha256",
                "role_prompt_version",
                "model_profile_identity",
                "model_profile_sha256",
                "usage_source",
                "upstream_commit",
                "runtime_session_id",
                "runtime_hash",
                "profile_hash",
                "policy_hash",
                "protocol_version",
                "route_key",
                "route_sha256",
                "provider",
                "model",
            )
            if any(values[name] is not None for name in capability_null_names):
                raise ValueError("frozen capability Attempt contains model/runtime provenance")
        else:
            raise ValueError("frozen Attempt executor kind is invalid")
        for name in (
            "workflow_definition_sha256",
            "executor_role_definition_sha256",
            "executor_capability_definition_sha256",
            "model_profile_sha256",
            "runtime_hash",
            "profile_hash",
            "policy_hash",
            "route_sha256",
            "definition_binding_sha256",
            "run_policy_sha256",
        ):
            value = values[name]
            if value is not None and _SHA256.fullmatch(value) is None:
                raise ValueError(f"frozen Attempt {name} is not a lowercase SHA-256")
        if frozen.executor_kind == "role":
            upstream_commit = values["upstream_commit"]
            if upstream_commit is None or _GIT_COMMIT.fullmatch(upstream_commit) is None:
                raise ValueError(
                    "frozen Attempt upstream_commit is not a lowercase full git SHA-1"
                )
            runtime_session_id = values["runtime_session_id"]
            if (
                runtime_session_id is None
                or len(runtime_session_id) > 256
                or any(character.isspace() for character in runtime_session_id)
            ):
                raise ValueError("frozen Attempt runtime_session_id is invalid")
        for name in (
            "workflow_key",
            "executor_kind",
            "executor_identity",
            "producer_kind",
        ):
            value = values[name]
            assert value is not None
            if not value:
                raise ValueError(f"frozen Attempt {name} is empty")
        if frozen.executor_kind == "role":
            for name in ("protocol_version", "route_key", "provider", "model"):
                value = values[name]
                assert value is not None
                if not value:
                    raise ValueError(f"frozen Attempt {name} is empty")
        return values

    @staticmethod
    def _validate_provenance(session: Session, *, scope: Scope, row: dict) -> None:
        producer_attempt_id = row.get("producer_attempt_id")
        names = (
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
        )
        if producer_attempt_id is None:
            if any(row.get(name) is not None for name in names):
                raise ValueError("artifact contains detached runtime provenance")
            return
        trusted = ArtifactStore._trusted_provenance(
            session,
            scope=scope,
            run_id=row["run_id"],
            step_id=row["step_id"],
            producer_attempt_id=producer_attempt_id,
        )
        if row.get("input_sha256") != trusted["attempt_input_sha256"]:
            raise ValueError("artifact input hash does not match producer Attempt")
        if row.get("content_sha256") != trusted["attempt_output_sha256"]:
            raise ValueError("artifact content hash does not match producer Attempt")
        common_required = (
            "step_id",
            "workflow_key",
            "workflow_version",
            "producer_kind",
            "definition_binding_sha256",
            "run_policy_sha256",
            "provenance_sha256",
        )
        if any(row.get(name) is None for name in common_required):
            raise ValueError("artifact Attempt provenance is incomplete")
        if trusted["executor_kind"] == "role":
            role_required = (
                "upstream_commit",
                "runtime_session_id",
                "runtime_hash",
                "profile_hash",
                "policy_hash",
                "protocol_version",
                "route_key",
                "route_sha256",
                "provider",
                "model",
            )
            if any(row.get(name) is None for name in role_required):
                raise ValueError("artifact runtime provenance is incomplete")
        else:
            capability_null_names = (
                "role_prompt_version",
                "provider",
                "model",
                "upstream_commit",
                "runtime_session_id",
                "runtime_hash",
                "profile_hash",
                "policy_hash",
                "protocol_version",
                "route_key",
                "route_sha256",
            )
            if any(row.get(name) is not None for name in capability_null_names):
                raise ValueError("capability artifact contains model/runtime provenance")
        for name in (
            "runtime_hash",
            "profile_hash",
            "policy_hash",
            "route_sha256",
            "definition_binding_sha256",
            "run_policy_sha256",
            "provenance_sha256",
        ):
            value = row.get(name)
            if value is not None and _SHA256.fullmatch(value) is None:
                raise ValueError(f"artifact provenance {name} is invalid")
        upstream_commit = row.get("upstream_commit")
        if upstream_commit is not None and _GIT_COMMIT.fullmatch(upstream_commit) is None:
            raise ValueError("artifact provenance upstream_commit is invalid")
        runtime_session_id = row.get("runtime_session_id")
        if runtime_session_id is not None and (
            not runtime_session_id
            or len(runtime_session_id) > 256
            or any(character.isspace() for character in runtime_session_id)
        ):
            raise ValueError("artifact provenance runtime_session_id is invalid")
        for name in (
            *names,
            "provider",
            "model",
            "workflow_key",
            "workflow_version",
            "role_prompt_version",
            "producer_kind",
        ):
            if name != "provenance_sha256" and row[name] != trusted[name]:
                raise ValueError(f"artifact {name} does not match frozen Attempt identity")
        if trusted["frozen_schema_name"] is not None and (
            row["schema_name"],
            row["schema_version"],
        ) != (trusted["frozen_schema_name"], trusted["frozen_schema_version"]):
            raise ValueError("artifact schema name/version does not match frozen Attempt identity")
        expected = ArtifactStore._provenance_hash(
            artifact_type=row["artifact_type"],
            schema_name=row["schema_name"],
            schema_version=row["schema_version"],
            producer_kind=row["producer_kind"],
            producer_attempt_id=producer_attempt_id,
            run_id=row["run_id"],
            step_id=row["step_id"],
            scope_type=row["scope_type"],
            scope_id=row["scope_id"],
            workflow_key=trusted["workflow_key"],
            workflow_version=trusted["workflow_version"],
            workflow_definition_sha256=trusted["workflow_definition_sha256"],
            executor_kind=trusted["executor_kind"],
            executor_identity=trusted["executor_identity"],
            executor_role_definition_sha256=trusted["executor_role_definition_sha256"],
            executor_capability_definition_sha256=trusted[
                "executor_capability_definition_sha256"
            ],
            role_prompt_version=trusted["role_prompt_version"],
            model_profile_identity=trusted["model_profile_identity"],
            model_profile_sha256=trusted["model_profile_sha256"],
            usage_source=trusted["usage_source"],
            upstream_commit=trusted["upstream_commit"],
            runtime_session_id=trusted["runtime_session_id"],
            runtime_hash=trusted["runtime_hash"],
            profile_hash=trusted["profile_hash"],
            policy_hash=trusted["policy_hash"],
            protocol_version=trusted["protocol_version"],
            route_key=trusted["route_key"],
            route_sha256=trusted["route_sha256"],
            definition_binding_sha256=trusted["definition_binding_sha256"],
            run_policy_sha256=trusted["run_policy_sha256"],
            provider=trusted["provider"],
            model=trusted["model"],
        )
        if row["provenance_sha256"] != expected:
            raise ValueError("artifact provenance hash does not match its identity envelope")

    def get(self, session: Session, *, scope: Scope, artifact_id: str) -> dict | None:
        row = (
            session.execute(
                select(artifacts).where(scope.where(artifacts), artifacts.c.id == artifact_id)
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        result = dict(row)
        self._validate_provenance(session, scope=scope, row=result)
        return result

    def require(self, session: Session, *, scope: Scope, artifact_id: str) -> dict:
        row = self.get(session, scope=scope, artifact_id=artifact_id)
        if row is None:
            raise NotFoundError("artifact not found")
        return row

    def read(self, session: Session, *, scope: Scope, artifact_id: str) -> dict:
        """Read one artifact and revalidate its producer/runtime provenance."""
        return self.require(session, scope=scope, artifact_id=artifact_id)

    def for_run(self, session: Session, *, scope: Scope, run_id: str) -> list[dict]:
        rows = session.execute(
            select(artifacts)
            .where(scope.where(artifacts), artifacts.c.run_id == run_id)
            .order_by(artifacts.c.created_at, artifacts.c.id)
        ).mappings()
        result = [dict(row) for row in rows]
        for row in result:
            self._validate_provenance(session, scope=scope, row=row)
        return result

    def link(
        self,
        session: Session,
        *,
        scope: Scope,
        run_id: str,
        from_artifact_id: str,
        to_artifact_id: str,
        link_kind: str,
        now_us: int,
    ) -> None:
        self.require(session, scope=scope, artifact_id=from_artifact_id)
        self.require(session, scope=scope, artifact_id=to_artifact_id)
        session.execute(
            artifact_links.insert().values(
                id=new_id(),
                scope_type=scope.scope_type.value,
                scope_id=scope.scope_id,
                run_id=run_id,
                from_artifact_id=from_artifact_id,
                to_artifact_id=to_artifact_id,
                link_kind=link_kind,
                created_at=now_us,
            )
        )

    def tombstone(
        self,
        session: Session,
        *,
        scope: Scope,
        artifact_id: str,
        reason: str,
        now_us: int,
    ) -> None:
        row = self.require(session, scope=scope, artifact_id=artifact_id)
        if row["deleted_at"] is not None:
            return
        session.execute(
            update(artifacts)
            .where(scope.where(artifacts), artifacts.c.id == artifact_id)
            .values(
                content_json=None,
                deleted_at=now_us,
                deletion_reason=reason,
            )
        )


class PublicReleaseService:
    """The only system -> user data bridge."""

    #: Schemas a release may carry; anything else is refused at release time.
    ALLOWED_RELEASE_SCHEMAS = {
        ("daily.ingest_projection", 1),
        ("harness.canary_manifest", 1),
    }

    def release(
        self,
        session: Session,
        *,
        source_artifact_id: str,
        public_manifest_sha256: str,
        release_policy_version: str,
        release_id: str,
        now_us: int,
    ) -> dict:
        """Freeze an immutable public release over a public system artifact."""
        source_row = (
            session.execute(
                select(artifacts).where(
                    artifacts.c.id == source_artifact_id,
                    artifacts.c.scope_type == ScopeType.system.value,
                    artifacts.c.sensitivity == ArtifactSensitivity.public.value,
                )
            )
            .mappings()
            .first()
        )
        if source_row is None:
            raise ApprovalConflictError("release source must be a public system artifact")
        source = dict(source_row)
        ArtifactStore._validate_provenance(
            session,
            scope=Scope.system(source["scope_id"]),
            row=source,
        )
        if (source["schema_name"], source["schema_version"]) not in self.ALLOWED_RELEASE_SCHEMAS:
            raise ApprovalConflictError(
                f"schema {source['schema_name']}@{source['schema_version']} is not releasable"
            )
        envelope = {
            "release_id": release_id,
            "source_schema_name": source["schema_name"],
            "source_schema_version": source["schema_version"],
            "source_content_sha256": source["content_sha256"],
            "public_manifest_sha256": public_manifest_sha256,
            "release_policy_version": release_policy_version,
        }
        release_sha256 = sha256_hex(envelope)
        session.execute(
            public_artifact_releases.insert().values(
                id=release_id,
                source_artifact_id=source_artifact_id,
                source_schema_name=source["schema_name"],
                source_schema_version=source["schema_version"],
                source_content_sha256=source["content_sha256"],
                public_manifest_sha256=public_manifest_sha256,
                release_policy_version=release_policy_version,
                release_sha256=release_sha256,
                created_at=now_us,
            )
        )
        row = self.get_release(session, release_id=release_id)
        assert row is not None
        return dict(row)

    def get_release(self, session: Session, *, release_id: str) -> dict | None:
        row = (
            session.execute(
                select(public_artifact_releases).where(public_artifact_releases.c.id == release_id)
            )
            .mappings()
            .first()
        )
        return dict(row) if row is not None else None

    def revoke(self, session: Session, *, release_id: str, now_us: int) -> None:
        row = self.get_release(session, release_id=release_id)
        if row is None:
            raise NotFoundError("release not found")
        assert row is not None
        if row["revoked_at"] is None:
            session.execute(
                update(public_artifact_releases)
                .where(public_artifact_releases.c.id == release_id)
                .values(revoked_at=now_us)
            )

    def projection(
        self,
        session: Session,
        *,
        release_id: str,
        user_id: str,
        projection_schema_name: str,
        projection_schema_version: int,
        content: Any,
        run_id: str,
        now_us: int,
    ) -> dict:
        """Create (or return) the owner's minimal projection of a release."""
        release = self.get_release(session, release_id=release_id)
        if release is None or release["revoked_at"] is not None:
            raise ApprovalConflictError("release missing or revoked")
        assert release is not None
        existing = (
            session.execute(
                select(public_artifact_projections).where(
                    public_artifact_projections.c.release_id == release_id,
                    public_artifact_projections.c.user_id == user_id,
                    public_artifact_projections.c.projection_schema_name == projection_schema_name,
                    public_artifact_projections.c.projection_schema_version
                    == projection_schema_version,
                )
            )
            .mappings()
            .first()
        )
        if existing is not None:
            return dict(existing)
        scope = Scope.user(user_id)
        artifact = self._artifact_store().create(
            session,
            scope=scope,
            run_id=run_id,
            artifact_type=f"projection.{projection_schema_name}",
            schema_name=projection_schema_name,
            schema_version=projection_schema_version,
            content=content,
            producer_kind=ProducerKind.deterministic,
            sensitivity=ArtifactSensitivity.public,
            now_us=now_us,
            workflow_key="projection",
        )
        session.execute(
            public_artifact_projections.insert().values(
                id=new_id(),
                release_id=release_id,
                user_id=user_id,
                projection_artifact_id=artifact["id"],
                release_sha256=release["release_sha256"],
                projection_schema_name=projection_schema_name,
                projection_schema_version=projection_schema_version,
                projection_sha256=artifact["content_sha256"],
                created_at=now_us,
            )
        )
        row = (
            session.execute(
                select(public_artifact_projections).where(
                    public_artifact_projections.c.release_id == release_id,
                    public_artifact_projections.c.user_id == user_id,
                )
            )
            .mappings()
            .first()
        )
        assert row is not None
        return dict(row)

    @staticmethod
    def _artifact_store() -> ArtifactStore:
        return ArtifactStore()


def canonical_release_hash(
    *,
    release_id: str,
    source_schema_name: str,
    source_schema_version: int,
    source_content_sha256: str,
    public_manifest_sha256: str,
    release_policy_version: str,
) -> str:
    """The frozen envelope hash every client can recompute."""
    return sha256_hex(
        canonical_json(
            {
                "release_id": release_id,
                "source_schema_name": source_schema_name,
                "source_schema_version": source_schema_version,
                "source_content_sha256": source_content_sha256,
                "public_manifest_sha256": public_manifest_sha256,
                "release_policy_version": release_policy_version,
            }
        )
    )
