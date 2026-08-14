from collections.abc import Callable
from typing import TYPE_CHECKING

from ..common import Bus, RegisterScope
from ..operators import Operator

if TYPE_CHECKING:
    from .connection import _Connection
    from .stage import Stage


class Program:
    """An operator-chain blueprint bound to a Stage."""

    def __init__(self, *operators: Callable[[], Operator]):
        self._operators = operators

    def __bool__(self):
        return bool(self._operators)

    def _instantiate(self):
        return [operator() for operator in self._operators]

    def declare(self, stage: "Stage"):
        # TODO: Give Program an operator specification that exposes register
        # declarations separately from its factory. Instantiating and
        # discarding an Operator is harmless for these lightweight operators,
        # but becomes undesirable once construction allocates substantial DSP
        # state or has side effects.
        for operator in self._instantiate():
            if operator.scope is RegisterScope.STAGE:
                stage._declare_registers(operator.registers)
            elif operator.scope is not RegisterScope.LANE:
                raise TypeError("Operator has an invalid register scope")

    def wire(self, stage: "Stage", connection: "_Connection | None" = None):
        return _Program(stage, self._instantiate(), connection)


class _Program:
    """An Operator chain bound to its Stage or connection control context."""

    def __init__(
        self,
        stage: "Stage",
        operators,
        connection: "_Connection | None" = None,
    ):
        self.stage = stage
        self.connection = connection
        self.operators = operators
        for operator in operators:
            if operator.scope is RegisterScope.STAGE:
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
        if not self.operators:
            if len(inputs) != 1:
                raise ValueError("an empty Program requires exactly one input")
            return inputs[0]

        first, *remaining = self.operators
        controller = self.connection if self.connection is not None else self.stage
        input_quiescence = tuple(bus.current.quiescent for bus in inputs)
        controller.control(first)
        bus = first.process(*inputs)
        bus.current.quiescent = first._output_quiescent(
            bus,
            *input_quiescence,
        )
        for operator in remaining:
            input_quiescent = bus.current.quiescent
            controller.control(operator)
            bus = operator.process(bus)
            bus.current.quiescent = operator._output_quiescent(
                bus,
                input_quiescent,
            )
        return bus
