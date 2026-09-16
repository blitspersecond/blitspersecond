"""`AudioEngine`: the clock, the relative timeline, and one demand per block.

This is the first file that drives a real engine rather than a stub, so it is
also the first end-to-end proof that a graph makes sound.

**The relative timeline is the subject.** A `dt` is an offset from an origin
the worker chooses when it admits the batch, which means the tests cannot
assert absolute timestamps from the caller's side -- they assert the thing
that actually matters, that the SPACING inside a batch survives whenever it
happens to be admitted. The test that submits one batch at clock zero and the
same batch four blocks later, and expects identical relative timing, is the
point of the whole design.

**Writes are committed after rendering, not before.** So a register read back
from a Stage describes the value at the latest settled boundary, while
Operators during the block saw the timestamped events. Both halves are
tested, because getting only one right is the likely mistake.
"""

import numpy as np
import pytest

from blitspersecond.audio.synth.bus import StereoBus
from blitspersecond.audio.constants import BLOCK_SIZE, SAMPLE_RATE
from blitspersecond.audio.synth.engine import AudioEngine
from blitspersecond.audio.synth.mixing_desk import MixingDesk
from blitspersecond.audio.synth.operators import Gain, Operator
from blitspersecond.audio.synth.program import Program
from blitspersecond.audio.synth.registers import RegisterSpec
from blitspersecond.audio.synth.stages import ApplyStage, CompositeStage, SourceStage


class _Tone(Operator):
    """A level-controlled constant, so arithmetic is readable in one sample."""

    registers = {"tone": RegisterSpec(1.0)}

    def __init__(self):
        self.seen = []

    def control(self, clock, registers, writes):
        self.value = registers["tone"]
        self.seen.append(tuple(w.timestamp for w in writes))

    def process(self, bus, /):
        bus.current.data.fill(float(self.value))
        bus.current.quiescent = False
        return bus


def _watcher(stage) -> _Tone:
    """The `_Tone` inside a source's chain, narrowed for the type checker."""
    operator = stage._program.operators[0]
    assert isinstance(operator, _Tone)
    return operator


@pytest.fixture
def engine() -> AudioEngine:
    return AudioEngine()


# -- the clock ---------------------------------------------------------


def test_a_new_engine_starts_at_zero(engine):
    assert engine.clock == 0
    assert engine.block == 0


def test_one_advance_is_one_block(engine):
    engine.advance()
    assert engine.clock == BLOCK_SIZE
    assert engine.block == 1


def test_a_second_of_audio_is_exactly_one_hundred_and_twenty_advances(engine):
    for _ in range(120):
        engine.advance()
    assert engine.clock == SAMPLE_RATE


def test_the_block_number_is_derived_from_the_clock(engine):
    """Never counted separately, so the two cannot drift apart."""
    for expected in range(4):
        assert engine.block == expected
        assert engine.clock == expected * BLOCK_SIZE
        engine.advance()


# -- the default output ------------------------------------------------


def test_an_engine_starts_with_a_silent_stereo_desk(engine):
    """What lets a Driver hold the device open before a game builds a graph."""
    assert isinstance(engine.output, MixingDesk)
    settled = engine.advance()
    assert isinstance(settled, StereoBus)
    assert settled.current.data.shape == (BLOCK_SIZE, 2)
    assert np.all(settled.current.data == 0.0)


def test_the_output_can_be_replaced(engine):
    source = engine.source(Program(_Tone))
    engine.output = source
    assert engine.output is source
    assert np.all(engine.advance().current.data == 1.0)


def test_a_stage_from_another_engine_cannot_be_the_output(engine):
    with pytest.raises(ValueError):
        engine.output = AudioEngine().source()


# -- building a graph --------------------------------------------------


def test_the_factories_build_stages_bound_to_this_engine(engine):
    assert isinstance(engine.source(), SourceStage)
    assert isinstance(engine.composite(), CompositeStage)
    assert isinstance(engine.apply(Program(_Tone)), ApplyStage)
    assert isinstance(engine.mixing_desk(), MixingDesk)
    assert engine.source()._engine is engine


