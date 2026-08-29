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
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from pharos.harness.configrev import (
    HarnessConfigSnapshot,
    WorkflowRoute,
    decode_snapshot_payload,
    validate_snapshot,
)
from pharos.harness.contracts import (
    ConfigIntegrityError,
    IdempotencyConflictError,
    NotFoundError,
    ScopeType,
    StaleConfigError,
)
from pharos.harness.definitions import WorkflowDefinition
from pharos.harness.registry import Registry
from pharos.harness.tables import (
    config_head,
    config_revisions,
    config_workflow_routes,
    runs,
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
