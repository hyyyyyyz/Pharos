"""Definition closure and immutable repository contract tests."""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError

import pytest
from pharos.db.session import session_scope
from pharos.harness.app import HarnessApp
from pharos.harness.contracts import ConfigIntegrityError, DefinitionError, StaleConfigError
from pharos.harness.definitions import (
    CapabilityDefinition,
    ModelProfileDefinition,
    ModelRouteDefinition,
    RoleDefinition,
    StepDefinition,
    WorkflowDefinition,
    canonical_json,
)
from pharos.harness.registry import CompiledWorkflowBinding, Registry
from pharos.harness.repository import HarnessDefinitionRepository, now_iso
from pharos.harness.tables import (
    capability_versions,
    model_profile_versions,
    role_versions,
    workflow_definition_bindings,
    workflow_versions,
)
from sqlalchemy import func, select


def _capability(key: str) -> CapabilityDefinition:
    return CapabilityDefinition(
        capability_key=key,
        version=1,
        action_schema="test.action@1",
        observation_schema="test.observation@1",
    )


def _profile() -> ModelProfileDefinition:
    return ModelProfileDefinition(
        profile_key="test-fake",
        version=1,
        selection_policy="fixed",
        routes=(
            ModelRouteDefinition(
                route_key="default",
                priority=1,
                provider="pharos-fake",
                model="pharos-fake-canary",
                usage_source="system_shared",
                credential_policy="none",
                allowed_runtime_kinds=("in_process_fake",),
            ),
        ),
    )


def _registry() -> Registry:
    registry = Registry()
    for key in ("test.read", "test.write", "unused"):
        registry.register_capability(_capability(key))
    registry.register_model_profile(_profile())
    registry.register_role(
        RoleDefinition(
            role_key="summarizer",
            version=1,
            prompt_template_version="test.prompt@1",
            input_schema="test.input@1",
            output_schema="test.output@1",
            model_profile="test-fake@1",
            runtime_kind="in_process_fake",
            capability_allowlist=("test.write@1",),
        )
    )
    registry.register(
        WorkflowDefinition(
            workflow_key="test.workflow",
            version=1,
            input_schema="test.input@1",
            output_schema="test.output@1",
            internal_no_legacy_writer=True,
            allowed_capabilities=("test.read@1", "test.write@1", "unused@1"),
            steps=(
                StepDefinition(key="read", kind="deterministic", capability="test.read@1"),
                StepDefinition(key="summarize", kind="agent", role="summarizer@1"),
            ),
        )
    )
    return registry


def test_binding_is_transitive_and_ignores_unrelated_registry_entries() -> None:
    registry = _registry()
    binding = registry.compile_workflow_binding("test.workflow@1")
    assert [item["identity"] for item in binding.capabilities] == ["test.read@1", "test.write@1"]
    assert [item["identity"] for item in binding.roles] == ["summarizer@1"]
    assert binding.roles[0]["model_profile"]["identity"] == "test-fake@1"
    assert binding.roles[0]["capability_allowlist"] == ["test.write@1"]
    assert "unused@1" not in {item["identity"] for item in binding.capabilities}

    registry.register_capability(_capability("another-unused"))
    assert registry.compile_workflow_binding("test.workflow@1").binding_sha256 == (
        binding.binding_sha256
    )


def test_bare_non_legacy_profile_is_fail_closed() -> None:
    registry = _registry()
    registry.register_role(
        RoleDefinition(
            role_key="bad",
            version=1,
            prompt_template_version="test.prompt@1",
            input_schema="test.input@1",
            output_schema="test.output@1",
            model_profile="test-fake",
            runtime_kind="in_process_fake",
        )
    )
    registry.register(
        WorkflowDefinition(
            workflow_key="bad.workflow",
            version=1,
            input_schema="test.input@1",
            output_schema="test.output@1",
            internal_no_legacy_writer=True,
            steps=(StepDefinition(key="run", kind="agent", role="bad@1"),),
        )
    )
    with pytest.raises(DefinitionError, match="must be versioned"):
        registry.compile_workflow_binding("bad.workflow@1")


