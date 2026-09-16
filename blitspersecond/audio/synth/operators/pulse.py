"""`Pulse`: a short burst of energy, for something else to ring."""

from collections.abc import Mapping
from numbers import Integral
from types import MappingProxyType
from typing import cast

from numpy import ndarray

from ..bus import Bus
from ...constants import BLOCK_SIZE, SAMPLE_RATE
from ..registers import RegisterSpec, RegisterWrite
from .operator import Operator


class Pulse(Operator):
    """A fixed-length burst of 1.0 on each rising gate edge.

    Half of a drum. On its own it is a click; struck into a `Resonator` it
    becomes a tuned body, which is how a kick is made -- the pulse is the
    beater and the resonator is the skin.

    **Only a RISING edge fires it.** Holding the gate open does not extend
    the burst and does not repeat it: the length is `pulse_ticks` and nothing
    else, so two hits need two edges.
    """

    #: On the class so quiescence is answerable before the first render.
    _active_this_block = False

    def __init__(self, ticks: int = 48):
        if not isinstance(ticks, Integral) or isinstance(ticks, bool):
            raise TypeError("pulse duration must be an integer number of ticks")
        self.registers = MappingProxyType(
            {
                "gate": RegisterSpec(False),
                "pulse_ticks": RegisterSpec(
                    int(ticks),
                    minimum=1,
                    maximum=SAMPLE_RATE,
                    unit="ticks",
                ),
            }
        )
        self._clock = 0
        self._registers: Mapping[str, object] = MappingProxyType({})
        self._writes: tuple[RegisterWrite, ...] = ()
        self._remaining = 0
        self._last_clock: int | None = None

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        self._clock = clock
        self._registers = registers
        self._writes = writes

    def process(self, bus: Bus) -> Bus:
        self._active_this_block = False
        if self._last_clock is None or self._clock != self._last_clock + BLOCK_SIZE:
            # A gap means the Stage slept. A burst is milliseconds long, so
            # resuming one that was interrupted would place it wrongly;
            # dropping it is the honest answer.
            self._remaining = 0

        output = bus.current.data
        # A source, so it owns the block outright rather than modifying it.
        output.fill(0.0)
        gate = cast(bool, self._registers["gate"])
        ticks = cast(int, self._registers["pulse_ticks"])
        start = 0
        for write in self._writes:
            stop = write.timestamp - self._clock
            self._apply(output, start, stop)
            for name, value in write.values:
                if name == "gate":
                    value = cast(bool, value)
                    if value and not gate:
                        self._remaining = ticks
                    gate = value
                else:
                    ticks = cast(int, value)
            start = stop
        self._apply(output, start, BLOCK_SIZE)
        self._last_clock = self._clock
        return bus

    def _output_quiescent(self, output: Bus, input_quiescent: bool) -> bool:
        return not self._active_this_block and self._remaining == 0

    def _apply(self, output: ndarray, start: int, stop: int):
        length = min(stop - start, self._remaining)
        if length:
            output[start : start + length] = 1.0
            self._remaining -= length
            self._active_this_block = True
