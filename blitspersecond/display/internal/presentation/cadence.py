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