def test_deterministic_capability_must_be_in_workflow_allowlist() -> None:
    registry = Registry()
    registry.register_capability(_capability("test.read"))
    registry.register(
        WorkflowDefinition(
            workflow_key="bad.deterministic",
            version=1,
            input_schema="test.input@1",
            output_schema="test.output@1",
            internal_no_legacy_writer=True,
            allowed_capabilities=(),
            steps=(
                StepDefinition(
                    key="read", kind="deterministic", capability="test.read@1"
                ),
            ),
        )
    )
    with pytest.raises(DefinitionError, match="outside the workflow allowlist"):
        registry.compile()
    with pytest.raises(DefinitionError, match="outside the workflow allowlist"):
        registry.compile_workflow_binding("bad.deterministic@1")


def test_mapped_capability_must_be_in_workflow_allowlist() -> None:
    registry = Registry()
    registry.register_capability(_capability("test.read"))
    registry.register(
        WorkflowDefinition(
            workflow_key="bad.mapped",
            version=1,
            input_schema="test.input@1",
            output_schema="test.output@1",
            internal_no_legacy_writer=True,
            allowed_capabilities=(),
            steps=(
                StepDefinition(
                    key="fanout",
                    kind="mapped",
                    capability="test.read@1",
                    max_fanout=2,
                ),
            ),
        )
    )
    with pytest.raises(DefinitionError, match="outside the workflow allowlist"):
        registry.compile()
    with pytest.raises(DefinitionError, match="outside the workflow allowlist"):
        registry.compile_workflow_binding("bad.mapped@1")


def test_repository_persists_idempotently_and_rejects_conflict(db) -> None:
    registry = _registry()
    workflow = registry.require_workflow("test.workflow@1")
    repository = HarnessDefinitionRepository()
    with session_scope() as session:
        first = repository.persist_workflow_binding(
            session, registry=registry, workflow=workflow, now=now_iso()
        )
        second = repository.persist_workflow_binding(
            session, registry=registry, workflow=workflow, now=now_iso()
        )
        assert first.binding_sha256 == second.binding_sha256
        row = repository.get_binding(session, first.binding_sha256)
        assert row is not None
        assert json.loads(row["binding_json"])["schema_version"] == 1

        changed = workflow.model_copy(update={"max_parallel_steps": 8})
        with pytest.raises(StaleConfigError):
            repository.upsert_workflow(session, changed, now_iso())


def test_bootstrap_persists_every_registered_definition_binding(db) -> None:
    app = HarnessApp()
    app.ensure_bootstrapped()
    app.ensure_bootstrapped()

    with session_scope() as session:
        for workflow in app.registry.all_workflows():
            binding = app.registry.compile_workflow_binding(workflow.identity())
            assert app.definition_repository.get_binding(
                session, binding.binding_sha256
            ) is not None

        counts = {
            table.name: session.execute(select(func.count()).select_from(table)).scalar_one()
            for table in (
                workflow_versions,
                workflow_definition_bindings,
                model_profile_versions,
                role_versions,
                capability_versions,
            )
        }
    assert counts == {
        "harness_workflow_versions": 2,
        "harness_workflow_definition_bindings": 2,
        "harness_model_profile_versions": 2,
        "harness_role_versions": 2,
        "harness_capability_versions": 3,
    }


def test_canonical_json_rejects_non_finite_numbers() -> None:
    with pytest.raises(ValueError, match="Out of range float values"):
        canonical_json({"value": float("nan")})


