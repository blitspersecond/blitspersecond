"""Three cascading rings per metric: frames, seconds, minutes.

A metric's history is 1024 frames, then 512 seconds, then 512 minutes --
2048 slots, sized to powers of two so a ring index is a mask rather than a
modulo. At 60fps that is ~17 seconds of per-frame detail, 8m32s of
per-second and 8h32m of per-minute, and it never grows: a gauge is 64 KiB
forever, a counter 24 KiB.

**Losing the oldest data is accepted, not a defect awaiting a fix.** (Kris,
2026-08-26: *"if it continues to work and loses the earliest data that's
acceptable."*) Level 2 fills after 512 minutes -- about 8.7 hours at 60fps
-- and then discards its oldest minute on every close. Verified past that
point: sums stay exact, nothing drifts, nothing corrupts, the window simply
moves. So a later reader who notices the earliest peaks are gone should NOT
grow the rings or bolt on a session-wide accumulator to "fix" it. That
trades the one property this design exists to provide -- memory that is
fixed and known -- for history nobody asked for.

**The levels cascade; they do not run in parallel.** A slot folds into the
level below it at the moment it is EVICTED, so eviction is the only roll-up
trigger -- no timer, no scheduler, no wall clock anywhere in this file. Each
observation therefore lives in exactly one level at a time and the three
windows are disjoint, which is why a reporter concatenates them and why
`frames.max` is NOT the session max (see `report.py`).

Deliberately keyed on the FRAME, never on wall time. Wall time is something
we record per frame, not the axis we record against. Keying on frames makes
both folds fixed integers (`framerate` frames to a second, 60 seconds to a
minute) and makes "a second" mean a GAME second, the only unit in which a
fixed-tick simulation can be honestly scored. Keying on loop ITERATIONS was
rejected: the fold ratio would become a display-derived rate rather than the
authored one, so "a second" would change length whenever presentation did.
That was originally argued against a cadence that retuned mid-session; it
holds more simply now, because the loop iterates on presentation and the
simulation does not, so iterations and frames are no longer even the same
count.

Ring sizes need no relationship to the fold ratio -- folds are driven by
eviction COUNT, not by ring capacity -- so nobody should "fix" 1024 to 960
to make it divide by 60.

**NaN means "not measured", everywhere, and zero never stands in for it.**
An unreported frame is not a frame that scored zero, and a ring that says
otherwise lies about data it does not have. Writing NaN costs the same as
writing zero (measured: 143ns against 152ns), so there is nothing to buy by
accepting the lie. Two things fall out of it on purpose:

* Statistics SKIP the gaps (`np.nanpercentile`) while plots and
  current-value reads FILL them (carry the last value forward). Both come
  out of the same storage. Carrying forward at WRITE time instead would
  weight every statistic by how often nobody happened to look.
* A careless read fails LOUDLY: `ring.sum()` returns NaN where
  `np.nansum(ring)` returns the answer, so a forgotten nan-aware reduction
  is obviously broken rather than quietly plausible. Same reasoning as the
  signed `deadline_error_ns` in `system.frame_pacing`.

`array.array('d')` rather than NumPy, which was measured and lost: scalar
element access boxes a Python float on every touch (722ns/push against
378ns), because NumPy is built for whole-row writes and bulk reads -- the
recorder's pattern, not this one. `np.frombuffer` over these arrays is
genuinely zero-copy, so the reporter still gets the bulk half for free.

`GaugeStream` and `CounterStream` repeat the cascade skeleton rather than
sharing a base class. `advance` runs once per metric per frame and `observe`
once per datum; both are hot enough that a `super()` hop is not worth the
handful of shared lines, and the two payloads differ in every field anyway.
"""

from array import array

NAN = float("nan")

FRAMES = 1024
SECONDS = 512
MINUTES = 512

_FRAME_MASK = FRAMES - 1
_SECOND_MASK = SECONDS - 1
_MINUTE_MASK = MINUTES - 1

SECONDS_PER_MINUTE = 60


