"""Database-bound persistence for one DeepSeek Harness Attempt.

The DSH gateway intentionally receives only an :class:`AttemptContext`; it
must not receive a SQLAlchemy session or a mutable configuration object.  This
adapter opens a short transaction for every callback, re-authenticates the
current configuration before reserving a launch, and delegates all row
mutations to :class:`HarnessAttemptRepository`'s owner/generation CAS.

No exception from the database, configuration validator, or repository is
returned to the runtime.  Besides avoiding accidental disclosure, the fixed
diagnostic makes a persistence failure safe to classify and retry at the
runner boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from pharos.db import session as db_session
from pharos.harness.configrev import emergency_stop_active
from pharos.harness.contracts import (
    AttemptErrorClass,
    AttemptState,
    DeliveryState,
    GatewayError,
    ScopeType,
)
from pharos.harness.dispatcher import HarnessDispatcher
from pharos.harness.dsh_gateway import DshLaunch
from pharos.harness.execution_snapshots import ExecutionSnapshotStore
from pharos.harness.model_gateway import AttemptContext
from pharos.harness.repository import (
    AttemptRuntimeLaunch,
    HarnessAttemptRepository,
    HarnessConfigService,
    Scope,
)
from pharos.harness.seams import Clock
from pharos.harness.tables import runs


class DshPersistenceError(GatewayError):
    """A fixed, deliberately de-identified persistence failure."""

    error_class = AttemptErrorClass.configuration

    def __init__(self) -> None:
        super().__init__("DSH persistence operation failed")


class DshPersistenceAdapter:
    """Persist DSH launch facts under the exact Attempt lease fence.

    ``session_factory`` is normally SQLAlchemy's ``sessionmaker``.  It is an
    explicit constructor argument so tests and a future database adapter can
    provide their own short-lived sessions without changing the DSH protocol.
    The adapter owns transaction boundaries and never commits a caller-owned
    session.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session] | None = None,
        *,
        config_service: HarnessConfigService,
        clock: Clock,
        attempt_repository: HarnessAttemptRepository | None = None,
    ) -> None:
        if not callable(config_service.current_validated):
            raise TypeError("config_service must provide current_validated")
        if not callable(clock.utc_epoch_us):
            raise TypeError("clock must provide utc_epoch_us")
        self._session_factory = session_factory or self._default_session_factory
        if not callable(self._session_factory):
            raise TypeError("session_factory must be callable")
        self._config = config_service
        self._clock = clock
        self._attempts = attempt_repository or HarnessAttemptRepository()

    @staticmethod
    def _default_session_factory() -> Session:
        factory = db_session._SessionLocal
        if factory is None:
            raise RuntimeError("database is not initialized")
        return factory()

    @contextmanager
    def _transaction(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except BaseException:
            # Rollback itself must not replace the fixed error raised to the
            # runtime; close/rollback failures are operational diagnostics only.
            with suppress(BaseException):
                session.rollback()
            raise
        finally:
            with suppress(BaseException):
                session.close()

    @staticmethod
    def _context_identity(context: AttemptContext) -> tuple[Scope, dict[str, Any]]:
        if not isinstance(context, AttemptContext):
            raise ValueError("invalid Attempt context")
        if context.runtime_kind != "dsh":
            raise ValueError("Attempt is not a DSH runtime")
        try:
            scope = Scope(ScopeType(context.scope_type), context.scope_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid Attempt scope") from exc
        # Keep this mapping in one place: every DB mutation below receives all
        # identity fields, so a stale callback cannot fall back to attempt_id.
        identity: dict[str, Any] = {
            "scope": scope,
            "run_id": context.run_id,
            "step_id": context.step_id,
            "attempt_id": context.attempt_id,
            "attempt_no": context.attempt_no,
            "lease_owner": context.lease_owner,
            "expected_state": AttemptState.running,
        }
        return scope, identity

    def _now(self) -> int:
        now = self._clock.utc_epoch_us()
        if type(now) is not int or now <= 0:
            raise ValueError("invalid clock")
        return now

    @staticmethod
    def _fixed_failure() -> DshPersistenceError:
        return DshPersistenceError()

    @staticmethod
    def _authenticate_frozen_context(
        session: Session,
        context: AttemptContext,
        *,
        now_us: int,
    ) -> None:
        """Authenticate a launch against its immutable execution contract.

        The context is assembled by the runner, but it is not an authority:
        a direct caller (or a stale worker) must not be able to select a
        different workflow, role, route, or model merely by supplying a
        self-consistent set of hashes.  ``read_attempt`` re-validates the
        complete Run/Attempt snapshot and its definition closure before this
        adapter writes any launch provenance.
        """
        snapshot = ExecutionSnapshotStore().read_attempt(
            session,
            scope=context.scope_type,
            scope_id=context.scope_id,
            attempt_id=context.attempt_id,
            require_for_execution=True,
        )
        if snapshot is None:  # defensive: require_for_execution is fail-closed
            raise ValueError("missing frozen Attempt snapshot")
        expected_identity = (
            context.run_id,
            context.step_id,
            context.attempt_id,
            context.attempt_no,
        )
        actual_identity = (
            snapshot.run_id,
            snapshot.step_id,
            snapshot.attempt_id,
            snapshot.attempt_no,
        )
        if expected_identity != actual_identity:
            raise ValueError("Attempt identity does not match frozen snapshot")

        expected_workflow = (
            f"{context.workflow_key}@{context.workflow_version}",
            context.workflow_definition_sha256,
            context.definition_binding_sha256,
            context.run_policy_sha256,
        )
        actual_workflow = (
            # The workflow and binding hashes are held by the immutable Run
            # snapshot; the policy hash is its canonical policy digest.
            snapshot.policy_snapshot.workflow_identity,
            snapshot.policy_snapshot.workflow_definition_sha256,
            snapshot.definition_binding_sha256,
            snapshot.policy_snapshot.policy_hash(),
        )
        if expected_workflow != actual_workflow:
            raise ValueError("workflow does not match frozen snapshot")

        expected_executor = (
            context.role,
            context.role_definition_sha256,
            context.runtime_kind,
            context.model_profile_identity,
            context.model_profile_sha256,
            context.model_route_key,
            context.model_route_sha256,
            context.usage_source,
            context.provider,
            context.model,
        )
        actual_executor = (
            snapshot.executor_identity,
            snapshot.executor_role_definition_sha256,
            snapshot.runtime_kind,
            snapshot.model_profile_identity,
            snapshot.model_profile_sha256,
            snapshot.model_route_key,
            snapshot.model_route_sha256,
            snapshot.usage_source,
            snapshot.provider,
            snapshot.model,
        )
        if context.runtime_kind != "dsh" or expected_executor != actual_executor:
            raise ValueError("executor does not match frozen snapshot")

        bindings = [
            binding
            for binding in snapshot.policy_snapshot.role_bindings
            if binding.role_identity == snapshot.executor_identity
        ]
        limits = [
            limit
            for limit in snapshot.policy_snapshot.role_limits
            if limit.role_identity == snapshot.executor_identity
        ]
        if len(bindings) != 1 or len(limits) != 1:
            raise ValueError("executor limits are not uniquely frozen")
        binding = bindings[0]
        route = binding.route
        output_ceiling = min(
            route.max_output_tokens or binding.role_definition.token_budget.output_tokens,
            binding.role_definition.token_budget.output_tokens,
            snapshot.step_definition.budget.output_tokens,
            snapshot.policy_snapshot.effective_budget.output_tokens,
            limits[0].max_output_tokens,
            snapshot.policy_snapshot.agent_limits.max_output_tokens,
        )
        input_ceiling = min(
            binding.role_definition.token_budget.input_tokens,
            snapshot.step_definition.budget.input_tokens,
            snapshot.policy_snapshot.effective_budget.input_tokens,
        )
        if (
            context.reasoning_effort != route.reasoning_effort
            or context.max_output_tokens is None
            or context.max_output_tokens > output_ceiling
            or context.max_input_tokens is None
            or context.max_input_tokens > input_ceiling
        ):
            raise ValueError("runtime limits do not match frozen snapshot")

        timeout_seconds = snapshot.timeout_seconds
        if timeout_seconds is None:
            timeout_seconds = snapshot.step_definition.budget.wall_seconds
        maximum_deadline = now_us + int(float(timeout_seconds) * 1_000_000)
        if context.deadline_at_us <= now_us or context.deadline_at_us > maximum_deadline:
            raise ValueError("runtime deadline exceeds the frozen timeout")

        # Input identity is a Run field rather than an Attempt snapshot field;
        # authenticate it in the same transaction and under the same owner.
        input_hash = session.execute(
            select(runs.c.input_sha256).where(
                runs.c.id == snapshot.run_id,
                runs.c.scope_type == snapshot.scope_type,
                runs.c.scope_id == snapshot.scope_id,
            )
        ).scalar_one_or_none()
        if input_hash != context.input_sha256:
            raise ValueError("input does not match frozen Run")

    def reserve_launch(self, context: AttemptContext, launch: DshLaunch) -> None:
        """Authorize and durably reserve a complete launch before ``spawn``."""
        try:
            _scope, identity = self._context_identity(context)
            if not isinstance(launch, DshLaunch):
                raise ValueError("invalid DSH launch")
            if launch.runtime_session_id != context.attempt_id:
                raise ValueError("runtime session is not bound to Attempt")
            if launch.deadline_at != context.deadline_at_us:
                raise ValueError("runtime deadline is not bound to Attempt")
            now = self._now()
            runtime_launch = AttemptRuntimeLaunch(
                runtime_session_id=launch.runtime_session_id,
                deadline_at=launch.deadline_at,
                upstream_commit=launch.upstream_commit,
                runtime_hash=launch.runtime_hash,
                profile_hash=launch.profile_hash,
                policy_hash=launch.policy_hash,
                protocol_version=launch.protocol_version,
            )
            with self._transaction() as session:
                if emergency_stop_active():
                    raise ValueError("DSH gate is closed")
                current = self._config.current_validated(session)
                if current is None:
                    raise ValueError("DSH configuration is unavailable")
                gates = current.snapshot.gates
                required = (
                    "harness_enabled",
                    "dispatcher_enabled",
                    "agent_steps_enabled",
                    "agent_runtime_enabled",
                )
                if any(gates.get(name) is not True for name in required):
                    raise ValueError("DSH gate is closed")
                if not HarnessDispatcher._route_allows_claim(  # noqa: SLF001
                    current.snapshot,
                    workflow_key=context.workflow_key,
                    workflow_version=context.workflow_version,
                    step_kind="agent",
                    runtime_kind="dsh",
                ):
                    raise ValueError("DSH route is not active")
                self._authenticate_frozen_context(session, context, now_us=now)
                # This conditional write takes the same SQLite write fence as
                # an operator config apply.  A gate cut committed after the
                # read therefore loses before any launch row can be written.
                self._config.fence_current(session, revision_id=current.revision_id)
                result = self._attempts.reserve_runtime_launch(
                    session,
                    **identity,
                    now_us=now,
                    launch=runtime_launch,
                )
                if result is None:
                    raise ValueError("Attempt lease is no longer valid")
        except DshPersistenceError:
            raise
        except Exception:
            raise self._fixed_failure() from None

    def attach_pid(self, context: AttemptContext, pid: int) -> None:
        """Attach the spawned PID under the same running Attempt CAS."""
        try:
            _scope, identity = self._context_identity(context)
            now = self._now()
            with self._transaction() as session:
                result = self._attempts.attach_child_process(
                    session,
                    **identity,
                    now_us=now,
                    child_pid=pid,
                )
                if not result:
                    raise ValueError("Attempt lease is no longer valid")
        except DshPersistenceError:
            raise
        except Exception:
            raise self._fixed_failure() from None

    def observe_delivery(self, context: AttemptContext, state: DeliveryState) -> bool | None:
        """Record one monotonic delivery observation.

        A false CAS result is returned unchanged: the transport treats it as
        an observer failure without needing an exception carrying DB details.
        Exceptions are converted to the fixed persistence error.
        """
        try:
            _scope, identity = self._context_identity(context)
            if not isinstance(state, DeliveryState):
                raise ValueError("invalid delivery state")
            now = self._now()
            with self._transaction() as session:
                return self._attempts.transition_delivery(
                    session,
                    **identity,
                    now_us=now,
                    delivery_state=state.value,
                )
        except DshPersistenceError:
            raise
        except Exception:
            raise self._fixed_failure() from None


# Explicit aliases keep the adapter discoverable for callers using either
# the protocol-oriented or database-oriented name.
DbDshPersistence = DshPersistenceAdapter
DatabaseDshPersistence = DshPersistenceAdapter
DshDatabasePersistence = DshPersistenceAdapter
SqlAlchemyDshPersistence = DshPersistenceAdapter
