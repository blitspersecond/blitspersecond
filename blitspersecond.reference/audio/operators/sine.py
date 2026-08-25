from types import MappingProxyType
from typing import cast

from numpy import add, cumsum, float64, multiply, ndarray, pi, sin, subtract

from ..common import Bus, FRAME_SIZE, RegisterSpec, RegisterWrite, SAMPLE_RATE
from ..common.constants import _SAMPLE_OFFSETS
from .operator import Operator

C_SHARP_5 = 554.3652619537442


class Sine(Operator):
    """Generate a continuous, frequency-controlled sine carrier."""

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
        self._registers = MappingProxyType({})
        self._writes = ()

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
        self._generate(output, start, FRAME_SIZE, frequency)
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
            frequencies = frequency[start:stop]
            scale = 2.0 * pi / SAMPLE_RATE
            cumsum(frequencies, out=samples)
            subtract(samples, frequencies, out=samples)
            multiply(samples, scale, out=samples)
            add(samples, self.phase, out=samples)
            sin(samples, out=samples)
            self.phase = (
                self.phase + float(frequencies.sum(dtype=float64)) * scale
            ) % (2.0 * pi)
            return

        step = 2.0 * pi * frequency / SAMPLE_RATE
        multiply(_SAMPLE_OFFSETS[:length], step, out=samples)
        add(samples, self.phase, out=samples)
        sin(samples, out=samples)
        self.phase = (self.phase + length * step) % (2.0 * pi)
