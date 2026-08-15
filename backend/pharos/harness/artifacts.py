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
from pharos.harness.repository import Scope, json_dump, new_id
from pharos.harness.tables import (
    artifact_links,
    artifacts,
    public_artifact_projections,
    public_artifact_releases,
)

MAX_INLINE_CONTENT_CHARS = 1_000_000


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
    ) -> dict:
        content_json = json_dump(content)
        if len(content_json) > MAX_INLINE_CONTENT_CHARS:
            raise ValueError("artifact content exceeds the inline cap")
        artifact_id = new_id()
        session.execute(
            artifacts.insert().values(
                id=artifact_id,
                scope_type=scope.scope_type.value,
                scope_id=scope.scope_id,
                user_id=scope.scope_id if scope.scope_type == ScopeType.user else None,
                run_id=run_id,
                step_id=step_id,
                artifact_type=artifact_type,
                schema_name=schema_name,
                schema_version=schema_version,
                content_json=content_json,
                content_sha256=content_hash(content),
                size_bytes=len(content_json.encode("utf-8")),
                sensitivity=sensitivity.value,
                producer_kind=producer_kind.value,
                workflow_key=workflow_key,
                workflow_version=workflow_version,
                role_prompt_version=role_prompt_version,
                provider=provider,
                model=model,
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

    def get(self, session: Session, *, scope: Scope, artifact_id: str) -> dict | None:
        row = (
            session.execute(
                select(artifacts).where(scope.where(artifacts), artifacts.c.id == artifact_id)
            )
            .mappings()
            .first()
        )
        return dict(row) if row is not None else None

    def require(self, session: Session, *, scope: Scope, artifact_id: str) -> dict:
        row = self.get(session, scope=scope, artifact_id=artifact_id)
        if row is None:
            raise NotFoundError("artifact not found")
        return row

    def for_run(self, session: Session, *, scope: Scope, run_id: str) -> list[dict]:
        rows = session.execute(
            select(artifacts)
            .where(scope.where(artifacts), artifacts.c.run_id == run_id)
            .order_by(artifacts.c.created_at, artifacts.c.id)
        ).mappings()
        return [dict(row) for row in rows]

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
        source = (
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
        if source is None:
            raise ApprovalConflictError("release source must be a public system artifact")
        assert source is not None
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
