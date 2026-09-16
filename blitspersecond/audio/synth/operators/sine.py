"""`Sine`: one frequency and nothing else, and the only carrier you can bend.

Deliberately not built on `_Oscillator`. It carries phase in radians rather
than as a 0..1 ramp, and -- alone among the carriers -- it accepts a
**signal** on `frequency`, which needs a different calculation entirely. The
two together make sharing the phasor cost more than it saves.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import cast

from numpy import add, cumsum, float64, multiply, ndarray, pi, sin, subtract

from ..bus import Bus
from ...constants import BLOCK_SIZE, SAMPLE_RATE, _SAMPLE_OFFSETS
from ..registers import RegisterSpec, RegisterWrite
from ._oscillator import C_SHARP_5
from .operator import Operator


class Sine(Operator):
    """A pure tone: one harmonic, and no timbre to speak of.

    On its own it sounds like a test signal, which is what makes it the
    useful one to build with -- it adds nothing you did not ask for, so what
    you hear is whatever shaped it.

    **`frequency` accepts a signal**, and it is currently the only carrier
    that does. Connecting an oscillator to it is vibrato, or FM if the
    control is fast enough. The others could take one too, but only once
    somebody implements the array-valued meaning for them; `accepts_signal`
    is a claim about the code, not a wish.
    """

    registers = MappingProxyType(
        {
            "frequency": RegisterSpec(
                C_SHARP_5,
                minimum=0.0,
                maximum=SAMPLE_RATE / 2,
                unit="Hz",
                accepts_signal=True,
            )
        }
    )

    def __init__(self):
        self.phase = 0.0
        self._clock = 0
        self._registers: Mapping[str, object] = MappingProxyType({})
        self._writes: tuple[RegisterWrite, ...] = ()

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        self._clock = clock
        self._registers = registers
        self._writes = writes

    def process(self, bus: Bus) -> Bus:
        output = bus.current.data
        frequency = cast(float | ndarray, self._registers["frequency"])
        start = 0
        for write in self._writes:
            stop = write.timestamp - self._clock
            self._generate(output, start, stop, frequency)
            for _name, value in write.values:
                frequency = cast(float, value)
            start = stop
        self._generate(output, start, BLOCK_SIZE, frequency)
        return bus

    def _generate(
        self,
        output: ndarray,
        start: int,
        stop: int,
        frequency: float | ndarray,
    ):
        length = stop - start
        if length == 0:
            return
        samples = output[start:stop]
        if isinstance(frequency, ndarray):
            # A moving frequency cannot be multiplied into a ramp: phase is
            # the INTEGRAL of frequency, so a changing pitch needs the running
            # sum of every step so far. `cumsum` includes the current sample,
            # which would advance phase one step early, so it is subtracted
            # back off -- the first sample of a block must continue exactly
            # where the last one left off.
            frequencies = frequency[start:stop]
            scale = 2.0 * pi / SAMPLE_RATE
            cumsum(frequencies, out=samples)
            subtract(samples, frequencies, out=samples)
            multiply(samples, scale, out=samples)
            add(samples, self.phase, out=samples)
            sin(samples, out=samples)
            # Summed in float64: accumulating a block's worth of phase in
            # float32 drifts audibly over a long note.
            self.phase = (
                self.phase + float(frequencies.sum(dtype=float64)) * scale
            ) % (2.0 * pi)
            return

        step = 2.0 * pi * frequency / SAMPLE_RATE
        multiply(_SAMPLE_OFFSETS[:length], step, out=samples)
        add(samples, self.phase, out=samples)
        sin(samples, out=samples)
        self.phase = (self.phase + length * step) % (2.0 * pi)

    def _output_quiescent(self, output: Bus, input_quiescent: bool) -> bool:
        return False
