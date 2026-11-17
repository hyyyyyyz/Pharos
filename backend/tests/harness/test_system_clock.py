"""Production wiring uses a real clock; tests keep injecting FakeClock."""

from __future__ import annotations

import time

from pharos.harness.app import HarnessApp
from pharos.harness.clock import SystemClock
from pharos.harness.fakes import FakeClock


def test_system_clock_tracks_wall_time_and_moves() -> None:
    clock = SystemClock()
    before = time.time()
    a = clock.utc_epoch_us()
    b = clock.utc_epoch_us()
    assert before - 1 <= a / 1_000_000 <= time.time() + 1
    assert b >= a
    assert abs(clock.utc_epoch_seconds() - time.time()) < 1


def test_default_harness_app_is_not_on_a_fake_clock() -> None:
    app = HarnessApp()
    assert isinstance(app.clock, SystemClock)
    assert app.executor.clock is app.clock


def test_injected_fake_clock_is_still_honoured() -> None:
    clock = FakeClock()
    assert HarnessApp(clock=clock).clock is clock
