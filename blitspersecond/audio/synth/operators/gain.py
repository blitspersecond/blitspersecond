"""`Gain`: scale a lane, and the pattern every controlled Operator follows."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import cast

from numpy import multiply, ndarray

from ..bus import Bus
from ...constants import BLOCK_SIZE
from ..registers import RegisterSpec, RegisterWrite
from .operator import Operator


class Gain(Operator):
    """Multiply a lane by `level`, at sample accuracy.

    **This is the reference implementation of the segmented write.** A block
    is 400 samples and a write lands at a sample, not at a block boundary, so
    the block is cut at each write timestamp and each piece is scaled by the
    value in force across it. Every operator that reads a register does this;
    Gain is the smallest place to see it whole.

    `level` accepts a signal, so another Stage's audio can drive it at sample
    rate -- a tremolo is an oscillator connected here, not a feature of Gain.
    The scalar and the signal take the same multiply, because NumPy broadcasts
    the scalar rather than building 400 copies of it.

    There is no minimum or maximum. Negative is not a mistake: it inverts
    phase, which is a real thing to ask for. See the board card on register
    ranges for what that leaves open.
    """

    def __init__(self, level: float = 1.0):
        # On the instance, not the class: the default comes from construction,
        # so each Gain declares its own.
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
        # Annotated rather than inferred: bare `()` infers as the empty-tuple
        # type, so anything reading these before `control` runs is told the
        # tuple can never have anything in it.
        self._registers: Mapping[str, object] = MappingProxyType({})
        self._writes: tuple[RegisterWrite, ...] = ()

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        self._clock = clock
        self._registers = registers
        self._writes = writes

    def process(self, bus: Bus) -> Bus:
        output = bus.current.data
        level = cast(float | ndarray, self._registers["level"])
        # Unity with nothing to do leaves the block alone. This is the common
        # case, not an optimisation for a rare one: every connection carries a
        # structural Gain and most of them are never written to.
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
            # Every pair here is a level: `control` filtered these writes to
            # the registers this operator declared, and Gain declares one.
            for _name, value in write.values:
                level = cast(float, value)
            start = stop
        self._apply(output, start, BLOCK_SIZE, level)
        return bus

    def _output_quiescent(self, output: Bus, input_quiescent: bool) -> bool:
        # Stateless: quiet in, quiet out. Deliberately does NOT claim silence
        # for `level == 0.0` -- the answer would be right but the level can
        # change back on the next block, and nothing upstream would have been
        # woken to notice.
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
