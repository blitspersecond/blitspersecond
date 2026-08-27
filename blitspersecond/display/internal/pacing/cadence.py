"""Deterministic simulation cadence driven by presentation slots."""

from __future__ import annotations

from fractions import Fraction


class PresentationCadence:
    """Distribute fixed simulation ticks over display presentations.

    The closest small rational approximation is deliberately used instead of
    accumulating wall time. For example, 60Hz simulation on a nominal 120Hz
    display becomes one tick per two presentations; a slightly slow delivered
    presentation stream therefore makes the game slightly slow rather than
    periodically placing ticks on adjacent frames.
    """

    def __init__(
        self,
        target_rate: float,
        refresh_rate: float,
        max_denominator: int = 40,
    ) -> None:
        if target_rate <= 0:
            raise ValueError("target_rate must be positive")
        if refresh_rate <= 0:
            raise ValueError("refresh_rate must be positive")
        if max_denominator <= 0:
            raise ValueError("max_denominator must be positive")

        self.target_rate = float(target_rate)
        self.refresh_rate = float(refresh_rate)
        # 40, not 20. A bound of 20 cannot express 60/175 = 12/35 or
        # 60/500 = 3/25, and `limit_denominator` does not fail when it cannot
        # -- it silently returns the nearest rational it CAN build. That put
        # 175Hz panels (the stock factory overclock on a 165Hz part, so real
        # shipping hardware) at 7/20, running the simulation at 61.25fps:
        # 2% fast forever, every timer and tween off by the same amount, and
        # nothing logged. Measured 2026-08-26; 35 is the smallest bound that
        # covers both, and every other rate tested resolves to an identical
        # ratio at 20, 25, 35, 40 and 60, so the headroom is free.
        self.ratio = Fraction(self.target_rate / self.refresh_rate).limit_denominator(max_denominator)
        if self.ratio.numerator == 0:
            # limit_denominator rounded a very slow target down to "never
            # tick" (e.g. 60-on-3000Hz). Floor it to the slowest representable
            # cadence -- one tick per max_denominator presentations -- rather
            # than silently freezing the simulation forever.
            self.ratio = Fraction(1, max_denominator)

        # Zero phase IS the ceiling schedule: game frame k presents at
        # ceil(k * refresh / target) -- frame 1 at presentation 3 on a 144Hz
        # display, not presentation 1. MEASURED, not inferred; see
        # `tests/test_cadence.py`.
        #
        # Two properties follow, and the engine now depends on both:
        #
        # 1. NEVER EARLY. A present lands at ceil(k * R / target) / R, which
        #    is always >= k / target. The frame is on or after its ideal
        #    deadline, never ahead of it.
        #
        # 2. INSIDE THE WINDOW. That same expression is < k / target + 1/R,
        #    so the frame lands within its own interval whenever
        #    1/R <= 1/target -- i.e. for any refresh at or above the target
        #    rate.
        #    Below the target the bound exceeds the window and this
        #    guarantee lapses -- but that is a description, not a fault. On a
        #    40Hz display (a Steam Deck battery cap, not an exotic panel) the
        #    ratio exceeds 1, ticks batch, and showing 40 frames of a 60Hz
        #    simulation is exactly what was asked for.
        #
        # The startup prime falls out of the same arithmetic rather than
        # being built: the first ceil(refresh / target) - 1 presentations
        # carry no tick, so the loop dispatches, simulates nothing, and
        # re-presents. Frame one therefore gets a full frame interval instead
        # of whatever was left of one, and the deadline grid is phased off a
        # real completed present. Two slots at 144Hz and 165Hz, one at 120Hz,
        # and none at 60Hz -- where ratio 1/1 makes this line a no-op and the
        # engine is in lockstep with nothing to spare, as it should be.
        #
        # This replaced a `denominator - numerator` rotation on 2026-08-26.
        # That kept the same cyclic gap sequence but started it two slots
        # early, so every frame presented ahead of its deadline -- by 4.17ms
        # to 9.72ms at 144Hz, 6.06ms to 10.61ms at 165Hz -- and none landed
        # inside its own window. Harmless while presentation timing was
        # independent of simulation timing; wrong the moment pacing was
        # expressed on a per-game-frame grid, which is what happened. Do not
        # reintroduce the rotation to "make the first frame tick sooner":
        # that first tick is the one this schedule deliberately gives room.
        self._initial_phase = 0
        self._phase = self._initial_phase

    @property
    def effective_rate(self) -> float:
        """Nominal simulation ticks per second produced by this cadence."""
        return self.refresh_rate * float(self.ratio)

    @property
    def rate_error(self) -> float:
        """Signed fraction by which the cadence misses the requested rate.

        Two different things show up here and only one is a fault, so read the
        MAGNITUDE, not the sign:

        * **A panel that is not a multiple of the target.** 59.94Hz gives
          -0.001 and that is the engine working correctly -- the ratio is
          1/1, no smaller rational is closer (the exact one is 1000/999), and
          a notional second on that display is genuinely 1001ms. Hiding it
          would mean double-ticking onto adjacent presentations, which is the
          precise thing this class exists to avoid.
        * **A ratio the denominator bound could not express.** 175Hz under
          the old bound of 20 gave +0.021, because 12/35 was available and
          excluded. That is a fault: a representable ratio was refused.

        Real panels land at or under 0.001 (59.94 -> -0.001, a measured
        143.98 -> -0.0001). A bound failure lands near 0.02. Anything in
        between is unexplored territory and worth a look rather than a
        silent run, which is why the caller warns on a threshold well inside
        that gap rather than on any deviation at all.
        """
        return (self.effective_rate - self.target_rate) / self.target_rate

    def reset(self) -> None:
        """Restart the phase pattern, e.g. when resuming after a suspension.

        Re-enters the ceiling schedule at its origin, exactly like
        construction -- so a resume gets the same startup prime a cold start
        does, and the first tick after it gets a full frame interval.
        """
        self._phase = self._initial_phase

    def advance(self) -> int:
        """Advance one presentation and return the number of simulation ticks."""
        phase = self._phase + self.ratio.numerator
        ticks, self._phase = divmod(phase, self.ratio.denominator)
        return ticks
