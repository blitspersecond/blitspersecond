"""`Metal`: a bank of inharmonic squares, which is what a cymbal is."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import cast

from numpy import (
    add, array, bool_, copyto, empty, float64, isfinite, less, multiply,
    ndarray, remainder, zeros,
)

from ..bus import Bus
from ...constants import BLOCK_SIZE, SAMPLE_RATE, _SAMPLE_OFFSETS
from ..registers import RegisterSpec, RegisterWrite
from .operator import Operator


class Metal(Operator):
    """Several rectangular oscillators at unrelated frequencies, summed.

    **The frequencies are deliberately not harmonic.** Struck metal has no
    harmonic series -- that is precisely why a cymbal has no pitch -- so a
    handful of squares at unrelated frequencies gives the clangorous,
    unpitched character that a filtered noise burst cannot. Six of them is
    the classic drum-machine answer; two make a cowbell.

    **Give it coprime whole numbers.** A bank at integer Hz repeats every
    `1 / gcd(frequencies)` seconds, so a set sharing a factor of 100 cycles
    a hundred times a second and buzzes. Pairwise coprime makes the gcd 1
    and the period exactly one second -- the longest whole numbers allow.

    That period is not only a musical property. **A bank that repeats
    exactly can be generated once and then read back**, which is what the
    frequencies in `METAL_BANK` were chosen to allow. This implementation
    does not do that yet: it computes every partial every block, at about
    0.71% of a block budget for six of them against 0.01% for a table
    lookup. See the board.

    **The rectangle is asymmetric on purpose.** Its two levels are `1 - duty`
    and `-duty`, so whatever the duty cycle the mean is exactly zero. A naive
    ±1 square at any duty but 50% carries a DC offset, and a bank of six
    would stack six of them into an audible thump on every strike.

    The bank is fixed at construction; `tune` scales all of it together, and
    its maximum is set so the highest partial cannot be tuned past Nyquist
    into aliasing.
    """

    def __init__(self, frequencies, tune: float = 1.0, duty: float = 0.48):
        frequencies = array(tuple(frequencies), dtype=float64)
        if frequencies.ndim != 1 or len(frequencies) == 0:
            raise ValueError("metal requires a non-empty frequency sequence")
        if not isfinite(frequencies).all() or (frequencies <= 0.0).any():
            raise ValueError("metal frequencies must be finite and positive")
        frequencies.setflags(write=False)

        self.registers = MappingProxyType(
            {
                "tune": RegisterSpec(
                    float(tune),
                    minimum=0.0,
                    maximum=(SAMPLE_RATE / 2) / float(frequencies.max()),
                    unit="ratio",
                ),
                "duty": RegisterSpec(
                    float(duty), minimum=0.0, maximum=1.0, unit="ratio"
                ),
            }
        )
        self.frequencies = frequencies
        #: One phase per partial, in float64 -- a bank running for minutes
        #: would drift audibly out of its own tuning in float32.
        self.phase = zeros(len(frequencies), dtype=float64)
        self._cycles = empty(BLOCK_SIZE, dtype=float64)
        self._wave = empty(BLOCK_SIZE, dtype="float32")
        self._mask = empty(BLOCK_SIZE, dtype=bool_)
        self._clock = 0
        self._registers: Mapping[str, object] = MappingProxyType({})
        self._writes: tuple[RegisterWrite, ...] = ()

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        self._clock = clock
        self._registers = registers
        self._writes = writes

    def process(self, bus: Bus) -> Bus:
        output = bus.current.data
        tune = cast(float, self._registers["tune"])
        duty = cast(float, self._registers["duty"])
        start = 0
        for write in self._writes:
            stop = write.timestamp - self._clock
            self._generate(output, start, stop, tune, duty)
            for name, value in write.values:
                if name == "tune":
                    tune = cast(float, value)
                else:
                    duty = cast(float, value)
            start = stop
        self._generate(output, start, BLOCK_SIZE, tune, duty)
        return bus

    def _generate(
        self, output: ndarray, start: int, stop: int, tune: float, duty: float
    ):
        length = stop - start
        if length == 0:
            return

        samples = output[start:stop]
        samples.fill(0.0)
        cycles = self._cycles[:length]
        wave = self._wave[:length]
        mask = self._mask[:length]
        for index, frequency in enumerate(self.frequencies):
            step = frequency * tune / SAMPLE_RATE
            multiply(_SAMPLE_OFFSETS[:length], step, out=cycles)
            add(cycles, self.phase[index], out=cycles)
            remainder(cycles, 1.0, out=cycles)
            less(cycles, duty, out=mask)
            # The asymmetry that cancels DC: high for `duty` of the cycle at
            # `1 - duty`, low for the rest at `-duty`.
            wave.fill(-duty)
            copyto(wave, 1.0 - duty, where=mask)
            add(samples, wave, out=samples)
            self.phase[index] = (self.phase[index] + length * step) % 1.0
        # Normalised by the bank size, so two partials and six arrive at
        # comparable levels.
        multiply(samples, 2.0 / len(self.frequencies), out=samples)
