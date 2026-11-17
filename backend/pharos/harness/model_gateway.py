"""Provider-neutral model access and the per-Attempt gateway seam.

The runner opens one handle for each Attempt.  A handle owns cancellation and
completion state while a factory may share only deterministic fake-script
cursor/observability state; no model call is ever routed through a process-wide
cancel flag.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from threading import Condition, Lock
from types import MappingProxyType
from typing import Protocol, cast, runtime_checkable

from pharos.harness.contracts import AttemptErrorClass, DeliveryState, GatewayError
from pharos.harness.fakes import FakeModel, ModelResult

_MISSING_SCRIPT_ENTRY = object()


class ModelGateway(Protocol):
    """The existing runner-facing gateway contract."""

    def complete(self, payload: dict) -> ModelResult: ...
    def cancel(self) -> None: ...


class FakeModelGateway:
    """The H1 default: scripted, offline, usage-accounted by the caller."""

    def __init__(self, model: FakeModel) -> None:
        self._model = model

    def complete(self, payload: dict) -> ModelResult:
        return self._model.complete(payload)

    def cancel(self) -> None:
        self._model.cancelled = True


@dataclass(frozen=True, slots=True)
class AttemptContext:
    """Immutable identity and model route for one model Attempt.

    ``deadline_at_us`` is a UTC epoch timestamp in microseconds, matching the
    durable execution contract. It is supplied by the caller rather than
    read from a clock here, keeping factory construction deterministic.
    """

    run_id: str
    step_id: str
    attempt_id: str
    attempt_no: int
    scope_type: str
    scope_id: str
    lease_owner: str
    workflow_key: str
    workflow_version: int
    workflow_definition_sha256: str
    definition_binding_sha256: str
    run_policy_sha256: str
    role: str
    runtime_kind: str
    role_definition_sha256: str
    model_profile_identity: str
    model_profile_sha256: str
    model_route_key: str
    model_route_sha256: str
    usage_source: str
    input_sha256: str
    deadline_at_us: int
    provider: str
    model: str
    reasoning_effort: str | None = None
    max_output_tokens: int | None = None
    max_input_tokens: int | None = None

    def __post_init__(self) -> None:
        for name in (
            "run_id",
            "step_id",
            "attempt_id",
            "scope_id",
            "lease_owner",
            "workflow_key",
            "role",
            "model_profile_identity",
            "model_route_key",
            "provider",
            "model",
        ):
            value = getattr(self, name)
            if type(value) is not str or not value.strip() or "\x00" in value:
                raise ValueError(f"{name} must be a non-empty string without NUL bytes")
        if self.scope_type not in {"user", "system"}:
            raise ValueError("scope_type must be user or system")
        if type(self.attempt_no) is not int or self.attempt_no < 1:
            raise ValueError("attempt_no must be a positive integer")
        if type(self.workflow_version) is not int or self.workflow_version < 1:
            raise ValueError("workflow_version must be a positive integer")
        if type(self.deadline_at_us) is not int or self.deadline_at_us <= 0:
            raise ValueError("deadline_at_us must be a positive epoch microsecond integer")
        for name in (
            "workflow_definition_sha256",
            "definition_binding_sha256",
            "run_policy_sha256",
            "role_definition_sha256",
            "model_profile_sha256",
            "model_route_sha256",
            "input_sha256",
        ):
            value = getattr(self, name)
            if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        if self.runtime_kind not in {"in_process_fake", "dsh"}:
            raise ValueError("runtime_kind is not supported")
        if self.usage_source not in {"official", "byok", "system_shared"}:
            raise ValueError("usage_source is not supported")
        if self.reasoning_effort is not None and (
            type(self.reasoning_effort) is not str
            or not self.reasoning_effort
            or "\x00" in self.reasoning_effort
        ):
            raise ValueError("reasoning_effort must be a non-empty safe string")
        if self.max_output_tokens is not None and (
            type(self.max_output_tokens) is not int or self.max_output_tokens <= 0
        ):
            raise ValueError("max_output_tokens must be a positive integer")
        if self.max_input_tokens is not None and (
            type(self.max_input_tokens) is not int or self.max_input_tokens <= 0
        ):
            raise ValueError("max_input_tokens must be a positive integer")


@runtime_checkable
class GatewayHandle(Protocol):
    """One Attempt's model handle with explicit lifecycle boundaries.

    ``complete`` is one-shot and marks the handle completed (or failed).
    ``cancel`` marks an open handle cancelled. ``close`` is idempotent and is
    the only operation permitted after a terminal state.
    """

    context: AttemptContext

    @property
    def delivery_state(self) -> DeliveryState: ...

    def complete(self, payload: dict) -> ModelResult: ...
    def cancel(self) -> None: ...
    def close(self) -> None: ...
    def retry_cleanup(self) -> None: ...


@runtime_checkable
class GatewayFactory(Protocol):
    """Opens a fresh handle bound to exactly one Attempt context."""

    def open(self, context: AttemptContext) -> GatewayHandle: ...


class GatewayLifecycleError(RuntimeError):
    """An operation was attempted after a handle's lifecycle boundary."""


