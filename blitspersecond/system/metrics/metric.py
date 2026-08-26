"""The two metric types, and the only surface a subsystem touches.

A counter tallies things that happened; a gauge is a level you measured.
Everything else the engine wants recorded is already recorded, and better,
somewhere else:

* **Static facts** -- presentation path, cadence, audio device, blocksize --
  are log lines, and already exist as log lines in
  `display.internal.pacing.session` and the audio driver's `stats()`.
* **Per-frame state** -- `flip_health`, `pacing_mode` -- is a column in
  `system.frame_pacing`, which is TRACING and deliberately not this.
* **Percentiles** are computed by a report, from the raw frames, so nothing
  has to be bucketed in advance by someone guessing which ranges will
  matter.

**These are handles, not storage.** A push goes to the frame's
`MetricBuffer` and nowhere else; the id is baked in at declaration so the
hot path does no dict lookup, no string hashing and no registry walk. The
fan-out into per-metric streams happens once, at the frame boundary.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .buffer import MetricBuffer


class Metric:
    """A declared metric: a name, a note on what it means, and an id."""

    __slots__ = ("_id", "_name", "_documentation", "_buffer")

    def __init__(
        self,
        metric_id: int,
        name: str,
        documentation: str,
        buffer: "MetricBuffer",
    ) -> None:
        self._id = metric_id
        self._name = name
        self._documentation = documentation
        self._buffer = buffer

    @property
    def name(self) -> str:
        """Dotted namespace path, e.g. `display.swap.ms`."""
        return self._name

    @property
    def documentation(self) -> str:
        """What this measures, for a reader of a report."""
        return self._documentation

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self._name}>"


class Counter(Metric):
    """A tally of things that happened. Only up.

    **Push a total, do not increment.** The hot path counts in a local and
    hands the total over once on the way out:

        blits = 0
        for quad in batch:
            ...
            blits += 1
        BLITS.push(blits)

    or better, where the number is already known rather than tallied:

        BLITS.push(len(batch))

    That is not style advice, it is the whole cost model. Measured over 2000
    blits in one frame: `push(len(batch))` costs 0.07us, counting in a local
    costs 39us, calling the metric per blit costs 91us, and going through
    the pyglet EventBus would cost 4212us -- 61% of a frame at 144Hz. A
    counter that is touched per event cannot be afforded at blit rates,
    which is exactly where counters are wanted.

    Several hot paths may report into the same counter in one frame; their
    pushes sum. A frame nobody reported reads as NaN, never as zero -- see
    `stream.py` for why that distinction is load-bearing.
    """

    __slots__ = ()

    def push(self, delta: float = 1.0) -> None:
        """Add one hot path's tally to this frame."""
        if delta < 0.0:
            # Monotonic by definition: blits cannot un-happen. Affordable to
            # check because counters are pushed once per hot path, not once
            # per event -- and a negative delta is a bug worth surfacing
            # rather than a reading worth recording.
            raise ValueError(
                f"{self._name} is a counter; cannot push a negative delta "
                f"({delta})"
            )
        self._buffer.log(self._id, delta)


class Gauge(Metric):
    """A measured level. Up, down, and negative.

    Signed on purpose. `display.deadline.ms` is negative when a frame is
    presented EARLY and positive when it is late; strip the sign and a frame
    that beat its deadline is indistinguishable from one that missed it by
    the same margin. `system.frame_pacing` already stores that field as `i8`
    while every other duration is `u8`, for the same reason.

    A gauge may be pushed several times in one frame -- on a 144Hz display a
    60fps simulation presents 2 or 3 times per game frame -- and all of them
    are kept as that frame's min, max, sum and count.
    """

    __slots__ = ()

    def push(self, value: float) -> None:
        """Record one observation of this level."""
        self._buffer.log(self._id, value)