def test_compiled_binding_is_deeply_immutable_and_authenticated() -> None:
    binding = _registry().compile_workflow_binding("test.workflow@1")
    with pytest.raises(FrozenInstanceError):
        binding.binding_sha256 = "0" * 64  # type: ignore[misc]
    with pytest.raises(TypeError):
        binding.value["schema_version"] = 2  # type: ignore[index]
    with pytest.raises(AttributeError):
        binding.value["capabilities"].append({})  # type: ignore[attr-defined]
    with pytest.raises(DefinitionError, match="hash mismatch"):
        CompiledWorkflowBinding(value=binding.value, binding_sha256="0" * 64)


def test_compiled_binding_rejects_forged_empty_transitive_closure() -> None:
    binding = _registry().compile_workflow_binding("test.workflow@1")
    forged = json.loads(binding.canonical_json())
    forged["capabilities"] = []
    with pytest.raises(DefinitionError, match="missing capability|closure"):
        CompiledWorkflowBinding(
            value=forged,
            binding_sha256=hashlib.sha256(canonical_json(forged).encode()).hexdigest(),
        )


def test_compiled_binding_rejects_role_capability_outside_workflow_allowlist() -> None:
    binding = _registry().compile_workflow_binding("test.workflow@1")
    forged = json.loads(binding.canonical_json())
    workflow = WorkflowDefinition.model_validate(forged["workflow"]["definition"])
    workflow = workflow.model_copy(update={"allowed_capabilities": ("test.read@1",)})
    forged["workflow"]["definition"] = workflow.model_dump(mode="json")
    forged["workflow"]["definition_sha256"] = workflow.definition_hash()
    with pytest.raises(DefinitionError, match="outside the workflow allowlist"):
        CompiledWorkflowBinding(
            value=forged,
            binding_sha256=hashlib.sha256(canonical_json(forged).encode()).hexdigest(),
        )


def test_registry_fake_runtime_is_an_exact_two_pair_allowlist() -> None:
    registry = _registry()
    registry.register_model_profile(
        ModelProfileDefinition(
            profile_key="bad-fake",
            version=1,
            selection_policy="fixed",
            routes=(
                ModelRouteDefinition(
                    route_key="default",
                    priority=1,
                    provider="not-fake",
                    model="canary",
                    usage_source="system_shared",
                    credential_policy="system_managed",
                    allowed_runtime_kinds=("in_process_fake",),
                ),
            ),
        )
    )
    registry.register_role(
        RoleDefinition(
            role_key="bad-fake",
            version=1,
            prompt_template_version="test.prompt@1",
            input_schema="test.input@1",
            output_schema="test.output@1",
            model_profile="bad-fake@1",
            runtime_kind="in_process_fake",
        )
    )
    with pytest.raises(DefinitionError, match="internal fake route"):
        registry.compile()


def test_repository_rejects_forged_binding_before_database_write(db) -> None:
    registry = _registry()
    binding = registry.compile_workflow_binding("test.workflow@1")
    value = json.loads(binding.canonical_json())
    value["roles"][0]["capability_allowlist"] = []
    forged = CompiledWorkflowBinding.__new__(CompiledWorkflowBinding)
    object.__setattr__(forged, "value", value)
    object.__setattr__(
        forged, "binding_sha256", hashlib.sha256(canonical_json(value).encode()).hexdigest()
    )
    with session_scope() as session, pytest.raises((DefinitionError, ConfigIntegrityError)):
        HarnessDefinitionRepository().upsert_binding(session, forged, now_iso())


def test_repository_rejects_forged_binding_with_credential_like_model(db) -> None:
    registry = _registry()
    binding = registry.compile_workflow_binding("test.workflow@1")
    value = json.loads(binding.canonical_json())
    profile_record = value["roles"][0]["model_profile"]
    profile = profile_record["definition"]
    profile["routes"][0]["model"] = "secret=exfiltration"
    # Recompute every attacker-controlled envelope hash.  The independent
    # binding validator must still reject the route metadata itself.
    profile_record["definition_sha256"] = hashlib.sha256(
        canonical_json(profile).encode()
    ).hexdigest()
    forged = CompiledWorkflowBinding.__new__(CompiledWorkflowBinding)
    object.__setattr__(forged, "value", value)
    object.__setattr__(
        forged, "binding_sha256", hashlib.sha256(canonical_json(value).encode()).hexdigest()
    )
    with session_scope() as session, pytest.raises(DefinitionError):
        HarnessDefinitionRepository().upsert_binding(session, forged, now_iso())


