"""`_Connection`: one input's private lane, and the shadowing rule.

**The shadowing rule is resolved in timestamp order, and that is the whole
subtlety.** A Stage write broadcasts to every lane until that lane is given
its own value for the register; from that sample onward the broadcast stops
reaching it. So a lane write shadows a Stage write *later* in the same block
and must not shadow one *earlier*. Two of the tests below are the same setup
with the timestamps swapped, because collecting the lane's owned names up
front or after the walk would each pass one of them and fail the other.

**The copy at the top of a lane is tested by mutating the source**, not by
comparing samples. An aliased lane and a copied one hold equal values right
up until somebody writes, which is exactly when it matters -- one settled
Stage commonly feeds several lanes at once.
"""

from types import MappingProxyType

import numpy as np
import pytest

from blitspersecond.audio.synth.block import BlockPool
from blitspersecond.audio.synth.bus import Bus
from blitspersecond.audio.synth.connection import _Connection, _InputGain
from blitspersecond.audio.constants import BLOCK_SIZE
from blitspersecond.audio.synth.operators import Operator
from blitspersecond.audio.synth.program import Program
from blitspersecond.audio.synth.registers import (
    RegisterScope,
    RegisterSpec,
    RegisterWrite,
)
from blitspersecond.audio.synth.stage import Stage


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


class _Source(Stage):
    """A Stage that emits one chosen level and says whether it has settled."""

    def __init__(self, engine, value=1.0, settles=False):
        super().__init__(engine)
        self.value = value
        self.settles = settles

    def _render(self):
        self._output.current.data.fill(self.value)
        self._output.current.quiescent = self.settles
        return self._output


class _Host(Stage):
    """The minimum Stage that can hold lanes, standing in for the real forms."""

    def __init__(self, engine, program=None):
        super().__init__(engine)
        self._connections: list[_Connection] = []
        self._program = program

    def connect(self, source, program=None):
        connection = _Connection(self, source, program or self._program)
        self._connections.append(connection)
        return connection

    def _connection(self, source):
        for connection in self._connections:
            if connection.source is source:
                return connection
        raise ValueError("not connected")

    def _render(self):
        return self._compositor.process(
            *[c.process() for c in self._connections]
        )


class _Filter(Operator):
    """A Stage-scoped operator with a register of its own."""

    registers = MappingProxyType({"cutoff": RegisterSpec(1000.0)})

    def __init__(self):
        self.seen: tuple[RegisterWrite, ...] = ()

    def control(self, clock, registers, writes):
        self.seen = writes


@pytest.fixture
def engine() -> _Engine:
    return _Engine()


def _cutoff(timestamp, stage, value, connection=None):
    return RegisterWrite(timestamp, stage, (("cutoff", value),), connection)


# -- the lane itself ---------------------------------------------------


def test_a_lane_copies_its_source_rather_than_aliasing_it(engine):
    source = _Source(engine, value=0.5)
    host = _Host(engine)
    lane = host.connect(source)

    lane.process()
    source._output.current.data.fill(9.0)

    assert np.all(lane.bus.current.data == 0.5)


def test_two_lanes_from_one_source_do_not_share_a_block(engine):
    """Why the copy happens even at unity gain."""
    source = _Source(engine, value=1.0)
    first, second = _Host(engine), _Host(engine)
    left, right = first.connect(source), second.connect(source)

    left.process()
    right.process()
    left.bus.current.data *= 10.0

    assert np.all(right.bus.current.data == 1.0)


def test_every_lane_gets_its_own_level_at_unity(engine):
    lane = _Host(engine).connect(_Source(engine))
    assert isinstance(lane.gain, _InputGain)
    assert lane.gain.scope is RegisterScope.LANE
    assert lane.registers["level"] == 1.0


def test_the_level_is_applied_to_the_lane_not_the_source(engine):
    source = _Source(engine, value=1.0)
    host = _Host(engine)
    lane = host.connect(source)
    lane._registers["level"] = 0.5

    lane.process()

    assert np.all(lane.bus.current.data == 0.5)
    assert np.all(source._output.current.data == 1.0)


