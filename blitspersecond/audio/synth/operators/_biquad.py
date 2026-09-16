"""The shared second-order filter: state, coefficients, and live control.

Low-pass, high-pass and band-pass are the same filter with three different
numerators. Everything else -- the denominator, the retained state, the
coefficient cache, the live `cutoff` and `q` -- lives here, so each of the
three is the three numbers that make it what it is.

**This is the engine's only use of scipy**, for one call to `lfilter`. Whether
that dependency survives is an open question -- an njit kernel may suit the
volume better without the API overhead, and scipy is a distribution burden on
top of the ones already carried. See the board card *Does the biquad need
scipy*; until it is answered this stays as it is rather than being guessed at
mid-rebuild.
"""

import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import cast

from numpy import array, ndarray, zeros
from scipy.signal import lfilter

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


class _Biquad(Operator):
    """A second-order IIR section, cut at every coefficient write."""

    def __init__(self, cutoff: float, q: float):
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
        self._b: ndarray | None = None
        self._a: ndarray | None = None
        #: The two samples of history that make this an IIR. Carried across
        #: blocks -- it IS the filter -- and across a coefficient change,
        #: which is what makes an abrupt sweep ring rather than click.
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
        output, self._zi = lfilter(
            self._b,
            self._a,
            bus.current.data[start:stop],
            zi=self._zi,
        )
        bus.current.data[start:stop] = output

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
        self._b = array(self._numerator(cosine, alpha)) / a0
        self._a = array([1.0, -2.0 * cosine / a0, (1.0 - alpha) / a0])
        self._key = key

    def _numerator(self, cosine: float, alpha: float):
        """The three coefficients that decide which filter this is."""
        raise NotImplementedError
