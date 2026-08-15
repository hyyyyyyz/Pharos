"""The configuration revision contract: one authority for gates and routing.

H0 freezes the snapshot/route/validation contract and the bootstrap rules;
H1 adds the tables and the operator-only apply service that persist it. The
invariant both share: activation, writer mode and gates are one immutable
snapshot with one canonical hash, activated by a single head CAS. Environment
variables are bootstrap defaults for brand-new databases only, and the one
runtime exception is the deny-only emergency stop.
"""

from __future__ import annotations

import os

from pharos.harness.contracts import (
    ActivationState,
    ExecutionMode,
    StrictModel,
)
from pharos.harness.definitions import canonical_json, sha256_hex
from pharos.harness.registry import Registry

#: The three business workflows this release knows about; they stay
#: disabled+legacy until their own phases (H2-H4) pass their gates.
BUSINESS_WORKFLOW_KEYS = (
    "literature.discovery",
    "daily.ingest",
    "daily.issue",
    "project.research_cycle",
)

#: Internal workflows with no legacy domain writer: their route may carry a
#: NULL execution_mode (only these).
CANARY_WORKFLOW_KEY = "harness.canary"

#: The gates. Every one is a plain boolean in the snapshot; the dependency
#: matrix below decides which combinations are legal.
GATE_NAMES = (
    "harness_enabled",
    "dispatcher_enabled",
    "canary_enabled",
    "agent_steps_enabled",
    "domain_publish_enabled",
    "fulltext_enabled",
    "desktop_bridge_enabled",
    "experiments_enabled",
)

#: Compatible env names, consulted ONLY when no config head exists yet.
_ENV_DEFAULTS = {
    "harness_enabled": "PHAROS_HARNESS_ENABLED",
    "dispatcher_enabled": "PHAROS_HARNESS_DISPATCHER_ENABLED",
    "canary_enabled": "PHAROS_HARNESS_CANARY_ENABLED",
    "agent_steps_enabled": "PHAROS_HARNESS_AGENT_STEPS_ENABLED",
    "domain_publish_enabled": "PHAROS_HARNESS_DOMAIN_PUBLISH_ENABLED",
    "fulltext_enabled": "PHAROS_HARNESS_FULLTEXT_ENABLED",
    "desktop_bridge_enabled": "PHAROS_HARNESS_DESKTOP_BRIDGE_ENABLED",
    "experiments_enabled": "PHAROS_HARNESS_EXPERIMENTS_ENABLED",
}

_ROUTE_ENV_DEFAULTS = {
    "literature.discovery": "PHAROS_DISCOVERY_EXECUTION",
    "daily.ingest": "PHAROS_DAILY_EXECUTION",
    "daily.issue": "PHAROS_DAILY_EXECUTION",
    "project.research_cycle": "PHAROS_PROJECT_RESEARCH_EXECUTION",
}

EMERGENCY_STOP_ENV = "PHAROS_HARNESS_EMERGENCY_STOP"


def emergency_stop_active() -> bool:
    """Deny-only runtime override. Never enables anything, never edits the DB."""
    raw = os.getenv(EMERGENCY_STOP_ENV)
    return raw is not None and raw.strip().lower() not in ("", "0", "false", "no", "off")


class WorkflowRoute(StrictModel):
    workflow_key: str
    active_version: int | None = None
    activation_state: ActivationState = ActivationState.disabled
    execution_mode: ExecutionMode | None = None

    def canonical(self) -> dict:
        data = {
            "workflow_key": self.workflow_key,
            "active_version": self.active_version,
            "activation_state": self.activation_state.value,
            "execution_mode": self.execution_mode.value if self.execution_mode else None,
        }
        return data


class HarnessConfigSnapshot(StrictModel):
    """One complete, immutable configuration snapshot.

    ``routes`` covers every workflow this build knows; anything absent is
    deterministically ``disabled + legacy`` and never inferred per-request.
    """

    gates: dict[str, bool]
    routes: tuple[WorkflowRoute, ...]
    parent_revision_id: str | None = None
    actor: str = ""
    reason: str = ""

    def canonical(self) -> dict:
        return {
            "gates": {name: bool(self.gates.get(name, False)) for name in GATE_NAMES},
            "routes": [route.canonical() for route in self.routes],
            "parent_revision_id": self.parent_revision_id,
            "actor": self.actor,
            "reason": self.reason,
        }

    def snapshot_hash(self) -> str:
        return sha256_hex(self.canonical())


