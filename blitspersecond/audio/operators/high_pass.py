from ._biquad import _Biquad


class HighPass(_Biquad):
    """Stateful second-order high-pass filter with live cutoff and Q."""

    def __init__(self, cutoff: float, q: float = 0.7071):
        super().__init__(cutoff, q)

    def _numerator(self, cosine: float, _alpha: float):
        return (
            (1.0 + cosine) / 2.0,
            -(1.0 + cosine),
            (1.0 + cosine) / 2.0,
        )
