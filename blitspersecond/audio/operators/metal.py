from types import MappingProxyType

from numpy import (
    add,
    array,
    bool_,
    copyto,
    empty,
    float64,
    isfinite,
    less,
    multiply,
    remainder,
    zeros,
)

from ..common import Bus, FRAME_SIZE, RegisterSpec, RegisterWrite, SAMPLE_RATE
from ..common.constants import _SAMPLE_OFFSETS
from .operator import Operator


class Metal(Operator):
    """Generate a fixed bank of rectangular metallic partials."""

    def __init__(self, frequencies, tune: float = 1.0, duty: float = 0.48):
        frequencies = array(tuple(frequencies), dtype=float64)
        if frequencies.ndim != 1 or len(frequencies) == 0:
            raise ValueError("metal requires a non-empty frequency sequence")
        if not isfinite(frequencies).all() or (frequencies <= 0.0).any():
            raise ValueError("metal frequencies must be finite and positive")
        frequencies.setflags(write=False)

        self.registers = MappingProxyType(
            {
                "tune": RegisterSpec(
                    float(tune),
                    minimum=0.0,
                    maximum=(SAMPLE_RATE / 2) / float(frequencies.max()),
                    unit="ratio",
                ),
                "duty": RegisterSpec(
                    float(duty), minimum=0.0, maximum=1.0, unit="ratio"
                ),
            }
        )
        self.frequencies = frequencies
        self.phase = zeros(len(frequencies), dtype=float64)
        self._cycles = empty(FRAME_SIZE, dtype=float64)
        self._wave = empty(FRAME_SIZE, dtype="float32")
        self._mask = empty(FRAME_SIZE, dtype=bool_)
        self._clock = 0
        self._registers = MappingProxyType({})
        self._writes = ()

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        self._clock = clock
        self._registers = registers
        self._writes = writes

    def process(self, bus: Bus) -> Bus:
        output = bus.current.data
        tune = self._registers["tune"]
        duty = self._registers["duty"]
        start = 0
        for write in self._writes:
            stop = write.timestamp - self._clock
            self._generate(output, start, stop, tune, duty)
            for name, value in write.values:
                if name == "tune":
                    tune = value
                else:
                    duty = value
            start = stop
        self._generate(output, start, FRAME_SIZE, tune, duty)
        return bus

    def _generate(self, output, start: int, stop: int, tune: float, duty: float):
        length = stop - start
        if length == 0:
            return

        samples = output[start:stop]
        samples.fill(0.0)
        cycles = self._cycles[:length]
        wave = self._wave[:length]
        mask = self._mask[:length]
        for index, frequency in enumerate(self.frequencies):
            step = frequency * tune / SAMPLE_RATE
            multiply(_SAMPLE_OFFSETS[:length], step, out=cycles)
            add(cycles, self.phase[index], out=cycles)
            remainder(cycles, 1.0, out=cycles)
            less(cycles, duty, out=mask)
            wave.fill(-duty)
            copyto(wave, 1.0 - duty, where=mask)
            add(samples, wave, out=samples)
            self.phase[index] = (self.phase[index] + length * step) % 1.0
        multiply(samples, 2.0 / len(self.frequencies), out=samples)
