from types import MappingProxyType
from typing import cast

from numpy import clip

from ..common import Bus, FRAME_SIZE, RegisterSpec, RegisterWrite
from .operator import Operator


class Clip(Operator):
    """Hard-clip this lane symmetrically at a live level."""

    def __init__(self, level: float = 1.0):
        self.registers = MappingProxyType(
            {"clip_level": RegisterSpec(float(level), minimum=0.0)}
        )
        self._clock = 0
        self._registers = MappingProxyType({})
        self._writes = ()

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        self._clock = clock
        self._registers = registers
        self._writes = writes

    def process(self, bus: Bus) -> Bus:
        level = cast(float, self._registers["clip_level"])
        start = 0
        for write in self._writes:
            stop = write.timestamp - self._clock
            self._apply(bus, start, stop, level)
            for _name, value in write.values:
                level = cast(float, value)
            start = stop
        self._apply(bus, start, FRAME_SIZE, level)
        return bus

    def _output_quiescent(self, output: Bus, input_quiescent: bool) -> bool:
        return input_quiescent

    @staticmethod
    def _apply(bus: Bus, start: int, stop: int, level: float):
        samples = bus.current.data[start:stop]
        clip(samples, -level, level, out=samples)
