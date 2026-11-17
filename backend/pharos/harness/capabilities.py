"""Typed Action -> Observation contracts for trusted Harness capabilities.

Capability executors are application code, but their inputs and outputs cross a
durable execution boundary.  This module keeps that boundary deliberately
small and closed:

* a capability is bound to one immutable ``CapabilityDefinition`` hash;
* schema names resolve only through an immutable, code-built registry;
* payloads are stored as canonical JSON plus a verified digest, not as mutable
  dictionaries;
* secrets, non-JSON numbers, oversized values and unknown fields fail closed.

Execution coordinates deliberately do not enter ``CapabilityAction``.  The
authenticated Attempt snapshot and ``producer_attempt_id`` remain the sole
authority for owner/run/step identity.  The runner persists
``action.action_sha256()`` as both the Attempt input hash and the produced
Artifact input hash; the Observation binds that same digest, while Artifact
provenance binds the producer Attempt back to its owner-scoped Run and Step.
This avoids leaking coordinates to an external executor or creating a second,
caller-forgeable identity envelope.

There is intentionally no import-by-name, entry point, plugin hook or user
supplied validator.  A process may construct a registry only from Pydantic
models already imported by trusted Pharos code, then the registry is sealed for
the rest of its lifetime.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, cast
from urllib.parse import parse_qsl, urlsplit

from pydantic import (
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from pharos.harness.contracts import (
    AttemptErrorClass,
    DeliveryState,
    IdempotencyKind,
    RetryClass,
    StrictModel,
)
from pharos.harness.definitions import CapabilityDefinition, canonical_json

CAPABILITY_CONTRACT_SCHEMA_VERSION = 1
MAX_CANONICAL_PAYLOAD_BYTES = 2 * 1024 * 1024
MAX_PAYLOAD_DEPTH = 32
MAX_PAYLOAD_NODES = 100_000
MAX_PAYLOAD_STRING_CHARS = 1_000_000

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_VERSIONED_IDENTITY_PATTERN = r"^[a-z][a-z0-9._-]{0,63}@[1-9][0-9]{0,5}$"
_SAFE_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,511}$"
_ERROR_CODE_PATTERN = r"^[a-z][a-z0-9._-]{0,127}$"

_BIDI_CONTROL_CLASSES = frozenset(
    {"LRE", "RLE", "LRO", "RLO", "PDF", "LRI", "RLI", "FSI", "PDI"}
)
_FORBIDDEN_METADATA_KEYS = frozenset(
    {
        "accesskey",
        "accesstoken",
        "apikey",
        "authorization",
        "bearer",
        "bearertoken",
        "clientsecret",
        "credential",
        "credentials",
        "oauthsecret",
        "oauthtoken",
        "password",
        "privatekey",
        "refreshtoken",
        "secret",
        "secrets",
        "sessiontoken",
    }
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?:^|[?&;,\s])(?:api[_-]?key|access[_-]?token|authorization|bearer|"
    r"client[_-]?secret|credential|oauth[_-]?token|password|private[_-]?key|"
    r"refresh[_-]?token|secret|session[_-]?token)\s*[:=]",
    re.IGNORECASE,
)
_KEY_NORMALIZER = re.compile(r"[^a-z0-9]")

SchemaKind = Literal["action", "observation", "mapped"]
ObservationStatus = Literal["succeeded", "partial", "empty_success", "failed"]
SuccessfulObservationStatus = Literal["succeeded", "partial", "empty_success"]


class CapabilityContractError(ValueError):
    """A typed capability envelope failed a trusted contract check."""


def _reject_json_constant(value: str) -> None:
    raise CapabilityContractError("payload contains a non-finite JSON number")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CapabilityContractError("payload contains a duplicate JSON object key")
        result[key] = value
    return result


def _parse_canonical_object(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except CapabilityContractError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError) as error:
        raise CapabilityContractError("payload is not strict JSON") from error
    if not isinstance(value, dict):
        raise CapabilityContractError("payload must be one JSON object")
    try:
        if canonical_json(value) != raw:
            raise CapabilityContractError("payload is not canonical JSON")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as error:
        if isinstance(error, CapabilityContractError):
            raise
        raise CapabilityContractError("payload is not canonical JSON") from error
    return value


def _normalized_metadata_key(value: str) -> str:
    return _KEY_NORMALIZER.sub("", value.lower())


def _string_is_credential_like(value: str) -> bool:
    lowered = value.lstrip().lower()
    if lowered.startswith(("bearer ", "sk-")) or _CREDENTIAL_ASSIGNMENT.search(value):
        return True
    if "://" not in value:
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.username is not None or parsed.password is not None:
        return True
    return any(
        _normalized_metadata_key(key) in _FORBIDDEN_METADATA_KEYS
        for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
    )


def _validate_safe_json(value: object) -> None:
    """Bound and scrub one JSON object without reflecting payload values."""

    nodes = 0

    def walk(item: object, *, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_PAYLOAD_NODES:
            raise CapabilityContractError("payload exceeds the node limit")
        if depth > MAX_PAYLOAD_DEPTH:
            raise CapabilityContractError("payload exceeds the nesting limit")
        if isinstance(item, Mapping):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise CapabilityContractError("payload contains a non-string object key")
                if _normalized_metadata_key(key) in _FORBIDDEN_METADATA_KEYS:
                    raise CapabilityContractError("payload contains a forbidden credential field")
                _validate_safe_text(key, label="payload key")
                walk(child, depth=depth + 1)
            return
        if isinstance(item, (list, tuple)):
            for child in item:
                walk(child, depth=depth + 1)
            return
        if isinstance(item, str):
            _validate_safe_text(item, label="payload string")
            if _string_is_credential_like(item):
                raise CapabilityContractError("payload contains credential-like data")
            return
        if isinstance(item, bool) or item is None or isinstance(item, int):
            return
        if isinstance(item, float):
            if not math.isfinite(item):
                raise CapabilityContractError("payload contains a non-finite JSON number")
            return
        raise CapabilityContractError("payload contains a non-JSON value")

    walk(value, depth=0)


def _validate_safe_text(value: str, *, label: str) -> str:
    if len(value) > MAX_PAYLOAD_STRING_CHARS:
        raise CapabilityContractError(f"{label} exceeds the character limit")
    for character in value:
        codepoint = ord(character)
        if 0xD800 <= codepoint <= 0xDFFF:
            raise CapabilityContractError(f"{label} contains invalid Unicode")
        if (codepoint < 32 and character not in "\t\n\r") or codepoint == 127:
            raise CapabilityContractError(f"{label} contains a control character")
        if unicodedata.bidirectional(character) in _BIDI_CONTROL_CLASSES:
            raise CapabilityContractError(f"{label} contains a bidi control character")
    return value


def _canonical_payload(value: Mapping[str, Any], *, max_bytes: int) -> tuple[str, str, int]:
    _validate_safe_json(value)
    try:
        raw = canonical_json(value)
        encoded = raw.encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as error:
        raise CapabilityContractError("payload is not canonical UTF-8 JSON") from error
    if len(encoded) > min(max_bytes, MAX_CANONICAL_PAYLOAD_BYTES):
        raise CapabilityContractError("payload exceeds the canonical byte limit")
    return raw, hashlib.sha256(encoded).hexdigest(), len(encoded)


class ValidatedPayload(StrictModel):
    """An immutable, self-verifying canonical JSON object."""

    schema_identity: StrictStr = Field(pattern=_VERSIONED_IDENTITY_PATTERN)
    schema_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    validator_id: StrictStr = Field(pattern=_VERSIONED_IDENTITY_PATTERN)
    canonical_json: StrictStr = Field(max_length=MAX_CANONICAL_PAYLOAD_BYTES)
    content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    size_bytes: StrictInt = Field(ge=2, le=MAX_CANONICAL_PAYLOAD_BYTES)

    @model_validator(mode="after")
    def _verify_canonical_payload(self) -> ValidatedPayload:
        value = _parse_canonical_object(self.canonical_json)
        _validate_safe_json(value)
        try:
            encoded = self.canonical_json.encode("utf-8", errors="strict")
        except UnicodeEncodeError as error:
            raise ValueError("payload is not canonical UTF-8 JSON") from error
        if len(encoded) != self.size_bytes:
            raise ValueError("payload size does not match canonical JSON")
        if hashlib.sha256(encoded).hexdigest() != self.content_sha256:
            raise ValueError("payload hash does not match canonical JSON")
        return self

    def value(self) -> dict[str, Any]:
        """Return a fresh mutable projection; the authenticated bytes stay frozen."""

        return _parse_canonical_object(self.canonical_json)


class MappedInstancePayload(StrictModel):
    """Stable identity and typed item payload for one physical mapped Step."""

    definition_step_key: StrictStr = Field(min_length=1, max_length=64, pattern=_SAFE_KEY_PATTERN)
    instance_key: StrictStr = Field(min_length=1, max_length=512, pattern=_SAFE_KEY_PATTERN)
    stable_item_key: StrictStr = Field(min_length=1, max_length=512, pattern=_SAFE_KEY_PATTERN)
    item: ValidatedPayload

    @field_validator("definition_step_key", "instance_key", "stable_item_key")
    @classmethod
    def _reject_secret_identity(cls, value: str) -> str:
        if _string_is_credential_like(value):
            raise ValueError("mapped identity contains credential-like data")
        return value

    @model_validator(mode="after")
    def _reject_singleton_identity(self) -> MappedInstancePayload:
        if self.instance_key == "__singleton__" or self.stable_item_key == "__singleton__":
            raise ValueError("mapped identity must not use the singleton sentinel")
        return self


class CapabilityAction(StrictModel):
    """One capability invocation bound to its immutable definition and schema."""

    schema_version: Literal[1] = 1
    capability_identity: StrictStr = Field(pattern=_VERSIONED_IDENTITY_PATTERN)
    capability_definition_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    contract_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    idempotency_key: StrictStr | None = Field(
        default=None, min_length=1, max_length=512, pattern=_SAFE_KEY_PATTERN
    )
    payload: ValidatedPayload
    mapped_instance: MappedInstancePayload | None = None

    @field_validator("idempotency_key")
    @classmethod
    def _reject_secret_idempotency_key(cls, value: str | None) -> str | None:
        if value is not None and _string_is_credential_like(value):
            raise ValueError("idempotency key contains credential-like data")
        return value

    def canonical(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def action_sha256(self) -> str:
        """Return the input digest the Attempt and Artifact must both persist."""

        return hashlib.sha256(canonical_json(self.canonical()).encode("utf-8")).hexdigest()


class TypedCapabilityError(StrictModel):
    """A bounded public error; no stack, response body or arbitrary details."""

    error_class: AttemptErrorClass
    code: StrictStr = Field(min_length=1, max_length=128, pattern=_ERROR_CODE_PATTERN)
    message: StrictStr = Field(min_length=1, max_length=512)
    delivery_state: DeliveryState = DeliveryState.NOT_STARTED
    retry_class: RetryClass | None = None

    @field_validator("message")
    @classmethod
    def _validate_public_message(cls, value: str) -> str:
        _validate_safe_text(value, label="capability error message")
        if _string_is_credential_like(value):
            raise ValueError("capability error message contains credential-like data")
        return value

    @model_validator(mode="after")
    def _validate_delivery_class(self) -> TypedCapabilityError:
        if (
            self.delivery_state in {DeliveryState.UNKNOWN, DeliveryState.SENT}
            and self.error_class is not AttemptErrorClass.indeterminate
        ):
            raise ValueError("possibly delivered capability errors must be indeterminate")
        if (
            self.retry_class is RetryClass.connect_timeout_unsent
            and self.delivery_state is not DeliveryState.NOT_STARTED
        ):
            raise ValueError("connect_timeout_unsent requires not_started delivery")
        return self


class CapabilityObservation(StrictModel):
    """A success payload or one typed error, cryptographically bound to an Action."""

    schema_version: Literal[1] = 1
    capability_identity: StrictStr = Field(pattern=_VERSIONED_IDENTITY_PATTERN)
    capability_definition_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    contract_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    action_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    status: ObservationStatus
    payload: ValidatedPayload | None = None
    error: TypedCapabilityError | None = None

    @model_validator(mode="after")
    def _validate_result_union(self) -> CapabilityObservation:
        if self.status != "failed":
            if self.payload is None or self.error is not None:
                raise ValueError("a non-failed observation requires only a payload")
        elif self.payload is not None or self.error is None:
            raise ValueError("a failed observation requires only a typed error")
        return self

    def canonical(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def observation_sha256(self) -> str:
        return hashlib.sha256(canonical_json(self.canonical()).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CapabilitySchema:
    """One statically imported strict schema available to a sealed registry."""

    identity: str
    kind: SchemaKind
    model: type[StrictModel]
    validator_id: str
    max_canonical_bytes: int = MAX_CANONICAL_PAYLOAD_BYTES


@dataclass(frozen=True, slots=True)
class _SchemaBinding:
    identity: str
    kind: SchemaKind
    model: type[StrictModel]
    validator_id: str
    schema_json: str
    schema_sha256: str
    max_canonical_bytes: int

    def canonical(self) -> dict[str, Any]:
        return {
            "identity": self.identity,
            "kind": self.kind,
            "max_canonical_bytes": self.max_canonical_bytes,
            "schema_json": self.schema_json,
            "schema_sha256": self.schema_sha256,
            "validator_id": self.validator_id,
        }

    def validate(self, value: object, *, hard_max_bytes: int | None = None) -> ValidatedPayload:
        raw = value.model_dump(mode="python") if isinstance(value, StrictModel) else value
        if not isinstance(raw, Mapping):
            raise CapabilityContractError(f"{self.kind} payload must be one object")
        # Inspect the caller's exact values before Pydantic can normalize them
        # (for example, JSON serialization may otherwise turn NaN into null).
        _validate_safe_json(raw)
        try:
            parsed = self.model.model_validate(dict(raw), strict=True)
            # Revalidation closes Pydantic's model_copy(update=...) escape hatch.
            parsed = self.model.model_validate(parsed.model_dump(mode="python"), strict=True)
        except (TypeError, ValueError, ValidationError) as error:
            raise CapabilityContractError(
                f"{self.kind} payload failed schema validation"
            ) from error
        normalized = parsed.model_dump(mode="json")
        if not isinstance(normalized, dict):
            raise CapabilityContractError(f"{self.kind} schema must produce one object")
        maximum = self.max_canonical_bytes
        if hard_max_bytes is not None:
            maximum = min(maximum, hard_max_bytes)
        raw_json, digest, size = _canonical_payload(normalized, max_bytes=maximum)
        return ValidatedPayload(
            schema_identity=self.identity,
            schema_sha256=self.schema_sha256,
            validator_id=self.validator_id,
            canonical_json=raw_json,
            content_sha256=digest,
            size_bytes=size,
        )

    def revalidate(
        self, payload: ValidatedPayload, *, hard_max_bytes: int | None = None
    ) -> ValidatedPayload:
        try:
            checked = ValidatedPayload.model_validate(
                payload.model_dump(mode="python"), strict=True
            )
        except (TypeError, ValueError, ValidationError) as error:
            raise CapabilityContractError("payload canonical metadata/hash is invalid") from error
        if checked.schema_identity != self.identity:
            raise CapabilityContractError("payload schema identity does not match the contract")
        if (
            checked.schema_sha256 != self.schema_sha256
            or checked.validator_id != self.validator_id
        ):
            raise CapabilityContractError(
                "payload schema hash/validator does not match the contract"
            )
        maximum = self.max_canonical_bytes
        if hard_max_bytes is not None:
            maximum = min(maximum, hard_max_bytes)
        if checked.size_bytes > maximum:
            raise CapabilityContractError("payload exceeds the schema byte limit")
        try:
            parsed = self.model.model_validate_json(checked.canonical_json, strict=True)
            parsed = self.model.model_validate(
                parsed.model_dump(mode="python"), strict=True
            )
        except (TypeError, ValueError, ValidationError) as error:
            raise CapabilityContractError(
                f"{self.kind} payload failed schema revalidation"
            ) from error
        rebuilt = self.validate(parsed, hard_max_bytes=hard_max_bytes)
        if rebuilt != checked:
            raise CapabilityContractError("payload canonical metadata does not match its schema")
        return checked


class CapabilityContract:
    """Trusted validator for one exact CapabilityDefinition identity/hash pair.

    Instances are minted only by :class:`TrustedCapabilityRegistry`; the token
    check prevents a caller from constructing a look-alike contract around an
    unregistered Pydantic class.
    """

    __slots__ = (
        "_action_schema",
        "_definition",
        "_observation_schema",
        "_registry",
        "_token",
    )

    def __init__(
        self,
        *,
        definition: CapabilityDefinition,
        action_schema: _SchemaBinding,
        observation_schema: _SchemaBinding,
        registry: TrustedCapabilityRegistry,
        token: object,
    ) -> None:
        self._definition = definition
        self._action_schema = action_schema
        self._observation_schema = observation_schema
        self._registry = registry
        self._token = token

    @property
    def definition(self) -> CapabilityDefinition:
        return self._definition

    @property
    def identity(self) -> str:
        return self._definition.identity()

    @property
    def definition_sha256(self) -> str:
        return self._definition.definition_hash()

    @property
    def action_schema_sha256(self) -> str:
        return self._action_schema.schema_sha256

    @property
    def action_schema_json(self) -> str:
        return self._action_schema.schema_json

    @property
    def observation_schema_sha256(self) -> str:
        return self._observation_schema.schema_sha256

    @property
    def observation_schema_json(self) -> str:
        return self._observation_schema.schema_json

    @property
    def action_validator_id(self) -> str:
        return self._action_schema.validator_id

    @property
    def observation_validator_id(self) -> str:
        return self._observation_schema.validator_id

    def canonical(self) -> dict[str, Any]:
        """Return the persistence-safe binding; it contains no callable identity."""

        definition = CapabilityDefinition.model_validate(
            self._definition.model_dump(mode="python"), strict=True
        )
        return {
            "schema_version": CAPABILITY_CONTRACT_SCHEMA_VERSION,
            "capability_definition": definition.canonical(),
            "capability_definition_sha256": definition.definition_hash(),
            "capability_identity": definition.identity(),
            "action_schema": self._action_schema.canonical(),
            "observation_schema": self._observation_schema.canonical(),
        }

    def contract_sha256(self) -> str:
        return hashlib.sha256(canonical_json(self.canonical()).encode("utf-8")).hexdigest()

    def _require_trusted(self) -> None:
        if not self._registry._owns(self, self._token):
            raise CapabilityContractError("capability contract is not registry-authenticated")

    def create_mapped_instance(
        self,
        *,
        definition_step_key: str,
        instance_key: str,
        stable_item_key: str,
        item_schema_identity: str,
        item: object,
    ) -> MappedInstancePayload:
        self._require_trusted()
        binding = self._registry._schema(item_schema_identity, expected_kind="mapped")
        return MappedInstancePayload(
            definition_step_key=definition_step_key,
            instance_key=instance_key,
            stable_item_key=stable_item_key,
            item=binding.validate(item),
        )

    def create_action(
        self,
        payload: object,
        *,
        idempotency_key: str | None,
        mapped_instance: MappedInstancePayload | None = None,
    ) -> CapabilityAction:
        self._require_trusted()
        if self._definition.idempotency is IdempotencyKind.stable_key:
            if idempotency_key is None:
                raise CapabilityContractError("stable-key capability requires an idempotency key")
        elif (
            self._definition.idempotency is IdempotencyKind.none
            and idempotency_key is not None
        ):
            raise CapabilityContractError(
                "non-idempotent capability must not claim an idempotency key"
            )
        if mapped_instance is not None:
            binding = self._registry._schema(
                mapped_instance.item.schema_identity, expected_kind="mapped"
            )
            checked_item = binding.revalidate(mapped_instance.item)
            mapped_instance = MappedInstancePayload.model_validate(
                {
                    **mapped_instance.model_dump(mode="python"),
                    "item": checked_item.model_dump(mode="python"),
                },
                strict=True,
            )
        action = CapabilityAction(
            capability_identity=self.identity,
            capability_definition_sha256=self.definition_sha256,
            contract_sha256=self.contract_sha256(),
            idempotency_key=idempotency_key,
            payload=self._action_schema.validate(payload),
            mapped_instance=mapped_instance,
        )
        return self.validate_action(action)

    def validate_action(self, action: CapabilityAction | Mapping[str, Any]) -> CapabilityAction:
        self._require_trusted()
        raw = action.model_dump(mode="python") if isinstance(action, CapabilityAction) else action
        try:
            checked = CapabilityAction.model_validate(raw, strict=True)
        except (TypeError, ValueError, ValidationError) as error:
            raise CapabilityContractError("capability action envelope is invalid") from error
        if (
            checked.capability_identity != self.identity
            or checked.capability_definition_sha256 != self.definition_sha256
            or checked.contract_sha256 != self.contract_sha256()
        ):
            raise CapabilityContractError("capability action definition binding does not match")
        checked_payload = self._action_schema.revalidate(checked.payload)
        if checked.mapped_instance is not None:
            item_binding = self._registry._schema(
                checked.mapped_instance.item.schema_identity, expected_kind="mapped"
            )
            item_binding.revalidate(checked.mapped_instance.item)
        if self._definition.idempotency is IdempotencyKind.stable_key:
            if checked.idempotency_key is None:
                raise CapabilityContractError("stable-key capability requires an idempotency key")
        elif (
            self._definition.idempotency is IdempotencyKind.none
            and checked.idempotency_key is not None
        ):
            raise CapabilityContractError(
                "non-idempotent capability must not claim idempotency"
            )
        return CapabilityAction.model_validate(
            {
                **checked.model_dump(mode="python"),
                "payload": checked_payload.model_dump(mode="python"),
            },
            strict=True,
        )

    def succeed(
        self,
        action: CapabilityAction,
        payload: object,
        *,
        status: SuccessfulObservationStatus = "succeeded",
    ) -> CapabilityObservation:
        checked_action = self.validate_action(action)
        result = CapabilityObservation(
            capability_identity=self.identity,
            capability_definition_sha256=self.definition_sha256,
            contract_sha256=self.contract_sha256(),
            action_sha256=checked_action.action_sha256(),
            status=status,
            payload=self._observation_schema.validate(
                payload, hard_max_bytes=self._definition.max_output_chars
            ),
        )
        return self.validate_observation(checked_action, result)

    def fail(
        self, action: CapabilityAction, error: TypedCapabilityError
    ) -> CapabilityObservation:
        checked_action = self.validate_action(action)
        checked_error = TypedCapabilityError.model_validate(
            error.model_dump(mode="python"), strict=True
        )
        self._validate_error_policy(checked_error)
        result = CapabilityObservation(
            capability_identity=self.identity,
            capability_definition_sha256=self.definition_sha256,
            contract_sha256=self.contract_sha256(),
            action_sha256=checked_action.action_sha256(),
            status="failed",
            error=checked_error,
        )
        return self.validate_observation(checked_action, result)

    def validate_observation(
        self,
        action: CapabilityAction,
        observation: CapabilityObservation | Mapping[str, Any],
    ) -> CapabilityObservation:
        self._require_trusted()
        checked_action = self.validate_action(action)
        raw = (
            observation.model_dump(mode="python")
            if isinstance(observation, CapabilityObservation)
            else observation
        )
        try:
            checked = CapabilityObservation.model_validate(raw, strict=True)
        except (TypeError, ValueError, ValidationError) as error:
            raise CapabilityContractError("capability observation envelope is invalid") from error
        if (
            checked.capability_identity != self.identity
            or checked.capability_definition_sha256 != self.definition_sha256
            or checked.contract_sha256 != self.contract_sha256()
            or checked.action_sha256 != checked_action.action_sha256()
        ):
            raise CapabilityContractError(
                "capability observation binding does not match its action"
            )
        if checked.status != "failed":
            assert checked.payload is not None
            payload = self._observation_schema.revalidate(
                checked.payload, hard_max_bytes=self._definition.max_output_chars
            )
            return CapabilityObservation.model_validate(
                {**checked.model_dump(mode="python"), "payload": payload.model_dump(mode="python")},
                strict=True,
            )
        assert checked.error is not None
        self._validate_error_policy(checked.error)
        return checked

    def _validate_error_policy(self, error: TypedCapabilityError) -> None:
        if error.retry_class is None:
            return
        if error.retry_class not in self._definition.retry_classes:
            raise CapabilityContractError("capability error requests an undeclared retry class")
        if self._definition.idempotency is IdempotencyKind.none:
            raise CapabilityContractError("non-idempotent capability errors cannot request retry")


class TrustedCapabilityRegistry:
    """Immutable registry of trusted schema classes and capability contracts."""

    __slots__ = ("_contracts", "_schemas", "_token")

    def __init__(
        self,
        *,
        schemas: Iterable[CapabilitySchema],
        capabilities: Iterable[CapabilityDefinition],
    ) -> None:
        token = object()
        schema_bindings: dict[str, _SchemaBinding] = {}
        validator_identities: set[str] = set()
        for schema in schemas:
            if re.fullmatch(_VERSIONED_IDENTITY_PATTERN, schema.identity) is None:
                raise CapabilityContractError("schema identity is not a versioned dotted key")
            if schema.kind not in {"action", "observation", "mapped"}:
                raise CapabilityContractError("schema kind is not supported")
            if re.fullmatch(_VERSIONED_IDENTITY_PATTERN, schema.validator_id) is None:
                raise CapabilityContractError("validator identity is not a versioned dotted key")
            if not isinstance(schema.model, type) or not issubclass(schema.model, StrictModel):
                raise CapabilityContractError("schema validator must be a StrictModel class")
            if (
                schema.model.model_config.get("extra") != "forbid"
                or schema.model.model_config.get("frozen") is not True
            ):
                raise CapabilityContractError(
                    "schema validator must remain frozen with extra fields forbidden"
                )
            if schema.max_canonical_bytes <= 0 or (
                schema.max_canonical_bytes > MAX_CANONICAL_PAYLOAD_BYTES
            ):
                raise CapabilityContractError("schema byte limit is outside the trusted bound")
            if schema.identity in schema_bindings:
                raise CapabilityContractError("schema identity is registered more than once")
            if schema.validator_id in validator_identities:
                raise CapabilityContractError("validator identity is registered more than once")
            try:
                schema_value = schema.model.model_json_schema(mode="validation")
                _validate_safe_json(schema_value)
                schema_json = canonical_json(schema_value)
                schema_bytes = schema_json.encode("utf-8", errors="strict")
            except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as error:
                raise CapabilityContractError("schema JSON is not canonical and bounded") from error
            if len(schema_bytes) > MAX_CANONICAL_PAYLOAD_BYTES:
                raise CapabilityContractError("schema JSON exceeds the canonical byte limit")
            schema_bindings[schema.identity] = _SchemaBinding(
                identity=schema.identity,
                kind=schema.kind,
                model=schema.model,
                validator_id=schema.validator_id,
                schema_json=schema_json,
                schema_sha256=hashlib.sha256(schema_bytes).hexdigest(),
                max_canonical_bytes=schema.max_canonical_bytes,
            )
            validator_identities.add(schema.validator_id)

        definitions: dict[str, CapabilityDefinition] = {}
        for candidate in capabilities:
            try:
                definition = CapabilityDefinition.model_validate(
                    candidate.model_dump(mode="python"), strict=True
                )
            except (TypeError, ValueError, ValidationError) as error:
                raise CapabilityContractError(
                    "capability definition is not strictly typed"
                ) from error
            identity = definition.identity()
            if re.fullmatch(_VERSIONED_IDENTITY_PATTERN, identity) is None:
                raise CapabilityContractError(
                    "capability identity is not a versioned dotted key"
                )
            if identity in definitions:
                raise CapabilityContractError("capability identity is registered more than once")
            action = schema_bindings.get(definition.action_schema)
            observation = schema_bindings.get(definition.observation_schema)
            if action is None or action.kind != "action":
                raise CapabilityContractError(
                    "capability action schema is not statically registered"
                )
            if observation is None or observation.kind != "observation":
                raise CapabilityContractError(
                    "capability observation schema is not statically registered"
                )
            definitions[identity] = definition

        self._token = token
        self._schemas = MappingProxyType(schema_bindings)
        contracts: dict[tuple[str, str], CapabilityContract] = {}
        for identity, definition in definitions.items():
            action = schema_bindings[definition.action_schema]
            observation = schema_bindings[definition.observation_schema]
            contract = CapabilityContract(
                definition=definition,
                action_schema=action,
                observation_schema=observation,
                registry=self,
                token=token,
            )
            contracts[(identity, definition.definition_hash())] = contract
        self._contracts = MappingProxyType(contracts)

    def require(self, *, identity: str, definition_sha256: str) -> CapabilityContract:
        contract = self._contracts.get((identity, definition_sha256))
        if contract is None:
            raise CapabilityContractError("capability identity/hash is not statically registered")
        return contract

    def _schema(self, identity: str, *, expected_kind: SchemaKind) -> _SchemaBinding:
        binding = self._schemas.get(identity)
        if binding is None or binding.kind != expected_kind:
            raise CapabilityContractError("schema identity/kind is not statically registered")
        return binding

    def _owns(self, contract: CapabilityContract, token: object) -> bool:
        if token is not self._token:
            return False
        return any(candidate is contract for candidate in self._contracts.values())

    @property
    def capability_identities(self) -> tuple[str, ...]:
        return tuple(sorted(identity for identity, _ in self._contracts))

    @property
    def schema_identities(self) -> tuple[str, ...]:
        return tuple(sorted(self._schemas))


def payload_value(payload: ValidatedPayload) -> dict[str, Any]:
    """Small typed helper for executors that need the validated JSON object."""

    return cast(dict[str, Any], payload.value())
