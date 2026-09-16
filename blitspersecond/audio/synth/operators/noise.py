"""`Noise`: every frequency at once, and the root of most percussion."""

from numbers import Integral

from numpy import float32, random

from ..bus import Bus
from .operator import Operator


class Noise(Operator):
    """Uniform white noise, deterministic from an explicit seed.

    **Seeded at construction, with no register.** Two graphs built the same
    way produce the same samples, which is what lets an offline render be
    diffed at all -- and it means a snare is the same snare every time it is
    struck rather than a new one. Randomness that varies per run would make
    every regression test approximate.

    Filtered hard, this is most of a drum kit: a narrow band around 200 Hz is
    a snare, a wide one up high is a hat.
    """

    def __init__(self, seed: int = 0):
        if not isinstance(seed, Integral) or isinstance(seed, bool):
            raise TypeError("noise seed must be an integer")
        if seed < 0:
            raise ValueError("noise seed cannot be negative")
        self._rng = random.default_rng(int(seed))

    def process(self, bus: Bus) -> Bus:
        output = bus.current.data
        # Generated straight into the block, then mapped 0..1 -> -1..1 in
        # place; no temporary the size of a block.
        self._rng.random(dtype=float32, out=output)
        output *= 2.0
        output -= 1.0
        return bus
