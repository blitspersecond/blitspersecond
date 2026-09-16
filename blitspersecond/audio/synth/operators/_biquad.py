"""The shared second-order filter: state, coefficients, and live control.

Low-pass, high-pass and band-pass are the same filter with three different
numerators. Everything else -- the denominator, the retained state, the
coefficient cache, the live `cutoff` and `q` -- lives here, so each of the
three is the three numbers that make it what it is.

**The recursion is a compiled kernel**, not a library call. Each sample needs
the two before it, so there is no bulk form; at 400 samples, 120 times a
second per filter instance, a per-call library boundary is the same order as
the arithmetic inside it. Owning the difference equation also puts the filter
state in our hands at block granularity, which is what any future smoothing of
a live cutoff change needs.
"""

import math
from collections.abc import Mapping
from functools import lru_cache
from types import MappingProxyType
from typing import cast

from numba import njit
from numpy import empty, float32, float64, ndarray, zeros

from ..bus import Bus
from ...constants import BLOCK_SIZE, SAMPLE_RATE
from ..registers import RegisterSpec, RegisterWrite
from .operator import Operator

#: The usable band, and the reason it stops short of the obvious endpoints.
#: At DC and at Nyquist this filter form puts a double pole on the unit
#: circle, where retained state can grow without bound -- so the range is a
#: STABILITY rule, not a taste one. It is not smoothing: an abrupt change
#: within the range can still produce a transient, which is separate work.
MINIMUM_FILTER_FREQUENCY = 20.0
MAXIMUM_FILTER_FREQUENCY = 0.45 * SAMPLE_RATE


@njit(cache=True)
def _biquad_segment(samples, b0, b1, b2, a1, a2, z0, z1):
    """Transposed direct form II over one span, returning the carried state.

    The state is the two accumulators, kept in float64 while the samples they
    are computed from and written back to are float32 -- so the recursion has
    the headroom of the wider type and only the stored result is rounded,
    which is what keeps a long decay from quantising itself into a buzz.
    """
    for index in range(samples.shape[0]):
        sample = float64(samples[index])
        output = b0 * sample + z0
        z0 = b1 * sample - a1 * output + z1
        z1 = b2 * sample - a2 * output
        samples[index] = float32(output)
    return z0, z1


@lru_cache(maxsize=1)
def _warm_biquad_kernel():
    """Compile the recursion before an AudioEngine can be started.

    Called from every `_Biquad.__init__`, so the cost lands while a game is
    building its graph. The first CALL would otherwise be the first block the
    filter renders, which is on the audio worker.
    """
    scratch = zeros(8, dtype=float32)
    _biquad_segment(scratch, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


class _Biquad(Operator):
    """A second-order IIR section, cut at every coefficient write."""

    def __init__(self, cutoff: float, q: float):
        _warm_biquad_kernel()
        self.registers = MappingProxyType(
            {
                "cutoff": RegisterSpec(
                    float(cutoff),
                    minimum=MINIMUM_FILTER_FREQUENCY,
                    maximum=MAXIMUM_FILTER_FREQUENCY,
                    unit="Hz",
                ),
                # No maximum, and a very high Q self-oscillates. Flagged on
                # the board card about register ranges: the cutoff bounds came
                # from a stability argument that this register looks to have
                # been missed by.
                "q": RegisterSpec(float(q), minimum=1e-6),
            }
        )
        self._clock = 0
        self._registers: Mapping[str, object] = MappingProxyType({})
        self._writes: tuple[RegisterWrite, ...] = ()
        #: The (cutoff, q) the cached coefficients were built for. A filter
        #: nobody automates computes its coefficients exactly once.
        self._key: tuple[float, float] | None = None
        self._coefficients_cache: tuple[float, float, float, float, float] = (
            1.0, 0.0, 0.0, 0.0, 0.0
        )
        #: The two accumulators that make this an IIR. Carried across blocks
        #: -- they ARE the filter -- and across a coefficient change, which is
        #: what makes an abrupt sweep ring rather than click.
        self._zi = zeros(2)

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        self._clock = clock
        self._registers = registers
        self._writes = writes

    def process(self, bus: Bus) -> Bus:
        cutoff = cast(float, self._registers["cutoff"])
        q = cast(float, self._registers["q"])
        start = 0
        for write in self._writes:
            stop = write.timestamp - self._clock
            self._apply(bus, start, stop, cutoff, q)
            for name, value in write.values:
                if name == "cutoff":
                    cutoff = cast(float, value)
                else:
                    q = cast(float, value)
            start = stop
        self._apply(bus, start, BLOCK_SIZE, cutoff, q)
        return bus

    def _output_quiescent(self, output: Bus, input_quiescent: bool) -> bool:
        # A filter rings after its input stops, so silence now proves nothing;
        # the retained state has to go quiet too, and stay quiet.
        return self._tail_quiescent(output, input_quiescent, self._zi)

    def _apply(self, bus: Bus, start: int, stop: int, cutoff: float, q: float):
        if start == stop:
            return
        self._coefficients(cutoff, q)
        b0, b1, b2, a1, a2 = self._coefficients_cache
        z0, z1 = _biquad_segment(
            bus.current.data[start:stop],
            b0, b1, b2, a1, a2,
            float(self._zi[0]),
            float(self._zi[1]),
        )
        self._zi[0] = z0
        self._zi[1] = z1

    def _coefficients(self, cutoff: float, q: float):
        """The RBJ cookbook biquad. Recomputed only when something moved."""
        key = (cutoff, q)
        if key == self._key:
            return
        angular = 2.0 * math.pi * cutoff / SAMPLE_RATE
        cosine = math.cos(angular)
        sine = math.sin(angular)
        alpha = sine / (2.0 * q)
        a0 = 1.0 + alpha
        b0, b1, b2 = self._numerator(cosine, alpha)
        self._coefficients_cache = (
            b0 / a0,
            b1 / a0,
            b2 / a0,
            -2.0 * cosine / a0,
            (1.0 - alpha) / a0,
        )
        self._key = key

    def _numerator(self, cosine: float, alpha: float):
        """The three coefficients that decide which filter this is."""
        raise NotImplementedError
