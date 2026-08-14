from types import MappingProxyType

from numpy import absolute, max as numpy_max

from ..common import Bus, RegisterScope, RegisterWrite

_SILENCE_FLOOR = 1e-4
_QUIET_FRAMES = 3


class Operator:
    """Consume prepared lane inputs and return one owned output Bus."""

    scope = RegisterScope.STAGE
    registers = MappingProxyType({})

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        pass

    def process(self, *inputs: Bus) -> Bus:
        if len(inputs) != 1:
            raise ValueError(f"Operator requires one input, got {len(inputs)}")
        return inputs[0]

    def _output_quiescent(self, output: Bus, *inputs: bool) -> bool:
        """Whether this output is silent now and needs no future processing.

        Unknown Operators stay awake. Operators with a proven propagation or
        retained-state rule override this internal hook; Program attaches the
        answer to the settled Bus passed to the next Operator.
        """
        return False

    def _tail_quiescent(
        self,
        output: Bus,
        input_quiescent: bool,
        *history,
    ) -> bool:
        """Retire inaudible retained state after three quiet Frames."""
        if not input_quiescent:
            self._tail_quiet_frames = 0
            return False

        arrays = (output.current.data, *history)
        if any(
            values.size
            and float(numpy_max(absolute(values))) >= _SILENCE_FLOOR
            for values in arrays
        ):
            self._tail_quiet_frames = 0
            return False

        self._tail_quiet_frames = getattr(
            self,
            "_tail_quiet_frames",
            0,
        ) + 1
        if self._tail_quiet_frames < _QUIET_FRAMES:
            return False

        for values in arrays:
            values.fill(0.0)
        return True
