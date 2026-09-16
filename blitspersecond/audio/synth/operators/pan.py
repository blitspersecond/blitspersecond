"""`_Pan`: place one mono signal between two speakers, at constant power."""

from math import cos, pi, sin
from collections.abc import Mapping
from types import MappingProxyType
from typing import cast

from numpy import add, clip, cos as np_cos, multiply, ndarray, sin as np_sin

from ..bus import Bus, StereoBus
from ...constants import BLOCK_SIZE
from ..registers import RegisterScope, RegisterSpec, RegisterWrite
from .operator import Operator


class _Pan(Operator):
    """One desk strip's stereo placement: -1 hard left, 0 centre, +1 right.

    **Constant power, not constant amplitude.** The two gains are `cos` and
    `sin` of one quarter-turn, so their squares always sum to one: a sound
    swept across the image holds its loudness, with no dip as it crosses the
    middle.

    Centre is `sqrt(0.5)` in each channel rather than 1.0, and that is not a
    centred signal being made quieter -- the PAIR still carries the same power
    as a hard-panned one, which is the whole point. Measured against a
    hard-panned signal, centre is +0.00 dB in stereo.

    What it does cost is mono. Folded to one channel the two halves add as
    amplitude rather than power, so a centred signal comes back **+3 dB**
    against a hard-panned one. That is the real trade this law makes, and the
    reason DAWs offer several: constant amplitude is the mirror image, mono-
    safe but with a 3 dB hole in the middle of every stereo sweep. Whether
    this is the law BPS wants is an open question; see the board.

    `sqrt((1-p)/2)` and `sqrt((1+p)/2)` are equally constant-power and need no
    transcendentals. They agree with these exactly at hard left, centre and
    hard right, and differ in between -- this form is linear in ANGLE, that
    one linear in power. Cost is not the reason to choose: the scalar path
    here computes one cos and one sin per SEGMENT, not per sample, which
    measured at 0.029% of a block budget per strip.

    **An Operator for its registers, not for the chain.** It inherits the
    scope, register and `control` machinery, but mono in and stereo out
    cannot wear the unary `process(bus)` shape, so the work happens in
    `route()` and this never appears in a Program. It is structural: the desk
    builds one per strip and nothing else may.
    """

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
        self._registers: Mapping[str, object] = MappingProxyType({})
        self._writes: tuple[RegisterWrite, ...] = ()

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        self._clock = clock
        self._registers = registers
        self._writes = writes

    def route(self, source: Bus, output: StereoBus) -> StereoBus:
        """Write the mono lane into both channels, cut at each write."""
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
        self._apply(incoming, outgoing, start, BLOCK_SIZE, pan)
        # Panning neither adds energy nor retains any, so the strip has
        # settled exactly when the lane feeding it has.
        output.current.quiescent = source.current.quiescent
        return output

    @staticmethod
    def _apply(
        incoming: ndarray,
        outgoing: ndarray,
        start: int,
        stop: int,
        pan: float | ndarray,
    ):
        if start == stop:
            return
        if isinstance(pan, ndarray):
            # Signal-rate, and allocation-free: the output's own two channels
            # are the scratch space. The left channel holds the angles while
            # they are built, the right takes sin() of them, then the left is
            # overwritten by cos() and both are multiplied by the input. A
            # signal is not validated by a RegisterSpec the way a scheduled
            # write is, so it is clipped here rather than trusted.
            stereo = outgoing[start:stop]
            angles = stereo[:, 0]
            clip(pan[start:stop], -1.0, 1.0, out=angles)
            add(angles, 1.0, out=angles)
            multiply(angles, pi / 4.0, out=angles)
            np_sin(angles, out=stereo[:, 1])
            np_cos(angles, out=angles)
            multiply(incoming[start:stop], angles, out=angles)
            multiply(incoming[start:stop], stereo[:, 1], out=stereo[:, 1])
            return
        # The endpoints are spelled out to land on exact 1.0 and 0.0 rather
        # than on cos/sin of a rounded quarter-turn. Not a range guard: a
        # scheduled write is already bounded to [-1, 1] by its RegisterSpec.
        if pan <= -1.0:
            left, right = 1.0, 0.0
        elif pan >= 1.0:
            left, right = 0.0, 1.0
        else:
            angle = (pan + 1.0) * pi / 4.0
            left, right = cos(angle), sin(angle)
        multiply(incoming[start:stop], left, out=outgoing[start:stop, 0])
        multiply(incoming[start:stop], right, out=outgoing[start:stop, 1])
