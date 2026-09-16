"""The base every audio kernel inherits: what an Operator is, and its arity.

An Operator is one step of DSP. It takes a lane's `Bus`, does its work **in
place** on that bus's current block, and returns the bus. Nothing is
allocated, and the block it was handed is the block it hands on.

**Arity is declared by inheritance.** `Operator` is unary and positional-only;
`Compositor` widens that and is only ever the HEAD of a chain. Inheriting from
the right one is the declaration -- there is no `*inputs` on a unary operator
to make the two look alike.

An Operator has no setters and a game never calls one. It declares registers
and `control()` hands it the settled values plus this block's timestamped
writes just before `process()` runs.

The vocabulary, the costs and the quiescence rules:
`bps/reference/audio/operators.md`.
"""

from types import MappingProxyType

from numpy import absolute, max as numpy_max

from ..bus import Bus
from ..registers import RegisterScope, RegisterWrite

#: Below this, a tail is inaudible and may be retired: 1e-4 is about -80 dB.
_SILENCE_FLOOR = 1e-4

#: How many consecutive blocks must sit under the floor before retained state
#: is thrown away. More than one because a single quiet block is not evidence
#: of a finished tail -- a signal can pass through near-zero on its way
#: somewhere. Three blocks is 25ms.
_QUIET_BLOCKS = 3


class Operator:
    """One step of DSP: consume a prepared lane, return the same lane.

    **Unary, and positional-only.** Every operator in a chain after the first
    takes exactly one bus, and this signature is what says so. The N-ary case
    -- an operator that reduces several lanes to one -- is `Compositor`
    below, which widens this deliberately. Inheriting from the right one of
    the two is how arity is declared; there is no `*inputs` on a unary
    operator to make the two look alike.
    """

    #: Whether this operator's registers belong to the Stage or to one input
    #: lane. Stage is the default because it is the coherent instrument
    #: answer: three lanes each holding an Envelope share one `gate`.
    scope = RegisterScope.STAGE

    #: Name to `RegisterSpec`. A class attribute here because an operator with
    #: fixed defaults needs only one; operators whose defaults come from
    #: construction arguments replace it with an instance attribute.
    registers = MappingProxyType({})

    #: Consecutive blocks seen under the silence floor. On the class so that
    #: `_tail_quiescent` works for any subclass without every one of them
    #: having to remember to initialise it -- and so no subclass needs an
    #: `__init__` it would otherwise not have.
    _tail_quiet_blocks = 0

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        """Take this block's register values and writes, before processing.

        `registers` is the settled view -- a scalar per name, or a `(400,)`
        array where another stage's audio is driving the register. `writes`
        are only those naming registers THIS operator declared, timestamped on
        the absolute sample clock, so `write.timestamp - clock` is the offset
        into the block.

        Does nothing by default: an operator with no registers has nothing to
        take.
        """

    def process(self, bus: Bus, /) -> Bus:
        return bus

    def _output_quiescent(self, output: Bus, input_quiescent: bool, /) -> bool:
        """Whether this output is silent now AND nothing more is coming.

        Not "is it zero". It is a claim about the future -- that with no new
        stimulus this operator has no retained output left to produce -- and
        only the operator knows its own state. A filter whose history is still
        ringing may be outputting near-silence and is not quiescent.

        **The default is to stay awake**, because an operator that has not
        thought about the question must not be skipped. Operators with a
        provable rule override this; a chain propagates the answer along the
        bus to the next one.
        """
        return False

    def _tail_quiescent(
        self,
        output: Bus,
        input_quiescent: bool,
        *history,
    ) -> bool:
        """Retire retained state once it has been inaudible for long enough.

        The shared answer for operators that ring after their input stops --
        filters, echo, reverb, resonator. While the input is still live there
        is nothing to retire. Once it stops, the output and every state array
        handed in must all sit under the floor for `_QUIET_BLOCKS` in a row,
        and only then is the state zeroed and the tail declared over.

        Zeroing rather than merely reporting quiet is the point: it means the
        next wake starts from silence instead of resuming a tail nobody heard.
        """
        if not input_quiescent:
            self._tail_quiet_blocks = 0
            return False

        arrays = (output.current.data, *history)
        if any(
            values.size
            and float(numpy_max(absolute(values))) >= _SILENCE_FLOOR
            for values in arrays
        ):
            self._tail_quiet_blocks = 0
            return False

        self._tail_quiet_blocks += 1
        if self._tail_quiet_blocks < _QUIET_BLOCKS:
            return False

        for values in arrays:
            values.fill(0.0)
        return True


class Compositor(Operator):
    """An Operator that consumes more than one lane at once.

    Only ever the HEAD of a Program: the chain hands the first operator every
    prepared lane and every operator after it the single bus produced so far.

    The default is the identity on one lane, so a subclass that forgets to
    override is a passthrough rather than a silent reducer.
    """

    def process(self, *inputs: Bus) -> Bus:
        if len(inputs) != 1:
            raise ValueError(
                f"{type(self).__name__} requires one input, got {len(inputs)}"
            )
        return inputs[0]

    def _output_quiescent(self, output: Bus, *inputs: bool) -> bool:
        return False