def bootstrap_snapshot(
    registry: Registry | None = None,
    *,
    actor: str = "bootstrap",
    reason: str = "default safe snapshot",
) -> HarnessConfigSnapshot:
    """The safe default: Harness fully off, every business route legacy.

    Only consulted when a brand-new database has no configuration head; it can
    never override an existing one. Env values only narrow the defaults from
    "off" -- a bootstrap snapshot can never smuggle an enabled gate past the
    validator.
    """
    gates = {name: False for name in GATE_NAMES}
    for name, env in _ENV_DEFAULTS.items():
        raw = os.getenv(env)
        if raw is not None and raw.strip().lower() in ("1", "true", "yes", "on"):
            gates[name] = True

    routes: list[WorkflowRoute] = []
    mode_overrides: dict[str, str] = {}
    for key, env in _ROUTE_ENV_DEFAULTS.items():
        raw = os.getenv(env)
        if raw:
            mode_overrides[key] = raw.strip().lower()
    for key in sorted(set(BUSINESS_WORKFLOW_KEYS) | {CANARY_WORKFLOW_KEY}):
        mode = mode_overrides.get(key)
        if mode in ("legacy", "shadow", "harness"):
            routes.append(
                WorkflowRoute(
                    workflow_key=key,
                    activation_state=(
                        ActivationState.active if mode != "legacy" else ActivationState.disabled
                    ),
                    execution_mode=ExecutionMode(mode),
                )
            )
        elif key == CANARY_WORKFLOW_KEY:
            # Internal canary: no legacy writer, so NULL execution_mode is
            # legal for it -- but it stays disabled until an operator revision.
            routes.append(
                WorkflowRoute(workflow_key=key, activation_state=ActivationState.disabled)
            )
        else:
            routes.append(WorkflowRoute(workflow_key=key, execution_mode=ExecutionMode.legacy))

    if registry is not None:
        for known in registry.all_workflows():
            if not any(route.workflow_key == known.workflow_key for route in routes):
                routes.append(
                    WorkflowRoute(
                        workflow_key=known.workflow_key, execution_mode=ExecutionMode.legacy
                    )
                )
    return HarnessConfigSnapshot(gates=gates, routes=tuple(routes), actor=actor, reason=reason)


def validate_snapshot(snapshot: HarnessConfigSnapshot, registry: Registry) -> list[str]:
    """Return every validation error; an empty list means the snapshot is legal.

    The dependency matrix: harness_enabled is the master gate; dispatcher,
    canary, agent, publish and full-text all require it; experiments is
    permanently denied while Decision 9 stands (no combination may enable it).
    """
    errors: list[str] = []
    gates = snapshot.gates
    enabled = gates.get("harness_enabled", False)
    if not enabled:
        dependents = [name for name in GATE_NAMES if name != "harness_enabled" and gates.get(name)]
        if dependents:
            errors.append("harness_enabled=0 conflicts with " + ", ".join(sorted(dependents)))
        for route in snapshot.routes:
            if route.execution_mode in (ExecutionMode.shadow, ExecutionMode.harness):
                errors.append(
                    f"route {route.workflow_key} mode {route.execution_mode.value} "
                    "requires harness_enabled=1"
                )
            if route.activation_state in (ActivationState.active, ActivationState.deprecated):
                errors.append(
                    f"route {route.workflow_key} is {route.activation_state.value} "
                    "while harness_enabled=0"
                )
    else:
        if not gates.get("dispatcher_enabled"):
            for route in snapshot.routes:
                if route.execution_mode in (ExecutionMode.shadow, ExecutionMode.harness):
                    errors.append(
                        f"route {route.workflow_key} mode {route.execution_mode.value} "
                        "requires dispatcher_enabled=1"
                    )
        if gates.get("experiments_enabled"):
            errors.append(
                "experiments_enabled=1 is denied while Decision 9 stands; "
                "no snapshot may enable experiment execution"
            )

    # Per-route validation against the registry.
    known_keys = {workflow.workflow_key for workflow in registry.all_workflows()}
    seen: set[str] = set()
    for route in snapshot.routes:
        if route.workflow_key in seen:
            errors.append(f"duplicate route for {route.workflow_key}")
        seen.add(route.workflow_key)
        if route.workflow_key not in known_keys:
            errors.append(f"route references unknown workflow {route.workflow_key}")
            continue
        # Key-level constraints (e.g. NULL execution_mode) come from any
        # registered version of the workflow.
        known = next(w for w in registry.all_workflows() if w.workflow_key == route.workflow_key)
        if route.execution_mode in (ExecutionMode.shadow, ExecutionMode.harness):
            if route.active_version is None:
                errors.append(
                    f"route {route.workflow_key} mode {route.execution_mode.value} "
                    "needs an active_version"
                )
            elif registry.workflow(f"{route.workflow_key}@{route.active_version}") is None:
                errors.append(
                    f"route {route.workflow_key} names unknown version " f"{route.active_version}"
                )
        if route.execution_mode == ExecutionMode.harness and not gates.get(
            "domain_publish_enabled"
        ):
            errors.append(
                f"route {route.workflow_key} mode harness requires domain_publish_enabled=1"
            )
        if route.execution_mode is None and not known.internal_no_legacy_writer:
            errors.append(
                f"route {route.workflow_key} may not use NULL execution_mode: "
                "it has a legacy domain writer"
            )
        if route.activation_state in (ActivationState.active, ActivationState.deprecated) and not route.active_version:
            errors.append(
                f"route {route.workflow_key} is {route.activation_state.value} "
                "but names no active_version"
            )

    # Agent/publish/full-text gates must be accompanied by their enablers.
    if gates.get("agent_steps_enabled") and not (enabled and gates.get("dispatcher_enabled")):
        errors.append("agent_steps_enabled requires harness_enabled and dispatcher_enabled")
    if gates.get("canary_enabled") and not (enabled and gates.get("dispatcher_enabled")):
        errors.append("canary_enabled requires harness_enabled and dispatcher_enabled")
    return errors


def config_hash_stable(snapshot: HarnessConfigSnapshot) -> str:
    """The canonical hash, identical across processes and restarts."""
    return sha256_hex(canonical_json(snapshot.canonical()))