def test_a_graph_reaches_the_terminal_desk(engine):
    """End to end: a source, through a desk, out as two channels."""
    engine.output.connect(engine.source(Program(_Tone)))
    stereo = engine.advance().current.data
    assert np.allclose(stereo[:, 0], np.sqrt(0.5))
    assert np.allclose(stereo[:, 1], np.sqrt(0.5))


def test_topology_is_frozen_while_the_engine_is_driven(engine):
    source = engine.source()
    engine._running = True

    with pytest.raises(RuntimeError):
        engine.source()
    with pytest.raises(RuntimeError):
        engine.output.connect(source)
    with pytest.raises(RuntimeError):
        engine.output = source


# -- the relative timeline ---------------------------------------------


def test_a_dt_is_an_offset_from_an_origin_the_worker_chooses(engine):
    source = engine.source(Program(_Tone))
    engine.output = source
    engine.schedule([(100, source, {"tone": 2.0})])

    engine.advance()

    assert _watcher(source).seen[0] == (100,)


def test_relative_spacing_survives_a_late_admission(engine):
    """The point of the design: a score cannot be smeared by when it lands."""
    first = AudioEngine()
    early = first.source(Program(_Tone))
    first.output = early
    first.schedule([(10, early, {"tone": 2.0}), (300, early, {"tone": 3.0})])
    first.advance()

    late = engine.source(Program(_Tone))
    engine.output = late
    for _ in range(4):
        engine.advance()
    engine.schedule([(10, late, {"tone": 2.0}), (300, late, {"tone": 3.0})])
    engine.advance()

    seen_early = _watcher(early).seen[0]
    seen_late = _watcher(late).seen[4]
    assert seen_early == (10, 300)
    assert seen_late == (4 * BLOCK_SIZE + 10, 4 * BLOCK_SIZE + 300)
    assert [t - seen_late[0] for t in seen_late] == [
        t - seen_early[0] for t in seen_early
    ]


def test_a_batch_spans_as_many_blocks_as_it_needs(engine):
    source = engine.source(Program(_Tone))
    engine.output = source
    engine.schedule(
        [
            (0, source, {"tone": 1.0}),
            (BLOCK_SIZE + 50, source, {"tone": 2.0}),
            (3 * BLOCK_SIZE, source, {"tone": 3.0}),
        ]
    )

    for _ in range(4):
        engine.advance()

    seen = _watcher(source).seen
    assert [len(block) for block in seen[:4]] == [1, 1, 0, 1]


def test_a_batch_admitted_mid_block_waits_for_the_next_one(engine):
    """The resolution boundary: what is being produced cannot be edited."""
    source = engine.source(Program(_Tone))
    engine.output = source
    engine.writes  # resolves this block with nothing in it

    engine.schedule([(0, source, {"tone": 5.0})])

    assert engine.writes == ()
    engine.advance()
    assert engine.writes[0].values == (("tone", 5.0),)


def test_two_writes_at_one_sample_keep_the_order_they_were_written(engine):
    """A retrigger is `gate=False` then `gate=True` at the same instant."""
    source = engine.source(Program(_Tone))
    engine.schedule([(0, source, {"tone": 1.0}), (0, source, {"tone": 2.0})])
    engine.advance()

    assert [w.values for w in engine._current_writes] == [
        (("tone", 1.0),),
        (("tone", 2.0),),
    ]


def test_inspecting_quiescence_never_admits_a_pending_batch(engine):
    source = engine.source(Program(_Tone))
    engine.schedule([(0, source, {"tone": 2.0})])

    assert engine._prepared_writes == ()
    assert engine.writes != ()


# -- committing --------------------------------------------------------


def test_a_write_lands_in_the_bank_after_the_block_it_belongs_to(engine):
    source = engine.source(Program(_Tone))
    engine.output = source
    engine.schedule([(100, source, {"tone": 7.0})])

    assert source.registers["tone"] == 1.0
    engine.advance()
    assert source.registers["tone"] == 7.0


