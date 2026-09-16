"""`SourceStage`, `CompositeStage`, `ApplyStage`: inputs against Program.

These are the first tests where a real graph runs end to end, so they assert
on **samples** as well as on structure -- a Composite that sums its lanes and
an Apply that multiplies them are distinguishable only by what comes out.

**The Composite/Apply distinction is the subject.** Composite is parallel:
one Program per lane, results summed, and a sleeping lane can simply be left
out of the sum. Apply is a junction: one Program, all lanes at its head, and
lane ORDER is meaning. So the test that a sleeping lane is still passed to an
Apply is not pedantry -- dropping it would promote the lane behind it and
silently swap Duck's signal for its control.
"""

import numpy as np
import pytest

from blitspersecond.audio.synth.block import BlockPool
from blitspersecond.audio.synth.bus import Bus
from blitspersecond.audio.constants import BLOCK_SIZE
from blitspersecond.audio.synth.operators import Compositor, Gain, Operator
from blitspersecond.audio.synth.program import Program
from blitspersecond.audio.synth.registers import RegisterScope, RegisterSpec, RegisterWrite
from blitspersecond.audio.synth.stages import ApplyStage, CompositeStage, SourceStage


class _Engine:
    def __init__(self):
        self.pool = BlockPool()
        self.clock = 0
        self.writes: tuple[RegisterWrite, ...] = ()
        self.frozen = False

    @property
    def block(self) -> int:
        return self.clock // BLOCK_SIZE

    @property
    def _prepared_writes(self):
        return self.writes

    def _assert_topology_mutable(self):
        if self.frozen:
            raise RuntimeError("frozen")

    def advance(self):
        self.clock += BLOCK_SIZE


class _Tone(Operator):
    """Writes a constant, so a graph's arithmetic is readable in one sample."""

    def __init__(self, value: float):
        self.value = value

    def process(self, bus, /):
        bus.current.data.fill(self.value)
        return bus


def _tone(value):
    return lambda: _Tone(value)


class _Multiply(Compositor):
    """A cross-input head, like RingMod: lane 0 times lane 1."""

    def process(self, *inputs):
        inputs[0].current.data *= inputs[1].current.data
        return inputs[0]


class _FirstLane(Compositor):
    """A head that takes whatever it is given, for tests about connecting."""

    def process(self, *inputs):
        return inputs[0]


class _Subtract(Compositor):
    """Order-sensitive, so swapping lanes is visible: lane 0 minus lane 1."""

    def process(self, *inputs):
        inputs[0].current.data -= inputs[1].current.data
        return inputs[0]


class _Laned(Operator):
    scope = RegisterScope.LANE
    registers = {"send": RegisterSpec(0.0)}


@pytest.fixture
def engine() -> _Engine:
    return _Engine()


def _first(stage) -> float:
    return float(stage.process().current.data[0])


# -- SourceStage -------------------------------------------------------


def test_a_source_with_no_program_is_silent(engine):
    assert _first(SourceStage(engine)) == 0.0


def test_a_source_runs_its_program(engine):
    assert _first(SourceStage(engine, Program(_tone(0.5)))) == 0.5


def test_a_source_has_no_connect(engine):
    """A source Program must not become an effect Program by accident."""
    assert not hasattr(SourceStage(engine), "connect")


def test_a_source_clears_its_working_lane_each_block(engine):
    """Mix hands the lane back somebody else's old row, so last block's
    contents are not this Stage's to reuse."""

    class _AddOne(Operator):
        def process(self, bus, /):
            bus.current.data += 1.0
            return bus

    source = SourceStage(engine, Program(_AddOne))
    assert _first(source) == 1.0
    engine.advance()
    assert _first(source) == 1.0


def test_a_source_claims_no_rows_after_the_first_block(engine):
    """Not "the row stays the same" -- it does not. Mix swaps lane zero's row
    into the output and hands back the output's old one, so a source's row
    alternates between two. What must not happen is a CLAIM, which would mean
    the graph allocating on the audio thread."""
    source = SourceStage(engine, Program(_tone(0.5)))
    source.process()
    settled = engine.pool.count

    for _ in range(4):
        engine.advance()
        source.process()

    assert engine.pool.count == settled


# -- CompositeStage ----------------------------------------------------


def test_a_composite_with_no_inputs_is_silent(engine):
    assert _first(CompositeStage(engine)) == 0.0


