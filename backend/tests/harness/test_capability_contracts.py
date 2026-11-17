"""Contract tests for statically registered typed capability boundaries."""

from __future__ import annotations

import json
import math
from typing import Any, Literal

import pytest
from pharos.harness.capabilities import (
    CapabilityAction,
    CapabilityContractError,
    CapabilityObservation,
    CapabilitySchema,
    MappedInstancePayload,
    TrustedCapabilityRegistry,
    TypedCapabilityError,
    ValidatedPayload,
    payload_value,
)
from pharos.harness.contracts import (
    AttemptErrorClass,
    DeliveryState,
    IdempotencyKind,
    RetryClass,
    StrictModel,
)
from pharos.harness.definitions import CapabilityDefinition, canonical_json
from pydantic import Field, StrictInt, StrictStr, ValidationError


class SearchAction(StrictModel):
    query: StrictStr = Field(min_length=1, max_length=200)
    limit: StrictInt = Field(ge=1, le=50)
    context: dict[str, Any] = Field(default_factory=dict)


class SearchObservation(StrictModel):
    ok: Literal[True]
    items: tuple[StrictStr, ...] = Field(max_length=50)
    coverage: StrictStr = Field(min_length=1, max_length=100)


class SearchObservationV2(StrictModel):
    ok: Literal[True]
    items: tuple[StrictStr, ...] = Field(max_length=50)
    coverage: StrictStr = Field(min_length=1, max_length=100)
    source_count: StrictInt = Field(ge=0)


class MappedPaper(StrictModel):
    paper_key: StrictStr = Field(min_length=1, max_length=100)
    title: StrictStr = Field(min_length=1, max_length=500)


def _definition(
    *,
    capability_key: str = "discovery.search_source",
    idempotency: IdempotencyKind = IdempotencyKind.stable_key,
    retry_classes: tuple[RetryClass, ...] = (RetryClass.connect_timeout_unsent,),
    max_output_chars: int = 10_000,
) -> CapabilityDefinition:
    return CapabilityDefinition(
        capability_key=capability_key,
        version=1,
        action_schema="discovery.search_action@1",
        observation_schema="discovery.search_observation@1",
        idempotency=idempotency,
        retry_classes=retry_classes,
        max_output_chars=max_output_chars,
    )


def _schemas(
    *, observation_model: type[StrictModel] = SearchObservation
) -> tuple[CapabilitySchema, ...]:
    return (
        CapabilitySchema(
            identity="discovery.search_action@1",
            kind="action",
            model=SearchAction,
            validator_id="pharos.discovery.search_action@1",
            max_canonical_bytes=4096,
        ),
        CapabilitySchema(
            identity="discovery.search_observation@1",
            kind="observation",
            model=observation_model,
            validator_id="pharos.discovery.search_observation@1",
            max_canonical_bytes=4096,
        ),
        CapabilitySchema(
            identity="paper.mapped_item@1",
            kind="mapped",
            model=MappedPaper,
            validator_id="pharos.paper.mapped_item@1",
            max_canonical_bytes=4096,
        ),
    )


def _registry(
    *,
    definition: CapabilityDefinition | None = None,
    observation_model: type[StrictModel] = SearchObservation,
) -> TrustedCapabilityRegistry:
    return TrustedCapabilityRegistry(
        schemas=_schemas(observation_model=observation_model),
        capabilities=(_definition() if definition is None else definition,),
    )


def _contract(*, definition: CapabilityDefinition | None = None):  # noqa: ANN202
    registry = _registry(definition=definition)
    current = _definition() if definition is None else definition
    return registry.require(
        identity=current.identity(), definition_sha256=current.definition_hash()
    )


def _action(*, mapped: bool = False) -> CapabilityAction:
    contract = _contract()
    mapped_instance = None
    if mapped:
        mapped_instance = contract.create_mapped_instance(
            definition_step_key="search_sources",
            instance_key="arxiv:q-01",
            stable_item_key="arxiv:q-01",
            item_schema_identity="paper.mapped_item@1",
            item={"paper_key": "2401.00001", "title": "A Paper"},
        )
    return contract.create_action(
        {"query": "agent harness", "limit": 10, "context": {}},
        idempotency_key="run-1:search_sources:arxiv:q-01:attempt1",
        mapped_instance=mapped_instance,
    )


