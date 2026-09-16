"""The shared phasor: a running ramp, and one line of maths per waveform.

Square, Saw and Triangle differ ONLY in what they do to a 0..1 ramp. Keeping
the ramp here means each waveform file is the arithmetic that distinguishes
it and nothing else -- and when something has to change about how phase is
carried, it changes in one place rather than three.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import cast

from numpy import add, multiply, ndarray, remainder

from ..bus import Bus
from ...constants import BLOCK_SIZE, SAMPLE_RATE, _SAMPLE_OFFSETS
from ..registers import RegisterSpec, RegisterWrite
from .operator import Operator

#: The default note, C#5. An arbitrary pitch that is audibly *a note* rather
#: than a round number -- an oscillator nobody has tuned should sound like it
#: was left untuned, not like a test tone.
C_SHARP_5 = 554.3652619537442


class _Oscillator(Operator):
    """A free-running ramp, cut at every frequency write.

    **Phase is normalised to 0..1, not radians**, because every waveform
    below is a piecewise function of position-within-a-cycle. Only Sine wants
    radians, which is one of the reasons it does not live here.

    **Phase is never reset by a frequency change.** A write changes the step
    from that sample onward and the ramp carries on from where it was, so
    retuning a held note bends it rather than restarting it. That is also why
    a sleeping Stage freezes phase instead of racing it forward: on waking,
    the carrier continues rather than jumping.
    """

    registers = MappingProxyType(
        {
            "frequency": RegisterSpec(
                C_SHARP_5,
                minimum=0.0,
                maximum=SAMPLE_RATE / 2,
                unit="Hz",
            )
        }
    )

    def __init__(self):
        self.phase = 0.0
        self._clock = 0
        self._registers: Mapping[str, object] = MappingProxyType({})
        self._writes: tuple[RegisterWrite, ...] = ()

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        self._clock = clock
        self._registers = registers
        self._writes = writes

    def process(self, bus: Bus) -> Bus:
        output = bus.current.data
        frequency = cast(float, self._registers["frequency"])
        start = 0
        for write in self._writes:
            stop = write.timestamp - self._clock
            self._generate(output, start, stop, frequency)
            for _name, value in write.values:
                frequency = cast(float, value)
            start = stop
        self._generate(output, start, BLOCK_SIZE, frequency)
        return bus

    def _generate(self, output: ndarray, start: int, stop: int, frequency: float):
        length = stop - start
        if length == 0:
            return
        step = frequency / SAMPLE_RATE
        samples = output[start:stop]
        # Built in the output's own memory: the ramp becomes the waveform in
        # place, so a block of audio costs no temporary array.
        multiply(_SAMPLE_OFFSETS[:length], step, out=samples)
        add(samples, self.phase, out=samples)
        remainder(samples, 1.0, out=samples)
        self._shape(samples)
        # Advanced by the segment's own length, so the cuts made by writes
        # cannot accumulate a phase error across a block.
        self.phase = (self.phase + length * step) % 1.0

    def _shape(self, ramp: ndarray):
        """Turn one 0..1 ramp into this waveform, in place."""
        raise NotImplementedError

    def _output_quiescent(self, output: Bus, input_quiescent: bool) -> bool:
        # An oscillator is never finished: it is a carrier, and something
        # downstream (an Envelope, a Gain) decides whether it is heard. Saying
        # otherwise would let a held note be skipped mid-sustain.
        return False