def test_a_composite_sums_its_lanes(engine):
    composite = CompositeStage(engine)
    composite.connect(SourceStage(engine, Program(_tone(0.25))))
    composite.connect(SourceStage(engine, Program(_tone(0.5))))
    assert _first(composite) == 0.75


def test_an_empty_composite_program_is_a_plain_submix(engine):
    composite = CompositeStage(engine, Program())
    composite.connect(SourceStage(engine, Program(_tone(1.0))))
    assert _first(composite) == 1.0


def test_each_composite_lane_runs_its_own_copy_of_the_program(engine):
    """The instrument shape: shared registers, independent state."""

    class _Halve(Operator):
        def process(self, bus, /):
            bus.current.data *= 0.5
            return bus

    composite = CompositeStage(engine, Program(_Halve))
    composite.connect(SourceStage(engine, Program(_tone(1.0))))
    composite.connect(SourceStage(engine, Program(_tone(1.0))))

    first, second = composite._connections
    assert first.program is not None and second.program is not None
    assert first.program.operators[0] is not second.program.operators[0]
    assert _first(composite) == 1.0  # 0.5 + 0.5


def test_a_public_gain_in_a_composite_program_is_shadowed_to_unity(engine):
    """A footgun, pinned rather than hidden.

    Every lane declares `level` from its structural `_InputGain`, and a lane's
    own value shadows the Stage's completely. So a public `Gain` in a
    Composite Program reads the lane's 1.0 and does nothing -- while
    `stage.registers["level"]` still reports the value that was asked for,
    which is what makes it hard to see. In a SourceStage there is no lane, so
    the same Program works as written.

    Faithful to the reference. If the collision is ever resolved -- by
    renaming one of them, or by refusing the declaration -- this test is the
    one that has to be rewritten to say so.
    """
    source = SourceStage(engine, Program(_tone(1.0), lambda: Gain(0.5)))
    assert _first(source) == 0.5

    composite = CompositeStage(engine, Program(lambda: Gain(0.5)))
    composite.connect(SourceStage(engine, Program(_tone(1.0))))

    assert composite.registers["level"] == 0.5
    assert _first(composite) == 1.0


def test_a_composite_declares_its_program_registers_before_any_input(engine):
    composite = CompositeStage(engine, Program(lambda: Gain(0.25)))
    assert composite.register_specs["level"].default == 0.25


def test_a_composite_leaves_sleeping_lanes_out_of_the_mix(engine):
    """Adding silence to a sum changes nothing, so this is free and safe."""
    composite = CompositeStage(engine)
    quiet = SourceStage(engine)
    loud = SourceStage(engine, Program(_tone(0.5)))
    composite.connect(quiet)
    composite.connect(loud)

    composite.process()
    engine.advance()

    assert _first(composite) == 0.5
    assert quiet.quiescent is True


# -- ApplyStage --------------------------------------------------------


def test_an_apply_with_no_inputs_is_silent(engine):
    assert _first(ApplyStage(engine, Program(_Multiply))) == 0.0


def test_an_apply_joins_its_lanes_in_one_program(engine):
    apply = ApplyStage(engine, Program(_Multiply))
    apply.connect(SourceStage(engine, Program(_tone(0.5))))
    apply.connect(SourceStage(engine, Program(_tone(0.25))))
    assert _first(apply) == 0.125


def test_an_apply_keeps_lane_order(engine):
    """Duck's first input is the signal and its second is the control."""
    apply = ApplyStage(engine, Program(_Subtract))
    apply.connect(SourceStage(engine, Program(_tone(1.0))))
    apply.connect(SourceStage(engine, Program(_tone(0.25))))
    assert _first(apply) == 0.75


def test_a_sleeping_lane_is_still_passed_to_an_apply(engine):
    """Dropping it would promote the lane behind it and swap the meanings."""
    apply = ApplyStage(engine, Program(_Subtract))
    silent = SourceStage(engine)
    apply.connect(SourceStage(engine, Program(_tone(1.0))))
    apply.connect(silent)

    apply.process()
    engine.advance()
    silent.process()
    assert silent.quiescent is True

    assert _first(apply) == 1.0
    assert len(apply._connections) == 2


