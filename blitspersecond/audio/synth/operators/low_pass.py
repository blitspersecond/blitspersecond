"""`LowPass`: keep the body, lose the fizz."""

from ._biquad import _Biquad


class LowPass(_Biquad):
    """Second-order low-pass, 12 dB per octave above the cutoff.

    The workhorse. A square or saw straight from an oscillator is harsher
    than almost any instrument wants; this is what makes one sound played
    rather than generated. Default Q of 0.7071 is Butterworth -- as flat as
    a second-order section gets, with no bump at the corner.
    """

    def __init__(self, cutoff: float, q: float = 0.7071):
        super().__init__(cutoff, q)

    def _numerator(self, cosine: float, alpha: float):
        return (
            (1.0 - cosine) / 2.0,
            1.0 - cosine,
            (1.0 - cosine) / 2.0,
        )
