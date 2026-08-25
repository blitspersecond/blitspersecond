from collections import deque
from time import perf_counter

import numpy as np

from blitspersecond.system.config import Config

from .abc import Metric
from .logger import Logger


class PaceMetric(Metric):
    """Simulation pace, measured in game time.

    The engine pushes one sample per fixed simulation tick (each sample is
    that tick's share of wall time, batch-averaged across catch-up bursts by
    the caller). Every ``fps * log_interval`` pushes is exactly
    ``log_interval`` seconds of GAME time; this metric wall-stamps those
    period boundaries and reports PACE -- game time delivered per wall
    second. A locked engine runs at ~100%; a slow game shows up directly
    (95% pace = losing three game-seconds a minute).

    Honesty notes -- the previous incarnation was built for a wall-scheduled
    loop and lied under presentation locking:
    - Samples are batch-averaged, so per-sample max/percentiles/jitter can
      never see a spike, only its smear. Stall detection belongs to
      PresentationMonitor; sustained slowness is what pace measures.
    - Nothing here compares samples against the nominal 1/fps step: on a
      59.94Hz display every healthy sample runs slightly "over target"
      forever, so that comparison brands perfect sessions 100% failures.
      Wall time per game-second is the only fair judge.

    While the engine is suspended no pushes arrive and the open period
    freezes; the engine calls ``rebase()`` on resume so the suspended gap is
    discarded rather than scored as one catastrophically slow second. The
    end-of-session report is emitted by ``report()``, called by the engine
    when the main loop exits (not atexit -- logging is still alive then).
    """

    def __init__(self, duration: int = 1, log_interval: int = 1):
        self._logger = Logger()
        self._fps = Config().display.fps

        # Rolling per-tick window backing the live gauges (average/max) the
        # demo HUDs read.
        self._queue = deque(maxlen=self._fps * duration)

        # Period bookkeeping: fps * log_interval pushes = log_interval
        # game-seconds. The wall stamp opens on the first push after a
        # boundary (or after rebase), so untracked gaps are never scored.
        self._log_every = self._fps * log_interval
        self._period_game_s = float(log_interval)
        self._period_samples = 0
        self._period_sum = 0.0
        self._period_max = 0.0
        self._period_started: float | None = None

        # One record per completed game-second: (wall_s, avg_tick_s, max_tick_s).
        # ~3 floats per game-second -- an hour of session is trivia, and it
        # replaces the old 36000-sample raw history.
        self._periods: list[tuple[float, float, float]] = []

    def push(self, sample: float):
        """Add one simulation tick's wall-time share; closes and logs a
        period each time a full game-second of ticks has landed."""
        if self._period_started is None:
            # First push after boot/rebase only OPENS the boundary: its own
            # wall share predates the stamp, so scoring it would make the
            # first period one tick short (and slightly, dishonestly fast).
            # It still feeds the HUD window.
            self._period_started = perf_counter()
            self._queue.append(sample)
            return
        self._queue.append(sample)
        self._period_sum += sample
        self._period_max = max(self._period_max, sample)
        self._period_samples += 1
        if self._period_samples >= self._log_every:
            self._close_period()

    def rebase(self):
        """Discard the open partial period. Called by the engine on resume:
        the suspended gap is not the game's fault and must not be scored."""
        self._period_samples = 0
        self._period_sum = 0.0
        self._period_max = 0.0
        self._period_started = None

    def _close_period(self):
        # Only reachable from push(), which stamps the period open.
        assert self._period_started is not None
        now = perf_counter()
        wall = now - self._period_started
        avg_tick = self._period_sum / self._period_samples
        pace = self._period_game_s / wall if wall > 0 else 0.0

        self._periods.append((wall, avg_tick, self._period_max))
        self._logger.info(
            f"Pace: {pace * 100:.1f}% "
            f"({self._period_game_s:.0f}s game in {wall:.3f}s wall), "
            f"avg tick {avg_tick * 1000:.2f}ms, "
            f"worst {self._period_max * 1000:.2f}ms"
        )

        # Next period opens at this boundary -- consecutive periods tile the
        # running wall clock exactly.
        self._period_samples = 0
        self._period_sum = 0.0
        self._period_max = 0.0
        self._period_started = now

    def report(self):
        """End-of-session pace report over every scored game-second."""
        if not self._periods:
            return

        walls = np.array([w for w, _, _ in self._periods])
        avgs = np.array([a for _, a, _ in self._periods])
        maxes = np.array([m for _, _, m in self._periods])
        paces = self._period_game_s / walls

        game_s = len(self._periods) * self._period_game_s
        wall_s = float(walls.sum())
        overall = game_s / wall_s * 100 if wall_s > 0 else 0.0
        slow = int(np.sum(paces < 0.99))

        # Gaming-convention percentiles: "p99 pace" = the pace beaten by 99%
        # of seconds. Low pace is bad, so that's the 1st percentile of the
        # distribution -- hence the inverted (50, 10, 1).
        p50, p90, p99 = (np.percentile(paces, p) * 100 for p in (50, 10, 1))
        self._logger.info("=== SESSION PACE ===")
        self._logger.info(
            f"Scored: {game_s:.0f}s game in {wall_s:.1f}s wall ({overall:.2f}% pace)"
        )
        self._logger.info(
            f"Pace percentiles: p50 {p50:.1f}%  p90 {p90:.1f}%  "
            f"p99 {p99:.1f}%  worst {paces.min() * 100:.1f}%"
        )
        self._logger.info(
            f"Slow seconds (<99% pace): {slow} of {len(self._periods)}"
        )
        self._logger.info(
            f"Ticks: avg {avgs.mean() * 1000:.2f}ms, "
            f"worst batch {maxes.max() * 1000:.2f}ms"
        )
        self._logger.info("====================")

    @property
    def average(self) -> float:
        """Mean per-tick wall share over the rolling window (seconds)."""
        return sum(self._queue) / len(self._queue) if self._queue else 0.0

    @property
    def max(self) -> float:
        """Worst per-tick wall share in the rolling window (seconds)."""
        return max(self._queue) if self._queue else 0.0
