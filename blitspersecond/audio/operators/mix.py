from .operator import Operator


class Mix(Operator):
    """Destructively composite consumable lane Frames into the Stage output.

    The first lane's current Frame becomes the accumulator. No caller may read
    any lane's pre-Mix current Frame after passing that lane to process().
    """

    def __init__(self, output):
        self.output = output

    def process(self, *inputs):
        if not inputs:
            self.output.clear()
            return self.output

        # Ownership invariant: every input is a consumable private lane Bus.
        # Lane zero is mutated in place, then gives its Frame to Stage output.
        accumulator = inputs[0].current
        quiescent = accumulator.quiescent
        for incoming in inputs[1:]:
            accumulator.data += incoming.current.data
            quiescent = quiescent and incoming.current.quiescent
        accumulator.quiescent = quiescent
        self.output.current.swap(accumulator)
        return self.output
