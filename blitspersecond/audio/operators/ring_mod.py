from numpy import multiply

from ..common import Bus
from .operator import Operator


class RingMod(Operator):
    """Multiply two consumable input lanes into the first lane."""

    def process(self, *inputs: Bus) -> Bus:
        if len(inputs) != 2:
            raise ValueError(f"RingMod requires two inputs, got {len(inputs)}")
        output, carrier = inputs
        multiply(
            output.current.data,
            carrier.current.data,
            out=output.current.data,
        )
        return output

    def _output_quiescent(
        self,
        output: Bus,
        signal_quiescent: bool,
        carrier_quiescent: bool,
    ) -> bool:
        return signal_quiescent or carrier_quiescent
