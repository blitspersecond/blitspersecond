from types import MappingProxyType
from typing import cast

from numpy import absolute, add, multiply, remainder, subtract

from ..common import Bus, FRAME_SIZE, RegisterSpec, RegisterWrite, SAMPLE_RATE
from ..common.constants import _SAMPLE_OFFSETS
from .operator import Operator
from .sine import C_SHARP_5


class Triangle(Operator):
    """Generate a continuous bipolar triangle carrier."""

    registers = MappingProxyType(
        {
            "frequency": RegisterSpec(
                C_SHARP_5,
                minimum=0.0,
                maximum=SAMPLE_RATE / 2,
                unit="Hz",
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

    def process(self, *inputs: Bus) -> Bus:
        if len(inputs) != 1:
            raise ValueError(f"Triangle requires one input, got {len(inputs)}")
        bus = inputs[0]
        output = bus.current.data
        frequency = cast(float, self._registers["frequency"])
        start = 0
        for write in self._writes:
            stop = write.timestamp - self._clock
            self._generate(output, start, stop, frequency)
            for _name, value in write.values:
                frequency = cast(float, value)
            start = stop
        self._generate(output, start, FRAME_SIZE, frequency)
        return bus

    def _generate(self, output, start: int, stop: int, frequency: float):
        length = stop - start
        if length == 0:
            return
        step = frequency / SAMPLE_RATE
        samples = output[start:stop]
        multiply(_SAMPLE_OFFSETS[:length], step, out=samples)
        add(samples, self.phase, out=samples)
        remainder(samples, 1.0, out=samples)
        subtract(samples, 0.5, out=samples)
        absolute(samples, out=samples)
        multiply(samples, 4.0, out=samples)
        subtract(samples, 1.0, out=samples)
        self.phase = (self.phase + length * step) % 1.0