def frame_slot(frame: int) -> int:
    """Ring index for a frame number."""
    return frame & _FRAME_MASK


class GaugeStream:
    """A measured level: min, max, sum and count per frame.

    All four fields are kept because a gauge can be observed several times
    within one frame -- on a 144Hz display a 60fps simulation presents 2 or 3
    times per game frame, so `display.swap.ms` lands 2 or 3 samples in one
    slot. That per-frame `count` is not bookkeeping, it IS the presentation
    cadence (2,2,3,2,3...), which is why nothing separate records it.

    Gauges are signed. `display.deadline.ms` is negative when a frame is
    presented EARLY and positive when it is late, and the sign is the whole
    signal -- which is why emptiness is flagged by `count`, never by a
    sentinel value in `min`. A metric reporting only negative values must
    report them, not pin its minimum at zero forever.
    """

    __slots__ = (
        "_mn0", "_mx0", "_sm0", "_ct0",
        "_mn1", "_mx1", "_sm1", "_ct1",
        "_mn2", "_mx2", "_sm2", "_ct2",
        "_second", "_minute",
        "_frames_filled", "_seconds_evicted",
        "_seconds_closed", "_minutes_closed",
        "_frames_per_second",
    )

    def __init__(self, frames_per_second: int = 60) -> None:
        self._frames_per_second = frames_per_second
        self._mn0 = array("d", [NAN]) * FRAMES
        self._mx0 = array("d", [NAN]) * FRAMES
        self._sm0 = array("d", [NAN]) * FRAMES
        self._ct0 = array("d", [0.0]) * FRAMES
        self._mn1 = array("d", [NAN]) * SECONDS
        self._mx1 = array("d", [NAN]) * SECONDS
        self._sm1 = array("d", [NAN]) * SECONDS
        self._ct1 = array("d", [0.0]) * SECONDS
        self._mn2 = array("d", [NAN]) * MINUTES
        self._mx2 = array("d", [NAN]) * MINUTES
        self._sm2 = array("d", [NAN]) * MINUTES
        self._ct2 = array("d", [0.0]) * MINUTES
        # Open accumulators for the second and minute being filled:
        # [min, max, sum, count].
        self._second = [NAN, NAN, 0.0, 0.0]
        self._minute = [NAN, NAN, 0.0, 0.0]
        self._frames_filled = 0
        self._seconds_evicted = 0
        self._seconds_closed = 0
        self._minutes_closed = 0

    def apply(self, slot: int, value: float) -> None:
        """Fold one observation into the open frame's slot.

        Named the same as `CounterStream.apply` so the flush loop calls it
        without testing which kind of stream it holds -- the two differ in
        what they store, never in how a staged datum reaches them.
        """
        if self._ct0[slot] == 0.0:
            self._mn0[slot] = value
            self._mx0[slot] = value
            self._sm0[slot] = value
            self._ct0[slot] = 1.0
        else:
            # `elif` is safe: a value below the running minimum cannot also
            # be above the running maximum once a slot holds a sample.
            if value < self._mn0[slot]:
                self._mn0[slot] = value
            elif value > self._mx0[slot]:
                self._mx0[slot] = value
            self._sm0[slot] += value
            self._ct0[slot] += 1.0

    def advance(self, frame: int) -> None:
        """Evict the slot `frame` is about to occupy, then clear it.

        Nothing is evicted until the ring has filled once, so the first
        `FRAMES` frames only take the early return. That is not a special
        case, it is the cascade: level 1 covers the window BEFORE level 0,
        and before frame 1024 there is no such window yet. Counting seconds
        from the first advance instead would close 17 seconds' worth of
        empty accumulators into level 1 and report them as measured.
        """
        if frame < FRAMES:
            return
        slot = frame & _FRAME_MASK
        count = self._ct0[slot]
        if count:
            second = self._second
            if second[3] == 0.0:
                second[0] = self._mn0[slot]
                second[1] = self._mx0[slot]
                second[2] = self._sm0[slot]
                second[3] = count
            else:
                if self._mn0[slot] < second[0]:
                    second[0] = self._mn0[slot]
                if self._mx0[slot] > second[1]:
                    second[1] = self._mx0[slot]
                second[2] += self._sm0[slot]
                second[3] += count

        self._frames_filled += 1
        if self._frames_filled >= self._frames_per_second:
            self._close_second()

        # Cleared last: an unmeasured frame must read as unmeasured, not as
        # whatever occupied this slot 1024 frames ago.
        self._mn0[slot] = NAN
        self._mx0[slot] = NAN
        self._sm0[slot] = NAN
        self._ct0[slot] = 0.0

    def _close_second(self) -> None:
        """Write the open second into level 1, evicting what it displaces.

        Symmetric with `advance`: the slot this second is about to occupy
        holds the second from `SECONDS` ago, and THAT is what folds into the
        open minute. Folding at close time instead would put the same data
        in level 1 and level 2 simultaneously -- the levels would overlap
        rather than cascade, and anything summing across them would count it
        twice.
        """
        index = self._seconds_closed & _SECOND_MASK

        displaced = self._ct1[index]
        if displaced:
            minute = self._minute
            if minute[3] == 0.0:
                minute[0] = self._mn1[index]
                minute[1] = self._mx1[index]
                minute[2] = self._sm1[index]
                minute[3] = displaced
            else:
                if self._mn1[index] < minute[0]:
                    minute[0] = self._mn1[index]
                if self._mx1[index] > minute[1]:
                    minute[1] = self._mx1[index]
                minute[2] += self._sm1[index]
                minute[3] += displaced
        if self._seconds_closed >= SECONDS:
            self._seconds_evicted += 1
            if self._seconds_evicted >= SECONDS_PER_MINUTE:
                self._close_minute()

        second = self._second
        observed = second[3]
        self._mn1[index] = second[0]
        self._mx1[index] = second[1]
        # A second in which nothing was observed records NaN, not 0.0 --
        # the sum of no samples is not zero, it is unknown.
        self._sm1[index] = second[2] if observed else NAN
        self._ct1[index] = observed

        second[0] = NAN
        second[1] = NAN
        second[2] = 0.0
        second[3] = 0.0
        self._frames_filled = 0
        self._seconds_closed += 1

    def _close_minute(self) -> None:
        """Write the open minute into level 2. Terminal: what it displaces
        is simply forgotten, which is what makes the window finite."""
        minute = self._minute
        index = self._minutes_closed & _MINUTE_MASK
        observed = minute[3]
        self._mn2[index] = minute[0]
        self._mx2[index] = minute[1]
        self._sm2[index] = minute[2] if observed else NAN
        self._ct2[index] = observed
        minute[0] = NAN
        minute[1] = NAN
        minute[2] = 0.0
        minute[3] = 0.0
        self._seconds_evicted = 0
        self._minutes_closed += 1

    @property
    def levels(self):
        """`(frames, seconds, minutes)`, each `(min, max, sum, count)`."""
        return (
            (self._mn0, self._mx0, self._sm0, self._ct0),
            (self._mn1, self._mx1, self._sm1, self._ct1),
            (self._mn2, self._mx2, self._sm2, self._ct2),
        )

    @property
    def closed(self):
        """`(seconds, minutes)` closed so far -- how much of levels 1 and 2
        holds real data rather than never-written NaN."""
        return self._seconds_closed, self._minutes_closed

    @property
    def pending(self):
        """`(second, minute)` accumulators not yet closed into their rings.

        Frames evicted from level 0 land here before the second they belong
        to is complete, so anything summing the whole session has to include
        them or it is short by up to one second plus one minute of data.
        """
        return tuple(self._second), tuple(self._minute)