def test_registry_binds_definition_schema_hashes_and_explicit_validators() -> None:
    definition = _definition()
    registry = _registry(definition=definition)
    contract = registry.require(
        identity=definition.identity(), definition_sha256=definition.definition_hash()
    )

    canonical = contract.canonical()
    assert canonical["capability_identity"] == "discovery.search_source@1"
    assert canonical["capability_definition_sha256"] == definition.definition_hash()
    assert canonical["action_schema"]["identity"] == definition.action_schema
    assert canonical["observation_schema"]["identity"] == definition.observation_schema
    assert contract.action_schema_sha256 == canonical["action_schema"]["schema_sha256"]
    assert contract.observation_schema_sha256 == canonical["observation_schema"][
        "schema_sha256"
    ]
    assert contract.action_validator_id == "pharos.discovery.search_action@1"
    assert contract.observation_validator_id == "pharos.discovery.search_observation@1"
    assert contract.action_schema_json == canonical["action_schema"]["schema_json"]
    assert contract.observation_schema_json == canonical["observation_schema"]["schema_json"]
    assert len(contract.contract_sha256()) == 64
    persisted = canonical_json(canonical)
    assert "function" not in persisted
    assert "0x" not in persisted
    assert "__main__" not in persisted


def test_schema_and_contract_hash_change_when_the_static_schema_changes() -> None:
    first = _contract()
    second_registry = _registry(observation_model=SearchObservationV2)
    definition = _definition()
    second = second_registry.require(
        identity=definition.identity(), definition_sha256=definition.definition_hash()
    )
    assert first.observation_schema_sha256 != second.observation_schema_sha256
    assert first.contract_sha256() != second.contract_sha256()


def test_registry_is_sealed_and_rejects_unknown_or_mismatched_bindings() -> None:
    definition = _definition()
    registry = _registry(definition=definition)
    assert registry.capability_identities == (definition.identity(),)
    assert registry.schema_identities == (
        "discovery.search_action@1",
        "discovery.search_observation@1",
        "paper.mapped_item@1",
    )
    assert not hasattr(registry, "register")
    with pytest.raises(CapabilityContractError, match="identity/hash"):
        registry.require(identity=definition.identity(), definition_sha256="0" * 64)
    with pytest.raises(CapabilityContractError, match="action schema"):
        TrustedCapabilityRegistry(
            schemas=(
                CapabilitySchema(
                    identity="discovery.search_action@1",
                    kind="observation",
                    model=SearchAction,
                    validator_id="pharos.discovery.search_action@1",
                ),
                _schemas()[1],
            ),
            capabilities=(definition,),
        )


@pytest.mark.parametrize(
    "schemas, message",
    [
        ((_schemas()[0], _schemas()[0], _schemas()[1]), "more than once"),
        (
            (
                CapabilitySchema(
                    identity="discovery.search_action@1",
                    kind="action",
                    model=dict,  # type: ignore[arg-type]
                    validator_id="pharos.discovery.search_action@1",
                ),
                _schemas()[1],
            ),
            "StrictModel",
        ),
        (
            (
                CapabilitySchema(
                    identity="discovery.search_action@1",
                    kind="action",
                    model=SearchAction,
                    validator_id="not versioned",
                ),
                _schemas()[1],
            ),
            "validator identity",
        ),
        (
            (
                _schemas()[0],
                CapabilitySchema(
                    identity="discovery.search_observation@1",
                    kind="observation",
                    model=SearchObservation,
                    validator_id="pharos.discovery.search_action@1",
                ),
            ),
            "validator identity is registered more than once",
        ),
    ],
)
def test_registry_rejects_dynamic_or_ambiguous_schema_entries(
    schemas: tuple[CapabilitySchema, ...], message: str
) -> None:
    with pytest.raises(CapabilityContractError, match=message):
        TrustedCapabilityRegistry(schemas=schemas, capabilities=(_definition(),))


