"""Production clock for durable Harness leases and deadlines."""

from __future__ import annotations

import time

NANOSECONDS_PER_MICROSECOND = 1_000
NANOSECONDS_PER_SECOND = 1_000_000_000


class SystemClock:
    """Read UTC epoch time from the host wall clock.

    Durable lease and deadline timestamps are compared across processes and
    restarts, so the production kernel needs epoch time rather than a process-
    local monotonic counter. Tests inject :class:`FakeClock` instead.
    """

    def utc_epoch_us(self) -> int:
        return time.time_ns() // NANOSECONDS_PER_MICROSECOND

    def utc_epoch_seconds(self) -> float:
        return time.time_ns() / NANOSECONDS_PER_SECOND