class GatewayKnownFailure(GatewayError):
    """A delivered model turn failed with authenticated token usage.

    This is distinct from an ordinary :class:`GatewayError`: the provider
    receipt and usage are known, so the runner must settle the reservation
    even though no Artifact may be published.  Runtime adapters expose only a
    typed ``ModelResult`` here; provider payloads and exception text never
    cross the durable boundary.
    """

    def __init__(
        self,
        message: str,
        *,
        error_class: AttemptErrorClass,
        result: ModelResult,
    ) -> None:
        if (
            not isinstance(error_class, AttemptErrorClass)
            or error_class is AttemptErrorClass.indeterminate
        ):
            raise TypeError("known gateway failure requires a determinate error class")
        if not isinstance(result, ModelResult):
            raise TypeError("known gateway failure requires a typed ModelResult")
        super().__init__(message)
        self.error_class = error_class
        self.result = result


class _HandleState(Enum):
    OPEN = "open"
    COMPLETING = "completing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CLOSED = "closed"


class _AttemptHandle:
    """Common lifecycle enforcement around a legacy gateway delegate."""

    def __init__(
        self,
        context: AttemptContext,
        delegate: ModelGateway,
        *,
        isolated_delegate: bool = False,
    ) -> None:
        self.context = context
        self._delegate = delegate
        self._isolated_delegate = isolated_delegate
        self._state = _HandleState.OPEN
        self._close_count = 0
        self._condition = Condition()
        self._active_operations = 0
        self._close_finished = False
        self._close_error: BaseException | None = None
        # Keep call and cleanup outcomes separately.  A failed cleanup must
        # never replace (or reclassify) the model-call outcome, and recovery
        # code needs to know whether it is retrying cleanup or the Attempt.
        self._call_error: BaseException | None = None
        self._delivery_state = DeliveryState.NOT_STARTED

    @property
    def state(self) -> str:
        with self._condition:
            return self._state.value

    @property
    def close_count(self) -> int:
        with self._condition:
            return self._close_count

    @property
    def delivery_state(self) -> DeliveryState:
        """A lock-protected, read-only delivery observation."""
        with self._condition:
            return self._delivery_state

    @property
    def call_error(self) -> BaseException | None:
        """The delegate call failure, if any, without exposing a mutable slot."""
        with self._condition:
            return self._call_error

    @property
    def close_error(self) -> BaseException | None:
        """The last delegate cleanup failure, if any."""
        with self._condition:
            return self._close_error

    def _sync_delegate_delivery(
        self,
        *,
        result: object | None = None,
        error: BaseException | None = None,
    ) -> None:
        """Copy typed delivery evidence exposed by a delegate or its error.

        Legacy delegates do not expose delivery facts, so the wrapper retains
        its conservative SENT/ACKNOWLEDGED fallback.  A transport-backed
        delegate can expose NOT_STARTED, UNKNOWN, or SENT on a failed call;
        those exact phases must win over the fallback and remain observable.
        """
        candidates: list[object] = []

        def read(obj: object, name: str) -> object | None:
            try:
                return getattr(obj, name, None)
            except BaseException:
                # Delivery is an optional observability seam.  A broken
                # property must not hide the original call or cleanup error.
                return None

        if result is not None:
            candidates.append(read(result, "delivery_state"))
            candidates.append(read(result, "deliveryState"))
        if error is not None:
            candidates.append(read(error, "delivery_state"))
        candidates.append(read(self._delegate, "delivery_state"))
        for candidate in candidates:
            if isinstance(candidate, DeliveryState):
                with self._condition:
                    self._delivery_state = candidate
                return

    def _require_open(self, operation: str) -> None:
        if self._state is not _HandleState.OPEN:
            raise GatewayLifecycleError(
                f"cannot {operation} gateway handle in {self._state.value} state"
            )

    def complete(self, payload: dict) -> ModelResult:
        if not isinstance(payload, dict):
            raise TypeError("gateway payload must be a dict")
        with self._condition:
            self._require_open("complete")
            self._state = _HandleState.COMPLETING
            # Cross the delivery boundary before entering delegate code.  A
            # raised error therefore cannot be misread as definitely unsent.
            self._delivery_state = DeliveryState.SENT
            self._active_operations += 1
        try:
            result = self._delegate.complete(payload)
        except Exception as error:
            # A new Attempt is required for retry: reusing this handle could
            # duplicate delivery after a provider-side failure.
            with self._condition:
                self._active_operations -= 1
                if self._state is _HandleState.COMPLETING:
                    self._state = _HandleState.FAILED
                self._call_error = error
                self._condition.notify_all()
            # Some delegates attach the exact transport phase to the raised
            # error or expose it as a property.  Preserve it after marking
            # the handle failed; never downgrade it to the SENT fallback.
            self._sync_delegate_delivery(error=error)
            raise
        except BaseException as error:
            # Release the in-flight slot even for process-control exceptions;
            # unlike ordinary exceptions, mark the handle terminally failed
            # without wrapping KeyboardInterrupt/SystemExit as a provider
            # error. The original exception is still re-raised unchanged.
            with self._condition:
                self._active_operations -= 1
                if self._state is _HandleState.COMPLETING:
                    self._state = _HandleState.FAILED
                self._call_error = error
                self._condition.notify_all()
            self._sync_delegate_delivery(error=error)
            raise
        self._sync_delegate_delivery(result=result)
        with self._condition:
            self._active_operations -= 1
            if self._state is not _HandleState.COMPLETING:
                self._condition.notify_all()
                lifecycle_error = GatewayLifecycleError(
                    f"complete returned after handle became {self._state.value}"
                )
                self._call_error = lifecycle_error
                raise lifecycle_error
            self._state = _HandleState.COMPLETED
            # A delegate that does not expose a phase is the legacy success
            # case and is conservatively treated as acknowledged.  Typed
            # transport delegates retain their exact phase instead.
            if self._delivery_state is DeliveryState.SENT:
                self._delivery_state = DeliveryState.ACKNOWLEDGED
            self._condition.notify_all()
            return result

    def cancel(self) -> None:
        with self._condition:
            if self._state not in {_HandleState.OPEN, _HandleState.COMPLETING}:
                raise GatewayLifecycleError(
                    f"cannot cancel gateway handle in {self._state.value} state"
                )
            # Claim the terminal state before calling the delegate. A
            # concurrently returning complete therefore cannot win by
            # overwriting CANCELLED after its provider call.
            self._state = _HandleState.CANCELLED
            if not self._isolated_delegate:
                return
            self._active_operations += 1
        try:
            # A shared legacy delegate has no Attempt identity. Isolated
            # delegates can receive cancel without affecting siblings.
            self._delegate.cancel()
            self._sync_delegate_delivery()
        except BaseException as error:
            with self._condition:
                self._call_error = error
            self._sync_delegate_delivery(error=error)
            raise
        finally:
            with self._condition:
                self._active_operations -= 1
                self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            if self._state is _HandleState.CLOSED:
                # Delegate cleanup is required to be bounded. A concurrent
                # close observes the same completion (and the same error)
                # rather than reporting success over half-finished cleanup.
                while not self._close_finished:
                    self._condition.wait()
                if self._close_error is not None:
                    raise self._close_error
                return
            if self._state not in {
                _HandleState.OPEN,
                _HandleState.COMPLETED,
                _HandleState.FAILED,
                _HandleState.CANCELLED,
            }:
                raise GatewayLifecycleError(
                    f"cannot close gateway handle in {self._state.value} state"
                )
            if self._active_operations:
                raise GatewayLifecycleError(
                    "cannot close gateway handle while an operation is in flight"
                )
            # Claim cleanup ownership while locked, but never call external
            # code under the Condition: a delegate may join a worker that is
            # finishing a callback through this handle.
            self._state = _HandleState.CLOSED
            self._close_count += 1
        error: BaseException | None = None
        close = getattr(self._delegate, "close", None)
        try:
            if self._isolated_delegate and callable(close):
                close()
        except BaseException as exc:
            error = exc
        finally:
            self._sync_delegate_delivery(error=error)
            with self._condition:
                self._close_error = error
                self._close_finished = True
                self._condition.notify_all()
        if error is not None:
            raise error

    def retry_cleanup(self) -> None:
        """Explicitly retry delegate cleanup after a failed ``close``.

        Ordinary ``close`` remains idempotent and replays one cleanup result to
        concurrent/duplicate callers. Recovery code must opt into a fresh
        bounded cleanup attempt through this method; otherwise a transient
        TERM/KILL/reap failure would be cached forever by the wrapper.
        """
        with self._condition:
            if self._state is not _HandleState.CLOSED:
                first_close = True
            else:
                first_close = False
                while not self._close_finished:
                    self._condition.wait()
                if self._close_error is None:
                    return
                self._close_finished = False
                self._close_error = None
                self._close_count += 1
        if first_close:
            self.close()
            return
        error: BaseException | None = None
        close = getattr(self._delegate, "close", None)
        try:
            if self._isolated_delegate and callable(close):
                close()
        except BaseException as exc:
            error = exc
        finally:
            self._sync_delegate_delivery(error=error)
            with self._condition:
                self._close_error = error
                self._close_finished = True
                self._condition.notify_all()
        if error is not None:
            raise error