def test_action_is_strict_canonical_immutable_and_cross_bound() -> None:
    definition = _definition()
    contract = _contract(definition=definition)
    action = contract.create_action(
        {"context": {"year": 2026}, "limit": 10, "query": "agent harness"},
        idempotency_key="run-1:search:attempt1",
    )
    assert action.capability_identity == definition.identity()
    assert action.capability_definition_sha256 == definition.definition_hash()
    assert action.contract_sha256 == contract.contract_sha256()
    assert action.payload.schema_identity == definition.action_schema
    assert action.payload.schema_sha256 == contract.action_schema_sha256
    assert action.payload.validator_id == contract.action_validator_id
    assert payload_value(action.payload) == {
        "context": {"year": 2026},
        "limit": 10,
        "query": "agent harness",
    }
    assert action.payload.canonical_json == canonical_json(payload_value(action.payload))
    assert action.action_sha256() == action.action_sha256()
    # Attempt/run/owner coordinates stay in the authenticated snapshot and
    # producer_attempt provenance, never in the executor-facing Action.
    assert {
        "run_id",
        "step_id",
        "attempt_id",
        "attempt_no",
        "scope_type",
        "scope_id",
        "user_id",
    }.isdisjoint(action.canonical())
    projected = payload_value(action.payload)
    projected["query"] = "mutated"
    assert payload_value(action.payload)["query"] == "agent harness"


def test_action_rejects_coercion_unknown_fields_and_forged_bindings() -> None:
    contract = _contract()
    with pytest.raises(CapabilityContractError, match="schema validation"):
        contract.create_action(
            {"query": "x", "limit": "10", "context": {}},
            idempotency_key="run-1:attempt1",
        )
    with pytest.raises(CapabilityContractError, match="schema validation"):
        contract.create_action(
            {"query": "x", "limit": 10, "context": {}, "validator": "dynamic"},
            idempotency_key="run-1:attempt1",
        )

    action = _action()
    with pytest.raises(CapabilityContractError, match="definition binding"):
        contract.validate_action(
            action.model_copy(update={"capability_definition_sha256": "0" * 64})
        )
    with pytest.raises(CapabilityContractError, match="schema hash/validator"):
        contract.validate_action(
            action.model_copy(
                update={
                    "payload": action.payload.model_copy(
                        update={"validator_id": "pharos.untrusted.validator@1"}
                    )
                }
            )
        )
    with pytest.raises(CapabilityContractError, match="envelope"):
        contract.validate_action({**action.model_dump(mode="python"), "extra": True})


def test_stable_idempotency_and_non_idempotent_claims_are_enforced() -> None:
    with pytest.raises(CapabilityContractError, match="requires an idempotency"):
        _contract().create_action(
            {"query": "x", "limit": 1, "context": {}}, idempotency_key=None
        )
    definition = _definition(
        capability_key="discovery.non_idempotent",
        idempotency=IdempotencyKind.none,
        retry_classes=(),
    )
    contract = _contract(definition=definition)
    with pytest.raises(CapabilityContractError, match="must not claim"):
        contract.create_action(
            {"query": "x", "limit": 1, "context": {}},
            idempotency_key="false-claim",
        )
    action = contract.create_action(
        {"query": "x", "limit": 1, "context": {}}, idempotency_key=None
    )
    assert action.idempotency_key is None


def test_mapped_instance_binds_stable_identity_schema_hash_and_item_hash() -> None:
    contract = _contract()
    mapped = contract.create_mapped_instance(
        definition_step_key="read_cards",
        instance_key="paper:2401.00001",
        stable_item_key="paper:2401.00001",
        item_schema_identity="paper.mapped_item@1",
        item={"title": "Harness", "paper_key": "2401.00001"},
    )
    action = contract.create_action(
        {"query": "x", "limit": 1, "context": {}},
        idempotency_key="run:read:2401.00001:attempt1",
        mapped_instance=mapped,
    )
    assert action.mapped_instance is not None
    assert action.mapped_instance.item.schema_sha256
    assert action.mapped_instance.item.validator_id == "pharos.paper.mapped_item@1"
    assert action.mapped_instance.item.content_sha256 == mapped.item.content_sha256

    forged = mapped.model_copy(
        update={"item": mapped.item.model_copy(update={"content_sha256": "0" * 64})}
    )
    with pytest.raises((CapabilityContractError, ValidationError), match="hash"):
        contract.create_action(
            {"query": "x", "limit": 1, "context": {}},
            idempotency_key="run:read:attempt1",
            mapped_instance=forged,
        )
    with pytest.raises(ValidationError, match="singleton"):
        MappedInstancePayload(
            definition_step_key="read_cards",
            instance_key="__singleton__",
            stable_item_key="paper:1",
            item=mapped.item,
        )


