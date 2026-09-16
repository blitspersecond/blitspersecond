"""`Resonator`: a body that rings when something hits it."""

import math
from typing import cast

from ..bus import Bus
from ...constants import SAMPLE_RATE
from ..registers import RegisterSpec, RegisterWrite
from ._biquad import MAXIMUM_FILTER_FREQUENCY, MINIMUM_FILTER_FREQUENCY
from .band_pass import BandPass
from .operator import Operator


class Resonator(Operator):
    """Ring at a pitch for a decay time, when excited.

    A very narrow band-pass is a bell. This is that, with the two numbers a
    musician actually wants -- what pitch, and how long it rings -- in place
    of the cutoff and Q that produce them. The mapping is
    `q = pi * frequency * decay`: ringing for twice as long means twice the Q.

    **`resonance_decay`, not `decay`.** The name is deliberately unlike an
    Envelope's, so a Program can hold both and address each without collision.
    Two things that fade are not the same register.
    """

    def __init__(self, frequency: float = 120.0, decay: float = 0.035):
        self.registers = {
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
        self._filter = BandPass(frequency, self._q(frequency, decay))

    @staticmethod
    def _q(frequency: float, decay: float) -> float:
        return math.pi * frequency * decay

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        """Translate musical control into the filter's own registers.

        The writes are rewritten as well as the settled values, so a pitch
        change lands on the same sample it was scheduled for rather than at
        the next block boundary.
        """
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
                    (("cutoff", frequency), ("q", self._q(frequency, decay))),
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
        # The body's ringing is the filter's retained state, so the filter is
        # the thing that knows whether the note has finished.
        return self._filter._output_quiescent(output, input_quiescent)
