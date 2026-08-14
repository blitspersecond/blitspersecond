from numbers import Integral
from types import MappingProxyType
from typing import cast

from ..common import Bus, FRAME_SIZE, RegisterSpec, RegisterWrite, SAMPLE_RATE
from .operator import Operator


class Pulse(Operator):
    """Emit a fixed-length unit pulse for each rising gate edge."""

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
        self._registers = MappingProxyType({})
        self._writes = ()
        self._remaining = 0
        self._last_clock: int | None = None

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        self._clock = clock
        self._registers = registers
        self._writes = writes

    def process(self, *inputs: Bus) -> Bus:
        if len(inputs) != 1:
            raise ValueError(f"Pulse requires one input, got {len(inputs)}")
        bus = inputs[0]
        self._active_this_frame = False
        if (
            self._last_clock is None
            or self._clock != self._last_clock + FRAME_SIZE
        ):
            self._remaining = 0

        output = bus.current.data
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
        self._apply(output, start, FRAME_SIZE)
        self._last_clock = self._clock
        return bus

    def _output_quiescent(self, output: Bus, input_quiescent: bool) -> bool:
        return not self._active_this_frame and self._remaining == 0

    def _apply(self, output, start: int, stop: int):
        length = min(stop - start, self._remaining)
        if length:
            output[start : start + length] = 1.0
            self._remaining -= length
            self._active_this_frame = True