@pytest.mark.parametrize("status", ["succeeded", "partial", "empty_success"])
def test_non_failed_observation_statuses_require_typed_payload(status: str) -> None:
    contract = _contract()
    action = _action()
    observation = contract.succeed(
        action,
        {"ok": True, "items": (), "coverage": "bounded"},
        status=status,  # type: ignore[arg-type]
    )
    assert observation.status == status
    assert observation.payload is not None
    assert observation.payload.schema_identity == "discovery.search_observation@1"
    assert observation.payload.schema_sha256 == contract.observation_schema_sha256
    assert observation.contract_sha256 == contract.contract_sha256()
    assert observation.action_sha256 == action.action_sha256()
    assert observation.observation_sha256() == observation.observation_sha256()


def test_failed_observation_requires_only_a_typed_error() -> None:
    contract = _contract()
    action = _action()
    error = TypedCapabilityError(
        error_class=AttemptErrorClass.timeout,
        code="provider.connect_timeout",
        message="Provider connection timed out before delivery.",
        delivery_state=DeliveryState.NOT_STARTED,
        retry_class=RetryClass.connect_timeout_unsent,
    )
    observation = contract.fail(action, error)
    assert observation.status == "failed"
    assert observation.payload is None
    assert observation.error == error
    with pytest.raises(ValidationError, match="requires only"):
        CapabilityObservation(
            capability_identity=action.capability_identity,
            capability_definition_sha256=action.capability_definition_sha256,
            contract_sha256=action.contract_sha256,
            action_sha256=action.action_sha256(),
            status="partial",
            error=error,
        )


def test_observation_rejects_wrong_action_contract_and_output_schema() -> None:
    contract = _contract()
    action = _action()
    observation = contract.succeed(
        action, {"ok": True, "items": ("a",), "coverage": "complete"}
    )
    with pytest.raises(CapabilityContractError, match="binding"):
        contract.validate_observation(
            action,
            observation.model_copy(update={"action_sha256": "0" * 64}),
        )
    with pytest.raises(CapabilityContractError, match="schema hash/validator"):
        contract.validate_observation(
            action,
            observation.model_copy(
                update={
                    "payload": observation.payload.model_copy(
                        update={"schema_sha256": "0" * 64}
                    )
                    if observation.payload
                    else None
                }
            ),
        )
    with pytest.raises(CapabilityContractError, match="schema validation"):
        contract.succeed(action, {"ok": True, "items": (), "coverage": "x", "extra": 1})


def test_error_retry_must_be_declared_idempotent_and_delivery_safe() -> None:
    contract = _contract()
    action = _action()
    undeclared = TypedCapabilityError(
        error_class=AttemptErrorClass.provider,
        code="provider.rate_limited",
        message="Provider rate limited the request.",
        retry_class=RetryClass.rate_limited,
    )
    with pytest.raises(CapabilityContractError, match="undeclared retry"):
        contract.fail(action, undeclared)
    with pytest.raises(ValidationError, match="indeterminate"):
        TypedCapabilityError(
            error_class=AttemptErrorClass.timeout,
            code="provider.unknown",
            message="Delivery is unknown.",
            delivery_state=DeliveryState.UNKNOWN,
        )
    with pytest.raises(ValidationError, match="not_started"):
        TypedCapabilityError(
            error_class=AttemptErrorClass.indeterminate,
            code="provider.sent",
            message="Delivery is unknown.",
            delivery_state=DeliveryState.SENT,
            retry_class=RetryClass.connect_timeout_unsent,
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"query": "x", "limit": 1, "context": {"api_key": "redacted"}},
        {"query": "x", "limit": 1, "context": {"value": "sk-example"}},
        {"query": "x", "limit": 1, "context": {"value": "Bearer example"}},
        {
            "query": "x",
            "limit": 1,
            "context": {"url": "https://user:password@example.invalid/paper"},
        },
        {
            "query": "x",
            "limit": 1,
            "context": {"url": "https://example.invalid/paper?access_token=value"},
        },
        {"query": "x\u202e", "limit": 1, "context": {}},
        {"query": "x\u0000", "limit": 1, "context": {}},
        {"query": "\ud800", "limit": 1, "context": {}},
    ],
)
def test_payloads_reject_secret_like_and_unsafe_unicode(payload: dict[str, Any]) -> None:
    with pytest.raises(CapabilityContractError):
        _contract().create_action(payload, idempotency_key="run:attempt1")


