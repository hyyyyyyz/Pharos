"""The background loop that makes the daily digest actually daily.

This module is the whole point of porting the pipeline into Pharos. In the
original ``arxiv_ws`` project the digest only advanced when the user personally
ran a skill in their terminal, so it stopped the moment life got in the way.
Here a task started from the FastAPI lifespan checks, once an hour, whether
today has been swept — and sweeps it if not.

The design follows from one assumption: **this runs on a laptop that sleeps.**

* It is *level-triggered*, not edge-triggered. Nothing fires "at 08:00"; each
  tick asks whether today's work is outstanding. A machine closed at 07:00 and
  reopened at 19:00 sweeps at 19:00 instead of missing the day entirely, and no
  timer needs to survive suspension for that to work.
* It *catches up across days*. If the machine was off for two days, the next
  tick widens the arXiv window to cover them in a single query rather than
  leaving a hole nobody will ever notice. Those papers are filed under today's
  digest date; ``published_at`` keeps the truth about when they appeared.
* It *cannot pile up*. Sweeps go through :class:`~pharos.daily.service.DailySweeper`,
  which permits exactly one at a time, so a sweep still running an hour later
  simply means the next tick finds the slot busy and does nothing.
* It *never takes the app down*. Every tick is wrapped: arXiv being unreachable,
  the provider erroring, or a bug in the sweep all log and wait for the next
  hour. An unattended job that dies on its first bad day is worse than no job.

Configuration is read from the environment here rather than added to
:class:`~pharos.config.Settings`, because that module is owned elsewhere in this
change. These belong in ``Settings`` once it is free to edit; the names are
already ``PHAROS_``-prefixed so the move is a rename of nothing.

    PHAROS_DAILY_ENABLED=0        disable the scheduler entirely
    PHAROS_DAILY_INTERVAL=3600    seconds between checks
    PHAROS_DAILY_STARTUP_DELAY=60 seconds before the first check
    PHAROS_DAILY_CATCHUP_DAYS=3   how far back a catch-up sweep may reach
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pharos.daily import reader
from pharos.daily.service import DailySweeper, latest_run, run_for_date, today
from pharos.db.models import DailyPaper
from pharos.db.session import session_scope

log = logging.getLogger(__name__)

__all__ = ["DailyScheduler"]

#: Default gap between checks. Hourly is far more often than a daily digest
#: needs, and that is the point: a frequent cheap check is what makes a missed
#: window self-healing. A tick with nothing to do costs one SQLite query.
_DEFAULT_INTERVAL_SECONDS = 3600.0

#: Grace period before the first check. Boot is the worst moment to make an
#: outbound request: a dev server restarting on every file save would hammer
#: arXiv, and a machine waking from sleep has not necessarily got its network
#: back yet.
_DEFAULT_STARTUP_DELAY_SECONDS = 60.0

#: Ceiling on how many days a catch-up sweep may cover. A wider window means
#: more arXiv pages for older, less interesting papers; after a long absence the
#: honest answer is "here is what is recent", not a 200-paper backlog.
_DEFAULT_CATCHUP_DAYS = 3

#: Floor on the interval, so a mistyped env var cannot turn this into a hot loop
#: against a free public API.
_MIN_INTERVAL_SECONDS = 60.0


def _env_float(name: str, default: float, *, minimum: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        log.warning("%s=%r is not a number; using %s", name, raw, default)
        return default
    if value < minimum:
        log.warning("%s=%s is below the %s floor; using the floor", name, value, minimum)
        return minimum
    return value


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


class DailyScheduler:
    """Owns the periodic catch-up task. One per app, started in the lifespan."""

    def __init__(self, sweeper: DailySweeper) -> None:
        self._sweeper = sweeper
        self._task: asyncio.Task[None] | None = None
        self.enabled = _env_flag("PHAROS_DAILY_ENABLED", True)
        self.interval = _env_float(
            "PHAROS_DAILY_INTERVAL",
            _DEFAULT_INTERVAL_SECONDS,
            minimum=_MIN_INTERVAL_SECONDS,
        )
        self.startup_delay = _env_float(
            "PHAROS_DAILY_STARTUP_DELAY",
            _DEFAULT_STARTUP_DELAY_SECONDS,
            minimum=0.0,
        )
        self.catchup_days = int(
            _env_float("PHAROS_DAILY_CATCHUP_DAYS", float(_DEFAULT_CATCHUP_DAYS), minimum=1.0)
        )

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if not self.enabled:
            log.info("daily scheduler disabled (PHAROS_DAILY_ENABLED)")
            return
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop())
        log.info(
            "daily scheduler started (first check in %.0fs, then every %.0fs)",
            self.startup_delay,
            self.interval,
        )

    async def aclose(self) -> None:
        """Stop the loop and wait for it to unwind."""
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        task.cancel()
        # Absorb the task's own cancellation without swallowing one aimed at us.
        await asyncio.gather(task, return_exceptions=True)

    # ------------------------------------------------------------ the loop

    async def _loop(self) -> None:
        await asyncio.sleep(self.startup_delay)
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — a bad tick must not end the loop
                log.exception("daily scheduler tick failed; will retry next interval")
            await asyncio.sleep(self.interval)

    async def _tick(self) -> None:
        """Sweep today if today's work is outstanding. Otherwise do nothing."""
        if self._sweeper.active_date is not None:
            log.debug("daily scheduler: a sweep is already running; skipping this tick")
            return

        target = today()
        reason, days = await asyncio.to_thread(self._decide, target, self.catchup_days)
        if reason is None:
            return

        log.info("daily scheduler: sweeping %s (%s, window=%dd)", target, reason, days)
        if not await self._sweeper.submit(target, days=days):
            # Raced a manual refresh between the check and here. Theirs wins;
            # ours would have done the same work.
            log.info("daily scheduler: a sweep started elsewhere; standing down")

    @staticmethod
    def _decide(target: dt.date, catchup_days: int) -> tuple[str | None, int]:
        """Should we sweep ``target``, and over how wide a window?

        Returns ``(reason, days)``, with ``reason`` ``None`` meaning "nothing to
        do". Kept static and synchronous so the policy is directly testable
        without a loop, a sweeper, or a clock.

        The cases, in order:

        * **No run today** — the ordinary path, and the one that covers a laptop
          that was asleep at whatever hour a cron would have fired.
        * **Today errored** — retry; the failure was probably transient.
        * **Today has no papers at all** — retry. The fetch layer cannot
          distinguish "arXiv is down" from "nothing matched today", so an empty
          day is ambiguous and re-asking is the only way to resolve it. On a
          genuinely quiet day this costs a handful of requests an hour,
          comfortably inside arXiv's one-per-three-seconds guidance; the
          alternative is a day silently lost to a thirty-second outage.

          Note this keys off the *stored papers*, not off ``run.fetched``.
          ``fetched`` counts rows a run newly inserted, and dedup is global, so
          any second sweep of a date necessarily reports zero — keying off it
          latched a day that already had papers into re-sweeping every hour for
          the rest of the day, forever, with nothing to show for it.
        * **Papers still pending** — retry, which is what turns "the user
          finally added an API key" into a digest that fills itself in, instead
          of a day of pending cards that stays pending forever. Only *pending*
          counts, and only when a provider is actually configured: an errored
          paper that fails every time (an abstract the model refuses, say) would
          otherwise drive an hourly arXiv sweep and an hourly provider call for
          the rest of the day. Errored papers are still retried by any sweep
          that happens for another reason — they just no longer cause one.

        The window widens to cover days missed entirely, so downtime leaves no
        hole. It is measured from the last run of *any* date, which is the last
        moment we know we looked at arXiv.
        """
        target_str = target.isoformat()
        with session_scope() as session:
            run = run_for_date(session, target_str)
            last = latest_run(session)
            last_date = last.date if last is not None else None
            status = run.status if run is not None else None
            # Counted inside the session because it answers "is there work left
            # for today", and nothing outside needs the rows themselves.
            total = _paper_count(session, target_str)
            pending = _pending_count(session, target_str)

        days = _catchup_window(target, last_date, catchup_days)

        if status is None:
            return "no run yet today", days
        if status == "running":
            # A run left ``running`` by a crash or an abrupt shutdown. The
            # sweeper is idle (checked by the caller), so nothing is actually
            # working on it and it will never finish on its own.
            return "previous run never finished", days
        if status == "error":
            return "previous run errored", days
        if not total:
            return "no papers for today yet", days
        if pending and reader.is_available():
            return f"{pending} papers still unread", 1
        return None, days


def _paper_count(session: Session, date_str: str) -> int:
    """How many papers a date holds at all, read or not."""
    return int(
        session.scalar(
            select(func.count()).select_from(DailyPaper).where(DailyPaper.date == date_str)
        )
        or 0
    )


def _pending_count(session: Session, date_str: str) -> int:
    """Papers for a date still awaiting a first reading.

    Deliberately excludes ``error``: a paper that fails deterministically would
    otherwise keep the day permanently "outstanding" and drive an hourly sweep.
    """
    return int(
        session.scalar(
            select(func.count())
            .select_from(DailyPaper)
            .where(DailyPaper.date == date_str, DailyPaper.read_status == "pending")
        )
        or 0
    )


def _catchup_window(target: dt.date, last_date: str | None, catchup_days: int) -> int:
    """How many days back a sweep should reach, given when we last ran.

    One day is the steady state. After downtime the window grows to cover the
    gap, so two days offline become a single wider arXiv query rather than two
    missing digests — capped, because after a long absence the useful answer is
    "what is recent", not the entire backlog.
    """
    if last_date is None:
        return 1
    try:
        gap = (target - dt.date.fromisoformat(last_date)).days
    except ValueError:
        return 1
    return max(1, min(gap, catchup_days))
