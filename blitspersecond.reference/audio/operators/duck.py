import math
from types import MappingProxyType

from ..common import Bus, FRAME_SIZE, RegisterSpec, RegisterWrite, SAMPLE_RATE
from .operator import Compositor


class Duck(Compositor):
    """Attenuate the first input from the second input's envelope."""

    def __init__(self, depth=1.0, attack=0.005, release=0.08):
        self.registers = MappingProxyType(
            {
                "depth": RegisterSpec(float(depth), minimum=0.0),
                "attack": RegisterSpec(
                    float(attack), minimum=0.0, unit="seconds"
                ),
                "release": RegisterSpec(
                    float(release), minimum=0.0, unit="seconds"
                ),
            }
        )
        self.envelope = 0.0
        self._clock = 0
        self._registers = MappingProxyType({})
        self._writes = ()

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
        self._apply(signal, control, start, FRAME_SIZE, depth, attack, release)
        return signal

    def _apply(self, signal, control, start, stop, depth, attack, release):
        attack_coefficient = self._coefficient(attack)
        release_coefficient = self._coefficient(release)
        output = signal.current.data
        sidechain = control.current.data
        envelope = self.envelope
        for index in range(start, stop):
            value = abs(float(sidechain[index]))
            coefficient = (
                attack_coefficient if value > envelope else release_coefficient
            )
            envelope = coefficient * envelope + (1.0 - coefficient) * value
            output[index] *= max(1.0 - depth * envelope, 0.0)
        self.envelope = envelope

    @staticmethod
    def _coefficient(seconds):
        if seconds == 0.0:
            return 0.0
        return math.exp(-1.0 / (seconds * SAMPLE_RATE))