def test_lanes_hold_independent_levels(engine):
    host = _Host(engine)
    quiet = host.connect(_Source(engine, value=1.0))
    loud = host.connect(_Source(engine, value=1.0))
    quiet._registers["level"] = 0.25

    quiet.process()
    loud.process()

    assert np.all(quiet.bus.current.data == 0.25)
    assert np.all(loud.bus.current.data == 1.0)


def test_a_stereo_stage_cannot_feed_a_mono_input(engine):
    stereo = _Source(engine)
    stereo.channels = 2
    with pytest.raises(ValueError):
        _Host(engine).connect(stereo)


def test_a_lane_runs_its_own_program_instance(engine):
    """Composite lanes share a blueprint, never an Operator."""
    host = _Host(engine, program=Program(_Filter))
    first = host.connect(_Source(engine))
    second = host.connect(_Source(engine))

    assert first.program is not None and second.program is not None
    assert first.program.operators[0] is not second.program.operators[0]


# -- the register layers -----------------------------------------------


def test_a_lane_reads_through_to_the_stage(engine):
    host = _Host(engine)
    host._declare_registers({"cutoff": RegisterSpec(1000.0)})
    lane = host.connect(_Source(engine))

    assert lane.registers["cutoff"] == 1000.0


def test_a_lane_value_shadows_the_stage_value_for_reading(engine):
    host = _Host(engine)
    host._declare_registers({"cutoff": RegisterSpec(1000.0)})
    lane = host.connect(_Source(engine))
    lane._registers["cutoff"] = 250.0

    assert lane.registers["cutoff"] == 250.0
    assert host.registers["cutoff"] == 1000.0


def test_allowing_an_override_does_not_claim_the_register(engine):
    """Permission to be addressed is not ownership; the broadcast still lands."""
    host = _Host(engine)
    host._declare_registers({"cutoff": RegisterSpec(1000.0)})
    lane = host.connect(_Source(engine))
    lane._allow_overrides({"cutoff": RegisterSpec(1000.0)})

    assert "cutoff" in lane.write_specs
    assert "cutoff" not in lane._registers
    assert lane.registers["cutoff"] == 1000.0


def test_conflicting_lane_declarations_are_refused(engine):
    lane = _Host(engine).connect(_Source(engine))
    with pytest.raises(ValueError):
        lane._declare_registers({"level": RegisterSpec(0.5)})


def test_conflicting_override_permissions_are_refused(engine):
    lane = _Host(engine).connect(_Source(engine))
    lane._allow_overrides({"cutoff": RegisterSpec(1.0)})
    with pytest.raises(ValueError):
        lane._allow_overrides({"cutoff": RegisterSpec(2.0)})


# -- the shadowing rule, in timestamp order ----------------------------


def test_a_stage_write_broadcasts_to_a_lane_that_has_not_claimed_it(engine):
    host = _Host(engine)
    host._declare_registers({"cutoff": RegisterSpec(1000.0)})
    lane = host.connect(_Source(engine))
    operator = _Filter()
    engine.writes = (_cutoff(0, host, 500.0),)

    lane.control(operator)

    assert operator.seen[0].values == (("cutoff", 500.0),)


def test_a_lane_write_shadows_a_later_stage_write(engine):
    host = _Host(engine)
    host._declare_registers({"cutoff": RegisterSpec(1000.0)})
    lane = host.connect(_Source(engine))
    operator = _Filter()
    engine.writes = (
        _cutoff(100, host, 250.0, connection=lane),
        _cutoff(200, host, 500.0),
    )

    lane.control(operator)

    assert [w.values for w in operator.seen] == [(("cutoff", 250.0),)]


def test_a_lane_write_does_not_shadow_an_earlier_stage_write(engine):
    """Same events as above with the timestamps swapped. Collecting the lane's
    owned names before the walk would wrongly drop the Stage write here."""
    host = _Host(engine)
    host._declare_registers({"cutoff": RegisterSpec(1000.0)})
    lane = host.connect(_Source(engine))
    operator = _Filter()
    engine.writes = (
        _cutoff(100, host, 500.0),
        _cutoff(200, host, 250.0, connection=lane),
    )

    lane.control(operator)

    assert [w.values for w in operator.seen] == [
        (("cutoff", 500.0),),
        (("cutoff", 250.0),),
    ]


