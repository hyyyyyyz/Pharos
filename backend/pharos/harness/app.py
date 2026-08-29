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
    HarnessConfigSnapshot,
    bootstrap_snapshot,
    emergency_stop_active,
)
from pharos.harness.contracts import (
    ApprovalState,
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
from pharos.harness.repository import HarnessConfigService, HarnessRunRepository, Scope, now_iso
from pharos.harness.runner import HarnessRunner, StepExecutor
from pharos.harness.state import HarnessStateService
from pharos.harness.tables import steps
from pharos.harness.usage import UsageLedger
from pharos.harness.workflows.canary import (
    CANARY_KEY,
    canary_capabilities,
    canary_roles,
    canary_workflow,
    expand,
    reduce,
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
        for role in canary_roles():
            self.registry.register_role(role)
        self.registry.register(canary_workflow())
        self.registry.compile()
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
            capabilities=build_executors(),
            state=self.state,
            usage=self.usage,
            events=self.events,
            clock=self.clock,
            expanders={CANARY_KEY: expand},
            run_reducers={CANARY_KEY: reduce},
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
            head = self.config_service.current(session)
            if head is not None:
                snapshot = self.config_service.current_snapshot(session)
                assert snapshot is not None
                return snapshot
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
        from pharos.harness.repository import HarnessWorkflowStore, now_iso

        store = HarnessWorkflowStore()
        for workflow in self.registry.all_workflows():
            store.upsert(session, workflow, now_iso())

    def current_snapshot(self) -> HarnessConfigSnapshot:
        with session_scope() as session:
            snapshot = self.config_service.current_snapshot(session)
            assert snapshot is not None
            return snapshot

    def route_for(self, workflow_key: str):
        snapshot = self.current_snapshot()
        for route in snapshot.routes:
            if route.workflow_key == workflow_key:
                return route
        return None

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
        snapshot = self.current_snapshot()
        return {
            "harness_enabled": snapshot.gates.get("harness_enabled", False),
            "dispatcher_enabled": snapshot.gates.get("dispatcher_enabled", False),
            "canary_enabled": snapshot.gates.get("canary_enabled", False),
            "agent_steps_enabled": snapshot.gates.get("agent_steps_enabled", False),
            "domain_publish_enabled": snapshot.gates.get("domain_publish_enabled", False),
            "emergency_stop": emergency_stop_active(),
            "head_revision_id": self._head_revision_id(),
            "head_hash": self._head_hash(),
        }

    def _head_revision_id(self) -> str | None:
        with session_scope() as session:
            head = self.config_service.current(session)
            return head["current_revision_id"] if head else None

    def _head_hash(self) -> str | None:
        with session_scope() as session:
            snapshot = self.config_service.current_snapshot(session)
            return snapshot.snapshot_hash() if snapshot else None

    def require_start_allowed(self, workflow_key: str) -> None:
        if emergency_stop_active():
            raise UnavailableError("harness is in emergency stop")
        snapshot = self.current_snapshot()
        if not snapshot.gates.get("harness_enabled"):
            raise UnavailableError("harness is disabled")
        if not snapshot.gates.get("dispatcher_enabled"):
            raise UnavailableError("harness dispatcher is disabled; runs would never execute")
        route = self.route_for(workflow_key)
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
        self.require_start_allowed(workflow_key)
        route = self.route_for(workflow_key)
        assert route is not None and route.active_version is not None
        workflow = self.registry.require_workflow(f"{workflow_key}@{route.active_version}")
        now = self.clock.utc_epoch_us()
        with session_scope() as session:
            revision_id = self._head_revision_id()
            assert revision_id is not None
            run = HarnessRunRepository().create(
                session,
                scope=scope,
                workflow=workflow,
                config_revision_id=revision_id,
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
