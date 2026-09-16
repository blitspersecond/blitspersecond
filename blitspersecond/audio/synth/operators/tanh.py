"""`Tanh`: saturation that softens rather than squares off."""

import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import cast

from numpy import multiply, tanh as numpy_tanh

from ..bus import Bus
from ...constants import BLOCK_SIZE
from ..registers import RegisterSpec, RegisterWrite
from .operator import Operator


class Tanh(Operator):
    """Hyperbolic-tangent saturation, normalised so unity stays unity.

    **`drive` changes the shape, not the level.** The output is divided by
    `tanh(drive)`, so a full-scale input still comes out at full scale however
    hard it is driven -- turning it up adds harmonics instead of volume. That
    is what makes it usable mid-chain without re-balancing everything after it.

    Unlike hard clipping this has no corner: the curve bends continuously, so
    it adds warmth and body where a hard clip adds a buzz. Both are wanted for
    different reasons, which is why the vocabulary has both.
    """

    def __init__(self, drive: float = 2.0):
        self.registers = MappingProxyType(
            {"drive": RegisterSpec(float(drive), minimum=1e-6, unit="linear")}
        )
        self._clock = 0
        self._registers: Mapping[str, object] = MappingProxyType({})
        self._writes: tuple[RegisterWrite, ...] = ()

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        self._clock = clock
        self._registers = registers
        self._writes = writes

    def process(self, bus: Bus) -> Bus:
        drive = cast(float, self._registers["drive"])
        start = 0
        for write in self._writes:
            stop = write.timestamp - self._clock
            self._apply(bus, start, stop, drive)
            for _name, value in write.values:
                drive = cast(float, value)
            start = stop
        self._apply(bus, start, BLOCK_SIZE, drive)
        return bus

    def _output_quiescent(self, output: Bus, input_quiescent: bool) -> bool:
        # Memoryless: it holds nothing, so quiet in is quiet out.
        return input_quiescent

    @staticmethod
    def _apply(bus: Bus, start: int, stop: int, drive: float):
        samples = bus.current.data[start:stop]
        multiply(samples, drive, out=samples)
        numpy_tanh(samples, out=samples)
        samples /= math.tanh(drive)
