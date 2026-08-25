from ._biquad import _Biquad


class BandPass(_Biquad):
    """Stateful second-order band-pass filter with live cutoff and Q."""

    def __init__(self, cutoff: float, q: float = 1.0):
        super().__init__(cutoff, q)

    def _numerator(self, cosine: float, alpha: float):
        return alpha, 0.0, -alpha
