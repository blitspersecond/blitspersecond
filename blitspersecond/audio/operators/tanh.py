import math
from types import MappingProxyType

from numpy import multiply, tanh as numpy_tanh

from ..common import Bus, FRAME_SIZE, RegisterSpec, RegisterWrite
from .operator import Operator


class Tanh(Operator):
    """Apply normalized hyperbolic-tangent saturation in place."""

    def __init__(self, drive: float = 2.0):
        self.registers = MappingProxyType(
            {"drive": RegisterSpec(float(drive), minimum=1e-6, unit="linear")}
        )
        self._clock = 0
        self._registers = MappingProxyType({})
        self._writes = ()

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        self._clock = clock
        self._registers = registers
        self._writes = writes

    def process(self, bus: Bus) -> Bus:
        drive = self._registers["drive"]
        start = 0
        for write in self._writes:
            stop = write.timestamp - self._clock
            self._apply(bus, start, stop, drive)
            for _name, value in write.values:
                drive = value
            start = stop
        self._apply(bus, start, FRAME_SIZE, drive)
        return bus

    def _output_quiescent(self, output: Bus, input_quiescent: bool) -> bool:
        return input_quiescent

    @staticmethod
    def _apply(bus: Bus, start: int, stop: int, drive: float):
        samples = bus.current.data[start:stop]
        multiply(samples, drive, out=samples)
        numpy_tanh(samples, out=samples)
        samples /= math.tanh(drive)
