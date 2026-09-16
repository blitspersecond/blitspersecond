"""`Reverb`: four combs in parallel, which is enough to suggest a room."""

from collections.abc import Mapping
from types import MappingProxyType

from numpy import empty, zeros

from ..bus import Bus
from ...constants import BLOCK_SIZE, SAMPLE_RATE
from ..registers import RegisterSpec, RegisterWrite
from .operator import Operator

#: Four comb lengths in milliseconds, deliberately not in simple ratios. If
#: they were, their repeats would land on top of each other and the result
#: would ring at one pitch instead of smearing into a tail.
_COMB_MS = (29.7, 37.1, 41.1, 43.7)

#: How much of each comb feeds back. Fixed rather than a register: it sets
#: the tail length, and exposing it alongside `amount` invites the pair that
#: runs away.
_FEEDBACK = 0.72


class Reverb(Operator):
    """A short room, mixed in under the dry signal by `amount`.

    Not a convolution and not trying to be a hall -- four feedback combs is
    the cheap classical trick, and it is enough to stop a dry sound feeling
    like it was recorded in a box.

    **This one vectorises, unlike `Echo` and `Duck`.** Every comb is at least
    29.7ms, which is longer than the 8.33ms block, so the samples a block
    reads back were written by an earlier block and the recurrence never
    closes inside one. That lets it move whole slices at a time instead of
    looping per sample, and is why it is not on the list of operators wanting
    a compiled kernel.
    """

    def __init__(self, amount: float = 0.3):
        self.registers = MappingProxyType(
            {"amount": RegisterSpec(float(amount), minimum=0.0, maximum=1.0)}
        )
        self._buffers = [
            zeros(max(1, round(ms / 1_000 * SAMPLE_RATE)), dtype="float32")
            for ms in _COMB_MS
        ]
        self._positions = [0] * len(self._buffers)
        self._wet = zeros(BLOCK_SIZE, dtype="float32")
        self._scratch = empty(BLOCK_SIZE, dtype="float32")
        self._clock = 0
        self._registers: Mapping[str, object] = MappingProxyType({})
        self._writes: tuple[RegisterWrite, ...] = ()

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
                source, buffer, self._positions[index], wet
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
        # Every comb has to have gone quiet, not just the output: a tail
        # still circulating in one of them has yet to be heard.
        return self._tail_quiescent(output, input_quiescent, *self._buffers)

    def _comb(self, source, buffer, position, wet):
        """One comb, in whole slices, wrapping at most once per block."""
        offset = 0
        scratch = self._scratch
        while offset < BLOCK_SIZE:
            count = min(len(buffer) - position, BLOCK_SIZE - offset)
            delayed = scratch[offset : offset + count]
            delayed[:] = buffer[position : position + count]
            wet[offset : offset + count] += delayed
            buffer[position : position + count] = (
                source[offset : offset + count] + _FEEDBACK * delayed
            )
            position = (position + count) % len(buffer)
            offset += count
        return position
