"""`HighPass`: lose the weight, keep the edge."""

from ._biquad import _Biquad


class HighPass(_Biquad):
    """Second-order high-pass, 12 dB per octave below the cutoff.

    Mostly used to get out of the way -- taking the bottom off a part so it
    does not fight the bass for the same few hertz.
    """

    def __init__(self, cutoff: float, q: float = 0.7071):
        super().__init__(cutoff, q)

    def _numerator(self, cosine: float, alpha: float):
        return (
            (1.0 + cosine) / 2.0,
            -(1.0 + cosine),
            (1.0 + cosine) / 2.0,
        )