def test_a_lane_scoped_register_never_receives_a_stage_write(engine):
    """`level` is the lane's from birth, so a Stage broadcast cannot reach it."""
    host = _Host(engine)
    host._declare_registers({"level": RegisterSpec(1.0)})
    lane = host.connect(_Source(engine))
    engine.writes = (RegisterWrite(0, host, (("level", 0.5),)),)

    lane.control(lane.gain)

    assert lane.gain._writes == ()


def test_a_write_for_another_lane_is_ignored(engine):
    host = _Host(engine)
    host._declare_registers({"cutoff": RegisterSpec(1000.0)})
    mine = host.connect(_Source(engine))
    theirs = host.connect(_Source(engine))
    operator = _Filter()
    engine.writes = (_cutoff(0, host, 250.0, connection=theirs),)

    mine.control(operator)

    assert operator.seen == ()


def test_a_write_for_another_stage_is_ignored(engine):
    host, other = _Host(engine), _Host(engine)
    host._declare_registers({"cutoff": RegisterSpec(1000.0)})
    lane = host.connect(_Source(engine))
    operator = _Filter()
    engine.writes = (_cutoff(0, other, 250.0),)

    lane.control(operator)

    assert operator.seen == ()


def test_writes_reach_the_operator_tagged_with_this_lane(engine):
    host = _Host(engine)
    host._declare_registers({"cutoff": RegisterSpec(1000.0)})
    lane = host.connect(_Source(engine))
    operator = _Filter()
    engine.writes = (_cutoff(0, host, 500.0),)

    lane.control(operator)

    assert operator.seen[0].connection is lane
    assert operator.seen[0].stage is host


def test_a_signal_on_the_lane_suppresses_writes_to_that_register(engine):
    host = _Host(engine)
    host._declare_registers({"cutoff": RegisterSpec(1000.0)})
    lane = host.connect(_Source(engine))
    lane._allow_overrides(
        {"cutoff": RegisterSpec(1000.0, accepts_signal=True)}
    )
    lane._connect_register(_Source(engine, value=0.2), "cutoff")
    operator = _Filter()
    engine.writes = (_cutoff(0, host, 500.0, connection=lane),)

    lane.control(operator)

    assert operator.seen == ()


def test_a_signal_on_the_stage_suppresses_broadcasts_to_its_lanes(engine):
    host = _Host(engine)
    host._declare_registers(
        {"cutoff": RegisterSpec(1000.0, accepts_signal=True)}
    )
    lane = host.connect(_Source(engine))
    host._connect_register(_Source(engine, value=0.2), "cutoff")
    operator = _Filter()
    engine.writes = (_cutoff(0, host, 500.0),)

    lane.control(operator)

    assert operator.seen == ()


# -- quiescence --------------------------------------------------------


def test_a_lane_is_awake_while_its_source_is(engine):
    host = _Host(engine)
    lane = host.connect(_Source(engine, settles=False))
    lane.process()
    assert lane.quiescent is False


def test_a_lane_sleeps_once_it_and_its_source_have_settled(engine):
    host = _Host(engine)
    lane = host.connect(_Source(engine, value=0.0, settles=True))
    lane.source.process()
    lane.process()
    assert lane.quiescent is True


def test_either_address_wakes_a_lane(engine):
    host = _Host(engine)
    source = _Source(engine, value=0.0, settles=True)
    lane = host.connect(source)
    source.process()
    lane.process()
    assert lane.quiescent is True

    engine.writes = (_cutoff(0, host, 1.0),)
    assert lane.quiescent is False

    engine.writes = (_cutoff(0, host, 1.0, connection=lane),)
    assert lane.quiescent is False


def test_a_released_lane_gives_back_its_row_and_goes_inactive(engine):
    pool = engine.pool
    host = _Host(engine)
    lane = host.connect(_Source(engine))
    row = lane.bus.current.id

    lane.release()

    # `active` is what the engine checks before committing a write, so
    # automation aimed at a lane that has gone does not resurrect it.
    assert lane.active is False
    assert Bus(pool).current.id == row


def test_using_a_released_lane_raises_rather_than_reading_a_reissued_row(engine):
    """The row now belongs to somebody else, so this must not quietly work."""
    host = _Host(engine)
    lane = host.connect(_Source(engine))
    lane.release()

    with pytest.raises(RuntimeError):
        lane.process()
