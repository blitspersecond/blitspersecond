"""`Crush`: bit depth and sample rate, both turned down on purpose."""

from collections.abc import Mapping
from numbers import Integral
from types import MappingProxyType
from typing import cast

from numpy import arange, ceil, divide, empty, multiply, rint, searchsorted

from ..bus import Bus
from ...constants import BLOCK_SIZE, SAMPLE_RATE
from ..registers import RegisterSpec, RegisterWrite
from .operator import Operator


class Crush(Operator):
    """Quantise amplitude, and hold samples at a lower rate.

    The two halves of what a cheap converter does to a signal, in one
    operator because they are almost always wanted together and are what
    makes something sound like sample hardware rather than like a synth.
    They remain independent: `bits` alone is a gritty noise floor that rides
    the signal, `rate` alone is the dull ring of an undersampled recording.

    **`rate` does not filter, and that is the sound.** Holding samples at
    11 kHz folds everything above 5.5 kHz back down the spectrum as
    inharmonic aliases, which is exactly what the hardware being imitated
    did. A resampler would remove them; this is not a resampler.

    **`bits` counts a signed converter's bits**, so 8 gives 128 steps either
    side of zero. At 1 bit a signal survives only as its sign, which is a
    legitimate place to put the dial rather than a degenerate case.

    The hold runs on a phase accumulator carried across blocks, so a rate
    that is not a whole divisor of 48 kHz -- 11,025 Hz, say -- stays at its
    true period instead of being rounded to the block grid.
    """

    def __init__(self, bits: int = 8, rate: float = SAMPLE_RATE):
        # Rejected here rather than by the register spec: `RegisterSpec`
        # carries the type in its default, so a float default would make
        # every later write a float write too.
        if not isinstance(bits, Integral) or isinstance(bits, bool):
            raise TypeError("crush bit depth must be an integer")
        self.registers = MappingProxyType(
            {
                "bits": RegisterSpec(int(bits), minimum=1, maximum=24, unit="bits"),
                "rate": RegisterSpec(
                    float(rate),
                    minimum=0.0,
                    maximum=float(SAMPLE_RATE),
                    unit="Hz",
                ),
            }
        )
        self._clock = 0
        self._registers: Mapping[str, object] = MappingProxyType({})
        self._writes: tuple[RegisterWrite, ...] = ()
        #: Samples until the next hold point. Negative means one is overdue,
        #: which is how a period finer than a block boundary is carried.
        self._phase = 0.0
        #: The sample currently being held, so a hold spans a block edge.
        self._held = 0.0

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        self._clock = clock
        self._registers = registers
        self._writes = writes

    def process(self, bus: Bus) -> Bus:
        bits = cast(int, self._registers["bits"])
        rate = cast(float, self._registers["rate"])
        start = 0
        for write in self._writes:
            stop = write.timestamp - self._clock
            self._apply(bus, start, stop, bits, rate)
            for name, value in write.values:
                if name == "bits":
                    bits = cast(int, value)
                else:
                    rate = cast(float, value)
            start = stop
        self._apply(bus, start, BLOCK_SIZE, bits, rate)
        return bus

    def _output_quiescent(self, output: Bus, input_quiescent: bool) -> bool:
        # `_held` outlives its input: a lane that stops mid-hold leaves this
        # holding a non-zero sample, and at a low rate it would keep emitting
        # that DC step for a good fraction of a second after everything
        # upstream went quiet. Quiescent only once the hold is empty too.
        return input_quiescent and self._held == 0.0

    def _apply(self, bus: Bus, start: int, stop: int, bits: int, rate: float):
        samples = bus.current.data[start:stop]
        if not samples.size:
            return

        steps = float(2 ** (bits - 1))
        multiply(samples, steps, out=samples)
        rint(samples, out=samples)
        divide(samples, steps, out=samples)

        if rate >= SAMPLE_RATE:
            # Every sample is its own hold point, so there is nothing to do
            # but leave the accumulator where a hold would next begin.
            self._held = float(samples[-1])
            self._phase = 0.0
            return
        if rate == 0.0:
            # A hold that never ends. Whatever was last latched is the output
            # from here until the rate comes back up.
            samples.fill(self._held)
            return

        period = SAMPLE_RATE / rate
        count = samples.size
        # Hold point k lands at ceil(phase + k * period). The accumulator
        # falls by one per sample and rises by one period per hold, so that
        # running total is where the k-th hold is due and the first sample at
        # or past it is where it happens. Solving for k directly is what keeps
        # this off a per-sample Python loop: the same arithmetic, once for the
        # whole block. `period >= 1` because `rate` cannot exceed the sample
        # rate, so the points are strictly increasing and none is skipped.
        if self._phase <= count - 1:
            holds = int((count - 1 - self._phase) // period) + 1
        else:
            holds = 0
        points = ceil(self._phase + arange(holds) * period).astype(int)

        # Gathered before anything is overwritten. Hold points only advance,
        # so no point reads a sample that an earlier hold already replaced.
        carried = empty(holds + 1, dtype=samples.dtype)
        carried[0] = self._held
        carried[1:] = samples[points]
        # Each sample takes the most recent hold at or before it; a sample
        # before the first hold takes index zero, the value carried in.
        samples[:] = carried[searchsorted(points, arange(count), side="right")]

        self._held = float(carried[-1])
        self._phase += holds * period - count
