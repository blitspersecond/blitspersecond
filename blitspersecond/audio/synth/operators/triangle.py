"""`Triangle`: fold the ramp at its midpoint."""

from numpy import absolute, multiply, ndarray, subtract

from ._oscillator import _Oscillator


class Triangle(_Oscillator):
    """A triangle: odd harmonics only, falling steeply at 12 dB per octave.

    Nearly a sine in tone, and the reason it stands in for strings and
    mallets in a small tone bank: mellow, but with just enough edge left to
    hear the pitch clearly on a small speaker.
    """

    def _shape(self, ramp: ndarray):
        # |ramp - 0.5| is a V from 0.5 down to 0 and back; scaling to -1..1
        # makes it the triangle.
        subtract(ramp, 0.5, out=ramp)
        absolute(ramp, out=ramp)
        multiply(ramp, 4.0, out=ramp)
        subtract(ramp, 1.0, out=ramp)
