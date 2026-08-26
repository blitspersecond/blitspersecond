"""Reading the rings: percentiles, session summaries, derived numbers.

Everything interesting is computed HERE, not stored. Percentiles, pace,
frames-per-second, worst-case -- none of them occupy a byte in a ring,
because a ring holding 1024 raw frames can answer them on demand and a ring
holding pre-computed answers cannot be asked anything else.

Two invariants a reader of this file has to hold on to, both of which are
easy to violate by accident and neither of which fails loudly:

**The levels are disjoint.** Level 0 covers the most recent ~1024 frames,
level 1 the period BEFORE that, level 2 the period before that. So
`frames.max` is not the session maximum -- it is the maximum of the last
seventeen seconds. Anything claiming to describe the session takes the max
across all three, and concatenates rather than merges.

**Distribution and tail are honest to different depths.** 1024 raw frames
support a p99.9 claim (1 in 1024 is the 99.902nd percentile); a p99.99 drawn
from level 0 alone would be one sample pretending to be a percentile. The
TAIL, though, never decays: `max` folds losslessly, so level 1's 512
second-maxima cover 30,720 frames exactly and level 2's cover 1.8 million.
So this module quotes percentiles from level 0 and worst-case from
everywhere, and says which is which.
"""

from typing import List, Optional, Sequence

import numpy as np

from .metric import Counter, Metric
from .stream import FRAMES, MINUTES, SECONDS


def _view(buffer) -> np.ndarray:
    """Zero-copy NumPy view over an `array('d')` ring."""
    return np.frombuffer(buffer, dtype=np.float64)


def _valid(buffer, closed: int, capacity: int) -> np.ndarray:
    """The written part of a ring that may not have filled yet."""
    view = _view(buffer)
    return view if closed >= capacity else view[:closed]


def carry_forward(values: np.ndarray) -> np.ndarray:
    """Fill unmeasured frames with the last measured value.

    For plotting a level and for "what is it right now" -- NOT for
    statistics, which must skip the gaps instead. A gauge that changed once
    and then sat still for 500 frames genuinely holds one measurement; carry
    it forward into a mean and the answer is weighted by how often nobody
    looked.
    """
    measured = ~np.isnan(values)
    if not measured.any():
        return values
    index = np.where(measured, np.arange(len(values)), 0)
    np.maximum.accumulate(index, out=index)
    return values[index]


class MetricSummary:
    """One metric's answer to "what has it been doing"."""

    __slots__ = ("name", "documentation", "kind", "observed", "capacity",
                 "minimum", "maximum", "mean", "percentiles", "total",
                 "session_worst")

    def __init__(self, **fields) -> None:
        for key, value in fields.items():
            setattr(self, key, value)

    @property
    def coverage(self) -> float:
        """Fraction of the frame window that was actually measured."""
        return self.observed / self.capacity if self.capacity else 0.0

    def __repr__(self) -> str:
        return f"<MetricSummary {self.name} observed={self.observed}>"


