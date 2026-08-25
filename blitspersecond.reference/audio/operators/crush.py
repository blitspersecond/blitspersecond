from numbers import Integral
from types import MappingProxyType
from typing import cast

from numpy import divide, multiply, rint

from ..common import Bus, FRAME_SIZE, RegisterSpec, RegisterWrite, SAMPLE_RATE
from .operator import Operator


class Crush(Operator):
    """Quantize amplitude and optionally hold samples at a lower rate."""

    def __init__(self, bits: int = 8, rate: float = SAMPLE_RATE):
        if not isinstance(bits, Integral) or isinstance(bits, bool):
            raise TypeError("crush bit depth must be an integer")
        bits = int(bits)
        self.registers = MappingProxyType(
            {
                "bits": RegisterSpec(bits, minimum=1, maximum=24, unit="bits"),
                "rate": RegisterSpec(
                    float(rate),
                    minimum=0.0,
                    maximum=float(SAMPLE_RATE),
                    unit="Hz",
                ),
            }
        )
        self._clock = 0
        self._registers = MappingProxyType({})
        self._writes = ()
        self._phase = 0.0
        self._held = 0.0

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        self._clock = clock
        self._registers = registers
        self._writes = writes

    def process(self, bus: Bus) -> Bus:
        bits = cast(int, self._registers["bits"])
        rate = cast(float, self._registers["rate"])
        start = 0
        for write in self._writes:
            stop = write.timestamp - self._clock
            self._apply(bus, start, stop, bits, rate)
            for name, value in write.values:
                if name == "bits":
                    bits = cast(int, value)
                else:
                    rate = cast(float, value)
            start = stop
        self._apply(bus, start, FRAME_SIZE, bits, rate)
        return bus

    def _apply(self, bus: Bus, start: int, stop: int, bits: int, rate: float):
        samples = bus.current.data[start:stop]
        quantization = float(2 ** (bits - 1))
        multiply(samples, quantization, out=samples)
        rint(samples, out=samples)
        divide(samples, quantization, out=samples)

        if not len(samples):
            return
        if rate >= SAMPLE_RATE:
            self._held = float(samples[-1])
            self._phase = 0.0
            return
        if rate == 0.0:
            samples.fill(self._held)
            return

        period = SAMPLE_RATE / rate
        for index in range(len(samples)):
            if self._phase <= 0.0:
                self._held = float(samples[index])
                self._phase += period
            samples[index] = self._held
            self._phase -= 1.0
