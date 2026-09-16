"""`Saw`: the ramp itself, rescaled to bipolar."""

from numpy import multiply, ndarray, subtract

from ._oscillator import _Oscillator


class Saw(_Oscillator):
    """A rising sawtooth: every harmonic, falling at 6 dB per octave.

    The brightest of the basic waveforms and the cheapest -- the phasor's
    ramp already IS a sawtooth, so this only moves it from 0..1 into -1..1.
    """

    def _shape(self, ramp: ndarray):
        multiply(ramp, 2.0, out=ramp)
        subtract(ramp, 1.0, out=ramp)