def test_normal_research_text_can_discuss_secrets_without_becoming_a_credential() -> None:
    action = _contract().create_action(
        {
            "query": "threshold secret sharing",
            "limit": 10,
            "context": {"note": "The paper studies secret sharing without credentials."},
        },
        idempotency_key="run:attempt1",
    )
    assert payload_value(action.payload)["query"] == "threshold secret sharing"


def test_non_finite_oversized_and_noncanonical_payloads_fail_closed() -> None:
    with pytest.raises(CapabilityContractError, match="non-finite"):
        _contract().create_action(
            {"query": "x", "limit": 1, "context": {"score": math.nan}},
            idempotency_key="run:attempt1",
        )

    definition = _definition(max_output_chars=32)
    contract = _contract(definition=definition)
    action = contract.create_action(
        {"query": "x", "limit": 1, "context": {}}, idempotency_key="run:attempt1"
    )
    with pytest.raises(CapabilityContractError, match="byte limit"):
        contract.succeed(
            action,
            {"ok": True, "items": (), "coverage": "a result larger than thirty two bytes"},
        )

    canonical = canonical_json({"query": "x"})
    with pytest.raises(ValidationError, match="not canonical"):
        ValidatedPayload(
            schema_identity="discovery.search_action@1",
            schema_sha256="a" * 64,
            validator_id="pharos.discovery.search_action@1",
            canonical_json=" " + canonical,
            content_sha256="a" * 64,
            size_bytes=len(canonical) + 1,
        )
    with pytest.raises(ValidationError, match="duplicate"):
        ValidatedPayload(
            schema_identity="discovery.search_action@1",
            schema_sha256="a" * 64,
            validator_id="pharos.discovery.search_action@1",
            canonical_json='{"query":"x","query":"y"}',
            content_sha256="a" * 64,
            size_bytes=25,
        )


def test_envelopes_and_typed_errors_forbid_unknown_fields_and_secret_messages() -> None:
    action = _action()
    with pytest.raises(ValidationError):
        CapabilityAction.model_validate({**action.model_dump(mode="python"), "stack": "trace"})
    with pytest.raises(ValidationError):
        TypedCapabilityError(
            error_class=AttemptErrorClass.provider,
            code="provider.error",
            message="api_key=do-not-store",
        )
    with pytest.raises(ValidationError):
        TypedCapabilityError.model_validate(
            {
                "error_class": "provider",
                "code": "provider.error",
                "message": "safe public error",
                "stack": "private trace",
            }
        )


def test_schema_models_cannot_declare_credential_fields() -> None:
    class UnsafeAction(StrictModel):
        api_key: StrictStr

    schemas = list(_schemas())
    schemas[0] = CapabilitySchema(
        identity="discovery.search_action@1",
        kind="action",
        model=UnsafeAction,
        validator_id="pharos.discovery.search_action@1",
    )
    with pytest.raises(CapabilityContractError, match="schema JSON"):
        TrustedCapabilityRegistry(schemas=schemas, capabilities=(_definition(),))


def test_schema_model_cannot_relax_frozen_or_extra_forbid_contract() -> None:
    class ExtensibleAction(StrictModel):
        model_config = {"extra": "allow", "frozen": False}
        query: StrictStr

    schemas = list(_schemas())
    schemas[0] = CapabilitySchema(
        identity="discovery.search_action@1",
        kind="action",
        model=ExtensibleAction,
        validator_id="pharos.discovery.search_action@1",
    )
    with pytest.raises(CapabilityContractError, match="frozen"):
        TrustedCapabilityRegistry(schemas=schemas, capabilities=(_definition(),))


def test_persisted_payload_metadata_is_self_verifying() -> None:
    action = _action(mapped=True)
    raw = action.model_dump(mode="json")
    restored = CapabilityAction.model_validate(json.loads(json.dumps(raw)), strict=False)
    checked = _contract().validate_action(restored)
    assert checked.action_sha256() == action.action_sha256()
    assert checked.payload.content_sha256 == action.payload.content_sha256
