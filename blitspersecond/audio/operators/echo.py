from numbers import Integral
from types import MappingProxyType

from numpy import zeros

from ..common import Bus, FRAME_SIZE, RegisterSpec, RegisterWrite
from .delay import MAX_DELAY_TICKS
from .operator import Operator


class Echo(Operator):
    """Dry/wet feedback echo with an explicit preparation-time memory bound."""

    def __init__(
        self,
        delay_ticks: int,
        feedback: float = 0.0,
        mix: float = 0.5,
        max_delay_ticks: int | None = None,
    ):
        if not isinstance(delay_ticks, Integral) or isinstance(delay_ticks, bool):
            raise TypeError("echo delay must be an integer number of ticks")
        delay_ticks = int(delay_ticks)
        if max_delay_ticks is None:
            max_delay_ticks = delay_ticks
        if not isinstance(max_delay_ticks, Integral) or isinstance(
            max_delay_ticks, bool
        ):
            raise TypeError("maximum echo delay must be an integer number of ticks")
        max_delay_ticks = int(max_delay_ticks)
        if max_delay_ticks < 1 or max_delay_ticks > MAX_DELAY_TICKS:
            raise ValueError(
                f"maximum echo delay must be between 1 and {MAX_DELAY_TICKS}"
            )

        self.registers = MappingProxyType(
            {
                "delay_ticks": RegisterSpec(
                    delay_ticks,
                    minimum=1,
                    maximum=max_delay_ticks,
                    unit="ticks",
                ),
                "feedback": RegisterSpec(
                    float(feedback), minimum=-0.999, maximum=0.999
                ),
                "mix": RegisterSpec(float(mix), minimum=0.0, maximum=1.0),
            }
        )
        self._buffer = zeros(max_delay_ticks + 1, dtype="float32")
        self._position = 0
        self._clock = 0
        self._registers = MappingProxyType({})
        self._writes = ()

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        self._clock = clock
        self._registers = registers
        self._writes = writes

    def process(self, bus: Bus) -> Bus:
        delay_ticks = self._registers["delay_ticks"]
        feedback = self._registers["feedback"]
        mix = self._registers["mix"]
        start = 0
        for write in self._writes:
            stop = write.timestamp - self._clock
            self._apply(bus, start, stop, delay_ticks, feedback, mix)
            for name, value in write.values:
                if name == "delay_ticks":
                    delay_ticks = value
                elif name == "feedback":
                    feedback = value
                else:
                    mix = value
            start = stop
        self._apply(bus, start, FRAME_SIZE, delay_ticks, feedback, mix)
        return bus

    def _output_quiescent(self, output: Bus, input_quiescent: bool) -> bool:
        return self._tail_quiescent(output, input_quiescent, self._buffer)

    def _apply(self, bus, start, stop, delay_ticks, feedback, mix):
        samples = bus.current.data
        buffer = self._buffer
        capacity = len(buffer)
        position = self._position
        for index in range(start, stop):
            read = (position - delay_ticks) % capacity
            delayed = float(buffer[read])
            dry = float(samples[index])
            buffer[position] = dry + feedback * delayed
            samples[index] = dry + mix * delayed
            position = (position + 1) % capacity
        self._position = position
