from types import MappingProxyType

from numpy import empty, zeros

from ..common import Bus, FRAME_SIZE, RegisterSpec, RegisterWrite, SAMPLE_RATE
from .operator import Operator

_COMB_MS = (29.7, 37.1, 41.1, 43.7)
_FEEDBACK = 0.72


class Reverb(Operator):
    """Four parallel feedback combs mixed beneath the dry signal."""

    def __init__(self, amount: float = 0.3):
        self.registers = MappingProxyType(
            {"amount": RegisterSpec(float(amount), minimum=0.0, maximum=1.0)}
        )
        self._buffers = [
            zeros(max(1, round(ms / 1_000 * SAMPLE_RATE)), dtype="float32")
            for ms in _COMB_MS
        ]
        self._positions = [0] * len(self._buffers)
        self._wet = zeros(FRAME_SIZE, dtype="float32")
        self._scratch = empty(FRAME_SIZE, dtype="float32")
        self._clock = 0
        self._registers = MappingProxyType({})
        self._writes = ()

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        self._clock = clock
        self._registers = registers
        self._writes = writes

    def process(self, bus: Bus) -> Bus:
        source = bus.current.data
        wet = self._wet
        wet.fill(0.0)
        for index, buffer in enumerate(self._buffers):
            self._positions[index] = self._comb(
                source,
                buffer,
                self._positions[index],
                wet,
            )

        amount = self._registers["amount"]
        start = 0
        for write in self._writes:
            stop = write.timestamp - self._clock
            source[start:stop] += amount * wet[start:stop] / len(self._buffers)
            for _name, value in write.values:
                amount = value
            start = stop
        source[start:] += amount * wet[start:] / len(self._buffers)
        return bus

    def _output_quiescent(self, output: Bus, input_quiescent: bool) -> bool:
        return self._tail_quiescent(
            output,
            input_quiescent,
            *self._buffers,
        )

    def _comb(self, source, buffer, position, wet):
        offset = 0
        scratch = self._scratch
        while offset < FRAME_SIZE:
            count = min(len(buffer) - position, FRAME_SIZE - offset)
            delayed = scratch[offset : offset + count]
            delayed[:] = buffer[position : position + count]
            wet[offset : offset + count] += delayed
            buffer[position : position + count] = (
                source[offset : offset + count] + _FEEDBACK * delayed
            )
            position = (position + count) % len(buffer)
            offset += count
        return position