def test_the_rest_of_an_apply_program_is_unary(engine):
    apply = ApplyStage(engine, Program(_Multiply, lambda: Gain(0.5)))
    apply.connect(SourceStage(engine, Program(_tone(1.0))))
    apply.connect(SourceStage(engine, Program(_tone(1.0))))
    assert _first(apply) == 0.5


def test_an_apply_shares_one_program_across_lanes(engine):
    apply = ApplyStage(engine, Program(_Multiply))
    apply.connect(SourceStage(engine))
    apply.connect(SourceStage(engine))
    assert all(c.program is None for c in apply._connections)


def test_a_lane_scoped_operator_cannot_live_in_a_shared_program(engine):
    """There is no single input identity for it to belong to."""
    with pytest.raises(ValueError):
        ApplyStage(engine, Program(_Laned))


# -- connecting, shared by both input forms ----------------------------


@pytest.mark.parametrize(
    "build",
    [lambda e: CompositeStage(e), lambda e: ApplyStage(e, Program(_FirstLane))],
)
class TestInputs:
    def test_connecting_twice_is_idempotent(self, engine, build):
        stage = build(engine)
        source = SourceStage(engine)
        stage.connect(source)
        stage.connect(source)
        assert len(stage._connections) == 1

    def test_disconnecting_releases_the_lane(self, engine, build):
        stage = build(engine)
        source = SourceStage(engine)
        stage.connect(source)
        lane = stage._connection(source)

        stage.disconnect(source)

        assert stage._connections == []
        assert lane.active is False

    def test_disconnecting_something_unconnected_is_ignored(self, engine, build):
        stage = build(engine)
        stage.disconnect(SourceStage(engine))
        assert stage._connections == []

    def test_a_stage_from_another_engine_is_refused(self, engine, build):
        stage = build(engine)
        with pytest.raises(ValueError):
            stage.connect(SourceStage(_Engine()))

    def test_lanes_cannot_be_rewired_while_the_engine_is_driven(
        self, engine, build
    ):
        stage = build(engine)
        source = SourceStage(engine)
        stage.connect(source)
        engine.frozen = True

        with pytest.raises(RuntimeError):
            stage.connect(SourceStage(engine))
        with pytest.raises(RuntimeError):
            stage.disconnect(source)

    def test_an_unconnected_stage_has_no_lane(self, engine, build):
        stage = build(engine)
        with pytest.raises(ValueError):
            stage._connection(SourceStage(engine))

    def test_a_stage_is_awake_while_any_lane_is(self, engine, build):
        stage = build(engine)
        stage.connect(SourceStage(engine, Program(_tone(1.0))))
        stage.process()
        assert stage._inputs_quiescent() is False

    def test_lane_registers_are_reachable_through_the_stage(self, engine, build):
        stage = build(engine)
        source = SourceStage(engine)
        stage.connect(source)

        assert stage.registers_for(source)["level"] == 1.0
        assert "level" in stage.register_specs_for(source)


# -- a graph with depth ------------------------------------------------


def test_a_shared_source_settles_once_for_the_whole_graph(engine):
    """Fan-out through two different Stage forms still pulls the source once."""

    class _Counting(Operator):
        def __init__(self):
            self.runs = 0

        def process(self, bus, /):
            self.runs += 1
            bus.current.data.fill(1.0)
            return bus

    counter = _Counting()
    source = SourceStage(engine, Program(lambda: counter))
    left = CompositeStage(engine)
    left.connect(source)
    right = ApplyStage(engine, Program(_Multiply))
    right.connect(source)
    right.connect(SourceStage(engine, Program(_tone(1.0))))
    top = CompositeStage(engine)
    top.connect(left)
    top.connect(right)

    assert _first(top) == 2.0
    assert counter.runs == 1


def test_a_stage_output_is_not_disturbed_by_a_downstream_lane(engine):
    """Mix is destructive, so a lane must be working on its own copy."""
    source = SourceStage(engine, Program(_tone(1.0)))
    composite = CompositeStage(engine, Program(lambda: Gain(0.5)))
    composite.connect(source)

    composite.process()

    assert np.all(source.process().current.data == 1.0)


def test_disconnecting_returns_the_lane_row_to_the_pool(engine):
    composite = CompositeStage(engine)
    source = SourceStage(engine)
    composite.connect(source)
    row = composite._connection(source).bus.current.id

    composite.disconnect(source)

    assert Bus(engine.pool).current.id == row