def test_a_stage_nothing_pulled_still_gets_its_value(engine):
    """So an Operator reached later starts from an authoritative value."""
    unreached = engine.source(Program(_Tone))
    engine.schedule([(0, unreached, {"tone": 4.0})])

    engine.advance()

    assert unreached.registers["tone"] == 4.0


def test_a_lane_write_lands_on_the_lane(engine):
    desk = engine.output
    source = engine.source(Program(_Tone))
    desk.connect(source)
    engine.schedule([(0, desk, source, {"level": 0.5, "pan": -1.0})])

    engine.advance()

    assert desk.registers_for(source)["level"] == 0.5
    assert desk.registers_for(source)["pan"] == -1.0


def test_automation_for_a_disconnected_lane_does_not_resurrect(engine):
    desk = engine.output
    source = engine.source(Program(_Tone))
    desk.connect(source)
    lane = desk._connection(source)
    engine.schedule([(BLOCK_SIZE + 10, desk, source, {"level": 0.25})])

    desk.disconnect(source)
    engine.advance()
    engine.advance()

    assert lane.active is False
    assert lane._registers["level"] == 1.0


# -- what schedule refuses ---------------------------------------------


def test_an_unknown_register_is_a_caller_error(engine):
    source = engine.source(Program(_Tone))
    with pytest.raises(ValueError, match="no register"):
        engine.schedule([(0, source, {"nonesuch": 1.0})])


def test_an_out_of_range_value_is_a_caller_error(engine):
    """Validated on the calling thread, where the traceback is useful."""
    desk = engine.output
    source = engine.source()
    desk.connect(source)
    with pytest.raises(ValueError):
        engine.schedule([(0, desk, source, {"pan": 2.0})])


def test_a_bad_event_admits_none_of_its_batch(engine):
    source = engine.source(Program(_Tone))
    with pytest.raises(ValueError):
        engine.schedule(
            [(0, source, {"tone": 1.0}), (0, source, {"nonesuch": 1.0})]
        )

    engine.advance()
    assert engine._current_writes == ()


@pytest.mark.parametrize(
    "dt, error",
    [(-1, ValueError), (1.5, TypeError), (True, TypeError)],
)
def test_a_dt_must_be_a_non_negative_whole_sample(engine, dt, error):
    source = engine.source(Program(_Tone))
    with pytest.raises(error):
        engine.schedule([(dt, source, {"tone": 1.0})])


def test_a_foreign_stage_cannot_be_addressed(engine):
    with pytest.raises(ValueError):
        engine.schedule([(0, AudioEngine().source(), {"tone": 1.0})])


def test_an_unconnected_lane_cannot_be_addressed(engine):
    desk = engine.output
    with pytest.raises(ValueError):
        engine.schedule([(0, desk, engine.source(), {"level": 0.5})])


@pytest.mark.parametrize(
    "event", [(0,), (0, None, None, None, None)]
)
def test_an_event_must_have_three_or_four_items(engine, event):
    with pytest.raises(ValueError):
        engine.schedule([event])


def test_an_empty_schedule_is_refused(engine):
    with pytest.raises(ValueError):
        engine.schedule([])


def test_a_write_with_no_values_is_refused(engine):
    source = engine.source(Program(_Tone))
    with pytest.raises(ValueError):
        engine.schedule([(0, source, {})])


def test_values_must_be_a_mapping(engine):
    source = engine.source(Program(_Tone))
    with pytest.raises(TypeError):
        engine.schedule([(0, source, [("tone", 1.0)])])


# -- steady state -------------------------------------------------------


def test_a_running_graph_claims_no_rows_after_it_settles(engine):
    """The allocation-free property, end to end rather than per component."""
    desk = engine.output
    for _ in range(3):
        desk.connect(engine.source(Program(_Tone, lambda: Gain(0.3))))

    engine.advance()
    settled = engine.pool.count

    for _ in range(20):
        engine.advance()

    assert engine.pool.count == settled
