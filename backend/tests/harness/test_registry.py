"""Harness contract, registry and fake-double tests: the H0 code gate.

Everything here runs offline -- no network, no real model, no API key, no
Zotero. The gates they pin:

- registry hash stability across processes/restarts;
- illegal workflows rejected (cycle, duplicate key, missing dependency,
  unbounded fan-out, missing timeout/attempt/budget, unversioned tool,
  role tools outside the workflow allowlist, non-idempotent publish,
  retryable non-idempotent side effect, approval without reject branch);
- strict schemas reject unknown fields and oversized content;
- fakes are deterministic and offline;
- the config snapshot contract: dependency matrix, bootstrap defaults, the
  permanent experiments deny gate, stable canonical hashes.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from pharos.harness import fakes
from pharos.harness.configrev import (
    CANARY_WORKFLOW_KEY,
    EMERGENCY_STOP_ENV,
    HarnessConfigSnapshot,
    WorkflowRoute,
    bootstrap_snapshot,
    config_hash_stable,
    decode_snapshot_payload,
    emergency_stop_active,
    validate_snapshot,
)
from pharos.harness.contracts import (
    ActivationState,
    AttemptErrorClass,
    DefinitionError,
    ExecutionMode,
    GatewayError,
    RetryClass,
)
from pharos.harness.definitions import (
    BudgetSpec,
    CapabilityDefinition,
    RetryPolicy,
    RoleDefinition,
    StepDefinition,
    WorkflowDefinition,
)
from pharos.harness.registry import Registry
from pydantic import ValidationError

# --------------------------------------------------------------------------
# A small legitimate definition set used across these tests.


def _capability(key: str = "canary.noop", **overrides) -> CapabilityDefinition:
    values = {
        "capability_key": key,
        "version": 1,
        "action_schema": "canary.action@1",
        "observation_schema": "canary.observation@1",
    }
    values.update(overrides)
    return CapabilityDefinition(**values)


def _workflow(**overrides) -> WorkflowDefinition:
    values = {
        "workflow_key": "harness.canary",
        "version": 1,
        "input_schema": "canary.input@1",
        "output_schema": "canary.output@1",
        "internal_no_legacy_writer": True,
        "allowed_capabilities": ("canary.noop@1",),
        "steps": (StepDefinition(key="start", kind="deterministic", capability="canary.noop@1"),),
    }
    values.update(overrides)
    return WorkflowDefinition(**values)


def _business_workflow(key: str) -> WorkflowDefinition:
    """A placeholder business workflow so config-snapshot routes can resolve."""
    return WorkflowDefinition(
        workflow_key=key,
        version=1,
        input_schema=f"{key}.input@1",
        output_schema=f"{key}.output@1",
        internal_no_legacy_writer=False,
        steps=(StepDefinition(key="start", kind="deterministic", capability="canary.noop@1"),),
    )


def _registry(*, with_canary: bool = True) -> Registry:
    registry = Registry()
    registry.register_capability(_capability())
    if with_canary:
        registry.register(_workflow())
    return registry


def _full_registry() -> Registry:
    """A registry that knows the four business workflows plus the canary."""
    registry = Registry()
    registry.register_capability(_capability())
    registry.register(_workflow())
    for key in (
        "literature.discovery",
        "daily.ingest",
        "daily.issue",
        "project.research_cycle",
    ):
        registry.register(_business_workflow(key))
    return registry


# --------------------------------------------------------------------------
# Registry


def test_registry_hash_is_stable_across_processes() -> None:
    registry = _registry()
    registry.compile()
    expected = registry.snapshot().canonical_hash()

    script = (
        "import sys; sys.path.insert(0, 'backend')\n"
        "from tests.harness.test_registry import _registry\n"
        "r = _registry(); r.compile(); print(r.snapshot().canonical_hash())"
    )
    # A fresh interpreter run proves the hash is not an artifact of this
    # process's object identity, import order or memory addresses.
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    # The child may not resolve imports identically; run it from backend/.
    if result.returncode != 0:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
            cwd="backend",
        )
    assert result.stdout.strip() == expected


def test_definition_hash_is_content_not_identity() -> None:
    first = _workflow()
    second = _workflow()
    assert first.definition_hash() == second.definition_hash()
    changed = _workflow(max_parallel_steps=8)
    assert first.definition_hash() != changed.definition_hash()


def test_duplicate_key_version_with_different_content_fails() -> None:
    registry = Registry()
    registry.register(_workflow())
    with pytest.raises(DefinitionError, match="already registered"):
        registry.register(
            _workflow(
                steps=(
                    StepDefinition(key="other", kind="deterministic", capability="canary.noop@1"),
                )
            )
        )


def test_cycle_rejected() -> None:
    registry = _registry(with_canary=False)
    registry.register(
        _workflow(
            steps=(
                StepDefinition(
                    key="a", kind="deterministic", capability="canary.noop@1", depends_on=("b",)
                ),
                StepDefinition(
                    key="b", kind="deterministic", capability="canary.noop@1", depends_on=("a",)
                ),
            )
        )
    )
    with pytest.raises(DefinitionError, match="cycle"):
        registry.compile()


def test_duplicate_step_key_rejected() -> None:
    registry = _registry(with_canary=False)
    registry.register(
        _workflow(
            steps=(
                StepDefinition(key="a", kind="deterministic", capability="canary.noop@1"),
                StepDefinition(key="a", kind="deterministic", capability="canary.noop@1"),
            )
        )
    )
    with pytest.raises(DefinitionError, match="duplicate step keys"):
        registry.compile()


def test_missing_dependency_rejected() -> None:
    registry = _registry(with_canary=False)
    registry.register(
        _workflow(
            steps=(
                StepDefinition(
                    key="a", kind="deterministic", capability="canary.noop@1", depends_on=("ghost",)
                ),
            )
        )
    )
    with pytest.raises(DefinitionError, match="unknown step ghost"):
        registry.compile()


def test_unbounded_fanout_rejected() -> None:
    registry = _registry(with_canary=False)
    registry.register(
        _workflow(steps=(StepDefinition(key="m", kind="mapped", capability="canary.noop@1"),))
    )
    with pytest.raises(DefinitionError, match="max_fanout"):
        registry.compile()


def test_unbounded_budget_rejected() -> None:
    registry = _registry(with_canary=False)
    registry.register(
        _workflow(
            default_budget=BudgetSpec(
                wall_seconds=0, model_calls=0, input_tokens=0, output_tokens=0
            ),
            steps=(
                StepDefinition(
                    key="a",
                    kind="deterministic",
                    capability="canary.noop@1",
                    timeout_seconds=None,
                    budget=BudgetSpec(wall_seconds=0),
                ),
            ),
        )
    )
    with pytest.raises(DefinitionError, match="no time bound"):
        registry.compile()


def test_mapped_consumer_needs_fan_in() -> None:
    registry = _registry(with_canary=False)
    registry.register(
        _workflow(
            steps=(
                StepDefinition(key="m", kind="mapped", capability="canary.noop@1", max_fanout=4),
                StepDefinition(
                    key="reduce",
                    kind="deterministic",
                    capability="canary.noop@1",
                    depends_on=("m",),
                ),
            )
        )
    )
    with pytest.raises(DefinitionError, match="fan_in"):
        registry.compile()


def test_min_success_needs_count_and_vice_versa() -> None:
    registry = _registry(with_canary=False)
    registry.register(
        _workflow(
            steps=(
                StepDefinition(key="m", kind="mapped", capability="canary.noop@1", max_fanout=4),
                StepDefinition(
                    key="reduce",
                    kind="deterministic",
                    capability="canary.noop@1",
                    depends_on=("m",),
                    fan_in="min_success",
                ),
            )
        )
    )
    with pytest.raises(DefinitionError, match="min_success_count"):
        registry.compile()

    registry = _registry(with_canary=False)
    registry.register(
        _workflow(
            steps=(
                StepDefinition(key="m", kind="mapped", capability="canary.noop@1", max_fanout=4),
                StepDefinition(
                    key="reduce",
                    kind="deterministic",
                    capability="canary.noop@1",
                    depends_on=("m",),
                    fan_in="all_success",
                    min_success_count=2,
                ),
            )
        )
    )
    with pytest.raises(DefinitionError, match="non-min_success fan-in"):
        registry.compile()


def test_role_tool_outside_workflow_allowlist_rejected() -> None:
    registry = Registry()
    registry.register_capability(_capability())
    registry.register_capability(_capability("forbidden.tool"))
    registry.register_role(
        RoleDefinition(
            role_key="reader",
            version=1,
            prompt_template_version="reader-zh@1",
            input_schema="reader.in@1",
            output_schema="reader.out@1",
            model_profile="reader",
            runtime_kind="in_process_fake",
            capability_allowlist=("forbidden.tool@1",),
        )
    )
    registry.register(
        _workflow(
            steps=(StepDefinition(key="a", kind="agent", role="reader@1"),),
        )
    )
    with pytest.raises(DefinitionError, match="never authorised"):
        registry.compile()


def test_publish_without_idempotency_rejected() -> None:
    registry = Registry()
    registry.register_capability(_capability())
    registry.register(
        _workflow(
            steps=(
                StepDefinition(
                    key="a", kind="deterministic", capability="canary.noop@1", publish=True
                ),
            ),
        )
    )
    with pytest.raises(DefinitionError, match="idempotency"):
        registry.compile()


def test_retryable_non_idempotent_side_effect_rejected() -> None:
    registry = Registry()
    registry.register_capability(_capability(retry_classes=(RetryClass.server_transient,)))
    registry.register(
        _workflow(
            steps=(
                StepDefinition(
                    key="a",
                    kind="deterministic",
                    capability="canary.noop@1",
                    retry=RetryPolicy(max_attempts=3),
                ),
            )
        )
    )
    with pytest.raises(DefinitionError, match="non-idempotent capability"):
        registry.compile()


def test_approval_without_reject_branch_rejected() -> None:
    registry = _registry(with_canary=False)
    registry.register(
        _workflow(
            steps=(
                StepDefinition(
                    key="a",
                    kind="deterministic",
                    capability="canary.noop@1",
                    approval_required=True,
                ),
            )
        )
    )
    with pytest.raises(DefinitionError, match="reject/expire"):
        registry.compile()


def test_same_key_version_identical_registration_is_idempotent() -> None:
    registry = Registry()
    registry.register(_workflow())
    registry.register(_workflow())  # must not raise


# --------------------------------------------------------------------------
# Strict schemas


def test_strict_workflow_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(
            {
                "workflow_key": "harness.canary",
                "version": 1,
                "input_schema": "x@1",
                "output_schema": "y@1",
                "steps": [],
                "surprise": True,
            }
        )


def test_definition_requires_version() -> None:
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(
            {
                "workflow_key": "harness.canary",
                "input_schema": "x@1",
                "output_schema": "y@1",
                "steps": [],
            }
        )


# --------------------------------------------------------------------------
# Fakes are deterministic and offline


def test_fake_clock_advances_exactly() -> None:
    clock = fakes.FakeClock()
    start = clock.utc_epoch_us()
    clock.advance(2.5)
    assert clock.utc_epoch_us() - start == 2_500_000
    assert clock.utc_epoch_seconds() == start / 1_000_000 + 2.5


def test_fake_model_scripts_all_failure_classes() -> None:
    clock = fakes.FakeClock()
    retryable = GatewayError("429")
    retryable.error_class = AttemptErrorClass.provider
    indeterminate = GatewayError("timeout after send")
    indeterminate.error_class = AttemptErrorClass.indeterminate
    model = fakes.FakeModel(clock=clock, script=[retryable, indeterminate, {"output": "ok"}])
    with pytest.raises(GatewayError) as e1:
        model.complete({"prompt": "x"})
    assert e1.value.error_class == AttemptErrorClass.provider
    with pytest.raises(GatewayError) as e2:
        model.complete({"prompt": "x"})
    assert e2.value.error_class == AttemptErrorClass.indeterminate
    assert model.complete({"prompt": "x"}).output == "ok"
    assert len(model.calls) == 3


def test_fake_capability_keeps_idempotent_results_and_crash_points() -> None:
    capability = fakes.FakeCapability(crash_points={"after_side_effect"})
    with pytest.raises(RuntimeError, match="after side effect"):
        capability.execute({"idempotency_key": "k1", "value": 1})
    # Crash after side effect means the effect happened; a retry with the same
    # key returns the recorded result rather than repeating it.
    assert capability.execute({"idempotency_key": "k1", "value": 1})["echo"] == 1
    assert len(capability.calls) == 2
    assert capability.last_side_effect_id == "k1"

    capability = fakes.FakeCapability(crash_points={"before_side_effect"})
    with pytest.raises(RuntimeError, match="before side effect"):
        capability.execute({"idempotency_key": "k2", "value": 2})
    assert "k2" not in capability.results, "a crash before the effect must not record a result"


# --------------------------------------------------------------------------
# Config snapshot contract


def test_bootstrap_default_is_fully_off(monkeypatch) -> None:
    for env in (
        "PHAROS_HARNESS_ENABLED",
        "PHAROS_HARNESS_DISPATCHER_ENABLED",
        "PHAROS_HARNESS_AGENT_STEPS_ENABLED",
        "PHAROS_HARNESS_DOMAIN_PUBLISH_ENABLED",
        "PHAROS_DISCOVERY_EXECUTION",
        "PHAROS_DAILY_EXECUTION",
        "PHAROS_PROJECT_RESEARCH_EXECUTION",
        EMERGENCY_STOP_ENV,
    ):
        monkeypatch.delenv(env, raising=False)
    registry = _full_registry()
    snapshot = bootstrap_snapshot(registry)
    assert snapshot.gates["harness_enabled"] is False
    assert all(not value for name, value in snapshot.gates.items() if name != "harness_enabled")
    discovery = next(r for r in snapshot.routes if r.workflow_key == "literature.discovery")
    assert discovery.execution_mode == ExecutionMode.legacy
    assert validate_snapshot(snapshot, registry) == []


def test_bootstrap_env_can_enable_but_validator_gates_it(monkeypatch) -> None:
    monkeypatch.setenv("PHAROS_HARNESS_ENABLED", "1")
    monkeypatch.setenv("PHAROS_HARNESS_DISPATCHER_ENABLED", "1")
    monkeypatch.setenv("PHAROS_HARNESS_CANARY_ENABLED", "1")
    registry = _full_registry()
    snapshot = bootstrap_snapshot(registry)
    # Enabling via env must still pass the full validator -- bootstrap is not
    # a back door around it.
    assert validate_snapshot(snapshot, registry) == []


def test_validator_rejects_dependents_without_master(monkeypatch) -> None:
    monkeypatch.delenv("PHAROS_HARNESS_ENABLED", raising=False)
    registry = _full_registry()
    snapshot = bootstrap_snapshot(registry)
    snapshot = HarnessConfigSnapshot(
        gates={**snapshot.gates, "canary_enabled": True},
        routes=snapshot.routes,
    )
    errors = validate_snapshot(snapshot, registry)
    assert any("harness_enabled=0" in error for error in errors)


def test_validator_requires_runtime_gate_to_depend_on_agent_steps() -> None:
    registry = _full_registry()
    snapshot = bootstrap_snapshot(registry)
    snapshot = HarnessConfigSnapshot(
        gates={
            **snapshot.gates,
            "harness_enabled": True,
            "dispatcher_enabled": True,
            "agent_runtime_enabled": True,
        },
        routes=snapshot.routes,
    )
    errors = validate_snapshot(snapshot, registry)
    assert any(
        "agent_runtime_enabled requires harness_enabled, dispatcher_enabled, "
        "and agent_steps_enabled"
        in error
        for error in errors
    )


def test_agent_steps_canary_does_not_enable_runtime_gate() -> None:
    snapshot = bootstrap_snapshot(_full_registry())
    assert snapshot.gates["agent_steps_enabled"] is False
    assert snapshot.gates["agent_runtime_enabled"] is False


def test_legacy_snapshot_decode_adds_only_runtime_gate_in_memory() -> None:
    original = bootstrap_snapshot(_full_registry()).canonical()
    original["gates"].pop("agent_runtime_enabled")
    decoded = decode_snapshot_payload(original)
    assert decoded.gates["agent_runtime_enabled"] is False
    assert "agent_runtime_enabled" not in original["gates"]


def test_legacy_snapshot_decode_does_not_relax_other_missing_or_unknown_gates() -> None:
    original = bootstrap_snapshot(_full_registry()).canonical()
    original["gates"].pop("agent_runtime_enabled")
    original["gates"].pop("dispatcher_enabled")
    with pytest.raises(ValidationError, match="missing gate"):
        decode_snapshot_payload(original)
    original = bootstrap_snapshot(_full_registry()).canonical()
    original["gates"].pop("agent_runtime_enabled")
    original["gates"]["future_gate"] = False
    with pytest.raises(ValidationError, match="unknown gate"):
        decode_snapshot_payload(original)


def test_validator_rejects_harness_mode_without_publish_gate(monkeypatch) -> None:
    monkeypatch.delenv("PHAROS_HARNESS_ENABLED", raising=False)
    registry = _full_registry()
    snapshot = bootstrap_snapshot(registry)
    routes = tuple(
        (
            WorkflowRoute(
                workflow_key="harness.canary",
                active_version=1,
                activation_state=ActivationState.active,
                execution_mode=ExecutionMode.harness,
            )
            if route.workflow_key == CANARY_WORKFLOW_KEY
            else route
        )
        for route in snapshot.routes
    )
    snapshot = HarnessConfigSnapshot(
        gates={
            **snapshot.gates,
            "harness_enabled": True,
            "dispatcher_enabled": True,
            "canary_enabled": True,
        },
        routes=routes,
    )
    errors = validate_snapshot(snapshot, registry)
    assert any("domain_publish_enabled" in error for error in errors)


def test_validator_permanently_denies_experiments() -> None:
    registry = _full_registry()
    snapshot = bootstrap_snapshot(registry)
    snapshot = HarnessConfigSnapshot(
        gates={
            **snapshot.gates,
            "harness_enabled": True,
            "dispatcher_enabled": True,
            "experiments_enabled": True,
        },
        routes=snapshot.routes,
    )
    errors = validate_snapshot(snapshot, registry)
    assert any("Decision 9" in error for error in errors)


def test_validator_rejects_null_mode_for_business_workflow() -> None:
    registry = _full_registry()
    snapshot = bootstrap_snapshot(registry)
    routes = tuple(
        WorkflowRoute(workflow_key=route.workflow_key, activation_state=ActivationState.disabled)
        for route in snapshot.routes
    )
    snapshot = HarnessConfigSnapshot(gates=snapshot.gates, routes=routes)
    errors = validate_snapshot(snapshot, registry)
    assert any("NULL execution_mode" in error for error in errors)


def test_config_hash_stable_across_construction_order() -> None:
    registry = _full_registry()
    first = bootstrap_snapshot(registry)
    second = bootstrap_snapshot(registry)
    assert config_hash_stable(first) == config_hash_stable(second)
    mutated = HarnessConfigSnapshot(
        gates=first.gates, routes=first.routes, actor="someone", reason="changed"
    )
    assert config_hash_stable(mutated) != config_hash_stable(first)


def test_emergency_stop_is_deny_only(monkeypatch) -> None:
    monkeypatch.delenv(EMERGENCY_STOP_ENV, raising=False)
    assert emergency_stop_active() is False
    monkeypatch.setenv(EMERGENCY_STOP_ENV, "1")
    assert emergency_stop_active() is True
    monkeypatch.setenv(EMERGENCY_STOP_ENV, "0")
    assert emergency_stop_active() is False
