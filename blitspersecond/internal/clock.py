"""The simulation clock: what a frame IS.

A rate and a count of completed ticks, with `time` derived as `frame * step`
on every read. `frame` is the only state.

**This module imports nothing from `time`, and that is the test.** Deciding
WHEN to tick belongs to the pacer, which owns the presentation grid and is
the only thing with the information to make that call; a clock that needed a
wall clock to say what a frame is would not be measuring in frames. One grep
says whether that still holds.

`bps/reference/engine/clock.md` is the authority -- the frame as the unit of
time, who owns 60 and in what order it is read, why `time` is derived rather
than accumulated, and what the count does and does not guarantee.
"""

from blitspersecond.common import fps


class Clock:
    """The engine's fixed-step simulation clock: a rate, and a frame count."""

    def __init__(self, rate: fps) -> None:
        self._rate = rate
        self._step = 1.0 / float(rate)
        self._frame = 0

    @property
    def rate(self) -> fps:
        """The rate this game was authored at."""
        return self._rate

    @property
    def step(self) -> float:
        """Seconds of nominal simulation time in one tick.

        The unit every tuned constant in a game is really expressed in --
        jump arcs, input windows, animation timing are all counted in frames,
        which is why the rate is settled at construction and never after.
        """
        return self._step

    @property
    def frame(self) -> int:
        """Completed simulation ticks. THE clock; everything else derives."""
        return self._frame

    @property
    def time(self) -> float:
        """Nominal simulation time in seconds: `frame * step`, exactly.

        Not measured, and deliberately not measurable -- derived on every
        read so the count stays the only state. For how long something really
        took, ask metrics.
        """
        return self._frame * self._step

    def tick(self) -> None:
        """Advance one fixed step. The imperative; `bps.loop` is what runs."""
        self._frame += 1
