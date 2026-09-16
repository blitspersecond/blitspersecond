"""`Square`: which half of the cycle are we in."""

from numpy import bool_, copyto, empty, less, ndarray

from ...constants import BLOCK_SIZE
from ._oscillator import _Oscillator


class Square(_Oscillator):
    """A 50% square: odd harmonics at 6 dB per octave.

    The chiptune voice, and the one that reads as "a computer playing a note"
    more than any other. Hard-switched between exactly -1 and +1 rather than
    computed, so it has no rounding softness at the transition.
    """

    def __init__(self):
        super().__init__()
        #: Reused across blocks. A boolean per sample, allocated once, rather
        #: than a fresh comparison result 120 times a second per voice.
        self._mask = empty(BLOCK_SIZE, dtype=bool_)

    def _shape(self, ramp: ndarray):
        mask = self._mask[: len(ramp)]
        less(ramp, 0.5, out=mask)
        ramp.fill(-1.0)
        copyto(ramp, 1.0, where=mask)
