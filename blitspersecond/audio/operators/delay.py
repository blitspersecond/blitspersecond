from numbers import Integral
from types import MappingProxyType

from ..common import Bus, RegisterSpec, RegisterWrite, SAMPLE_RATE
from .operator import Operator

MAX_DELAY_SECONDS = 4
MAX_DELAY_TICKS = MAX_DELAY_SECONDS * SAMPLE_RATE


class Delay(Operator):
    """Shift this lane by an integer number of sample-clock ticks."""

    def __init__(self, ticks: int = 1):
        if not isinstance(ticks, Integral) or isinstance(ticks, bool):
            raise TypeError("delay must be an integer number of ticks")
        ticks = int(ticks)
        self.registers = MappingProxyType(
            {
                "delay_ticks": RegisterSpec(
                    ticks,
                    minimum=0,
                    maximum=MAX_DELAY_TICKS,
                    unit="ticks",
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
        ticks = self._registers["delay_ticks"]
        # A boundary write affects this Frame; an intra-Frame write settles
        # into the Stage bank for the following Frame.
        for write in self._writes:
            if write.timestamp == self._clock:
                for _name, value in write.values:
                    ticks = value
        bus.delay(self, ticks)
        return bus

    def _output_quiescent(self, output: Bus, input_quiescent: bool) -> bool:
        return output.current.quiescent
