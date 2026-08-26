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
        max_denominator: int = 20,
    ) -> None:
        if target_rate <= 0:
            raise ValueError("target_rate must be positive")
        if refresh_rate <= 0:
            raise ValueError("refresh_rate must be positive")
        if max_denominator <= 0:
            raise ValueError("max_denominator must be positive")

        self.target_rate = float(target_rate)
        self.refresh_rate = float(refresh_rate)
        self.ratio = Fraction(self.target_rate / self.refresh_rate).limit_denominator(max_denominator)
        if self.ratio.numerator == 0:
            # limit_denominator rounded a very slow target down to "never
            # tick" (e.g. 60-on-3000Hz). Floor it to the slowest representable
            # cadence -- one tick per max_denominator presentations -- rather
            # than silently freezing the simulation forever.
            self.ratio = Fraction(1, max_denominator)

        # Rotate the cadence so the first presentation carries an immediate
        # simulation tick.  Subsequent ticks are spread by the phase accumulator.
        #
        # Two separate things fall out of this line, and only one of them
        # is what a reader expects. MEASURED, not inferred -- see
        # `tests/test_cadence.py`.
        #
        # 1. The GAP SEQUENCE is the ceiling rotation. Ticks are spaced as
        #    ceil(k * refresh / target) would space them: 60-on-144 gives
        #    3,2,3,2,2 and 60-on-165 gives 3,3,3,2. Floor would give
        #    2,2,3,2,3 and 2,3,3,3 -- same average rate, different phase.
        #    Do not "simplify" this to a zero initial phase; it is the same
        #    amount of code and a different pacing policy.
        #
        # 2. The ORIGIN is one full frame early, and that is NOT the ceiling
        #    schedule. Ceiling would place game frame 1 at presentation 3;
        #    this places it at presentation 1, so the whole schedule runs
        #    1-2 presentations ahead of ceiling forever. Every frame
        #    therefore presents EARLY of its ideal target-rate deadline --
        #    by 4.17ms to 9.72ms at 144Hz, 6.06ms to 10.61ms at 165Hz -- and
        #    the error is bounded but never converges.
        #
        # So the "cumulative deadline is never early" property that ceiling
        # is usually chosen FOR does not hold here. Nothing currently depends
        # on it: the deadline grid in `session.py` advances by 1/refresh
        # uniformly, so no per-game-frame deadline exists to be early
        # against, and presentation timing is independent of simulation
        # timing by design. It would start to matter the day deadline pacing
        # is expressed on a per-game-frame grid, which is exactly when
        # someone should revisit this line rather than trust its name.
        self._initial_phase = self.ratio.denominator - self.ratio.numerator
        self._phase = self._initial_phase

    @property
    def effective_rate(self) -> float:
        """Nominal simulation ticks per second produced by this cadence."""
        return self.refresh_rate * float(self.ratio)

    def reset(self) -> None:
        """Restart the phase pattern, e.g. when resuming after a suspension.

        The next presentation carries an immediate tick, exactly like the
        first presentation after construction.
        """
        self._phase = self._initial_phase

    def advance(self) -> int:
        """Advance one presentation and return the number of simulation ticks."""
        phase = self._phase + self.ratio.numerator
        ticks, self._phase = divmod(phase, self.ratio.denominator)
        return ticks
