from types import MappingProxyType
from typing import cast

from numpy import multiply, ndarray

from ..common import Bus, FRAME_SIZE, RegisterSpec, RegisterWrite
from .operator import Operator


class Gain(Operator):
    def __init__(self, level: float = 1.0):
        self.registers = MappingProxyType(
            {
                "level": RegisterSpec(
                    float(level),
                    unit="linear",
                    accepts_signal=True,
                )
            }
        )
        self._clock = 0
        self._registers = MappingProxyType({})
        self._writes = ()

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        self._clock = clock
        self._registers = registers
        self._writes = writes

    def process(self, bus: Bus) -> Bus:
        output = bus.current.data
        level = cast(float | ndarray, self._registers["level"])
        if (
            not isinstance(level, ndarray)
            and level == 1.0
            and not self._writes
        ):
            return bus

        start = 0
        for write in self._writes:
            stop = write.timestamp - self._clock
            self._apply(output, start, stop, level)
            for _name, value in write.values:
                level = cast(float, value)
            start = stop
        self._apply(output, start, FRAME_SIZE, level)
        return bus

    def _output_quiescent(self, output: Bus, input_quiescent: bool) -> bool:
        return input_quiescent

    @staticmethod
    def _apply(
        output: ndarray,
        start: int,
        stop: int,
        level: float | ndarray,
    ):
        if isinstance(level, ndarray):
            multiply(
                output[start:stop],
                level[start:stop],
                out=output[start:stop],
            )
        elif level != 1.0:
            output[start:stop] *= level
