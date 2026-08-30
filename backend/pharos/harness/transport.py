"""Attempt-scoped, fail-closed stdio transport for the official DSH SDK wire.

This is intentionally a small synchronous parent-side boundary.  It launches
one process for one Attempt, supplies an exact environment, and never imports
or invokes the real DSH runtime itself.  A runtime protocol error is terminal:
the child is escalated through TERM/KILL and reaped before the error escapes.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import queue
import select
import selectors
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol, TypeAlias

from pydantic import ValidationError

from .contracts import DeliveryState
from .protocol import (
    NOTIFICATION_MODELS,
    REQUEST_METHODS,
    InitializeParams,
    InitializeResult,
    PromptOutcome,
    SessionEvent,
    SessionEventNotification,
    SessionPromptParams,
    SessionPromptResult,
    SessionStatusNotification,
    TextBlock,
    TokenUsage,
)


class HarnessTransportError(RuntimeError):
    """Base error for startup, protocol, bounds and process failures."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.delivery_state: DeliveryState | None = None
        self.cleanup_error_type: str | None = None


class HarnessProtocolError(HarnessTransportError):
    """The child emitted a malformed or disallowed wire message."""


class HarnessTimeoutError(HarnessTransportError, TimeoutError):
    """A phase deadline elapsed."""


class HarnessProcessError(HarnessTransportError):
    """The child exited before a required protocol boundary."""


DeliveryObserverResult: TypeAlias = bool | None


class DeliveryObserver(Protocol):
    """A bounded, typed persistence seam for one Attempt's delivery facts."""

    def __call__(self, state: DeliveryState) -> DeliveryObserverResult:
        """Persist ``state`` and return false/raise if persistence failed."""


class HarnessDeliveryError(HarnessTransportError):
    """A delivery fact could not be durably observed."""

    def __init__(self, phase: DeliveryState) -> None:
        self.phase = phase
        self.timed_out = False
        super().__init__(f"delivery observer failed during {phase.value}")


class HarnessDeliveryTimeoutError(HarnessDeliveryError):
    """The observer did not finish within its independent phase deadline."""

    def __init__(self, phase: DeliveryState) -> None:
        super().__init__(phase)
        self.timed_out = True
        self.args = (f"delivery observer deadline exceeded during {phase.value}",)


class HarnessDeliveryCapacityError(HarnessDeliveryError):
    """The process-level bounded observer executor is saturated."""

    def __init__(self, phase: DeliveryState) -> None:
        super().__init__(phase)
        self.capacity_rejected = True
        self.args = (f"delivery observer capacity exceeded during {phase.value}",)


# Explicit aliases make the seam discoverable without creating multiple error
# implementations for callers that use either the DSH or Attempt vocabulary.
AttemptDeliveryObserver = DeliveryObserver
AttemptDeliveryObserverError = HarnessDeliveryError
AttemptDeliveryObserverTimeoutError = HarnessDeliveryTimeoutError
AttemptDeliveryObserverCapacityError = HarnessDeliveryCapacityError

_DELIVERY_OBSERVER_PREVIOUS: dict[DeliveryState, DeliveryState] = {
    DeliveryState.UNKNOWN: DeliveryState.NOT_STARTED,
    DeliveryState.SENT: DeliveryState.UNKNOWN,
    DeliveryState.ACKNOWLEDGED: DeliveryState.SENT,
}

_DELIVERY_OBSERVER_WORKERS = 4
_DELIVERY_OBSERVER_QUEUE_SIZE = 4
_MAX_DELIVERY_OBSERVER_TIMEOUT_SECONDS = 5.0


@dataclass(slots=True)
class _ObserverJob:
    callback: DeliveryObserver
    state: DeliveryState
    completed: threading.Event
    succeeded: list[bool]


class _DeliveryObserverPool:
    """A process-level fixed daemon pool with a hard admission bound.

    A callback is untrusted application code and cannot be force-stopped from
    Python.  Fixed workers cap the damage from permanently blocked callbacks;
    the fixed-capacity queue makes saturation an immediate fail-closed result
    rather than an unbounded accumulation of callback threads or jobs.
    """

    def __init__(self) -> None:
        capacity = _DELIVERY_OBSERVER_WORKERS + _DELIVERY_OBSERVER_QUEUE_SIZE
        self._jobs: queue.Queue[_ObserverJob] = queue.Queue(capacity)
        self._capacity = threading.BoundedSemaphore(capacity)
        self._lock = threading.Lock()
        self._workers_started = False

    def _start_workers(self) -> None:
        if self._workers_started:
            return
        with self._lock:
            if self._workers_started:
                return
            for index in range(_DELIVERY_OBSERVER_WORKERS):
                worker = threading.Thread(
                    target=self._run,
                    name=f"pharos-delivery-observer-{index}",
                    daemon=True,
                )
                worker.start()
            self._workers_started = True

    def submit(
        self, callback: DeliveryObserver, state: DeliveryState
    ) -> tuple[threading.Event, list[bool]]:
        self._start_workers()
        if not self._capacity.acquire(blocking=False):
            raise HarnessDeliveryCapacityError(state)
        completed = threading.Event()
        succeeded = [False]
        job = _ObserverJob(callback, state, completed, succeeded)
        try:
            self._jobs.put_nowait(job)
        except queue.Full:
            # The semaphore and queue share the same hard outstanding-job
            # bound.  A Full result should therefore be unreachable, but keep
            # the release fail-closed if that invariant is ever disturbed.
            self._capacity.release()
            raise HarnessDeliveryCapacityError(state) from None
        return completed, succeeded

    def _run(self) -> None:
        while True:
            job = self._jobs.get()
            try:
                try:
                    result = job.callback(job.state)
                    job.succeeded[0] = result is None or result is True
                except BaseException:
                    # Do not retain or expose callback exceptions; they can
                    # contain provider/database details or secrets.
                    job.succeeded[0] = False
            finally:
                self._jobs.task_done()
                self._capacity.release()
                # Signal only after the outstanding-job permit is returned,
                # so the same Attempt can submit its next phase without a
                # transient false capacity rejection.
                job.completed.set()


_DELIVERY_OBSERVER_POOL = _DeliveryObserverPool()


class HarnessTurnError(HarnessTransportError):
    """A delivered turn ended without a publishable success result.

    The raw provider failure message and SDK event transcript are intentionally
    not retained.  The accounting evidence is still carried so a caller can
    settle or reconcile the Attempt without pretending that the call cost 0.
    """

    def __init__(
        self,
        reason: dict[str, Any],
        *,
        message_id: str,
        usage: TokenUsage | None,
        output: Sequence[TextBlock],
    ) -> None:
        self.reason = _sanitize_turn_reason(reason)
        self.message_id = message_id
        self.usage = usage
        self.output = tuple(output)
        super().__init__(f"runtime turn ended with reason {self.reason.get('kind', 'unknown')}")
        self.delivery_state = DeliveryState.ACKNOWLEDGED


