"""`Duck`: push one signal down out of the way of another."""

import math
from collections.abc import Mapping
from types import MappingProxyType

from ..bus import Bus
from ...constants import BLOCK_SIZE, SAMPLE_RATE
from ..registers import RegisterSpec, RegisterWrite
from .operator import Compositor


class Duck(Compositor):
    """Attenuate the first input by how loud the second one is.

    Two lanes, and **their order is the meaning**: lane zero is the signal
    that gets pushed down, lane one is the control that does the pushing.
    The control is never mixed into the output -- it is only listened to --
    which is what makes this different from any amount of mixing.

    What it is for: a pad or a noise bed that needs to get out of the way
    every time something hits, without anybody automating a level by hand.
    Set `depth` for how far it drops, `attack` for how fast it gets out of
    the way, `release` for how slowly it comes back.
    """

    def __init__(self, depth=1.0, attack=0.005, release=0.08):
        self.registers = MappingProxyType(
            {
                "depth": RegisterSpec(float(depth), minimum=0.0),
                "attack": RegisterSpec(float(attack), minimum=0.0, unit="seconds"),
                "release": RegisterSpec(
                    float(release), minimum=0.0, unit="seconds"
                ),
            }
        )
        #: The follower's current estimate of how loud the control lane is.
        #: Carried across blocks: a release longer than 8ms spans several.
        self.envelope = 0.0
        self._clock = 0
        self._registers: Mapping[str, object] = MappingProxyType({})
        self._writes: tuple[RegisterWrite, ...] = ()

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        self._clock = clock
        self._registers = registers
        self._writes = writes

    def process(self, *inputs: Bus) -> Bus:
        if len(inputs) != 2:
            raise ValueError(f"Duck requires two inputs, got {len(inputs)}")
        signal, control = inputs
        depth = self._registers["depth"]
        attack = self._registers["attack"]
        release = self._registers["release"]
        start = 0
        for write in self._writes:
            stop = write.timestamp - self._clock
            self._apply(signal, control, start, stop, depth, attack, release)
            for name, value in write.values:
                if name == "depth":
                    depth = value
                elif name == "attack":
                    attack = value
                else:
                    release = value
            start = stop
        self._apply(signal, control, start, BLOCK_SIZE, depth, attack, release)
        return signal

    def _apply(self, signal, control, start, stop, depth, attack, release):
        # A per-sample Python loop, and genuinely serial: each envelope value
        # depends on the one before it, AND on a comparison that chooses
        # which coefficient to use. Unlike `Reverb`'s combs there is no
        # window in which the recurrence unrolls, so this is the operator
        # with the strongest case for a compiled kernel. See the board.
        attack_coefficient = self._coefficient(attack)
        release_coefficient = self._coefficient(release)
        output = signal.current.data
        sidechain = control.current.data
        envelope = self.envelope
        for index in range(start, stop):
            value = abs(float(sidechain[index]))
            # Rising uses `attack`, falling uses `release`. That asymmetry is
            # the whole behaviour: grab quickly, let go slowly.
            coefficient = (
                attack_coefficient if value > envelope else release_coefficient
            )
            envelope = coefficient * envelope + (1.0 - coefficient) * value
            output[index] *= max(1.0 - depth * envelope, 0.0)
        self.envelope = envelope

    @staticmethod
    def _coefficient(seconds):
        """The one-pole coefficient that reaches 1/e of the way in `seconds`."""
        if seconds == 0.0:
            return 0.0
        return math.exp(-1.0 / (seconds * SAMPLE_RATE))