class Report:
    """A read-only view over every stream at one moment."""

    def __init__(
        self,
        metrics: Sequence[Metric],
        streams: Sequence,
        framerate: int,
        frame: int,
    ) -> None:
        self._metrics = metrics
        self._streams = streams
        self._framerate = framerate
        self._frame = frame

    @property
    def frame(self) -> int:
        return self._frame

    def summarise(self, name: str) -> Optional[MetricSummary]:
        for metric, stream in zip(self._metrics, self._streams):
            if metric.name == name:
                return self._summarise(metric, stream)
        return None

    def summaries(self) -> List[MetricSummary]:
        return [
            self._summarise(metric, stream)
            for metric, stream in zip(self._metrics, self._streams)
        ]

    def _summarise(self, metric: Metric, stream) -> MetricSummary:
        levels = stream.levels
        seconds_closed, minutes_closed = stream.closed

        if isinstance(metric, Counter):
            frames = _view(levels[0][0])
            observed = int((~np.isnan(frames)).sum())
            second_sums = _valid(levels[1][0], seconds_closed, SECONDS)
            minute_sums = _valid(levels[2][0], minutes_closed, MINUTES)
            (pending_second, _), (pending_minute, _) = stream.pending
            # Disjoint levels, so the session total is their SUM, not a max
            # -- plus whatever has been evicted into an accumulator that has
            # not closed yet, or the total is short by up to one second and
            # one minute of frames.
            total = float(
                np.nansum(frames) + np.nansum(second_sums)
                + np.nansum(minute_sums) + pending_second + pending_minute
            )
            # NOT a cross-level max. A counter's levels hold DIFFERENT UNITS
            # -- level 0 is per frame, level 1 per second, level 2 per minute
            # -- so maxing across them compares a frame against a second and
            # reports the busiest second as the busiest frame. The per-frame
            # peak simply is not retained once a frame has been folded away;
            # say so rather than invent it.
            worst = _nanmax(frames)
            return MetricSummary(
                name=metric.name,
                documentation=metric.documentation,
                kind="counter",
                observed=observed,
                capacity=FRAMES,
                minimum=_nanmin(frames),
                maximum=_nanmax(frames),
                mean=_nanmean(frames),
                percentiles=_percentiles(frames),
                total=total,
                session_worst=worst,
            )

        minimums, maximums, sums, counts = levels[0]
        frame_minimums = _view(minimums)
        frame_maximums = _view(maximums)
        frame_sums = _view(sums)
        frame_counts = _view(counts)
        observed = int((~np.isnan(frame_minimums)).sum())

        # Per-frame MEAN is the honest per-frame value of a gauge sampled
        # more than once in a frame: two swaps in one frame are one frame's
        # worth of swapping, not two.
        with np.errstate(invalid="ignore", divide="ignore"):
            per_frame = np.where(
                frame_counts > 0, frame_sums / frame_counts, np.nan
            )

        # Legitimate here where it was not for a counter: every gauge level
        # stores a maximum in the same unit, and max folds losslessly, so
        # this is EXACT over the whole session even where the distribution
        # is long gone.
        session_worst = _nanmax_across(
            frame_maximums,
            _valid(levels[1][1], seconds_closed, SECONDS),
            _valid(levels[2][1], minutes_closed, MINUTES),
        )
        return MetricSummary(
            name=metric.name,
            documentation=metric.documentation,
            kind="gauge",
            observed=observed,
            capacity=FRAMES,
            minimum=_nanmin(frame_minimums),
            maximum=_nanmax(frame_maximums),
            mean=_nanmean(per_frame),
            percentiles=_percentiles(per_frame),
            total=float(np.nansum(frame_sums)),
            session_worst=session_worst,
        )

    def pace(self) -> Optional[float]:
        """Game time delivered per unit of wall time, over the frame window.

        The number the old `PaceMetric` existed to produce, derived rather
        than accumulated. A locked engine runs at ~1.0; 0.95 means losing
        three game-seconds a minute. Deliberately NOT a comparison of each
        frame against its nominal step -- on a 59.94Hz display every healthy
        frame runs microscopically "over target" forever, and scoring that
        way brands a perfect session a total failure.
        """
        summary = self.summarise("engine.frame.ms")
        if summary is None or not summary.observed:
            return None
        elapsed = summary.mean * summary.observed
        if elapsed <= 0.0:
            return None
        nominal = summary.observed * (1000.0 / self._framerate)
        return nominal / elapsed

    def log(self, logger) -> None:
        """Emit the session report. Called while logging is still alive --
        from the loop's epilogue, never from atexit, which fires after test
        runners have closed their handlers."""
        summaries = [s for s in self.summaries() if s.observed]
        if not summaries:
            logger.info("No metrics were recorded this session.")
            return

        logger.info("=== SESSION METRICS ===")
        logger.info(f"Frames serviced: {self._frame}")
        pace = self.pace()
        if pace is not None:
            logger.info(
                f"Pace: {pace * 100:.2f}% "
                f"(game time delivered per unit of wall time)"
            )
        for summary in summaries:
            if summary.kind == "counter":
                logger.info(
                    f"  {summary.name}: {summary.total:,.0f} total, "
                    f"{summary.mean:.2f}/frame, "
                    f"peak {summary.maximum:.0f} in the frame window"
                )
            else:
                p50, p90, p99 = summary.percentiles
                logger.info(
                    f"  {summary.name}: avg {summary.mean:.3f} "
                    f"p50 {p50:.3f} p90 {p90:.3f} p99 {p99:.3f} "
                    f"max {summary.maximum:.3f} "
                    f"(worst all session {summary.session_worst:.3f})"
                )
            if summary.observed < summary.capacity:
                logger.info(
                    f"      over {summary.observed} measured frames "
                    f"of the last {summary.capacity}"
                )
        logger.info("=======================")


def _percentiles(values: np.ndarray):
    """p50/p90/p99 over measured frames only.

    Quoted from level 0, so honest to p99.9 and no further -- see the module
    docstring. Unmeasured frames are skipped rather than filled: a gauge is
    not at zero, and not at its last value either, in a frame where nothing
    observed it.
    """
    if not np.isfinite(values).any():
        return (float("nan"),) * 3
    return tuple(float(p) for p in np.nanpercentile(values, (50, 90, 99)))


def _nanmin(values: np.ndarray) -> float:
    if not np.isfinite(values).any():
        return float("nan")
    return float(np.nanmin(values))


def _nanmax(values: np.ndarray) -> float:
    if not np.isfinite(values).any():
        return float("nan")
    return float(np.nanmax(values))


def _nanmean(values: np.ndarray) -> float:
    if not np.isfinite(values).any():
        return float("nan")
    return float(np.nanmean(values))


def _nanmax_across(*arrays: np.ndarray) -> float:
    """Worst value across every level. `max` folds losslessly, so this is
    exact over the whole session even where the distribution is long gone."""
    worst = float("nan")
    for values in arrays:
        if values.size and np.isfinite(values).any():
            candidate = float(np.nanmax(values))
            if worst != worst or candidate > worst:
                worst = candidate
    return worst