def _is_finite_positive_number(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


@dataclass(frozen=True, slots=True)
class AttemptTransportConfig:
    """All launch, environment and resource policy for one Attempt."""

    argv: tuple[str, ...]
    cwd: str
    allowed_routes: frozenset[tuple[str, str]]
    profile: str = "sdk"
    expected_server_version: str = "0.0.1"
    env: Mapping[str, str] = field(default_factory=dict)
    env_allowlist: frozenset[str] = frozenset({"PATH", "HOME", "DSH_HOME", "LANG", "LC_ALL"})
    initialize_timeout_seconds: float = 5.0
    prompt_timeout_seconds: float = 30.0
    idle_timeout_seconds: float = 30.0
    shutdown_timeout_seconds: float = 2.0
    term_timeout_seconds: float = 1.0
    kill_timeout_seconds: float = 1.0
    reap_timeout_seconds: float = 2.0
    max_frame_bytes: int = 256 * 1024
    max_buffer_bytes: int = 512 * 1024
    max_json_depth: int = 64
    max_event_bytes: int = 128 * 1024
    max_total_event_bytes: int = 512 * 1024
    max_events: int = 1024
    max_output_bytes: int = 256 * 1024
    max_stderr_bytes: int = 64 * 1024
    delivery_observer_timeout_seconds: float = 1.0

    def __post_init__(self) -> None:
        if os.name != "posix":
            raise ValueError("AttemptTransport currently supports POSIX process groups only")
        if not isinstance(self.argv, (tuple, list)):
            raise ValueError("argv must be a fixed string sequence")
        argv = tuple(self.argv)
        object.__setattr__(self, "argv", argv)
        if not argv or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv):
            raise ValueError("argv must be a non-empty fixed string tuple")
        if not os.path.isabs(argv[0]):
            raise ValueError("argv[0] must be an absolute executable path")
        if not isinstance(self.profile, str) or not self.profile:
            raise ValueError("profile must be non-empty")
        if not isinstance(self.expected_server_version, str) or not self.expected_server_version:
            raise ValueError("expected_server_version must be non-empty")
        if any(value.startswith("--profile=") for value in argv):
            raise ValueError("--profile must be a separate argv pair")
        profile_indices = [index for index, value in enumerate(argv) if value == "--profile"]
        if len(profile_indices) != 1:
            raise ValueError("argv must select --profile exactly once")
        profile_index = profile_indices[0]
        if profile_index + 1 >= len(argv) or argv[profile_index + 1] != self.profile:
            raise ValueError("argv profile does not match configured profile")
        if not isinstance(self.cwd, str):
            raise ValueError("cwd must be an absolute directory")
        path = Path(self.cwd)
        if not path.is_absolute():
            raise ValueError("cwd must be an absolute directory")
        try:
            canonical_cwd = str(path.resolve(strict=True))
        except OSError as error:
            raise ValueError("cwd must resolve to an existing directory") from error
        if not Path(canonical_cwd).is_dir():
            raise ValueError("cwd must resolve to an existing directory")
        object.__setattr__(self, "cwd", canonical_cwd)
        env = dict(self.env)
        env_allowlist = frozenset(self.env_allowlist)
        if any(not isinstance(route, tuple) or len(route) != 2 for route in self.allowed_routes):
            raise ValueError("allowed_routes must contain explicit provider/model pairs")
        allowed_routes = frozenset((route[0], route[1]) for route in self.allowed_routes)
        object.__setattr__(self, "env", MappingProxyType(env))
        object.__setattr__(self, "env_allowlist", env_allowlist)
        object.__setattr__(self, "allowed_routes", allowed_routes)
        if not allowed_routes or any(
            any(not isinstance(value, str) or not value or "\x00" in value for value in route)
            for route in allowed_routes
        ):
            raise ValueError("allowed_routes must contain explicit provider/model pairs")
        if any(
            not isinstance(key, str) or not key or not isinstance(value, str)
            for key, value in env.items()
        ):
            raise ValueError("env must contain string names and values")
        if any("\x00" in key or "=" in key or "\x00" in value for key, value in env.items()):
            raise ValueError("env names must not contain '=' and env must not contain NUL bytes")
        if any(len(key) > 256 or len(value) > 64 * 1024 for key, value in env.items()):
            raise ValueError("env entry is too large")
        if set(env) - env_allowlist:
            raise ValueError("env contains a non-allowlisted variable")
        positive = (
            self.initialize_timeout_seconds,
            self.prompt_timeout_seconds,
            self.idle_timeout_seconds,
            self.shutdown_timeout_seconds,
            self.delivery_observer_timeout_seconds,
            self.term_timeout_seconds,
            self.kill_timeout_seconds,
            self.reap_timeout_seconds,
        )
        if any(not _is_finite_positive_number(value) for value in positive):
            raise ValueError("deadlines must be positive")
        if self.delivery_observer_timeout_seconds > _MAX_DELIVERY_OBSERVER_TIMEOUT_SECONDS:
            raise ValueError("delivery observer deadline exceeds the bounded maximum")
        bounds = (
            self.max_frame_bytes,
            self.max_buffer_bytes,
            self.max_json_depth,
            self.max_event_bytes,
            self.max_total_event_bytes,
            self.max_events,
            self.max_output_bytes,
            self.max_stderr_bytes,
        )
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in bounds
        ):
            raise ValueError("byte bounds must be positive integers")
        if self.max_event_bytes > self.max_frame_bytes:
            raise ValueError("max_event_bytes cannot exceed max_frame_bytes")
        if self.max_total_event_bytes < self.max_event_bytes:
            raise ValueError("max_total_event_bytes cannot be smaller than max_event_bytes")


