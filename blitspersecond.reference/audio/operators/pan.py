from math import cos, pi, sin
from types import MappingProxyType
from typing import cast

from numpy import add, clip, cos as np_cos, multiply, ndarray, sin as np_sin

from ..common import (
    Bus,
    FRAME_SIZE,
    RegisterScope,
    RegisterSpec,
    RegisterWrite,
)
from ..common.bus import StereoBus
from .operator import Operator


class _Pan(Operator):
    """Route one mono desk strip into stereo with constant-power pan."""

    scope = RegisterScope.LANE

    def __init__(self):
        self.registers = MappingProxyType(
            {
                "pan": RegisterSpec(
                    0.0,
                    minimum=-1.0,
                    maximum=1.0,
                    unit="position",
                    accepts_signal=True,
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

    def route(self, source: Bus, output: StereoBus) -> StereoBus:
        incoming = source.current.data
        outgoing = output.current.data
        pan = cast(float | ndarray, self._registers["pan"])
        start = 0
        for write in self._writes:
            stop = write.timestamp - self._clock
            self._apply(incoming, outgoing, start, stop, pan)
            for _name, value in write.values:
                pan = cast(float, value)
            start = stop
        self._apply(incoming, outgoing, start, FRAME_SIZE, pan)
        output.current.quiescent = source.current.quiescent
        return output

    @staticmethod
    def _apply(
        incoming,
        outgoing,
        start: int,
        stop: int,
        pan: float | ndarray,
    ):
        if start == stop:
            return
        if isinstance(pan, ndarray):
            stereo = outgoing[start:stop]
            angles = stereo[:, 0]
            clip(pan[start:stop], -1.0, 1.0, out=angles)
            add(angles, 1.0, out=angles)
            multiply(angles, pi / 4.0, out=angles)
            np_sin(angles, out=stereo[:, 1])
            np_cos(angles, out=angles)
            multiply(incoming[start:stop], angles, out=angles)
            multiply(
                incoming[start:stop],
                stereo[:, 1],
                out=stereo[:, 1],
            )
            return
        if pan <= -1.0:
            left, right = 1.0, 0.0
        elif pan >= 1.0:
            left, right = 0.0, 1.0
        else:
            angle = (pan + 1.0) * pi / 4.0
            left, right = cos(angle), sin(angle)
        multiply(incoming[start:stop], left, out=outgoing[start:stop, 0])
        multiply(incoming[start:stop], right, out=outgoing[start:stop, 1])
