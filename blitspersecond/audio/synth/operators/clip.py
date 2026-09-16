"""`Clip`: saturation with a corner, where `Tanh` has a curve."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import cast

from numpy import clip as numpy_clip

from ..bus import Bus
from ...constants import BLOCK_SIZE
from ..registers import RegisterSpec, RegisterWrite
from .operator import Operator


class Clip(Operator):
    """Hard-limit this lane symmetrically at a live level.

    **The corner is the whole point.** The transfer curve is a straight line
    with a sudden bend at the ceiling, and a bend that sharp throws a long
    series of odd harmonics -- an edge, a buzz, a fuzzbox. `Tanh` bends the
    same signal continuously and so adds body instead. The two are not rival
    implementations of one idea; they sound different because the shape of the
    corner is what a listener is hearing.

    **Unlike `Tanh`, nothing is normalised afterwards.** `clip_level` is the
    ceiling in the literal sense, so lowering it makes the signal quieter as
    well as dirtier. That is the honest reading of a ceiling and it means one
    operator covers both jobs: leave the level at unity and it is a safety
    limiter that only acts on peaks, pull it down to 0.2 and it is an effect
    that the whole signal lives inside.

    A ceiling of zero is silence. It is not special-cased because it is not a
    special case -- the signal is held between zero and zero.
    """

    def __init__(self, level: float = 1.0):
        self.registers = MappingProxyType(
            {"clip_level": RegisterSpec(float(level), minimum=0.0, unit="linear")}
        )
        self._clock = 0
        self._registers: Mapping[str, object] = MappingProxyType({})
        self._writes: tuple[RegisterWrite, ...] = ()

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
        self._apply(bus, start, BLOCK_SIZE, level)
        return bus

    def _output_quiescent(self, output: Bus, input_quiescent: bool) -> bool:
        # Memoryless: it holds nothing, so quiet in is quiet out.
        return input_quiescent

    @staticmethod
    def _apply(bus: Bus, start: int, stop: int, level: float):
        samples = bus.current.data[start:stop]
        numpy_clip(samples, -level, level, out=samples)