class AttemptTransport:
    """One child process and one official SDK session for one Attempt.

    Process groups are a POSIX parent-process boundary only. Full production
    tree isolation (container/cgroup policy and a Windows equivalent) is a
    later deployment gate, not implemented by this transport.
    """

    def __init__(
        self,
        config: AttemptTransportConfig,
        *,
        delivery_observer: DeliveryObserver | None = None,
    ) -> None:
        self.config = config
        self._process: subprocess.Popen[bytes] | None = None
        self._closed = False
        self._failed: HarnessTransportError | None = None
        self._request_number = 0
        self._seen_response_ids: set[str | int] = set()
        self._initialized = False
        self._prompted = False
        self._session_id: str | None = None
        self._provider: str | None = None
        self._model: str | None = None
        self._reasoning_effort: str | None = None
        self._max_tokens: int | None = None
        self._stderr_count = 0
        self._stderr_hash = hashlib.sha256()
        self._pgid: int | None = None
        self._next_event_seq = 0
        self._delivery_state = DeliveryState.NOT_STARTED
        self._delivery_observer = delivery_observer
        self._observed_delivery_states: set[DeliveryState] = set()
        self._observer_execution_active = False
        self._observer_timed_out = False

    @property
    def process(self) -> subprocess.Popen[bytes] | None:
        return self._process

    @property
    def pid(self) -> int | None:
        return None if self._process is None else self._process.pid

    @property
    def stderr(self) -> str:
        """Return only a non-sensitive diagnostic summary, never child text."""
        return f"bytes={self._stderr_count} sha256={self._stderr_hash.hexdigest()}"

    @property
    def delivery_state(self) -> DeliveryState:
        """Return parent-side evidence, never a claim of durable DB state.

        With no observer this is intentionally only in-memory compatibility
        evidence; production callers must provide the persistence seam.
        """

        return self._delivery_state

    def initialize(
        self,
        *,
        provider: str,
        model: str,
        reasoning_effort: str | None = None,
        max_tokens: int | None = None,
    ) -> InitializeResult:
        self._ensure_not_terminal()
        if self._initialized:
            raise HarnessProtocolError("initialize may only be sent once")
        if (
            not isinstance(provider, str)
            or not isinstance(model, str)
            or (reasoning_effort is not None and not isinstance(reasoning_effort, str))
        ):
            raise HarnessProtocolError("invalid initialize parameters")
        if (provider, model) not in self.config.allowed_routes:
            raise HarnessProtocolError("model route is not admitted for this Attempt")
        if any(
            len(value) > self.config.max_frame_bytes
            for value in (provider, model, reasoning_effort or "")
        ):
            raise HarnessProtocolError("initialize parameter exceeds the outbound bound")
        try:
            params = InitializeParams(
                cwd=str(Path(self.config.cwd).resolve()),
                provider=provider,
                model=model,
                reasoningEffort=reasoning_effort,
                maxTokens=max_tokens,
            )
        except ValidationError:
            raise HarnessProtocolError("invalid initialize parameters") from None
        self._spawn()
        try:
            result = self._request(
                "initialize",
                params.model_dump(exclude_none=True),
                self.config.initialize_timeout_seconds,
            )
            parsed = InitializeResult.model_validate(result)
            if (
                parsed.serverInfo.name != "deepseek-harness-sdk-runtime"
                or parsed.serverInfo.version != self.config.expected_server_version
            ):
                raise HarnessProtocolError("unexpected SDK server identity")
            self._initialized = True
            self._provider = provider
            self._model = model
            self._reasoning_effort = reasoning_effort
            self._max_tokens = max_tokens
            return parsed
        except (HarnessTransportError, ValidationError) as error:
            wrapped = (
                error
                if isinstance(error, HarnessTransportError)
                else HarnessProtocolError("malformed initialize response")
            )
            self._fail(wrapped)
            raise wrapped from None

    def prompt(self, session_id: str, text: str | Sequence[dict[str, Any]]) -> PromptOutcome:
        """Submit exactly one text-only prompt and await receipt, final and idle."""

        self._ensure_not_terminal()
        if not self._initialized:
            raise HarnessProtocolError("initialize is required before prompt")
        if self._prompted:
            raise HarnessProtocolError("an Attempt accepts exactly one prompt")
        try:
            params = self._prepare_prompt(session_id, text)
        except HarnessTransportError as error:
            error.delivery_state = self._delivery_state
            self._fail(error)
            raise error from None
        canonical_prompt_text = params.contentBlocks[0].text
        self._session_id = session_id
        self._prompted = True
        request_id = self._new_request_id()
        try:
            # Once a write begins, failure may mean a partial frame reached the
            # child. Unknown is persisted before the first byte, and sent is
            # persisted only after the complete frame write.
            self._observe_delivery(DeliveryState.UNKNOWN)
            self._write(
                self._request_frame(request_id, "session/prompt", params.model_dump()),
                timeout=self.config.prompt_timeout_seconds,
            )
            self._observe_delivery(DeliveryState.SENT)
            result, events, _output = self._collect_prompt(
                request_id,
                self.config.prompt_timeout_seconds,
                self.config.idle_timeout_seconds,
                canonical_prompt_text,
            )
            receipt = SessionPromptResult.model_validate(result)
            usage, validated_output = _validate_prompt_outcome(
                events,
                receipt,
                canonical_prompt_text,
                self._provider,
                self._model,
                self._reasoning_effort,
                self._max_tokens,
                self.config.max_output_bytes,
            )
            end = next(event for event in events if event.type == "turn/end")
            reason = (end.data or {}).get("reason", {})
            if reason.get("kind") != "completed":
                raise HarnessTurnError(
                    reason,
                    message_id=receipt.messageId,
                    usage=usage,
                    output=validated_output,
                )
            if usage is None:
                raise HarnessProtocolError("completed prompt has no validated usage")
            candidate = PromptOutcome(
                messageId=receipt.messageId,
                usage=usage,
                output=validated_output,
            )
            # A candidate cannot be published until the one-Attempt runtime
            # has completed the official shutdown handshake, reached EOF and
            # been reaped.  This closes the idle-to-late-output race.
            self.shutdown()
            return candidate
        except (HarnessTransportError, ValidationError) as error:
            wrapped = (
                error
                if isinstance(error, HarnessTransportError)
                else HarnessProtocolError("malformed prompt outcome")
            )
            wrapped.delivery_state = self._delivery_state
            self._fail(wrapped)
            raise wrapped from None

    def _prepare_prompt(
        self, session_id: str, text: str | Sequence[dict[str, Any]]
    ) -> SessionPromptParams:
        if not isinstance(session_id, str):
            raise HarnessProtocolError("invalid prompt parameters")
        if isinstance(text, str):
            block: dict[str, Any] = {"type": "text", "text": text}
        elif isinstance(text, (list, tuple)) and len(text) == 1 and isinstance(text[0], dict):
            block = text[0]
        else:
            raise HarnessProtocolError("an Attempt accepts exactly one text block")
        if block.get("type") != "text" or not isinstance(block.get("text"), str):
            raise HarnessProtocolError("an Attempt accepts exactly one text block")
        raw_text = block["text"]
        if (
            len(session_id) > self.config.max_frame_bytes
            or len(raw_text) > self.config.max_frame_bytes
        ):
            raise HarnessProtocolError("prompt parameter exceeds the outbound bound")
        try:
            if len(session_id.encode("utf-8")) > self.config.max_frame_bytes:
                raise HarnessProtocolError("session id exceeds the outbound byte bound")
            if len(raw_text.encode("utf-8")) > self.config.max_frame_bytes:
                raise HarnessProtocolError("prompt text exceeds the outbound byte bound")
        except UnicodeEncodeError:
            raise HarnessProtocolError("prompt parameters are not valid UTF-8") from None
        try:
            return SessionPromptParams.model_validate(
                {"sessionId": session_id, "contentBlocks": [block]}
            )
        except ValidationError:
            raise HarnessProtocolError("invalid prompt parameters") from None

    def _observe_delivery(self, state: DeliveryState) -> None:
        """Record one bounded delivery transition through the optional seam.

        The callback deliberately receives only a closed enum.  State is
        updated *after* callback success so an unsuccessful durable write
        cannot be mistaken for evidence already stored in the database.
        """

        previous = _DELIVERY_OBSERVER_PREVIOUS.get(state)
        if (
            previous is None
            or state in self._observed_delivery_states
            or self._delivery_state is not previous
        ):
            raise HarnessDeliveryError(state)
        observer = self._delivery_observer
        if observer is not None:
            if self._observer_execution_active or self._observer_timed_out:
                raise HarnessDeliveryError(state)
            self._observer_execution_active = True
            try:
                completed, succeeded = _DELIVERY_OBSERVER_POOL.submit(observer, state)
            except HarnessDeliveryCapacityError:
                self._observer_execution_active = False
                raise
            observer_timeout = min(
                self.config.delivery_observer_timeout_seconds,
                self.config.prompt_timeout_seconds,
            )
            if not completed.wait(observer_timeout):
                self._observer_timed_out = True
                self._observer_execution_active = False
                raise HarnessDeliveryTimeoutError(state) from None
            self._observer_execution_active = False
            if not succeeded[0]:
                raise HarnessDeliveryError(state) from None
        self._observed_delivery_states.add(state)
        self._delivery_state = state

    def shutdown(self) -> None:
        """Use official shutdown, then independently wait/reap the child."""

        if self._process is None:
            self._closed = True
            return
        if self._closed:
            return
        try:
            if self._process.poll() is not None:
                raise HarnessProcessError("runtime exited before the shutdown handshake")
            request_id = self._new_request_id()
            self._write(
                self._request_frame(request_id, "shutdown"),
                timeout=self.config.shutdown_timeout_seconds,
            )
            _result = self._collect_response(
                request_id,
                self.config.shutdown_timeout_seconds,
                allow_notifications=False,
                drain_after_response=True,
            )
            if _result != {}:
                raise HarnessProtocolError("shutdown result must be an empty object")
            self._close_stdin()
            self._wait(self.config.shutdown_timeout_seconds, "shutdown")
            self.reap(self.config.reap_timeout_seconds)
            if self._require_process().returncode != 0:
                raise HarnessProcessError("runtime exited nonzero after shutdown")
            if self._process_group_exists():
                self._terminate_ladder()
                raise HarnessProcessError("runtime left descendants after shutdown")
            self._pgid = None
            self._close_pipes()
            self._closed = True
        except HarnessTransportError:
            self._terminate_ladder()
            self._closed = True
            raise

    def terminate(self) -> None:
        """Local Attempt-scoped TERM; this is not an SDK cancel method."""
        if self._closed:
            return
        self._signal_group(signal.SIGTERM)

    def kill(self) -> None:
        """Local Attempt-scoped KILL."""
        if self._closed:
            return
        self._signal_group(signal.SIGKILL)

    def reap(self, timeout_seconds: float | None = None) -> None:
        """Prove child exit; never leave an Attempt child behind."""

        process = self._process
        if process is None:
            return
        timeout = self.config.reap_timeout_seconds if timeout_seconds is None else timeout_seconds
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise HarnessTimeoutError("runtime reap deadline exceeded") from error

    def close(self) -> None:
        """Best-effort bounded cleanup for context-manager use."""
        if self._closed:
            return
        if self._process is None:
            self._closed = True
            return
        try:
            self.shutdown()
        except HarnessTransportError:
            if not self._closed:
                self._terminate_ladder()
                self._closed = True

    def __enter__(self) -> AttemptTransport:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    def _spawn(self) -> None:
        if self._process is not None:
            raise HarnessProtocolError("Attempt child already exists")
        try:
            self._process = subprocess.Popen(
                list(self.config.argv),
                cwd=self.config.cwd,
                env=dict(self.config.env),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                start_new_session=True,
            )
        except (OSError, ValueError) as error:
            raise HarnessProcessError(f"failed to spawn runtime: {error}") from error
        # start_new_session makes the child a session and process-group leader,
        # so the group id is the pid by contract.  Reading it back races a
        # short-lived leader that forked a descendant and then exited.
        self._pgid = self._process.pid

    def _ensure_not_terminal(self) -> None:
        if self._failed is not None:
            raise self._failed
        if self._closed:
            raise HarnessTransportError("transport is closed")

    def _new_request_id(self) -> str:
        self._request_number += 1
        return f"attempt-{self._request_number}"

    @staticmethod
    def _request_frame(
        request_id: str, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        frame: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            frame["params"] = params
        return frame

    def _request(self, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
        request_id = self._new_request_id()
        self._write(self._request_frame(request_id, method, params), timeout=timeout)
        return self._collect_response(request_id, timeout, allow_notifications=False)

    def _collect_response(
        self,
        request_id: str,
        timeout: float,
        *,
        allow_notifications: bool = True,
        drain_after_response: bool = False,
    ) -> dict[str, Any]:
        result, _, _ = self._collect(
            request_id,
            timeout,
            None,
            allow_notifications,
            drain_after_response=drain_after_response,
        )
        return result

    def _collect_prompt(
        self, request_id: str, prompt_timeout: float, idle_timeout: float, prompt_text: str
    ) -> tuple[dict[str, Any], list[SessionEvent], list[TextBlock]]:
        def acknowledge_response(result: dict[str, Any], events: list[SessionEvent]) -> None:
            try:
                receipt = SessionPromptResult.model_validate(result)
            except ValidationError:
                raise HarnessProtocolError("malformed prompt response") from None
            _validate_prompt_receipt(events, receipt, prompt_text)
            self._observe_delivery(DeliveryState.ACKNOWLEDGED)

        return self._collect(
            request_id,
            prompt_timeout,
            idle_timeout,
            True,
            prompt_text,
            response_hook=acknowledge_response,
        )

    def _collect(
        self,
        request_id: str,
        first_timeout: float,
        idle_timeout: float | None,
        allow_notifications: bool = True,
        prompt_text: str | None = None,
        drain_after_response: bool = False,
        response_hook: Callable[[dict[str, Any], list[SessionEvent]], None] | None = None,
    ) -> tuple[dict[str, Any], list[SessionEvent], list[TextBlock]]:
        response: dict[str, Any] | None = None
        events: list[SessionEvent] = []
        output: list[TextBlock] = []
        assistant_usage: TokenUsage | None = None
        chunk_usage: TokenUsage | None = None
        saw_running = False
        saw_idle = False
        receipt_seen = False
        turn_start_seen = False
        step_start_seen = False
        step_end_seen = False
        user_message_seen = False
        session_title_seen = False
        request_header_seen = False
        request_context_seen = False
        model_selection_seen = False
        finish_seen = False
        assistant_seen = False
        turn_end_seen = False
        event_count = 0
        total_event_bytes = 0
        output_bytes = 0
        deadline = time.monotonic() + first_timeout
        idle_deadline: float | None = None
        stdout_buffer = bytearray()
        selector = selectors.DefaultSelector()
        process = self._require_process()
        assert process.stdout is not None and process.stderr is not None
        try:
            os.set_blocking(process.stdout.fileno(), False)
            os.set_blocking(process.stderr.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        except OSError as error:
            selector.close()
            raise HarnessProcessError("failed to register runtime pipes") from error
        try:
            draining = False
            while response is None or (idle_timeout is not None and not saw_idle) or draining:
                now = time.monotonic()
                active_deadline = (
                    idle_deadline
                    if response is not None and idle_deadline is not None
                    else deadline
                )
                remaining = active_deadline - now
                if remaining <= 0:
                    phase = "idle" if response is not None else "request"
                    raise HarnessTimeoutError(f"runtime {phase} deadline exceeded")
                if process.poll() is not None and not selector.get_map():
                    if draining:
                        break
                    raise HarnessProcessError(
                        "runtime exited before required response/idle boundary"
                    )
                try:
                    ready = selector.select(min(remaining, 0.05))
                except OSError as error:
                    raise HarnessProcessError("failed to poll runtime pipes") from error
                if not ready:
                    if process.poll() is not None:
                        if draining:
                            # A child may have exited while a descendant still
                            # holds one of the inherited pipes. Probe every fd
                            # until EOF; never mistake leader exit for drain.
                            for key in list(selector.get_map().values()):
                                try:
                                    probe = os.read(key.fd, 64 * 1024)
                                except BlockingIOError:
                                    continue
                                except OSError as error:
                                    raise HarnessProcessError(
                                        "failed to drain runtime pipe"
                                    ) from error
                                if not probe:
                                    selector.unregister(key.fileobj)
                                    continue
                                if key.data == "stdout":
                                    raise HarnessProtocolError(
                                        "late stdout after shutdown response"
                                    )
                                self._consume_stderr(probe)
                            if not selector.get_map():
                                break
                            continue
                        raise HarnessProcessError(
                            "runtime exited before required response/idle boundary"
                        )
                    continue
                for key, _ in ready:
                    try:
                        chunk = os.read(key.fd, 64 * 1024)
                    except OSError as error:
                        raise HarnessProcessError("failed to read runtime pipe") from error
                    if key.data == "stderr":
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        self._consume_stderr(chunk)
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        if stdout_buffer:
                            raise HarnessProtocolError("runtime ended with a broken JSON frame")
                        continue
                    if draining:
                        raise HarnessProtocolError("late stdout after shutdown response")
                    stdout_buffer.extend(chunk)
                    if len(stdout_buffer) > self.config.max_buffer_bytes:
                        raise HarnessProtocolError("stdout buffer limit exceeded")
                    if (
                        b"\n" not in stdout_buffer
                        and len(stdout_buffer) > self.config.max_frame_bytes
                    ):
                        raise HarnessProtocolError("broken or oversized JSON frame")
                    while b"\n" in stdout_buffer:
                        line, _, remainder = stdout_buffer.partition(b"\n")
                        stdout_buffer = bytearray(remainder)
                        if not line:
                            raise HarnessProtocolError("blank stdout frame")
                        if len(line) > self.config.max_frame_bytes:
                            raise HarnessProtocolError("JSON frame limit exceeded")
                        wire_frame_bytes = len(line)
                        frame = self._parse_frame(bytes(line))
                        if "id" in frame:
                            if frame["id"] != request_id:
                                raise HarnessProtocolError(
                                    "response id does not match outstanding request"
                                )
                            if frame["id"] in self._seen_response_ids:
                                raise HarnessProtocolError("duplicate response id")
                            self._seen_response_ids.add(frame["id"])
                            if "error" in frame:
                                raise HarnessProtocolError("runtime returned a JSON-RPC error")
                            result = frame.get("result")
                            if not isinstance(result, dict):
                                raise HarnessProtocolError("response result must be an object")
                            if idle_timeout is not None and not receipt_seen:
                                raise HarnessProtocolError(
                                    "prompt response preceded its inbox receipt"
                                )
                            if response_hook is not None:
                                response_hook(result, events)
                            response = result
                            if drain_after_response:
                                draining = True
                                idle_deadline = (
                                    time.monotonic() + self.config.shutdown_timeout_seconds
                                )
                                continue
                            if idle_timeout is not None and not saw_idle:
                                idle_deadline = time.monotonic() + idle_timeout
                            continue
                        method = frame["method"]
                        if method not in NOTIFICATION_MODELS:
                            raise HarnessProtocolError(f"unknown notification method: {method}")
                        if not allow_notifications:
                            raise HarnessProtocolError("notification received during shutdown")
                        params = frame.get("params")
                        if not isinstance(params, dict):
                            raise HarnessProtocolError("notification params must be an object")
                        try:
                            notification = NOTIFICATION_MODELS[method].model_validate(params)
                        except ValidationError as error:
                            raise HarnessProtocolError(
                                f"malformed {method} notification"
                            ) from error
                        if method.startswith("subagent."):
                            raise HarnessProtocolError("subagent notifications are disabled")
                        if isinstance(notification, SessionEventNotification):
                            self._accept_session(notification.sessionId)
                            if saw_idle:
                                raise HarnessProtocolError("event received after idle status")
                            if turn_end_seen:
                                raise HarnessProtocolError("event received after turn/end")
                            event = notification.event
                            if event.seq != self._next_event_seq:
                                raise HarnessProtocolError(
                                    "session event sequence gap or duplicate"
                                )
                            self._next_event_seq += 1
                            event_count += 1
                            # Resource accounting is based on attacker-owned
                            # wire bytes, including the JSON-RPC envelope and
                            # whitespace, not a smaller normalized re-encoding.
                            event_bytes = wire_frame_bytes
                            total_event_bytes += event_bytes
                            if event_count > self.config.max_events:
                                raise HarnessProtocolError("event count limit exceeded")
                            if event_bytes > self.config.max_event_bytes:
                                raise HarnessProtocolError("session event limit exceeded")
                            if total_event_bytes > self.config.max_total_event_bytes:
                                raise HarnessProtocolError("session event buffer limit exceeded")
                            events.append(event)
                            if (
                                event.type == "agent/inbox/spliced"
                                and prompt_text is not None
                                and _is_candidate_receipt(event)
                            ):
                                if receipt_seen:
                                    raise HarnessProtocolError("duplicate inbox receipt")
                                if event_count != 1 or saw_running:
                                    raise HarnessProtocolError(
                                        "inbox receipt must be the first prompt event"
                                    )
                                receipt_seen = True
                            elif event.type == "agent/inbox/spliced":
                                if not receipt_seen or not turn_start_seen:
                                    raise HarnessProtocolError(
                                        "inbox mutation preceded receipt or turn/start"
                                    )
                            if event.type == "turn/start":
                                if not receipt_seen or not saw_running:
                                    raise HarnessProtocolError(
                                        "turn/start preceded receipt or running status"
                                    )
                                if turn_start_seen:
                                    raise HarnessProtocolError("duplicate turn/start")
                                turn_start_seen = True
                            if event.type == "step/start":
                                if not turn_start_seen or step_start_seen or step_end_seen:
                                    raise HarnessProtocolError(
                                        "duplicate or out-of-order step/start"
                                    )
                                step_start_seen = True
                            if event.type == "user/message":
                                if not step_start_seen or step_end_seen or user_message_seen:
                                    raise HarnessProtocolError(
                                        "duplicate or out-of-order user/message"
                                    )
                                user_message_seen = True
                            if event.type == "session/title":
                                if not user_message_seen or session_title_seen:
                                    raise HarnessProtocolError(
                                        "duplicate or out-of-order session/title"
                                    )
                                session_title_seen = True
                            if event.type == "model/selection":
                                if (
                                    not user_message_seen
                                    or request_header_seen
                                    or model_selection_seen
                                ):
                                    raise HarnessProtocolError(
                                        "duplicate or out-of-order model/selection"
                                    )
                                selection = event.data or {}
                                if (
                                    selection.get("provider") != self._provider
                                    or selection.get("model") != self._model
                                ):
                                    raise HarnessProtocolError(
                                        "model selection does not match initialize"
                                    )
                                model_selection_seen = True
                            if event.type == "request/header":
                                if (
                                    not step_start_seen
                                    or not user_message_seen
                                    or step_end_seen
                                    or request_header_seen
                                ):
                                    raise HarnessProtocolError(
                                        "duplicate or out-of-order request/header"
                                    )
                                config = ((event.data or {}).get("header") or {}).get("config")
                                if not isinstance(config, dict) or (
                                    config.get("provider") != self._provider
                                    or config.get("model") != self._model
                                ):
                                    raise HarnessProtocolError(
                                        "request header does not match initialize"
                                    )
                                if self._reasoning_effort is not None and (
                                    config.get("reasoningEffort") != self._reasoning_effort
                                ):
                                    raise HarnessProtocolError(
                                        "request reasoning effort does not match initialize"
                                    )
                                if self._max_tokens is not None and (
                                    config.get("maxTokens") != self._max_tokens
                                ):
                                    raise HarnessProtocolError(
                                        "request token limit does not match initialize"
                                    )
                                request_header_seen = True
                            if event.type == "request/context":
                                if not request_header_seen or request_context_seen or step_end_seen:
                                    raise HarnessProtocolError(
                                        "duplicate or out-of-order request/context"
                                    )
                                context = event.data or {}
                                if (
                                    context.get("provider") != self._provider
                                    or context.get("model") != self._model
                                ):
                                    raise HarnessProtocolError(
                                        "request context does not match initialize"
                                    )
                                request_context_seen = True
                            if event.type == "assistant/chunk":
                                if (
                                    not request_header_seen
                                    or not request_context_seen
                                    or not step_start_seen
                                    or step_end_seen
                                    or assistant_seen
                                    or finish_seen
                                ):
                                    raise HarnessProtocolError(
                                        "assistant chunk is outside the active model stream"
                                    )
                                chunk = (event.data or {}).get("chunk", {})
                                if chunk.get("type") == "finish":
                                    finish_seen = True
                            if event.type == "assistant/message":
                                if idle_timeout is not None and not receipt_seen:
                                    raise HarnessProtocolError(
                                        "assistant/message preceded inbox receipt"
                                    )
                                if not saw_running:
                                    raise HarnessProtocolError(
                                        "assistant/message preceded running status"
                                    )
                                if assistant_seen:
                                    raise HarnessProtocolError("duplicate assistant/message")
                                if (
                                    not step_start_seen
                                    or step_end_seen
                                    or not request_header_seen
                                    or not request_context_seen
                                    or not finish_seen
                                ):
                                    raise HarnessProtocolError(
                                        "assistant/message preceded a complete model stream"
                                    )
                                assistant_seen = True
                                raw_usage = (event.data or {}).get("usage")
                                if raw_usage is not None:
                                    assistant_usage = TokenUsage.model_validate(raw_usage)
                                    if chunk_usage is not None and chunk_usage != assistant_usage:
                                        raise HarnessProtocolError(
                                            "assistant and chunk usage differ"
                                        )
                                message = (event.data or {}).get("message", {})
                                blocks = (
                                    message.get("content", []) if isinstance(message, dict) else []
                                )
                                try:
                                    output_bytes += len(
                                        json.dumps(
                                            blocks, separators=(",", ":"), allow_nan=False
                                        ).encode()
                                    )
                                except (TypeError, ValueError) as error:
                                    raise HarnessProtocolError(
                                        "assistant output is not canonical JSON"
                                    ) from error
                                if output_bytes > self.config.max_output_bytes:
                                    raise HarnessProtocolError("assistant output limit exceeded")
                                output = [
                                    TextBlock.model_validate(block)
                                    for block in blocks
                                    if isinstance(block, dict) and block.get("type") == "text"
                                ]
                            if (
                                event.type == "assistant/chunk"
                                and (event.data or {}).get("chunk", {}).get("type") == "usage"
                            ):
                                if chunk_usage is not None:
                                    raise HarnessProtocolError("duplicate assistant usage chunk")
                                chunk_usage = TokenUsage.model_validate(
                                    (event.data or {})["chunk"]["usage"]
                                )
                                if assistant_usage is not None and chunk_usage != assistant_usage:
                                    raise HarnessProtocolError("assistant and chunk usage differ")
                            if event.type == "step/end":
                                if not step_start_seen or step_end_seen:
                                    raise HarnessProtocolError("duplicate or out-of-order step/end")
                                if assistant_seen and not finish_seen:
                                    raise HarnessProtocolError("step/end preceded stream finish")
                                step_end_seen = True
                            if event.type == "turn/end":
                                if idle_timeout is not None and not receipt_seen:
                                    raise HarnessProtocolError("turn/end preceded inbox receipt")
                                if not saw_running:
                                    raise HarnessProtocolError("turn/end preceded running status")
                                if turn_end_seen:
                                    raise HarnessProtocolError("duplicate turn/end")
                                if not turn_start_seen:
                                    raise HarnessProtocolError("turn/end preceded turn/start")
                                if step_start_seen and not step_end_seen:
                                    raise HarnessProtocolError("turn/end preceded step/end")
                                turn_end_seen = True
                        elif isinstance(notification, SessionStatusNotification):
                            self._accept_session(notification.sessionId)
                            if saw_idle:
                                raise HarnessProtocolError("status received after idle status")
                            if notification.status == "running":
                                if (
                                    not receipt_seen
                                    or saw_running
                                    or saw_idle
                                    or turn_start_seen
                                    or assistant_seen
                                    or turn_end_seen
                                ):
                                    raise HarnessProtocolError(
                                        "duplicate or out-of-order running status"
                                    )
                                saw_running = True
                            else:
                                if not saw_running:
                                    raise HarnessProtocolError("idle status without running status")
                                if saw_idle:
                                    raise HarnessProtocolError("duplicate idle status")
                                if idle_timeout is not None and not receipt_seen:
                                    raise HarnessProtocolError("idle status preceded inbox receipt")
                                if idle_timeout is not None and not turn_end_seen:
                                    raise HarnessProtocolError("idle status preceded turn/end")
                                saw_idle = True
                                if draining:
                                    draining = False
                                    if selector.get_map():
                                        # For shutdown, any subsequent stdout is checked by the
                                        # next read; stderr is drained until EOF.
                                        pass
        finally:
            selector.close()
        if stdout_buffer:
            raise HarnessProtocolError("runtime left a partial JSON frame")
        if response is None:
            raise HarnessProcessError("runtime did not return a response")
        if idle_timeout is not None and not saw_idle:
            raise HarnessTimeoutError("runtime idle deadline exceeded")
        return response, events, output

    def _accept_session(self, session_id: str) -> None:
        if self._session_id is None or session_id != self._session_id:
            raise HarnessProtocolError("notification belongs to the wrong session")

    def _parse_frame(self, raw: bytes) -> dict[str, Any]:
        if len(raw) > self.config.max_frame_bytes:
            raise HarnessProtocolError("JSON frame limit exceeded")
        _validate_json_nesting(raw, self.config.max_json_depth)
        try:
            value = json.loads(
                raw.decode("utf-8"),
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
                object_pairs_hook=_unique_object_pairs,
            )
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError, RecursionError) as error:
            raise HarnessProtocolError("invalid JSON frame") from error
        if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
            raise HarnessProtocolError("invalid JSON-RPC envelope")
        keys = set(value)
        if "method" in value:
            method = value.get("method")
            if not isinstance(method, str) or method not in REQUEST_METHODS | set(
                NOTIFICATION_MODELS
            ):
                raise HarnessProtocolError("unknown or malformed method")
            if "id" in value:
                raise HarnessProtocolError("child request frames are not allowed")
            if method in REQUEST_METHODS:
                raise HarnessProtocolError("child request frames are not allowed")
            allowed = {"jsonrpc", "method", "params"}
            if keys - allowed:
                raise HarnessProtocolError("unknown notification field")
            if "params" not in value:
                raise HarnessProtocolError("notification params missing")
            return value
        if "id" not in value:
            raise HarnessProtocolError("frame is neither response nor notification")
        response_id = value["id"]
        if isinstance(response_id, bool) or not isinstance(response_id, (str, int)):
            raise HarnessProtocolError("invalid response id")
        if keys not in ({"jsonrpc", "id", "result"}, {"jsonrpc", "id", "error"}):
            raise HarnessProtocolError("unknown response field")
        if "error" in value and (
            not isinstance(value["error"], dict)
            or set(value["error"]) - {"code", "message", "data"}
            or not isinstance(value["error"].get("code"), int)
            or isinstance(value["error"].get("code"), bool)
            or not isinstance(value["error"].get("message"), str)
        ):
            raise HarnessProtocolError("malformed JSON-RPC error")
        return value

    def _write(self, frame: dict[str, Any], *, timeout: float) -> None:
        process = self._require_process()
        if process.stdin is None:
            raise HarnessProcessError("runtime stdin is unavailable")
        try:
            payload = (json.dumps(frame, separators=(",", ":"), ensure_ascii=False) + "\n").encode(
                "utf-8"
            )
        except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as error:
            raise HarnessProtocolError("outbound frame is not valid UTF-8 JSON") from error
        if len(payload) - 1 > self.config.max_frame_bytes:
            raise HarnessProtocolError("outbound JSON frame limit exceeded")
        end = time.monotonic() + timeout
        fd = process.stdin.fileno()
        offset = 0
        try:
            os.set_blocking(fd, False)
            while offset < len(payload):
                try:
                    offset += os.write(fd, payload[offset:])
                    continue
                except BlockingIOError:
                    remaining = end - time.monotonic()
                    if remaining <= 0:
                        raise HarnessTimeoutError("runtime stdin write deadline exceeded") from None
                    _, writable, _ = select.select([], [fd], [], remaining)
                    if not writable:
                        raise HarnessTimeoutError("runtime stdin write deadline exceeded") from None
        except HarnessTransportError:
            raise
        except (BrokenPipeError, OSError) as error:
            raise HarnessProcessError("failed to write runtime stdin") from error

    def _consume_stderr(self, chunk: bytes) -> None:
        if self._stderr_count + len(chunk) > self.config.max_stderr_bytes:
            raise HarnessProtocolError("stderr limit exceeded")
        self._stderr_count += len(chunk)
        self._stderr_hash.update(chunk)

    def _close_stdin(self) -> None:
        process = self._process
        if process is not None and process.stdin is not None:
            with suppress(OSError):
                process.stdin.close()

    def _close_pipes(self) -> None:
        """Close reaped child pipes exactly once; never retain per-Attempt FDs."""

        process = self._process
        if process is None:
            return
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None and not pipe.closed:
                with suppress(OSError):
                    pipe.close()

    def _wait(self, timeout: float, phase: str) -> None:
        process = self._require_process()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise HarnessTimeoutError(f"runtime {phase} deadline exceeded") from error

    def _signal_group(self, signal_number: int) -> None:
        if self._pgid is None:
            return
        try:
            os.killpg(self._pgid, signal_number)
        except ProcessLookupError:
            return
        except PermissionError as error:
            raise HarnessProcessError("permission denied while signaling runtime group") from error

    def _process_group_exists(self) -> bool:
        if self._pgid is None:
            return False
        try:
            os.killpg(self._pgid, 0)
        except ProcessLookupError:
            return False
        except PermissionError as error:
            raise HarnessProcessError("cannot verify runtime process group") from error
        return True

    def _wait_process_group(self, timeout: float, phase: str) -> None:
        process = self._require_process()
        deadline = time.monotonic() + timeout
        while True:
            process.poll()
            verification_error: HarnessProcessError | None = None
            try:
                group_exists = self._process_group_exists()
            except HarnessProcessError as error:
                # Some kernels briefly report EPERM while ownership changes
                # during process teardown. Never treat it as clean; retry only
                # within the same hard deadline and fail if it persists.
                verification_error = error
                group_exists = True
            if not group_exists:
                self.reap(max(deadline - time.monotonic(), 0.001))
                self._pgid = None
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if verification_error is not None:
                    raise verification_error
                raise HarnessTimeoutError(f"runtime {phase} process-group deadline exceeded")
            time.sleep(min(0.01, remaining))

    def _terminate_ladder(self) -> None:
        if self._process is None:
            return
        self.terminate()
        try:
            self._wait_process_group(self.config.term_timeout_seconds, "TERM")
            self._close_pipes()
            return
        except HarnessTimeoutError:
            pass
        self.kill()
        self._wait_process_group(self.config.kill_timeout_seconds, "KILL")
        self.reap(self.config.reap_timeout_seconds)
        self._close_pipes()

    def _fail(self, error: HarnessTransportError) -> None:
        self._failed = error
        try:
            self._terminate_ladder()
        except HarnessTransportError as cleanup_error:
            # Preserve the provider/delivery/accounting classification.  The
            # handle can inspect the sanitized cleanup type and retry close;
            # cleanup failure must not replace the original Attempt outcome.
            error.cleanup_error_type = type(cleanup_error).__name__
            return
        self._closed = True

    def _require_process(self) -> subprocess.Popen[bytes]:
        if self._process is None:
            raise HarnessProcessError("runtime has not been started")
        return self._process


# Concise aliases for callers that use the H1.5 terminology.
DeepSeekHarnessTransport = AttemptTransport
DeepSeekHarnessTransportConfig = AttemptTransportConfig


def _is_candidate_receipt(event: SessionEvent) -> bool:
    if event.type != "agent/inbox/spliced" or not isinstance(event.data, dict):
        return False
    data = event.data
    inserted = data.get("inserted")
    return (
        data.get("target") == "next-turn"
        and data.get("start") == 0
        and isinstance(inserted, list)
        and len(inserted) == 1
        and data.get("removedCount", 0) == 0
        and "outcome" not in data
    )


def _validate_prompt_receipt(
    events: Sequence[SessionEvent], receipt: SessionPromptResult, prompt_text: str
) -> SessionEvent:
    """Validate the one receipt that can acknowledge a submitted prompt."""

    receipt_events = [event for event in events if _is_candidate_receipt(event)]
    if len(receipt_events) != 1:
        raise HarnessProtocolError("prompt must have exactly one matching inbox receipt")
    receipt_event = receipt_events[0]
    inserted = (receipt_event.data or {}).get("inserted", [])
    message = inserted[0] if inserted else {}
    if message.get("id") != receipt.messageId:
        raise HarnessProtocolError("inbox receipt message id does not match prompt response")
    if message.get("role") != "user" or message.get("source") != {"kind": "user"}:
        raise HarnessProtocolError("inbox receipt is not a direct user message")
    if message.get("content") != [{"type": "text", "text": prompt_text}]:
        raise HarnessProtocolError("inbox receipt content does not match submitted prompt")
    return receipt_event


def _sanitize_turn_reason(reason: dict[str, Any]) -> dict[str, Any]:
    """Retain classification/accounting fields without provider-controlled text."""

    kind = reason.get("kind")
    sanitized: dict[str, Any] = {"kind": kind if isinstance(kind, str) else "unknown"}
    if kind == "error" and isinstance(reason.get("error"), dict):
        failure = reason["error"]
        safe_failure = {
            key: failure[key]
            for key in ("code", "status", "providerRetryAfterMs", "requestId")
            if key in failure
        }
        sanitized["error"] = safe_failure
    elif kind == "aborted" and isinstance(reason.get("reason"), dict):
        nested_kind = reason["reason"].get("kind")
        sanitized["reason"] = {"kind": nested_kind if isinstance(nested_kind, str) else "unknown"}
    return sanitized


def _validate_prompt_outcome(
    events: list[SessionEvent],
    receipt: SessionPromptResult,
    prompt_text: str,
    provider: str | None,
    model: str | None,
    reasoning_effort: str | None,
    max_tokens: int | None,
    max_output_bytes: int,
) -> tuple[TokenUsage | None, list[TextBlock]]:
    receipt_event = _validate_prompt_receipt(events, receipt, prompt_text)
    receipt_events = [event for event in events if _is_candidate_receipt(event)]
    # ``_validate_prompt_receipt`` establishes this cardinality.  Keeping the
    # local list makes the later event-order checks explicit and avoids ever
    # trusting a transport-side in-memory acknowledgement as validation.
    assert len(receipt_events) == 1
    inserted = (receipt_event.data or {}).get("inserted", [])
    message = inserted[0]

    starts = [event for event in events if event.type == "turn/start"]
    ends = [event for event in events if event.type == "turn/end"]
    if len(starts) != 1 or len(ends) != 1:
        raise HarnessProtocolError("prompt must have exactly one turn/start and turn/end")
    start = starts[0]
    end = ends[0]
    start_data = start.data or {}
    end_data = end.data or {}
    turn = start_data.get("turn")
    if turn != 1 or end_data.get("turn") != turn:
        raise HarnessProtocolError("fresh Attempt must complete one matching turn")
    reason = end_data.get("reason", {})

    removals = [
        event
        for event in events
        if event.type == "agent/inbox/spliced" and not _is_candidate_receipt(event)
    ]
    if len(removals) != 1 or (removals[0].data or {}) != {
        "target": "next-turn",
        "start": 0,
        "removedCount": 1,
        "inserted": [],
    }:
        raise HarnessProtocolError("prompt receipt must be consumed exactly once")
    receipt_index = events.index(receipt_event)
    start_index = events.index(start)
    removal_index = events.index(removals[0])
    end_index = events.index(end)
    if not receipt_index < start_index < removal_index < end_index:
        raise HarnessProtocolError("prompt receipt and turn boundaries are out of order")

    step_starts = [event for event in events if event.type == "step/start"]
    step_ends = [event for event in events if event.type == "step/end"]
    users = [event for event in events if event.type == "user/message"]
    headers = [event for event in events if event.type == "request/header"]
    contexts = [event for event in events if event.type == "request/context"]
    assistants = [event for event in events if event.type == "assistant/message"]
    chunks = [event for event in events if event.type == "assistant/chunk"]
    titles = [event for event in events if event.type == "session/title"]
    selections = [event for event in events if event.type == "model/selection"]
    if len(assistants) > 1 or len(titles) > 1 or len(selections) > 1:
        raise HarnessProtocolError("prompt emitted duplicate singleton events")

    completed = reason.get("kind") == "completed"
    if completed and any(
        len(group) != 1 for group in (step_starts, step_ends, users, headers, contexts, assistants)
    ):
        raise HarnessProtocolError("completed prompt is missing its one-step lifecycle")
    if not step_starts:
        if completed or any((step_ends, users, headers, contexts, assistants, chunks)):
            raise HarnessProtocolError("turn without a step contains step-scoped events")
        return None, []
    if len(step_starts) != 1 or len(step_ends) != 1:
        raise HarnessProtocolError("prompt must not leave a partial or repeated step")

    step_start = step_starts[0]
    step_end = step_ends[0]
    step_start_data = step_start.data or {}
    step_end_data = step_end.data or {}
    step = step_start_data.get("step")
    if (
        step != 1
        or step_start_data.get("turn") != turn
        or step_end_data != {"turn": turn, "step": step}
    ):
        raise HarnessProtocolError("fresh Attempt must execute one matching step")
    if not removal_index < events.index(step_start) < events.index(step_end) < end_index:
        raise HarnessProtocolError("step boundaries are out of order")

    if users:
        if len(users) != 1:
            raise HarnessProtocolError("prompt must enter the model surface exactly once")
        user = users[0]
        if (user.data or {}) != message:
            raise HarnessProtocolError("user/message does not match the accepted prompt")
        if not events.index(step_start) < events.index(user) < events.index(step_end):
            raise HarnessProtocolError("user/message is outside its step")
    if headers:
        if len(headers) != 1:
            raise HarnessProtocolError("prompt emitted duplicate request/header events")
        config = ((headers[0].data or {}).get("header") or {}).get("config") or {}
        if config.get("provider") != provider or config.get("model") != model:
            raise HarnessProtocolError("request/header route does not match initialize")
        if reasoning_effort is not None and config.get("reasoningEffort") != reasoning_effort:
            raise HarnessProtocolError("request/header reasoning effort changed")
        if max_tokens is not None and config.get("maxTokens") != max_tokens:
            raise HarnessProtocolError("request/header token limit changed")
    if contexts:
        if len(contexts) != 1:
            raise HarnessProtocolError("prompt emitted duplicate request/context events")
        context = contexts[0].data or {}
        if context.get("provider") != provider or context.get("model") != model:
            raise HarnessProtocolError("request/context route does not match initialize")
    has_model_stream = bool(chunks or assistants)
    if has_model_stream and any(len(group) != 1 for group in (users, headers, contexts)):
        raise HarnessProtocolError("model stream is missing its one-request lifecycle")
    if has_model_stream and not (
        events.index(users[0])
        < events.index(headers[0])
        < events.index(contexts[0])
        < events.index(step_end)
    ):
        raise HarnessProtocolError("model request events are out of order")
    if has_model_stream:
        header_data = headers[0].data or {}
        if header_data.get("reason") != "initial" or "startsSeries" in header_data:
            raise HarnessProtocolError("fresh Attempt requires one initial request header")
        if titles:
            title_data = titles[0].data or {}
            if title_data.get("messageSeqs") != [users[0].seq] or not (
                events.index(users[0]) < events.index(titles[0]) < events.index(headers[0])
            ):
                raise HarnessProtocolError("session title does not cite its prompt message")
        if selections and not (
            events.index(users[0]) < events.index(selections[0]) < events.index(headers[0])
        ):
            raise HarnessProtocolError("model selection is out of order")

    if not chunks:
        if completed or assistants or reason.get("kind") == "max-tokens":
            raise HarnessProtocolError("turn is missing its model stream")
        return None, []

    chunk_indexes = [events.index(event) for event in chunks]
    if not max(chunk_indexes) < events.index(step_end):
        raise HarnessProtocolError("assistant stream is outside its step")
    assembled, stream_usage, finish_reason = _validate_model_stream(chunks, turn, step)
    try:
        assembled_bytes = len(
            json.dumps(assembled, separators=(",", ":"), allow_nan=False).encode("utf-8")
        )
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise HarnessProtocolError("assistant output is not canonical UTF-8 JSON") from error
    if assembled_bytes > max_output_bytes:
        raise HarnessProtocolError("assistant output limit exceeded")
    validated_output = [
        TextBlock.model_validate(block) for block in assembled if block.get("type") == "text"
    ]

    turn_kind = reason.get("kind")
    finish_kind = finish_reason.get("kind")
    if turn_kind == "completed" and finish_reason != {"kind": "stop"}:
        raise HarnessProtocolError("completed prompt did not finish normally")
    if turn_kind == "max-tokens" and finish_reason != {"kind": "max-tokens"}:
        raise HarnessProtocolError("max-tokens turn has a different stream finish")
    if turn_kind == "error" and (
        finish_kind not in {"error", "aborted"}
        or finish_reason.get("failure") != reason.get("error")
    ):
        raise HarnessProtocolError("error turn and stream failure differ")
    if turn_kind not in {"completed", "max-tokens", "error"}:
        raise HarnessProtocolError("non-model turn unexpectedly emitted a model stream")

    if not assistants:
        # The official agent loop deliberately does not synthesize an
        # assistant/message for adapter finish-error/finish-aborted.  The
        # validated stream still carries delivery and usage evidence.
        if turn_kind != "error":
            raise HarnessProtocolError("model stream is missing its assistant/message")
        return stream_usage, validated_output

    assistant = assistants[0]
    assistant_data = assistant.data or {}
    if assistant_data.get("turn") != turn or assistant_data.get("step") != step:
        raise HarnessProtocolError("assistant does not belong to the active turn and step")
    if assistant_data.get("interrupted") is True and completed:
        raise HarnessProtocolError("an interrupted assistant message cannot be successful")
    assistant_message = assistant_data.get("message")
    source = assistant_message.get("source") if isinstance(assistant_message, dict) else None
    if (
        not isinstance(source, dict)
        or source.get("provider") != provider
        or source.get("model") != model
    ):
        raise HarnessProtocolError("assistant provenance does not match initialize")
    assistant_index = events.index(assistant)
    if not max(chunk_indexes) < assistant_index < events.index(step_end):
        raise HarnessProtocolError("assistant stream, message and step are out of order")
    if assistant.sourceEventSeqs != [event.seq for event in chunks]:
        raise HarnessProtocolError("assistant sourceEventSeqs do not bind its complete stream")
    content = assistant_message.get("content") if isinstance(assistant_message, dict) else None
    if content != assembled:
        raise HarnessProtocolError("assistant content differs from its source stream")
    raw_usage = assistant_data.get("usage")
    if raw_usage is not None and TokenUsage.model_validate(raw_usage) != stream_usage:
        raise HarnessProtocolError("assistant usage differs from its source stream")
    if completed and (
        not validated_output
        or any(not block.text.strip() for block in validated_output)
        or stream_usage is None
        or not isinstance(raw_usage, dict)
    ):
        raise HarnessProtocolError("completed prompt requires visible text and usage")
    if turn_kind == "max-tokens" and stream_usage is None:
        raise HarnessProtocolError("max-tokens prompt requires usage evidence")
    return stream_usage, validated_output


def _validate_model_stream(
    chunks: list[SessionEvent], turn: int, step: int
) -> tuple[list[dict[str, Any]], TokenUsage | None, dict[str, Any]]:
    """Reassemble admitted chunks and bind them to the final message."""

    order: list[int] = []
    open_blocks: dict[int, tuple[str, str]] = {}
    closed_blocks: dict[int, dict[str, Any]] = {}
    usage: TokenUsage | None = None
    finish: dict[str, Any] | None = None
    for position, event in enumerate(chunks):
        data = event.data or {}
        if data.get("turn") != turn or data.get("step") != step:
            raise HarnessProtocolError("assistant chunk belongs to a different turn or step")
        chunk = data.get("chunk") or {}
        kind = chunk.get("type")
        if finish is not None:
            raise HarnessProtocolError("assistant stream continued after terminal finish")
        if kind == "block-start":
            index = chunk["index"]
            if index in open_blocks or index in closed_blocks:
                raise HarnessProtocolError("assistant stream repeated a block index")
            order.append(index)
            open_blocks[index] = (chunk["blockType"], "")
        elif kind in {"text-delta", "reasoning-delta"}:
            index = chunk["index"]
            expected = "text" if kind == "text-delta" else "reasoning"
            current = open_blocks.get(index)
            if current is None or current[0] != expected:
                raise HarnessProtocolError("assistant delta has no matching open block")
            open_blocks[index] = (current[0], current[1] + chunk["text"])
        elif kind == "block-end":
            index = chunk["index"]
            current = open_blocks.pop(index, None)
            block = chunk["block"]
            if current is None or block != {"type": current[0], "text": current[1]}:
                raise HarnessProtocolError("assistant block-end differs from streamed deltas")
            closed_blocks[index] = block
        elif kind == "usage":
            if usage is not None or open_blocks:
                raise HarnessProtocolError("assistant usage is duplicated or precedes block-end")
            usage = TokenUsage.model_validate(chunk["usage"])
        elif kind == "finish":
            if open_blocks or position != len(chunks) - 1:
                raise HarnessProtocolError("assistant finish is not the final closed-stream chunk")
            finish = chunk["reason"]
    if finish is None:
        raise HarnessProtocolError("assistant stream has no terminal finish")
    if set(order) != set(closed_blocks):
        raise HarnessProtocolError("assistant stream left an unclosed block")
    return [closed_blocks[index] for index in order], usage, finish


def _unique_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate JSON object keys instead of accepting last-write-wins."""

    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _validate_json_nesting(raw: bytes, max_depth: int) -> None:
    """Bound object/array nesting before handing attacker bytes to ``json``."""

    depth = 0
    in_string = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == ord("\\"):
                escaped = True
            elif byte == ord('"'):
                in_string = False
            continue
        if byte == ord('"'):
            in_string = True
        elif byte in (ord("{"), ord("[")):
            depth += 1
            if depth > max_depth:
                raise HarnessProtocolError("JSON nesting limit exceeded")
        elif byte in (ord("}"), ord("]")):
            depth -= 1
