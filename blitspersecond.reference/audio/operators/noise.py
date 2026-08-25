from numbers import Integral

from numpy import float32, random

from ..common import Bus
from .operator import Operator


class Noise(Operator):
    """Generate deterministic uniform white noise from an explicit seed."""

    def __init__(self, seed: int = 0):
        if not isinstance(seed, Integral) or isinstance(seed, bool):
            raise TypeError("noise seed must be an integer")
        if seed < 0:
            raise ValueError("noise seed cannot be negative")
        self._rng = random.default_rng(int(seed))

    def process(self, bus: Bus) -> Bus:
        output = bus.current.data
        self._rng.random(dtype=float32, out=output)
        output *= 2.0
        output -= 1.0
        return bus
