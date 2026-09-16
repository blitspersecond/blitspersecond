"""`Mix`: several finished lanes become one, without moving a block.

Every Stage ends in one of these. It is structural rather than a choice a
game makes -- there is no configuring it away, which is why `Stage` builds it
directly instead of taking a compositor argument.
"""

from typing import cast

from ..bus import Bus, StereoBus
from .operator import Compositor


class Mix(Compositor):
    """Sum consumable lanes into the Stage output, destructively.

    **Lane zero is the accumulator.** The other lanes are added into its
    block, and then that block is *swapped* into the Stage output rather than
    copied: the summed row becomes the output's row, and the output's previous
    row goes back to lane zero to be overwritten next block. So mixing N lanes
    costs N-1 additions and no allocation at all.

    **Nothing may read a lane's block after passing it here.** The lane no
    longer holds what it held -- it holds either the sum or the output's old
    contents. That is safe today because every lane re-ingests from its source
    before its next use and a source clears its working bus, so no stale block
    outlives the mix that consumed it.

    Deliberately shape-polymorphic: ordinary Stages feed mono buses and a
    MixingDesk feeds stereo ones, and the arithmetic is identical at both
    shapes. There is one `Mix`, not a mono and a stereo one.
    """

    def __init__(self, output: Bus | StereoBus):
        # The one place the stereo case is admitted, rather than three
        # suppressions further down arguing with the same fact. `Operator` is
        # typed on the mono `Bus` because every other operator IS mono; a
        # stereo Mix is genuinely outside that hierarchy, and making the whole
        # hierarchy generic to describe one class in one place would cost more
        # than it explains. `StereoBus` and `Bus` present the same three
        # members this uses -- `current`, `clear`, and a `current.swap`.
        self.output = cast(Bus, output)

    def process(self, *inputs: Bus) -> Bus:
        if not inputs:
            # A Stage with nothing to mix outputs silence, not its last block.
            self.output.clear()
            return self.output

        accumulator = inputs[0].current
        quiescent = accumulator.quiescent
        for incoming in inputs[1:]:
            accumulator.data += incoming.current.data
            quiescent = quiescent and incoming.current.quiescent
        # Silent only if EVERY lane was: one sounding lane wakes the mix.
        accumulator.quiescent = quiescent
        self.output.current.swap(accumulator)
        return self.output
