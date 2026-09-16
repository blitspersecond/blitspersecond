"""`RingMod`: two lanes multiplied, and neither one survives."""

from numpy import multiply

from ..bus import Bus
from .operator import Compositor


class RingMod(Compositor):
    """Multiply a carrier lane into a signal lane, sample by sample.

    **Neither input frequency comes out the other side.** Multiplying two
    sines gives their sum and their difference and nothing else: 200 Hz
    against 300 Hz produces 100 Hz and 500 Hz, with no 200 and no 300 anywhere
    in the result. That is what separates this from mixing, and it is why it
    sounds the way it does -- the products of a harmonic series against a
    single tone land at intervals that are not harmonics of anything, so the
    ear hears metal, glass, and voices that are not quite voices.

    **The two lanes are not interchangeable**, even though multiplication is.
    Lane zero is where the result is written, so it is the one consumed; lane
    one is read and left alone. Ordering them signal-then-carrier is a
    convention worth keeping for legibility, but the sound is the same either
    way round.

    Quiescent as soon as EITHER lane is, which is the opposite of `Mix`.
    Anything multiplied by silence is silence, so one lane stopping ends the
    output -- a carrier that has finished decaying takes the signal with it.
    """

    def process(self, *inputs: Bus) -> Bus:
        if len(inputs) != 2:
            raise ValueError(f"RingMod requires two inputs, got {len(inputs)}")
        signal, carrier = inputs
        multiply(
            signal.current.data,
            carrier.current.data,
            out=signal.current.data,
        )
        return signal

    def _output_quiescent(self, output: Bus, *inputs: bool) -> bool:
        signal_quiescent, carrier_quiescent = inputs
        return signal_quiescent or carrier_quiescent
