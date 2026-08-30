"""Assembly point: registry, config authority, dispatcher, runner, gates.

One object owns the whole H1 kernel lifecycle. The FastAPI lifespan creates
it; tests create isolated instances over temporary databases. Everything
here is synchronous except the background loop, which the lifespan wraps in
an asyncio task and cancels on shutdown.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from pharos.db.session import session_scope
from pharos.harness.approvals import ApprovalRepository
from pharos.harness.artifacts import ArtifactStore
from pharos.harness.configrev import (
    GATE_NAMES,
    HarnessConfigSnapshot,
    bootstrap_snapshot,
    emergency_stop_active,
)
from pharos.harness.contracts import (
    ApprovalState,
    ConfigIntegrityError,
    ExecutionMode,
    RunState,
    StateError,
    StepState,
    UnavailableError,
)
from pharos.harness.dispatcher import HarnessDispatcher
from pharos.harness.events import EventStore
from pharos.harness.fakes import FakeClock, FakeModel
from pharos.harness.model_gateway import FakeModelGateway
from pharos.harness.registry import Registry
from pharos.harness.repository import (
    HarnessConfigService,
    HarnessDefinitionRepository,
    HarnessRunRepository,
    Scope,
    now_iso,
)
from pharos.harness.runner import AgentOutputContract, HarnessRunner, StepExecutor
from pharos.harness.state import HarnessStateService
from pharos.harness.tables import steps
from pharos.harness.usage import UsageLedger
from pharos.harness.workflows.canary import (
    CANARY_KEY,
    CANARY_V1_IDENTITY,
    CANARY_V2_IDENTITY,
    canary_capabilities,
    canary_dsh_workflow,
    canary_model_profiles,
    canary_roles,
    canary_workflow,
    expand,
    expand_dsh,
    reduce,
    resolve_canary_model_profile,
    validate_canary_actor_output,
)

log = logging.getLogger(__name__)

CANARY_EXECUTION_WORKFLOWS = frozenset({CANARY_KEY})

#: The background loop's idle poll interval (seconds). The DB is the truth;
#: this only bounds wake-up latency, never correctness.
POLL_SECONDS = 0.5


class HarnessApp:
    """The kernel: definitions, config, dispatcher, runner, gates."""

    def __init__(
        self,
        *,
        clock: FakeClock | None = None,
        fake_model: FakeModel | None = None,
        dispatcher: HarnessDispatcher | None = None,
    ) -> None:
        self.clock = clock or FakeClock()
        self.registry = Registry()
        for capability in canary_capabilities():
            self.registry.register_capability(capability)
        roles = canary_roles()
        for role in roles:
            self.registry.register_role(role)
        for profile in canary_model_profiles():
            self.registry.register_model_profile(profile)
        self.registry.register(canary_workflow())
        self.registry.register(canary_dsh_workflow())
        self.registry.compile()
        self.definition_repository = HarnessDefinitionRepository()
        agent_model_routes: dict[str, tuple[str, str]] = {}
        for role in roles:
            if role.identity() == "canary_actor@1" and role.model_profile == "canary":
                agent_model_routes[role.identity()] = resolve_canary_model_profile("canary")
                continue
            route = self.registry.require_model_profile(role.model_profile).resolve_route(
                role.runtime_kind
            )
            agent_model_routes[role.identity()] = (route.provider, route.model)
        self.config_service = HarnessConfigService(self.registry)
        self.state = HarnessStateService()
        self.events = EventStore()
        self.usage = UsageLedger()
        self.artifacts = ArtifactStore()
        self.approvals = ApprovalRepository()
        self.gateway = FakeModelGateway(fake_model or FakeModel(clock=self.clock))
        from pharos.harness.workflows.canary import build_executors

        self.executor = StepExecutor(
            gateway=self.gateway,
            artifacts=self.artifacts,
            agent_output_contracts={
                role.identity(): AgentOutputContract(
                    schema_name=role.output_schema.rsplit("@", 1)[0],
                    schema_version=int(role.output_schema.rsplit("@", 1)[1]),
                    prompt_version=role.prompt_template_version,
                    provider=agent_model_routes[role.identity()][0],
                    model=agent_model_routes[role.identity()][1],
                    validator=validate_canary_actor_output,
                )
                for role in roles
            },
            capabilities=build_executors(),
            state=self.state,
            usage=self.usage,
            events=self.events,
            clock=self.clock,
            # Execution hooks are version identities. A route selecting v2
            # must never silently run the v1 expander/reducer pair.
            expanders={CANARY_V1_IDENTITY: expand, CANARY_V2_IDENTITY: expand_dsh},
            run_reducers={CANARY_V1_IDENTITY: reduce, CANARY_V2_IDENTITY: reduce},
        )
        self.dispatcher = dispatcher or HarnessDispatcher(
            state_service=self.state, config_service=self.config_service
        )
        self.runner = HarnessRunner(self.dispatcher, self.executor)
        self._loop_task: asyncio.Task | None = None
        self._stopping = False

    # ------------------------------------------------------------- lifecycle

    def ensure_bootstrapped(self) -> HarnessConfigSnapshot:
        """Store definitions and apply the safe default when no head exists."""
        with session_scope() as session:
            self._store_definitions(session)
            current = self.config_service.current_validated(session)
            if current is not None:
                return current.snapshot
            snapshot = bootstrap_snapshot(self.registry)
            self.config_service.apply(
                session,
                snapshot=snapshot,
                expected_head_revision=None,
                actor="bootstrap",
                reason="default safe snapshot",
                now=now_iso(),
            )
            return snapshot

    def _store_definitions(self, session) -> None:  # noqa: ANN001
        created_at = now_iso()
        for workflow in self.registry.all_workflows():
            self.definition_repository.persist_workflow_binding(
                session,
                registry=self.registry,
                workflow=workflow,
                now=created_at,
            )

    def current_snapshot(self) -> HarnessConfigSnapshot:
        with session_scope() as session:
            snapshot = self.config_service.current_snapshot(session)
            if snapshot is None:
                raise ConfigIntegrityError("config head is not bootstrapped")
            return snapshot

    def route_for(self, workflow_key: str):
        with session_scope() as session:
            current = self.config_service.current_validated(session)
            if current is None:
                raise ConfigIntegrityError("config head is not bootstrapped")
            return next(
                (route for route in current.snapshot.routes if route.workflow_key == workflow_key),
                None,
            )

    def start_loop(self) -> asyncio.Task:
        if self._loop_task is not None and not self._loop_task.done():
            return self._loop_task
        self._stopping = False
        self._loop_task = asyncio.create_task(self._loop())
        return self._loop_task

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                await asyncio.to_thread(self.cycle)
            except Exception:  # noqa: BLE001 -- the loop must survive bad cycles
                log.exception("harness cycle failed")
            await asyncio.sleep(POLL_SECONDS)

    async def stop_loop(self) -> None:
        self._stopping = True
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
            self._loop_task = None

    def cycle(self) -> None:
        """One synchronous pass: reaper, control, approvals, claim, reduce."""
        now = self.clock.utc_epoch_us()
        with session_scope() as session:
            self.dispatcher.reap_expired(session, now_us=now)
            self.dispatcher.activate_retries(session, now_us=now)
            self.approvals.expire_outstanding(session, now_us=now)
            self._resolve_expired_approval_steps(session, now_us=now)
        self.runner.apply_pending_control(now_us=now)
        self.runner.tick(now_us=now)
        self.runner.reduce_all(now_us=now)

    def _resolve_expired_approval_steps(self, session, *, now_us: int) -> int:
        """A step whose approval expired resolves through its reject branch."""
        from sqlalchemy import select

        from pharos.harness.tables import approvals as approvals_table

        rows = (
            session.execute(
                select(steps.c.id, steps.c.definition_json)
                .join(
                    approvals_table,
                    (approvals_table.c.step_id == steps.c.id)
                    & (approvals_table.c.state == ApprovalState.expired.value),
                )
                .where(steps.c.state == StepState.waiting_for_approval.value)
            )
            .mappings()
            .all()
        )
        resolved = 0
        for row in rows:
            definition = json.loads(row["definition_json"])
            if definition.get("approval_on_reject", "fail") == "skip":
                self.state.transition_step(
                    session,
                    step_id=row["id"],
                    target=StepState.skipped,
                    now_us=now_us,
                    skip_reason="approval_expired",
                )
            else:
                self.state.transition_step(
                    session,
                    step_id=row["id"],
                    target=StepState.failed,
                    now_us=now_us,
                    error_code="approval_expired",
                )
            resolved += 1
        return resolved

    # ----------------------------------------------------------------- gates

    def gate_status(self) -> dict:
        with session_scope() as session:
            current = self.config_service.current_validated(session)
        if current is None:
            return {
                **{name: False for name in GATE_NAMES},
                "emergency_stop": emergency_stop_active(),
                "head_revision_id": None,
                "head_hash": None,
            }
        snapshot = current.snapshot
        return {
            **{name: snapshot.gates[name] for name in GATE_NAMES},
            "emergency_stop": emergency_stop_active(),
            "head_revision_id": current.revision_id,
            "head_hash": current.snapshot_sha256,
        }

    def _head_revision_id(self) -> str | None:
        with session_scope() as session:
            current = self.config_service.current_validated(session)
            return current.revision_id if current else None

    def _head_hash(self) -> str | None:
        with session_scope() as session:
            current = self.config_service.current_validated(session)
            return current.snapshot_sha256 if current else None

    def require_start_allowed(self, workflow_key: str) -> None:
        if emergency_stop_active():
            raise UnavailableError("harness is in emergency stop")
        with session_scope() as session:
            current = self.config_service.current_validated(session)
            if current is None:
                raise ConfigIntegrityError("config head is not bootstrapped")
            self._check_start_allowed(current.snapshot, workflow_key)

    @staticmethod
    def _check_start_allowed(snapshot: HarnessConfigSnapshot, workflow_key: str) -> None:
        if not snapshot.gates.get("harness_enabled"):
            raise UnavailableError("harness is disabled")
        if not snapshot.gates.get("dispatcher_enabled"):
            raise UnavailableError("harness dispatcher is disabled; runs would never execute")
        route = next((item for item in snapshot.routes if item.workflow_key == workflow_key), None)
        if route is None or route.activation_state.value != "active":
            raise UnavailableError(f"workflow {workflow_key} is not active")
        if workflow_key in CANARY_EXECUTION_WORKFLOWS and not snapshot.gates.get("canary_enabled"):
            raise UnavailableError("canary workflow is disabled")
        if route.execution_mode is not None and route.execution_mode != ExecutionMode.harness:
            raise UnavailableError(
                f"workflow {workflow_key} is in {route.execution_mode.value} mode"
            )

    # ---------------------------------------------------------------- runs

    def create_run(
        self,
        *,
        scope: Scope,
        workflow_key: str,
        input: dict,
        idempotency_key: str,
        initiator: str,
        project_id: str | None = None,
    ) -> dict:
        """Create (or replay) a run under the current config head."""
        now = self.clock.utc_epoch_us()
        with session_scope() as session:
            # Keep validation, route/version selection, gate checks, the
            # conditional head write fence, and all run/step writes in this
            # one transaction.  Splitting these reads across sessions lets a
            # concurrent operator cut over between authorization and insert.
            current = self.config_service.current_validated(session)
            if current is None:
                raise ConfigIntegrityError("config head is not bootstrapped")
            snapshot = current.snapshot
            if emergency_stop_active():
                raise UnavailableError("harness is in emergency stop")
            self._check_start_allowed(snapshot, workflow_key)
            route = next(
                (item for item in snapshot.routes if item.workflow_key == workflow_key), None
            )
            if route is None or route.active_version is None:
                # _check_start_allowed handles the normal route denial; this
                # keeps the version lookup fail-closed if the contract grows.
                raise UnavailableError(f"workflow {workflow_key} is not active")
            workflow = self.registry.require_workflow(f"{workflow_key}@{route.active_version}")
            self.config_service.fence_current(session, revision_id=current.revision_id)
            run = HarnessRunRepository().create(
                session,
                scope=scope,
                workflow=workflow,
                config_revision_id=current.revision_id,
                input=input,
                idempotency_key=idempotency_key,
                initiator=initiator,
                now_us=now,
                project_id=project_id,
            )
            if run["state"] == "queued":
                created_steps = self.runner.activate_run(session, scope=scope, run=run, now_us=now)
                for step in created_steps:
                    if step["state"] != "pending":
                        continue
                    # Only dependency-free roots become ready at activation;
                    # the reduction pass promotes dependents as their
                    # dependencies reach terminal states.
                    if json.loads(step["depends_on_json"] or "[]"):
                        continue
                    self.state.transition_step(
                        session,
                        step_id=step["id"],
                        target=StepState.ready,
                        now_us=now,
                        ready_at=now,
                    )
                created = HarnessRunRepository().require(session, scope=scope, run_id=run["id"])
                return dict(created)
            return dict(run)

    def get_run(self, *, scope: Scope, run_id: str) -> dict:
        with session_scope() as session:
            return HarnessRunRepository().require(session, scope=scope, run_id=run_id)

    def list_runs(self, *, scope: Scope, limit: int, after: int | None = None) -> list[dict]:
        with session_scope() as session:
            return HarnessRunRepository().list(session, scope=scope, limit=limit, after_seq=after)

    def pause(self, *, scope: Scope, run_id: str) -> dict:
        with session_scope() as session:
            HarnessRunRepository().require(session, scope=scope, run_id=run_id)
            self.state.request_pause(session, run_id=run_id, now_us=self.clock.utc_epoch_us())
            run = HarnessRunRepository().require(session, scope=scope, run_id=run_id)
            return dict(run)

    def cancel(self, *, scope: Scope, run_id: str) -> dict:
        with session_scope() as session:
            HarnessRunRepository().require(session, scope=scope, run_id=run_id)
            self.state.request_cancel(session, run_id=run_id, now_us=self.clock.utc_epoch_us())
            run = HarnessRunRepository().require(session, scope=scope, run_id=run_id)
            return dict(run)

    def resume(self, *, scope: Scope, run_id: str) -> dict:
        """Clear the pause request and put a paused run back in the queue."""
        from sqlalchemy import update

        from pharos.harness.tables import runs as runs_table

        with session_scope() as session:
            run = HarnessRunRepository().require(session, scope=scope, run_id=run_id)
            if run["state"] != RunState.paused.value:
                raise StateError("run is not paused")
            session.execute(
                update(runs_table)
                .where(scope.where(runs_table), runs_table.c.id == run_id)
                .values(pause_requested_at=None)
            )
            self.state.transition_run(
                session,
                run_id=run_id,
                target=RunState.queued,
                now_us=self.clock.utc_epoch_us(),
            )
            run = HarnessRunRepository().require(session, scope=scope, run_id=run_id)
            return dict(run)

    def steps_for(self, *, scope: Scope, run_id: str) -> list[dict]:
        with session_scope() as session:
            HarnessRunRepository().require(session, scope=scope, run_id=run_id)
            return self.runner.step_repository.for_run(session, scope=scope, run_id=run_id)

    # ------------------------------------------------------------- approvals

    def decide_approval(
        self,
        *,
        scope: Scope,
        approval_id: str,
        decision: ApprovalState,
        resolver_user_id: str,
        reason: str,
    ) -> dict:
        now = self.clock.utc_epoch_us()
        with session_scope() as session:
            approval = self.approvals.decide(
                session,
                scope=scope,
                approval_id=approval_id,
                decision=decision,
                resolver_user_id=resolver_user_id,
                reason=reason,
                now_us=now,
            )
            step_id = approval["step_id"]
            if step_id is None:
                return dict(approval)
            step = session.execute(steps.select().where(steps.c.id == step_id)).mappings().first()
            if step is not None and step["state"] == StepState.waiting_for_approval.value:
                definition = json.loads(step["definition_json"])
                if decision == ApprovalState.approved:
                    self.state.transition_step(
                        session,
                        step_id=step_id,
                        target=StepState.ready,
                        now_us=now,
                        lease_owner=None,
                        lease_expires_at=None,
                    )
                else:
                    on_reject = definition.get("approval_on_reject", "fail")
                    if on_reject == "skip":
                        self.state.transition_step(
                            session,
                            step_id=step_id,
                            target=StepState.skipped,
                            now_us=now,
                            skip_reason=f"approval_{decision.value}",
                        )
                    else:
                        self.state.transition_step(
                            session,
                            step_id=step_id,
                            target=StepState.failed,
                            now_us=now,
                            error_code=f"approval_{decision.value}",
                        )
            return dict(approval)

    def pending_approvals(self, *, scope: Scope, run_id: str) -> list[dict]:
        with session_scope() as session:
            HarnessRunRepository().require(session, scope=scope, run_id=run_id)
            return self.approvals.pending_for_run(session, scope=scope, run_id=run_id)

    # ---------------------------------------------------------------- events

    def replay_events(self, *, scope: Scope, run_id: str, after_seq: int, limit: int):
        with session_scope() as session:
            self.events.require_run(session, scope=scope, run_id=run_id)
            return self.events.replay(
                session, scope=scope, run_id=run_id, after_seq=after_seq, limit=limit
            )

    # ------------------------------------------------------------ artifacts

    def artifacts_for(self, *, scope: Scope, run_id: str) -> list[dict]:
        with session_scope() as session:
            HarnessRunRepository().require(session, scope=scope, run_id=run_id)
            return self.artifacts.for_run(session, scope=scope, run_id=run_id)
