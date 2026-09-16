"""`Delay`: move this lane backwards in time."""

from collections.abc import Mapping
from numbers import Integral
from types import MappingProxyType
from typing import cast

from ..bus import Bus
from ...constants import SAMPLE_RATE
from ..registers import RegisterSpec, RegisterWrite
from .operator import Operator

#: Four seconds. Not a DSP limit but a memory one: a delay's ring costs
#: 1.6KB per block of depth, so this bounds one lane at about 768KB.
MAX_DELAY_SECONDS = 4
MAX_DELAY_TICKS = MAX_DELAY_SECONDS * SAMPLE_RATE


class Delay(Operator):
    """Shift the lane by a whole number of samples.

    The thinnest possible operator: all the machinery is the Bus's ring, and
    this only says how far back to reach. Two Delays in one chain get
    separate rings keyed on the operator, so their positions cannot alias.

    **A `delay_ticks` write only takes effect on a block boundary.** Moving
    the read head part-way through a block has no coherent meaning -- the
    samples before the write came from one distance and the ones after would
    come from another, with a discontinuity between them. So an intra-block
    write settles into the bank and applies from the next block.
    """

    def __init__(self, ticks: int = 1):
        if not isinstance(ticks, Integral) or isinstance(ticks, bool):
            raise TypeError("delay must be an integer number of ticks")
        ticks = int(ticks)
        self.registers = MappingProxyType(
            {
                "delay_ticks": RegisterSpec(
                    ticks, minimum=0, maximum=MAX_DELAY_TICKS, unit="ticks"
                )
            }
        )
        self._clock = 0
        self._registers: Mapping[str, object] = MappingProxyType({})
        self._writes: tuple[RegisterWrite, ...] = ()

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        self._clock = clock
        self._registers = registers
        self._writes = writes

    def process(self, bus: Bus) -> Bus:
        ticks = cast(int, self._registers["delay_ticks"])
        for write in self._writes:
            if write.timestamp == self._clock:
                for _name, value in write.values:
                    ticks = cast(int, value)
        bus.delay(self, ticks)
        return bus

    def _output_quiescent(self, output: Bus, input_quiescent: bool) -> bool:
        # The ring knows: it stays awake while anything it holds has yet to
        # come out, even when its input has already stopped.
        return output.current.quiescent