class FakeGatewayFactory:
    """Offline factory adapting the current :class:`FakeModelGateway`.

    Each ``open`` owns a new ``cancelled`` flag.  The script cursor remains
    shared and locked so opening a second handle does not replay script entry
    zero; calls remain visible on the source ``FakeModel`` for existing tests.
    """

    supported_runtime_kinds = frozenset({"in_process_fake"})
    durable_runtime = False

    def __init__(self, gateway: FakeModel | ModelGateway) -> None:
        if isinstance(gateway, FakeModel):
            source_model = gateway
        elif isinstance(gateway, FakeModelGateway):
            source_model = gateway._model
        else:
            raise TypeError("FakeGatewayFactory requires FakeModel or FakeModelGateway")
        self._source_model = source_model
        self._open_count = 0
        self._open_count_lock = Lock()
        self._script_lock = Lock()

    @property
    def open_count(self) -> int:
        with self._open_count_lock:
            return self._open_count

    def open(self, context: AttemptContext) -> GatewayHandle:
        _validate_context(context)
        with self._open_count_lock:
            self._open_count += 1
        return _AttemptHandle(
            context,
            _FactoryFakeDelegate(self._source_model, self._script_lock),
            isolated_delegate=True,
        )


class _FactoryFakeDelegate:
    """One independently cancellable view over a shared deterministic model.

    ``FakeModel.complete`` intentionally keeps its old public behaviour.  The
    factory cannot call it directly because its global ``cancelled`` field is
    exactly the sibling-cancellation bug this seam is meant to remove.  This
    small delegate reproduces its offline script semantics while serialising
    script index allocation and recording aggregate calls on the source model.
    """

    def __init__(self, source: FakeModel, lock: Lock) -> None:
        self._source = source
        self._lock = lock
        self._state_lock = Lock()
        self.cancelled = False

    def complete(self, payload: dict) -> ModelResult:
        with self._state_lock:
            if self.cancelled:
                raise GatewayError("cancelled")
        with self._lock:
            index = len(self._source.calls)
            self._source.calls.append(payload)
            script = self._source.script
            entry = (
                script[index]
                if not callable(script) and index < len(script)
                else _MISSING_SCRIPT_ENTRY
            )
            callback = script if callable(script) else None
        if callback is not None:
            entry = callback(index, payload)
        elif entry is _MISSING_SCRIPT_ENTRY:
            entry = ModelResult(
                output={
                    "ok": True,
                    "workflow": payload.get("workflow"),
                    "step": payload.get("step"),
                }
            )
        if isinstance(entry, GatewayError):
            raise entry
        if isinstance(entry, dict):
            return ModelResult(**entry)
        return cast(ModelResult, entry)

    def cancel(self) -> None:
        with self._state_lock:
            self.cancelled = True


