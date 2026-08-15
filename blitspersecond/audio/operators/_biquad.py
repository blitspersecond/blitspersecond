import math
from types import MappingProxyType

from numpy import array, zeros
from scipy.signal import lfilter

from ..common import Bus, FRAME_SIZE, RegisterSpec, RegisterWrite, SAMPLE_RATE
from .operator import Operator


MINIMUM_FILTER_FREQUENCY = 20.0
MAXIMUM_FILTER_FREQUENCY = 0.45 * SAMPLE_RATE


class _Biquad(Operator):
    """Shared state and live-control mechanics for the biquad filters."""

    def __init__(self, cutoff: float, q: float):
        self.registers = MappingProxyType(
            {
                "cutoff": RegisterSpec(
                    float(cutoff),
                    minimum=MINIMUM_FILTER_FREQUENCY,
                    maximum=MAXIMUM_FILTER_FREQUENCY,
                    unit="Hz",
                ),
                "q": RegisterSpec(float(q), minimum=1e-6),
            }
        )
        self._clock = 0
        self._registers = MappingProxyType({})
        self._writes = ()
        self._key = None
        self._b = None
        self._a = None
        self._zi = zeros(2)

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        self._clock = clock
        self._registers = registers
        self._writes = writes

    def process(self, bus: Bus) -> Bus:
        cutoff = self._registers["cutoff"]
        q = self._registers["q"]
        start = 0
        for write in self._writes:
            stop = write.timestamp - self._clock
            self._apply(bus, start, stop, cutoff, q)
            for name, value in write.values:
                if name == "cutoff":
                    cutoff = value
                else:
                    q = value
            start = stop
        self._apply(bus, start, FRAME_SIZE, cutoff, q)
        return bus

    def _output_quiescent(self, output: Bus, input_quiescent: bool) -> bool:
        return self._tail_quiescent(output, input_quiescent, self._zi)

    def _apply(self, bus: Bus, start: int, stop: int, cutoff: float, q: float):
        if start == stop:
            return
        self._coefficients(cutoff, q)
        output, self._zi = lfilter(
            self._b,
            self._a,
            bus.current.data[start:stop],
            zi=self._zi,
        )
        bus.current.data[start:stop] = output

    def _coefficients(self, cutoff: float, q: float):
        key = (cutoff, q)
        if key == self._key:
            return
        angular = 2.0 * math.pi * cutoff / SAMPLE_RATE
        cosine = math.cos(angular)
        sine = math.sin(angular)
        alpha = sine / (2.0 * q)
        a0 = 1.0 + alpha
        self._b = array(self._numerator(cosine, alpha)) / a0
        self._a = array([1.0, -2.0 * cosine / a0, (1.0 - alpha) / a0])
        self._key = key

    def _numerator(self, cosine: float, alpha: float):
        raise NotImplementedError
