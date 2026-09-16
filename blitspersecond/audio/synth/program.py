"""A Program: the recipe for an operator chain, and the chain itself.

These are two different things wearing one word, and keeping them apart is
the point of this module.

A `Program` is a **blueprint** -- a tuple of zero-argument factories, holding
no DSP state and belonging to nobody. `Program(Square, lambda: Envelope(...))`
is a patch, written once and usable everywhere. An Operator that takes
construction arguments is wrapped, because what is stored is the factory: a
bare class is only spellable where the class needs nothing.

A `_BoundProgram` is what you get when that blueprint is wired to a Stage:
real Operators, with real state, belonging to exactly one lane. Wiring the
same blueprint twice gives two chains that share nothing, which is how a
Composite Stage gives every input lane its own envelope while the score
addresses one `gate`.

That split is the whole reason a Program stores factories rather than
Operators. Handing out one Operator to several lanes would have them share a
filter's history and an oscillator's phase, and two notes would fight over
one voice.
"""

from collections.abc import Callable, Mapping
from typing import Protocol

from .bus import Bus
from .operators import Operator
from .registers import RegisterScope, RegisterSpec


class Controller(Protocol):
    """Whatever supplies an Operator with its registers.

    A Stage is one, and so is a single input lane. Spelled as a Protocol
    rather than importing both: `Stage` and `_Connection` each import what
    they need from here, so naming them back would be a cycle -- and a
    Program genuinely does not care which of the two it is talking to. It
    also means a chain can be exercised against a small stub, the same way
    `StageEngine` lets a Stage be driven by a clock that is not an engine.
    """

    def _declare_registers(self, registers: Mapping[str, RegisterSpec]) -> None: ...

    def control(self, operator: Operator) -> None: ...


class Lane(Controller, Protocol):
    """One input connection: a Controller that can shadow Stage values too."""

    def _allow_overrides(self, registers: Mapping[str, RegisterSpec]) -> None: ...


class Program:
    """An operator chain as a recipe: what to build, not something built."""

    def __init__(self, *operators: Callable[[], Operator]):
        self._operators = operators

    def __bool__(self):
        """Whether this Program does anything. An empty one is a wire."""
        return bool(self._operators)

    def _instantiate(self) -> list[Operator]:
        return [operator() for operator in self._operators]

    def declare(self, stage: Controller):
        """Publish this blueprint's Stage registers without wiring it.

        For a Composite Stage, which holds its blueprint and wires it afresh
        per lane: until a lane exists there is no chain to read registers
        off, but the Stage must still be able to say what it accepts.

        **This builds Operators and throws them away.** The cost is real --
        constructing a Pluck allocates two delay buffers and warms a compiled
        kernel -- but it is paid entirely during graph construction, which
        cannot happen while a Driver is running. So it never lands on the
        audio thread. The hazard it leaves is a future Operator whose
        constructor acquires something: that would be acquired and dropped
        here with nobody to release it. Declaring registers separately from
        the factory is the fix if one ever does.
        """
        for operator in self._instantiate():
            if operator.scope is RegisterScope.STAGE:
                stage._declare_registers(operator.registers)
            elif operator.scope is not RegisterScope.LANE:
                raise TypeError("Operator has an invalid register scope")

    def wire(
        self,
        stage: Controller,
        connection: Lane | None = None,
    ) -> "_BoundProgram":
        """Build one private chain of this blueprint, bound to a Stage."""
        return _BoundProgram(stage, self._instantiate(), connection)


class _BoundProgram:
    """One live Operator chain, and the register context it is controlled by.

    Named apart from `Program` deliberately. They are not a public and
    private pair of one thing: one is a recipe that can be wired any number
    of times, the other is a single chain with state that must not be shared.
    """

    def __init__(
        self,
        stage: Controller,
        operators: list[Operator],
        connection: Lane | None = None,
    ):
        self.stage = stage
        self.connection = connection
        self.operators = operators
        for operator in operators:
            if operator.scope is RegisterScope.STAGE:
                # Stage-scoped registers are shared by every lane. A lane may
                # still be given its own value for one, which is what
                # `_allow_overrides` permits -- so a score can address one
                # input's envelope without moving the other lanes.
                stage._declare_registers(operator.registers)
                if connection is not None:
                    connection._allow_overrides(operator.registers)
            elif operator.scope is RegisterScope.LANE:
                if connection is None:
                    raise ValueError(
                        "a Lane-scoped Operator requires an input connection"
                    )
                connection._declare_registers(operator.registers)
            else:
                raise TypeError("Operator has an invalid register scope")

    def __bool__(self):
        return bool(self.operators)

    def process(self, *inputs: Bus) -> Bus:
        """Run the chain: N lanes into the head, then one bus all the way.

        **Only the first Operator sees more than one lane.** That is the
        arity split made concrete -- a `Compositor` at the head reduces the
        inputs to one bus, and everything after it is ordinary unary work on
        whatever the previous step returned.

        Quiescence travels with the bus rather than being recomputed at the
        end. Each Operator is asked whether ITS output has settled, given
        what it was handed, and the answer rides on the block into the next
        one. An Operator that cannot prove it says no, and the whole chain
        stays awake -- which is the safe direction.
        """
        if not self.operators:
            # An empty Program is a wire, and a wire joins two things.
            if len(inputs) != 1:
                raise ValueError("an empty Program requires exactly one input")
            return inputs[0]

        first, *remaining = self.operators
        # A lane's own register view where there is one, so a lane override
        # shadows the Stage value; otherwise the Stage's.
        controller = self.connection if self.connection is not None else self.stage
        input_quiescence = tuple(bus.current.quiescent for bus in inputs)
        controller.control(first)
        bus = first.process(*inputs)
        bus.current.quiescent = first._output_quiescent(bus, *input_quiescence)
        for operator in remaining:
            input_quiescent = bus.current.quiescent
            controller.control(operator)
            bus = operator.process(bus)
            bus.current.quiescent = operator._output_quiescent(bus, input_quiescent)
        return bus