class LegacyGatewayFactory:
    """Adapt an existing runner-style gateway to the factory seam.

    A shared legacy object's no-argument cancellation cannot safely be sent
    without risking sibling Attempts. ``shared_gateway`` therefore provides
    compatibility without delegate cancellation; ``gateway_factory`` creates
    an isolated, independently cancellable delegate for every Attempt. The
    choice is explicit so a callable gateway object is never misclassified.

    Delegate ``cancel`` and ``close`` implementations must themselves be
    bounded. The production DSH handle supplies stricter phase deadlines and
    TERM/KILL/reap guarantees instead of relying on this compatibility seam.
    """

    supported_runtime_kinds = frozenset({"in_process_fake"})
    durable_runtime = False

    def __init__(
        self,
        *,
        shared_gateway: ModelGateway | None = None,
        gateway_factory: Callable[[], ModelGateway] | None = None,
    ) -> None:
        if (shared_gateway is None) == (gateway_factory is None):
            raise TypeError("provide exactly one of shared_gateway or gateway_factory")
        if shared_gateway is not None and (
            not hasattr(shared_gateway, "complete") or not hasattr(shared_gateway, "cancel")
        ):
            raise TypeError("shared_gateway must implement complete and cancel")
        if gateway_factory is not None and not callable(gateway_factory):
            raise TypeError("gateway_factory must be callable")
        self._shared_gateway = shared_gateway
        self._gateway_factory = gateway_factory
        self._open_count = 0
        self._open_count_lock = Lock()
        # Keep references as well as ids.  Retaining the delegate prevents a
        # later Python object from reusing an id and being mistaken for the
        # already-open delegate.
        self._isolated_delegates: dict[int, object] = {}

    @property
    def open_count(self) -> int:
        with self._open_count_lock:
            return self._open_count

    def open(self, context: AttemptContext) -> GatewayHandle:
        _validate_context(context)
        isolated = self._gateway_factory is not None
        delegate = (
            self._gateway_factory() if self._gateway_factory is not None else self._shared_gateway
        )
        if delegate is None or not hasattr(delegate, "complete") or not hasattr(delegate, "cancel"):
            raise TypeError("legacy gateway must implement complete and cancel")
        if isolated:
            with self._open_count_lock:
                delegate_id = id(delegate)
                if delegate_id in self._isolated_delegates:
                    raise TypeError("isolated gateway factory reused the same delegate")
                self._isolated_delegates[delegate_id] = delegate
        with self._open_count_lock:
            self._open_count += 1
        return _AttemptHandle(context, delegate, isolated_delegate=isolated)


