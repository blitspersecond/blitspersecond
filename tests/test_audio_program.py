"""`Program` and `_BoundProgram`: a recipe, and the chain built from it.

**The separation is the subject.** A Program holds factories, not Operators,
so that wiring it twice gives two chains that share nothing. Most of these
tests are about proving that independence in the way that would actually
fail -- by giving an operator state and checking one chain cannot see the
other's -- rather than by asserting two objects are `is not` each other,
which is true of almost anything.

The chains are driven through a stub Controller rather than a Stage. That is
the point of the Protocol: a Program's dependency is "something that declares
registers and controls operators", and a stub is a truthful instance of that,
not a fake standing in for a real one.
"""

import numpy as np
import pytest

from blitspersecond.audio.synth.block import BlockPool
from blitspersecond.audio.synth.bus import Bus
from blitspersecond.audio.synth.operators import Compositor, Gain, Operator
from blitspersecond.audio.synth.program import Program, _BoundProgram
from blitspersecond.audio.synth.registers import RegisterScope, RegisterSpec


class _Stub:
    """A Controller: declares registers, and controls with settled values."""

    def __init__(self, **values):
        self.declared: dict[str, RegisterSpec] = {}
        self.overrides: dict[str, RegisterSpec] = {}
        self.values = values
        self.controlled: list[Operator] = []

    def _declare_registers(self, registers):
        self.declared.update(registers)

    def _allow_overrides(self, registers):
        self.overrides.update(registers)

    def control(self, operator):
        self.controlled.append(operator)
        settled = {
            name: self.values.get(name, spec.default)
            for name, spec in operator.registers.items()
        }
        operator.control(0, settled, ())


class _Counting(Operator):
    """An operator with state, so sharing one would be visible."""

    def __init__(self):
        self.runs = 0

    def process(self, bus, /):
        self.runs += 1
        bus.current.data += 1.0
        return bus


class _Laned(Operator):
    scope = RegisterScope.LANE
    registers = {"send": RegisterSpec(0.0)}


class _Broken(Operator):
    scope = "sideways"  # type: ignore[assignment]


@pytest.fixture
def bus() -> Bus:
    return Bus(BlockPool())


# -- a blueprint is not a chain ----------------------------------------


def test_a_program_builds_nothing_until_it_is_wired():
    built = []

    Program(lambda: built.append(1) or Operator())
    assert built == []


def test_wiring_twice_gives_chains_that_share_no_state(bus):
    program = Program(_Counting)
    stage = _Stub()
    first = program.wire(stage)
    second = program.wire(stage)

    first.process(bus)
    first.process(bus)
    second.process(bus)

    ran_first, ran_second = first.operators[0], second.operators[0]
    assert isinstance(ran_first, _Counting)
    assert isinstance(ran_second, _Counting)
    assert ran_first.runs == 2
    assert ran_second.runs == 1


def test_an_empty_program_is_falsey_and_a_wired_one_follows_it():
    stage = _Stub()
    assert not Program()
    assert not Program().wire(stage)
    assert Program(Operator)
    assert Program(Operator).wire(stage)


def test_an_empty_program_passes_its_one_lane_through(bus):
    assert Program().wire(_Stub()).process(bus) is bus


def test_an_empty_program_cannot_join_two_lanes(bus):
    """A wire joins two things; it cannot reduce three."""
    chain = Program().wire(_Stub())
    with pytest.raises(ValueError):
        chain.process(bus, Bus(BlockPool()))
    with pytest.raises(ValueError):
        chain.process()


# -- declaring registers -----------------------------------------------


def test_wiring_declares_its_operators_stage_registers():
    stage = _Stub()
    Program(lambda: Gain(0.5)).wire(stage)
    assert stage.declared["level"].default == 0.5


def test_declare_publishes_registers_without_building_a_chain():
    """A Composite Stage must say what it accepts before a lane exists."""
    stage = _Stub()
    Program(lambda: Gain(0.25)).declare(stage)
    assert stage.declared["level"].default == 0.25


