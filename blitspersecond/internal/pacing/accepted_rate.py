"""What rate will this panel actually let us run at?

A game declares a rate. A panel advertises one. When the two grids are not
commensurate they slide against each other and a frame periodically has
nowhere to go -- 60 ticks a second against a 59.94Hz panel leaves six
hundredths of a frame over every second, which surfaces as a repeated frame
roughly every 17 seconds, forever. That beat is monotonic and reads as a
stutter.

The fix is to stop asking the panel to carry a rate it cannot divide. Take the
tick-to-slot ratio the panel's NOMINAL rate implies, then apply it to the rate
the panel actually reports:

    ratio = desired / nominal          60 on 144  ->   5/12, exactly
    step  = ratio applied to measured  60 on 144.0 -> 16.667ms
                                       60 on 143.998848 -> 16.667ms + a hair

`desired` is what the author asked for. `accepted` is what the panel will
carry without a beat. On a 59.94Hz panel a 60fps game is accepted at 59.94 --
one tick per presentation, exactly, indefinitely.

**Both halves of that matter.** The ratio comes from the nominal rate because
nominal rates are integers, so the ratio is exact and there is nothing to
approximate and no bound to tune. The step comes from the measured rate
because that is the grid the frames actually land on, and rounding it is what
lets the two slide apart again.

**Do not fit the ratio to the measured rate instead.** It looks more precise
and it rebuilds the fault:

    60fps on 59.94Hz, ratio      1/1  -> 59.94fps, realigns every  0.017s
    60fps on 59.94Hz, ratio 1000/999  -> 60.00fps, realigns every 16.667s

The second delivers the asked-for rate exactly. It is also the 17-second beat,
reconstructed -- 1000 ticks across 999 slots means one slot somewhere in every
999 carries two ticks, and one frame is thrown away. **A ratio that is nearly
but not quite a simpler one is the worst case there is**, because it
concentrates all of its irregularity into a single rare event, which is
exactly what the eye catches. Prefer a simple ratio slightly off the rate
asked for.

**This does not divide presentation slots into ticks.** The ratio is used
once, to choose a step; the pacer then free-runs on its own deadline grid
exactly as it does now, and presentation still never grants a tick. A withheld
present cannot withhold simulation -- which is the difference between choosing
a commensurate rate and letting the display drive the clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction


#: Above this the accepted rate is worth reporting. Not a refusal: what the
#: engine should DO about a rate it cannot carry is the low-refresh-rate
#: policy's question, and this only makes the condition visible. Every panel
#: rate the engine knows of lands at or under 0.1%, so this sits an order of
#: magnitude clear of the honest cases. Kris, 2026-09-10: smooth motion is
#: preferable, and a percent of rate is a price worth paying for it.
RATE_TOLERANCE = 0.01


@dataclass(frozen=True, slots=True)
class AcceptedRate:
    """The rate a panel will carry without a beat, and how far it is off."""

    desired: float
    refresh: float
    nominal: int
    ratio: Fraction
    rate: float

    @property
    def step(self) -> float:
        """Seconds between ticks at the accepted rate."""
        return 1.0 / self.rate

    @property
    def error(self) -> float:
        """Signed fraction the accepted rate misses the desired one by.

        Negative is slow. A tenth of a percent is a panel that genuinely is
        not a multiple of anything -- every NTSC rate is its nominal over
        1.001 -- and is the mechanism working rather than a fault.
        """
        return (self.rate - self.desired) / self.desired

    @property
    def pattern_seconds(self) -> float:
        """How long the tick-to-slot pattern takes to repeat.

        The quantity to watch. Ticks are spread as evenly across the pattern
        as the ratio allows, so a short one is invisible; a long one
        concentrates its irregularity and becomes a beat. A third of a second
        is the worst any known panel produces.
        """
        return self.ratio.denominator / self.refresh

    @property
    def exact(self) -> bool:
        """Whether the panel carries the desired rate with no adjustment."""
        return self.rate == self.desired

    @property
    def within_tolerance(self) -> bool:
        return abs(self.error) <= RATE_TOLERANCE


def accept_rate(desired: int, refresh: float) -> AcceptedRate:
    """Choose the rate nearest `desired` whose grid divides the panel's.

    `refresh` is the rate the panel reports, and should be the most exact one
    the platform will give -- a rational mode rate or a live composition
    clock, not an integer that has already been rounded. Rounding it is what
    this exists to avoid; rounding is applied here, once, and only to pick the
    ratio.
    """
    if desired <= 0:
        raise ValueError("desired rate must be positive")
    if refresh <= 0:
        raise ValueError("refresh rate must be positive")

    nominal = round(refresh)
    ratio = Fraction(int(desired), nominal)
    return AcceptedRate(
        desired=float(desired),
        refresh=float(refresh),
        nominal=nominal,
        ratio=ratio,
        rate=refresh * float(ratio),
    )