def _validate_context(context: AttemptContext) -> None:
    if not isinstance(context, AttemptContext):
        raise TypeError("gateway factory context must be an AttemptContext")


class RuntimeGatewayFactory:
    """Route a frozen Attempt to exactly one runtime-specific factory.

    The mapping is process configuration, never Run input.  In particular, a
    DSH entry is admitted only when its concrete factory declares both the
    ``dsh`` runtime kind and a durable persistence/provenance boundary.
    """

    def __init__(self, factories: Mapping[str, GatewayFactory]) -> None:
        mapping = dict(factories)
        if not mapping or set(mapping) - {"in_process_fake", "dsh"}:
            raise ValueError("runtime gateway mapping contains an unsupported runtime kind")
        for runtime_kind, factory in mapping.items():
            supported: object = getattr(factory, "supported_runtime_kinds", frozenset())
            if not isinstance(supported, frozenset) or runtime_kind not in supported:
                raise TypeError("runtime factory does not advertise its configured runtime kind")
            if runtime_kind == "dsh":
                # A duck-typed marker is not an execution boundary: arbitrary
                # application objects could otherwise claim durable PID,
                # delivery and cleanup semantics. Import lazily to avoid the
                # gateway module's dependency on this neutral protocol layer.
                from pharos.harness.dsh_gateway import DshGatewayFactory

                if type(factory) is not DshGatewayFactory:
                    raise TypeError("DSH runtime requires the concrete sealed factory")
                if factory.durable_runtime is not True:
                    raise TypeError("DSH runtime factory is not durably configured")
        self._factories = MappingProxyType(mapping)
        self.supported_runtime_kinds = frozenset(mapping)

    @property
    def durable_runtime(self) -> bool:
        """Compatibility marker recomputed from the live sealed assembly."""
        return self.has_durable_dsh_runtime

    @property
    def has_durable_dsh_runtime(self) -> bool:
        """Whether the validated mapping contains the concrete DSH factory."""
        factory = self._factories.get("dsh")
        if factory is None:
            return False
        from pharos.harness.dsh_gateway import DshGatewayFactory

        return type(factory) is DshGatewayFactory and factory.durable_runtime is True

    def open(self, context: AttemptContext) -> GatewayHandle:
        _validate_context(context)
        factory = self._factories.get(context.runtime_kind)
        if factory is None:
            raise ValueError("no gateway factory is configured for the Attempt runtime kind")
        if context.runtime_kind == "dsh" and not self.has_durable_dsh_runtime:
            raise TypeError("DSH runtime factory is no longer durably configured")
        return factory.open(context)