def test_declare_builds_and_discards_one_operator_per_factory():
    """Pinned because it is a real cost, paid at graph build time only."""
    built = []
    Program(lambda: built.append(1) or Operator()).declare(_Stub())
    assert built == [1]


def test_a_lane_scoped_operator_declares_on_the_lane_not_the_stage():
    stage, lane = _Stub(), _Stub()
    Program(_Laned).wire(stage, lane)
    assert "send" in lane.declared
    assert "send" not in stage.declared


def test_a_lane_scoped_operator_has_nowhere_to_live_without_a_lane():
    with pytest.raises(ValueError):
        Program(_Laned).wire(_Stub())


def test_declare_ignores_lane_scoped_operators():
    """They belong to a connection that does not exist yet."""
    stage = _Stub()
    Program(_Laned).declare(stage)
    assert stage.declared == {}


def test_a_stage_register_on_a_lane_may_be_shadowed():
    """How a score addresses one input's envelope without moving the others."""
    stage, lane = _Stub(), _Stub()
    Program(lambda: Gain(1.0)).wire(stage, lane)
    assert "level" in stage.declared
    assert "level" in lane.overrides


@pytest.mark.parametrize("build", [lambda p, s: p.wire(s), lambda p, s: p.declare(s)])
def test_an_operator_with_a_nonsense_scope_is_refused(build):
    with pytest.raises(TypeError):
        build(Program(_Broken), _Stub())


# -- running the chain -------------------------------------------------


def test_operators_run_in_order(bus):
    order = []

    def step(tag):
        class Step(Operator):
            def process(self, b, /):
                order.append(tag)
                return b

        return Step

    Program(step("a"), step("b"), step("c")).wire(_Stub()).process(bus)
    assert order == ["a", "b", "c"]


def test_every_operator_is_controlled_before_it_runs(bus):
    stage = _Stub()
    chain = Program(_Counting, _Counting).wire(stage)
    stage.controlled.clear()

    chain.process(bus)

    assert stage.controlled == chain.operators


def test_the_chain_carries_the_block_through(bus):
    Program(_Counting, _Counting, _Counting).wire(_Stub()).process(bus)
    assert np.all(bus.current.data == 3.0)


def test_a_lane_controls_the_chain_when_there_is_one(bus):
    """A lane's values shadow the Stage's, so the lane must do the control."""
    stage, lane = _Stub(level=1.0), _Stub(level=0.0)
    Program(Gain).wire(stage, lane).process(bus)

    assert lane.controlled and not stage.controlled


def test_only_the_head_sees_more_than_one_lane(bus):
    seen = []

    class Head(Compositor):
        def process(self, *inputs):
            seen.append(len(inputs))
            return inputs[0]

    class Tail(Operator):
        def process(self, b, /):
            seen.append("unary")
            return b

    Program(Head, Tail).wire(_Stub()).process(bus, Bus(BlockPool()))
    assert seen == [2, "unary"]


# -- quiescence rides the bus ------------------------------------------


def test_an_operator_that_cannot_prove_it_keeps_the_chain_awake(bus):
    bus.current.quiescent = True
    Program(Operator).wire(_Stub()).process(bus)
    assert bus.current.quiescent is False


def test_a_stateless_chain_passes_quiet_through(bus):
    bus.current.quiescent = True
    result = Program(Gain, Gain).wire(_Stub()).process(bus)
    assert result.current.quiescent is True


def test_one_operator_that_stays_awake_wakes_everything_after_it(bus):
    bus.current.quiescent = True
    result = Program(Gain, Operator, Gain).wire(_Stub()).process(bus)
    assert result.current.quiescent is False


def test_the_head_is_asked_about_every_lane(bus):
    asked = []

    class Head(Compositor):
        def process(self, *inputs):
            return inputs[0]

        def _output_quiescent(self, output, *inputs):
            asked.append(inputs)
            return all(inputs)

    other = Bus(BlockPool())
    bus.current.quiescent = True
    other.current.quiescent = False

    Program(Head).wire(_Stub()).process(bus, other)
    assert asked == [(True, False)]


def test_a_bound_program_is_what_wire_returns():
    assert isinstance(Program(Operator).wire(_Stub()), _BoundProgram)
