"""Contract tests for the per-Attempt model gateway seam."""

from __future__ import annotations

import dataclasses
import threading
from typing import Any, cast

import pytest
from pharos.harness.fakes import FakeClock, FakeModel, ModelResult
from pharos.harness.model_gateway import (
    AttemptContext,
    FakeGatewayFactory,
    GatewayLifecycleError,
    LegacyGatewayFactory,
)


def context(attempt: str, *, attempt_no: int = 1) -> AttemptContext:
    return AttemptContext(
        run_id="run-1",
        step_id="step-1",
        attempt_id=attempt,
        attempt_no=attempt_no,
        scope_type="user",
        scope_id="owner-1",
        lease_owner="worker-1",
        workflow_key="harness.canary",
        role="canary_actor@1",
        deadline_at_us=1_700_000_001_000_000,
        provider="fake",
        model="canary",
    )


def test_attempt_context_is_frozen_and_strict() -> None:
    current = context("attempt-1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        current.model = "other"  # type: ignore[misc]
    assert current.deadline_at_us == 1_700_000_001_000_000

    invalid = {
        "run_id": "run-1",
        "step_id": "step-1",
        "attempt_id": "attempt-1",
        "attempt_no": 1,
        "scope_type": "user",
        "scope_id": "owner-1",
        "lease_owner": "worker-1",
        "workflow_key": "harness.canary",
        "role": "canary_actor@1",
        "deadline_at_us": 1,
        "provider": "fake",
        "model": "canary",
    }
    for field, value in {
        "run_id": "",
        "scope_id": "\x00owner",
        "attempt_no": True,
        "deadline_at_us": 0,
    }.items():
        with pytest.raises(ValueError):
            AttemptContext(**cast(dict[str, Any], {**invalid, field: value}))


def test_fake_factory_opens_independent_handles_and_cancel_is_scoped() -> None:
    clock = FakeClock()
    source = FakeModel(clock=clock, script=[ModelResult(output={"attempt": "ok"})])
    factory = FakeGatewayFactory(source)
    first = factory.open(context("attempt-1"))
    second = factory.open(context("attempt-2", attempt_no=2))

    assert factory.open_count == 2
    assert first is not second
    assert first.context is not second.context
    first.cancel()
    with pytest.raises(GatewayLifecycleError):
        first.complete({"input": "first"})
    assert second.complete({"input": "second"}).output == {"attempt": "ok"}


def test_factory_open_count_is_thread_safe() -> None:
    factory = FakeGatewayFactory(FakeModel(clock=FakeClock()))
    handles: list[object] = []
    threads = [
        threading.Thread(
            target=lambda index=index: handles.append(factory.open(context(f"a-{index}")))
        )
        for index in range(20)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)
    assert all(not thread.is_alive() for thread in threads)
    assert factory.open_count == 20
    assert len({id(handle) for handle in handles}) == 20


def test_handle_close_is_idempotent_and_terminal_operations_fail_closed() -> None:
    factory = FakeGatewayFactory(FakeModel(clock=FakeClock()))
    handle = factory.open(context("attempt-1"))
    handle.close()
    handle.close()
    assert handle.close_count == 1  # type: ignore[attr-defined]
    with pytest.raises(GatewayLifecycleError):
        handle.complete({})
    with pytest.raises(GatewayLifecycleError):
        handle.cancel()

    cancelled = factory.open(context("attempt-2"))
    cancelled.cancel()
    cancelled.close()
    cancelled.close()
    with pytest.raises(GatewayLifecycleError):
        cancelled.complete({})
    with pytest.raises(GatewayLifecycleError):
        cancelled.cancel()


def test_complete_is_one_shot_and_close_remains_available() -> None:
    factory = FakeGatewayFactory(FakeModel(clock=FakeClock()))
    handle = factory.open(context("attempt-1"))
    handle.complete({"input": "value"})
    with pytest.raises(GatewayLifecycleError):
        handle.complete({"input": "again"})
    with pytest.raises(GatewayLifecycleError):
        handle.cancel()
    handle.close()


def test_legacy_factory_does_not_cancel_a_shared_sibling() -> None:
    source = FakeModelGatewayForTest()
    factory = LegacyGatewayFactory(shared_gateway=source)
    first = factory.open(context("attempt-1"))
    second = factory.open(context("attempt-2"))
    first.cancel()
    assert second.complete({}).output == {"ok": True}
    assert source.cancel_calls == 0


def test_concurrent_cancel_wins_and_close_rejects_in_flight_complete() -> None:
    delegate = BlockingGateway()
    handle = LegacyGatewayFactory(gateway_factory=lambda: delegate).open(context("attempt-1"))
    complete_result: list[object] = []

    def complete() -> None:
        try:
            complete_result.append(handle.complete({}))
        except BaseException as error:  # noqa: BLE001 - assert the terminal winner
            complete_result.append(error)

    complete_thread = threading.Thread(target=complete)
    complete_thread.start()
    assert delegate.started.wait(timeout=1)

    duplicate_result: list[object] = []

    def duplicate_complete() -> None:
        try:
            handle.complete({})
        except BaseException as error:  # noqa: BLE001 - assert one-call ownership
            duplicate_result.append(error)

    duplicate_thread = threading.Thread(target=duplicate_complete)
    duplicate_thread.start()
    duplicate_thread.join(timeout=1)
    assert isinstance(duplicate_result[0], GatewayLifecycleError)
    assert delegate.complete_calls == 1

    cancel_thread = threading.Thread(target=handle.cancel)
    cancel_thread.start()
    assert delegate.cancelled.wait(timeout=1)
    cancel_thread.join(timeout=1)
    assert not cancel_thread.is_alive()

    # Cleanup ownership stays with the runner thread. It may not block behind
    # a still-running completion after a concurrent cancel request.
    with pytest.raises(GatewayLifecycleError, match="operation is in flight"):
        handle.close()
    delegate.release.set()
    complete_thread.join(timeout=1)
    assert not complete_thread.is_alive()
    assert isinstance(complete_result[0], GatewayLifecycleError)
    handle.close()
    assert handle.state == "closed"  # type: ignore[attr-defined]
    handle.close()
    assert handle.close_count == 1  # type: ignore[attr-defined]
    assert delegate.close_calls == 1


def test_process_control_exception_is_rethrown_with_failed_terminal_state() -> None:
    handle = LegacyGatewayFactory(gateway_factory=KeyboardInterruptGateway).open(
        context("attempt-1")
    )
    with pytest.raises(KeyboardInterrupt):
        handle.complete({})
    assert handle.state == "failed"  # type: ignore[attr-defined]
    with pytest.raises(GatewayLifecycleError):
        handle.cancel()
    handle.close()


def test_close_calls_delegate_outside_lock_and_replays_cleanup_error() -> None:
    delegate = CallbackCloseGateway()
    handle = LegacyGatewayFactory(gateway_factory=lambda: delegate).open(context("attempt-1"))
    delegate.handle = handle
    with pytest.raises(CleanupError, match="cleanup failed"):
        handle.close()
    assert delegate.state_seen == "closed"
    with pytest.raises(CleanupError, match="cleanup failed"):
        handle.close()
    assert delegate.close_calls == 1


def test_legacy_factory_requires_an_explicit_delegate_mode() -> None:
    source = FakeModelGatewayForTest()
    with pytest.raises(TypeError, match="exactly one"):
        LegacyGatewayFactory()
    with pytest.raises(TypeError, match="exactly one"):
        LegacyGatewayFactory(shared_gateway=source, gateway_factory=lambda: source)


class FakeModelGatewayForTest:
    """A tiny legacy-shaped spy used to pin non-global cancellation."""

    def __init__(self) -> None:
        self.cancel_calls = 0

    def complete(self, payload: dict) -> ModelResult:
        return ModelResult(output={"ok": True})

    def cancel(self) -> None:
        self.cancel_calls += 1


class BlockingGateway:
    """Legacy-shaped delegate that exposes each lifecycle race explicitly."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.cancelled = threading.Event()
        self.closed = threading.Event()
        self.close_calls = 0
        self.complete_calls = 0

    def complete(self, payload: dict) -> ModelResult:
        self.complete_calls += 1
        self.started.set()
        self.release.wait(timeout=2)
        return ModelResult(output={"late": True})

    def cancel(self) -> None:
        self.cancelled.set()

    def close(self) -> None:
        self.close_calls += 1
        self.closed.set()


class KeyboardInterruptGateway:
    def complete(self, payload: dict) -> ModelResult:
        raise KeyboardInterrupt

    def cancel(self) -> None:
        raise AssertionError("cancel must not run after terminal failure")


class CleanupError(RuntimeError):
    pass


class CallbackCloseGateway(FakeModelGatewayForTest):
    def __init__(self) -> None:
        super().__init__()
        self.handle: Any = None
        self.state_seen: str | None = None
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        self.state_seen = self.handle.state
        raise CleanupError("cleanup failed")
