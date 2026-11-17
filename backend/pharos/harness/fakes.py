"""Deterministic test doubles: clock, model gateway, capability executor.

Everything here is offline and scripted. Tests drive the Harness kernel with
these; the product drives it with real implementations behind the same
protocols (see :mod:`pharos.harness.seams`). A fake that ever touches the
network, reads an env key or consults wall time is a bug, and the tests assert
as much.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pharos.harness.contracts import AttemptErrorClass, GatewayError, StrictModel

MICROSECONDS_PER_SECOND = 1_000_000


@dataclass
class FakeClock:
    """A clock tests can push forward deterministically.

    ``utc_epoch_us`` is the canonical lease/deadline time used by the kernel.
    """

    start_us: int = 1_700_000_000_000_000
    _now_us: int = field(init=False)

    def __post_init__(self) -> None:
        self._now_us = self.start_us

    def utc_epoch_us(self) -> int:
        return self._now_us

    def utc_epoch_seconds(self) -> float:
        return self._now_us / MICROSECONDS_PER_SECOND

    def advance(self, seconds: float) -> int:
        self._now_us += int(seconds * MICROSECONDS_PER_SECOND)
        return self._now_us

    def set(self, epoch_us: int) -> None:
        self._now_us = epoch_us


class ModelResult(StrictModel):
    """One typed, structured completion from the fake gateway."""

    output: Any = None
    finish_reason: str = "stop"
    input_tokens: int = 10
    output_tokens: int = 20
    cost_micros: int = 0
    provider_request_id: str = "fake-req-1"
    error: str | None = None


@dataclass
class FakeModel:
    """Scripted OpenAI-compatible gateway with failure injection.

    ``script`` is a list of per-call results; callers can also supply a
    function of the call payload. A ``GatewayError`` entry simulates 429/5xx
    (``retryable``) or a post-send timeout (``indeterminate``) and counts as a
    call with usage, exactly like a real provider might.
    """

    clock: FakeClock
    script: list[Any] | Callable[[int, dict], Any] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)
    cancelled: bool = False

    def complete(self, payload: dict) -> ModelResult:
        if self.cancelled:
            raise GatewayError("cancelled")
        self.calls.append(payload)
        index = len(self.calls) - 1
        entry = (
            self.script(index, payload)
            if callable(self.script)
            else (self.script[index] if index < len(self.script) else ModelResult())
        )
        if isinstance(entry, GatewayError):
            raise entry
        if isinstance(entry, dict):
            return ModelResult(**entry)
        return entry


@dataclass
class FakeCapability:
    """A deterministic capability with call recording and crash injection.

    ``crash_points`` accepts ``"before_side_effect"`` and ``"after_side_effect"``;
    a crash point fires once and is then consumed, so the retry the kernel
    makes afterwards follows the normal path. Results are keyed by the
    idempotency key the kernel passes in, so a retried call returns the
    original result instead of repeating the side effect -- the
    provider-side idempotency the kernel's crash-window tests lean on.
    """

    results: dict[str, Any] = field(default_factory=dict)
    calls: list[dict] = field(default_factory=list)
    crash_points: set[str] = field(default_factory=set)
    last_side_effect_id: str | None = None

    def execute(self, action: dict) -> Any:
        key = str(action.get("idempotency_key") or "")
        call = {"action": action, "key": key}
        self.calls.append(call)
        if key and key in self.results:
            # A retry after a crash returns the original result: the effect
            # already happened, and this is the provider-side idempotency the
            # kernel leans on.
            return self.results[key]
        if "before_side_effect" in self.crash_points:
            self.crash_points.discard("before_side_effect")
            raise RuntimeError("injected crash before side effect")
        result = {"ok": True, "key": key, "echo": action.get("value")}
        if key:
            self.results[key] = result
        self.last_side_effect_id = key
        if "after_side_effect" in self.crash_points:
            self.crash_points.discard("after_side_effect")
            raise RuntimeError("injected crash after side effect")
        return result


class FakeWakeup:
    """Process-in-process wakeup; losing an event costs only latency."""

    def __init__(self) -> None:
        self.signals = 0

    def signal(self) -> None:
        self.signals += 1

    def drain(self) -> bool:
        had = self.signals > 0
        self.signals = 0
        return had


def gate_error(code: AttemptErrorClass, message: str = "provider error") -> GatewayError:
    error = GatewayError(message)
    error.error_class = code
    return error


def json_result(value: Any) -> dict:
    return {"output": json.dumps(value), "finish_reason": "stop"}
