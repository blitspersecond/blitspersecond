"""`BandPass`: keep one region and discard both ends."""

from ._biquad import _Biquad


class BandPass(_Biquad):
    """Second-order band-pass, falling away on both sides of the cutoff.

    What turns noise into percussion: a burst of white noise through a narrow
    band is a snare or a hat depending on where the band sits. Default Q of
    1.0 is wider than it sounds -- percussion usually wants it higher.
    """

    def __init__(self, cutoff: float, q: float = 1.0):
        super().__init__(cutoff, q)

    def _numerator(self, cosine: float, alpha: float):
        return alpha, 0.0, -alpha