class CounterStream:
    """A monotonic tally: one summed delta per frame.

    One array at level 0, not four. On a delta stream `min`/`max` are
    meaningless -- callers tally in a local and hand over a total once per
    hot path, so the number of pushes says how many code paths reported, not
    anything about the measurement -- and the frame's value is simply their
    sum. NaN marks a frame nobody reported.

    Levels 1 and 2 DO carry a count, because that is where the information
    would otherwise be lost: at level 0 a NaN is visible as a NaN, but a
    second folded from 60 frames needs to say how many of them were actually
    observed, or a sum over a partially-observed second silently understates
    and reads as a real dip.
    """

    __slots__ = (
        "_sm0", "_sm1", "_ct1", "_sm2", "_ct2",
        "_second_sum", "_second_count", "_minute_sum", "_minute_count",
        "_frames_filled", "_seconds_evicted",
        "_seconds_closed", "_minutes_closed",
        "_frames_per_second",
    )

    def __init__(self, frames_per_second: int = 60) -> None:
        self._frames_per_second = frames_per_second
        self._sm0 = array("d", [NAN]) * FRAMES
        self._sm1 = array("d", [NAN]) * SECONDS
        self._ct1 = array("d", [0.0]) * SECONDS
        self._sm2 = array("d", [NAN]) * MINUTES
        self._ct2 = array("d", [0.0]) * MINUTES
        self._second_sum = 0.0
        self._second_count = 0.0
        self._minute_sum = 0.0
        self._minute_count = 0.0
        self._frames_filled = 0
        self._seconds_evicted = 0
        self._seconds_closed = 0
        self._minutes_closed = 0

    def apply(self, slot: int, delta: float) -> None:
        """Add one hot path's tally to the open frame's slot."""
        current = self._sm0[slot]
        # `!= itself` is the NaN test without the function call -- 13ns
        # cheaper than math.isnan(), on a path that runs per datum.
        if current != current:
            self._sm0[slot] = delta
        else:
            self._sm0[slot] = current + delta

    def advance(self, frame: int) -> None:
        """Evict the slot `frame` is about to occupy, then clear it.

        Early-returns until the ring has filled once -- see `GaugeStream`.
        """
        if frame < FRAMES:
            return
        slot = frame & _FRAME_MASK
        value = self._sm0[slot]
        if value == value:
            self._second_sum += value
            self._second_count += 1.0

        self._frames_filled += 1
        if self._frames_filled >= self._frames_per_second:
            self._close_second()

        self._sm0[slot] = NAN

    def _close_second(self) -> None:
        """Write the open second into level 1, evicting what it displaces.
        See `GaugeStream._close_second` for why eviction, not close, is what
        folds into the minute."""
        index = self._seconds_closed & _SECOND_MASK

        displaced = self._ct1[index]
        if displaced:
            self._minute_sum += self._sm1[index]
            self._minute_count += displaced
        if self._seconds_closed >= SECONDS:
            self._seconds_evicted += 1
            if self._seconds_evicted >= SECONDS_PER_MINUTE:
                self._close_minute()

        observed = self._second_count
        self._sm1[index] = self._second_sum if observed else NAN
        self._ct1[index] = observed
        self._second_sum = 0.0
        self._second_count = 0.0
        self._frames_filled = 0
        self._seconds_closed += 1

    def _close_minute(self) -> None:
        """Write the open minute into level 2. Terminal; what it displaces
        is forgotten."""
        index = self._minutes_closed & _MINUTE_MASK
        observed = self._minute_count
        self._sm2[index] = self._minute_sum if observed else NAN
        self._ct2[index] = observed
        self._minute_sum = 0.0
        self._minute_count = 0.0
        self._seconds_evicted = 0
        self._minutes_closed += 1

    @property
    def levels(self):
        """`(frames, seconds, minutes)`. Frames carry a sum alone; seconds
        and minutes carry `(sum, observed_frames)`."""
        return (
            (self._sm0,),
            (self._sm1, self._ct1),
            (self._sm2, self._ct2),
        )

    @property
    def closed(self):
        """`(seconds, minutes)` closed so far."""
        return self._seconds_closed, self._minutes_closed

    @property
    def pending(self):
        """`((sum, count), (sum, count))` for the open second and minute --
        see `GaugeStream.pending`."""
        return (
            (self._second_sum, self._second_count),
            (self._minute_sum, self._minute_count),
        )
