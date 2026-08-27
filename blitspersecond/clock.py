"""The simulation clock: what a frame IS.

The engine measures in elapsed FRAMES, not seconds. A second is sixty frames,
and it is sixty frames whatever the hardware did to earn them. `time` is
`frame * step`, exactly, on every platform and in every run -- so a replay of
the same inputs produces the same numbers, and nothing a scheduler does can
change what a tick was worth.

**This module imports nothing from `time`, and that is the point.** Deciding
WHEN to tick is the pacer's problem; a clock that needed a wall clock to say
what a frame is would not be measuring in frames. Everything the scheduling
half used to hold -- the origin, the deadline, the snap grid, the spin, the
overrun rule -- lives with the thing that owns the presentation grid, because
that is the only place with the information to make those decisions.

What survives here is the ledger, and the engine owns it because the engine
is what a frame count means. Display realises pictures on a panel and audio
feeds a device-paced worker; both are consumers of this count rather than
sources for it, which is what lets an engine-owned clock be the neutral
arbiter two real-time domains can reference. See
`docs/bps/DECOUPLED_PRESENTATION.md`.
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

        Not measured, and deliberately not measurable. Two runs that tick the
        same number of times report the same time here whatever the wall
        clock did in between, which is what makes it safe for game logic that
        expects to be reproducible. If you want to know how long something
        really took, ask metrics.
        """
        return self._frame * self._step

    def tick(self) -> None:
        """Advance one fixed step. The imperative; `bps.loop` is what runs."""
        self._frame += 1