def test_existing_binding_payload_definition_errors_map_to_integrity_error() -> None:
    registry = _registry()
    binding = registry.compile_workflow_binding("test.workflow@1")
    value = json.loads(binding.canonical_json())
    value["roles"][0]["capability_allowlist"] = []
    raw = canonical_json(value)
    row = {
        "binding_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "binding_json": raw,
        "schema_version": 1,
        "workflow_key": "test.workflow",
        "workflow_version": 1,
        "workflow_definition_sha256": value["workflow"]["definition_sha256"],
    }
    with pytest.raises(ConfigIntegrityError, match="definition is invalid"):
        HarnessDefinitionRepository._checked_binding_row(row)  # noqa: SLF001


def test_get_binding_maps_broken_persisted_closure_to_integrity_error(
    db, monkeypatch
) -> None:
    registry = _registry()
    repository = HarnessDefinitionRepository()
    with session_scope() as session:
        binding = repository.persist_workflow_binding(
            session,
            registry=registry,
            workflow=registry.require_workflow("test.workflow@1"),
            now=now_iso(),
        )

        def broken_closure(*args, **kwargs):  # noqa: ANN002, ANN003
            raise DefinitionError("missing persisted capability")

        monkeypatch.setattr(repository, "_verify_binding_rows", broken_closure)
        with pytest.raises(ConfigIntegrityError, match="closure is invalid"):
            repository.get_binding(session, binding.binding_sha256)


def test_repository_direct_role_write_revalidates_runtime_profile(db) -> None:
    repository = HarnessDefinitionRepository()
    profile = _profile()
    incompatible_role = RoleDefinition(
        role_key="bad-dsh",
        version=1,
        prompt_template_version="test.prompt@1",
        input_schema="test.input@1",
        output_schema="test.output@1",
        model_profile=profile.identity(),
        runtime_kind="dsh",
    )
    with session_scope() as session:
        repository.upsert_model_profile(session, profile, now_iso())
        with pytest.raises(DefinitionError, match="must allow dsh"):
            repository.upsert_role(session, incompatible_role, now_iso())


def test_repository_revalidates_untrusted_model_copies_before_insert(db) -> None:
    repository = HarnessDefinitionRepository()
    profile = _profile()
    role = RoleDefinition(
        role_key="reader",
        version=1,
        prompt_template_version="test.prompt@1",
        input_schema="test.input@1",
        output_schema="test.output@1",
        model_profile=profile.identity(),
        runtime_kind="in_process_fake",
    )
    with session_scope() as session:
        repository.upsert_model_profile(session, profile, now_iso())
        forged_role = role.model_copy(update={"runtime_kind": "not-a-runtime"})
        with pytest.raises(DefinitionError, match="valid typed definition"):
            repository.upsert_role(session, forged_role, now_iso())

        forged_profile = profile.model_copy(
            update={
                "routes": (
                    profile.routes[0].model_copy(update={"model": "api_key=secret"}),
                )
            }
        )
        with pytest.raises(DefinitionError, match="valid typed definition"):
            repository.upsert_model_profile(session, forged_profile, now_iso())


def test_concurrent_persistence_is_idempotent(db) -> None:
    registry = _registry()
    workflow = registry.require_workflow("test.workflow@1")
    barrier = threading.Barrier(2)

    def persist() -> str:
        barrier.wait()
        with session_scope() as session:
            return (
                HarnessDefinitionRepository()
                .persist_workflow_binding(
                    session, registry=registry, workflow=workflow, now=now_iso()
                )
                .binding_sha256
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: persist(), range(2)))
    assert results[0] == results[1]
