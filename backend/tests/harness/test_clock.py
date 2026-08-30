"""Production and test clock wiring for the Harness kernel."""

from __future__ import annotations

import time

from pharos.harness.app import HarnessApp
from pharos.harness.clock import SystemClock
from pharos.harness.fakes import FakeClock
from pharos.harness.runner import StepExecutor


def test_system_clock_reports_current_epoch_microseconds() -> None:
    before = time.time_ns() // 1_000
    observed = SystemClock().utc_epoch_us()
    after = time.time_ns() // 1_000

    assert before <= observed <= after


def test_production_assembly_defaults_to_system_clock() -> None:
    app = HarnessApp()

    assert isinstance(app.clock, SystemClock)
    assert app.executor.clock is app.clock
    assert app.fake_model.clock is app.clock
    assert isinstance(StepExecutor().clock, SystemClock)


def test_harness_app_preserves_an_explicit_fake_clock() -> None:
    clock = FakeClock()
    app = HarnessApp(clock=clock)

    assert app.clock is clock
    assert app.executor.clock is clock
    assert app.fake_model.clock is clock
