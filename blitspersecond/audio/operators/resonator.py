import math
from types import MappingProxyType
from typing import cast

from ..common import Bus, RegisterSpec, RegisterWrite, SAMPLE_RATE
from ._biquad import MAXIMUM_FILTER_FREQUENCY, MINIMUM_FILTER_FREQUENCY
from .band_pass import BandPass
from .operator import Operator


class Resonator(Operator):
    """Ring at a frequency for a decay time after receiving excitation."""

    def __init__(self, frequency: float = 120.0, decay: float = 0.035):
        self.registers = MappingProxyType(
            {
                "frequency": RegisterSpec(
                    float(frequency),
                    minimum=MINIMUM_FILTER_FREQUENCY,
                    maximum=MAXIMUM_FILTER_FREQUENCY,
                    unit="Hz",
                ),
                "resonance_decay": RegisterSpec(
                    float(decay),
                    minimum=1.0 / SAMPLE_RATE,
                    unit="seconds",
                ),
            }
        )
        self._filter = BandPass(frequency, self._q(frequency, decay))

    @staticmethod
    def _q(frequency: float, decay: float) -> float:
        return math.pi * frequency * decay

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        frequency = cast(float, registers["frequency"])
        decay = cast(float, registers["resonance_decay"])
        mapped_writes = []
        for write in writes:
            for name, value in write.values:
                if name == "frequency":
                    frequency = cast(float, value)
                else:
                    decay = cast(float, value)
            mapped_writes.append(
                RegisterWrite(
                    write.timestamp,
                    write.stage,
                    (
                        ("cutoff", frequency),
                        ("q", self._q(frequency, decay)),
                    ),
                    write.connection,
                )
            )
        self._filter.control(
            clock,
            {
                "cutoff": registers["frequency"],
                "q": self._q(
                    cast(float, registers["frequency"]),
                    cast(float, registers["resonance_decay"]),
                ),
            },
            tuple(mapped_writes),
        )

    def process(self, bus: Bus) -> Bus:
        return self._filter.process(bus)

    def _output_quiescent(self, output: Bus, input_quiescent: bool) -> bool:
        return self._filter._output_quiescent(output, input_quiescent)
