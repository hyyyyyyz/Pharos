"""The production clock.

The kernel takes its notion of time (lease expiry, deadlines, event stamps)
from an injected ``Clock`` (see :mod:`pharos.harness.seams`). Tests inject
``FakeClock`` to step time deterministically; production must use this one.
Before it existed ``HarnessApp()`` silently fell back to a ``FakeClock`` frozen at
a fixed 2023 instant, so leases never expired and every timestamp was identical.
"""

from __future__ import annotations

import time

MICROSECONDS_PER_SECOND = 1_000_000


class SystemClock:
    """Wall-clock UTC time."""

    def utc_epoch_us(self) -> int:
        return time.time_ns() // 1000

    def utc_epoch_seconds(self) -> float:
        return time.time_ns() / 1_000_000_000
