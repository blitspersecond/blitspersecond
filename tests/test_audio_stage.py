"""`Stage`: the settled cache, the feedback boundary, and quiescence.

Driven through a one-method subclass and a stub engine, which is what the
`StageEngine` Protocol is for. A real AudioEngine would bring a clock that
moves on its own, and every test here needs to say exactly which block it is
on.

**Renders are counted, not inferred.** Most of this file is about how often
`_render` runs, because that is what the cache, the sleep path and the
feedback boundary all actually change. Asserting on returned samples would
pass whether a Stage rendered once, twice, or not at all.

**Quiescence is tested for its failure direction.** Being awake when you
could sleep costs CPU; being asleep when you should not is a dropped note. So
every ambiguous case here asserts `False` -- stay awake -- and a test that
expects sleep has to set up proof of silence explicitly.
"""

import numpy as np
import pytest

from blitspersecond.audio.synth.block import BlockPool
from blitspersecond.audio.synth.bus import Bus
from blitspersecond.audio.constants import BLOCK_SIZE
from blitspersecond.audio.synth.operators import Gain
from blitspersecond.audio.synth.registers import RegisterSpec, RegisterWrite
from blitspersecond.audio.synth.stage import Stage


class _Engine:
    """A StageEngine: a clock, a pool, and whatever writes were prepared."""

    def __init__(self):
        self.pool = BlockPool()
        self.clock = 0
        self.writes: tuple[RegisterWrite, ...] = ()
        self.prepared = True
        self.frozen = False

    @property
    def block(self) -> int:
        return self.clock // BLOCK_SIZE

    @property
    def _prepared_writes(self):
        return self.writes if self.prepared else ()

    def _assert_topology_mutable(self):
        if self.frozen:
            raise RuntimeError("audio topology cannot change while driven")

    def advance(self):
        self.clock += BLOCK_SIZE


class _Counting(Stage):
    """A Stage that records every render and emits a chosen level."""

    def __init__(self, engine, value=0.0, quiescent=False):
        super().__init__(engine)
        self.renders = 0
        self.value = value
        self.settles = quiescent

    def _render(self):
        self.renders += 1
        self._output.current.data.fill(self.value)
        self._output.current.quiescent = self.settles
        return self._output


@pytest.fixture
def engine() -> _Engine:
    return _Engine()


# -- settling once per block -------------------------------------------


def test_a_stage_renders_once_however_often_it_is_pulled(engine):
    """Fan-out correctness: a shared oscillator advances its phase once."""
    stage = _Counting(engine)
    for _ in range(5):
        stage.process()
    assert stage.renders == 1


def test_every_pull_in_one_block_gets_the_same_object(engine):
    stage = _Counting(engine)
    assert stage.process() is stage.process()


def test_a_new_block_renders_again(engine):
    stage = _Counting(engine)
    stage.process()
    engine.advance()
    stage.process()
    assert stage.renders == 2


def test_a_stage_starts_mono_and_silent(engine):
    stage = _Counting(engine)
    assert stage.channels == 1
    assert isinstance(stage._output, Bus)
    assert np.all(stage.process().current.data == 0.0)


# -- the feedback boundary ---------------------------------------------


class _Recursive(Stage):
    """A Stage that pulls itself while rendering, as a graph cycle does."""

    def __init__(self, engine):
        super().__init__(engine)
        self.renders = 0
        self.saw = []

    def _render(self):
        self.renders += 1
        self.saw.append(self.process().current.data[0])
        self._output.current.data.fill(float(self.renders))
        # Declaring itself active every block. A block is born quiescent, so
        # a Stage that never says otherwise settles to sleep after its first
        # render -- correct, and not what this test is about.
        self._output.current.quiescent = False
        return self._output


def test_a_cycle_reads_the_previous_block_instead_of_recursing(engine):
    stage = _Recursive(engine)
    stage.process()
    engine.advance()
    stage.process()
    engine.advance()
    stage.process()

    assert stage.renders == 3
    # First pull sees the zero-filled initial output; then each block sees
    # what the one before it produced.
    assert stage.saw == [0.0, 1.0, 2.0]


def test_a_recursive_pull_does_not_mark_the_block_settled(engine):
    """Otherwise the real render would be discarded as already done."""
    stage = _Recursive(engine)
    stage.process()
    assert stage.renders == 1
    assert np.all(stage._settled.current.data == 1.0)


# -- quiescence --------------------------------------------------------


def test_a_stage_that_has_never_rendered_is_awake(engine):
    """Never having run is not proof of silence."""
    assert _Counting(engine).quiescent is False


def test_a_stage_that_settled_silence_sleeps(engine):
    stage = _Counting(engine, quiescent=True)
    stage.process()
    assert stage.quiescent is True


def test_a_sleeping_stage_is_not_rendered_again(engine):
    stage = _Counting(engine, quiescent=True)
    stage.process()
    engine.advance()
    stage.process()
    assert stage.renders == 1


def test_a_sleeping_stage_still_answers_with_its_settled_block(engine):
    stage = _Counting(engine, value=0.0, quiescent=True)
    stage.process()
    engine.advance()
    assert stage.process() is stage._settled


def test_waking_a_stage_makes_it_render_again(engine):
    stage = _Counting(engine, quiescent=True)
    stage.process()
    engine.advance()
    stage._wake()
    stage.process()
    assert stage.renders == 2


def test_a_write_addressed_here_keeps_the_stage_awake(engine):
    stage = _Counting(engine, quiescent=True)
    stage.process()
    engine.advance()
    engine.writes = (RegisterWrite(400, stage, (("level", 0.5),)),)

    assert stage.quiescent is False


def test_a_write_addressed_elsewhere_does_not(engine):
    stage = _Counting(engine, quiescent=True)
    other = _Counting(engine)
    stage.process()
    engine.writes = (RegisterWrite(0, other, (("level", 0.5),)),)

    assert stage.quiescent is True


def test_asking_whether_a_stage_sleeps_never_admits_a_pending_batch(engine):
    """Inspection must not pull a scheduled batch into the current block."""
    stage = _Counting(engine, quiescent=True)
    stage.process()
    engine.writes = (RegisterWrite(0, stage, (("level", 0.5),)),)
    engine.prepared = False

    assert stage.quiescent is True


def test_a_connected_signal_keeps_its_target_awake(engine):
    """Sleeping would need a reverse link to wake it, and there is none."""
    stage = _Counting(engine, quiescent=True)
    stage._declare_registers({"level": RegisterSpec(1.0, accepts_signal=True)})
    stage.process()
    assert stage.quiescent is True

    stage.connect_signal(_Counting(engine), to="level")
    stage.process()

    assert stage.quiescent is False


def test_a_stage_asked_about_itself_mid_answer_stays_awake(engine):
    """A cycle asking whether it is asleep has no fixed point."""
    stage = _Counting(engine, quiescent=True)
    stage.process()
    stage._checking_quiescent = True
    assert stage.quiescent is False


# -- registers ---------------------------------------------------------


def test_declaring_a_register_seeds_its_default(engine):
    stage = _Counting(engine)
    stage._declare_registers({"level": RegisterSpec(0.25)})
    assert stage.registers["level"] == 0.25
    assert stage.register_specs["level"].default == 0.25


def test_the_same_declaration_twice_is_agreement(engine):
    """Three lanes each holding an Envelope share one gate."""
    stage = _Counting(engine)
    stage._declare_registers({"gate": RegisterSpec(False)})
    stage._declare_registers({"gate": RegisterSpec(False)})
    assert stage.registers["gate"] is False


def test_two_declarations_that_disagree_are_refused(engine):
    stage = _Counting(engine)
    stage._declare_registers({"level": RegisterSpec(1.0)})
    with pytest.raises(ValueError):
        stage._declare_registers({"level": RegisterSpec(0.5)})


def test_a_declaration_without_a_spec_is_refused(engine):
    with pytest.raises(TypeError):
        _Counting(engine)._declare_registers({"level": 1.0})


def test_a_later_declaration_does_not_reset_a_live_value(engine):
    stage = _Counting(engine)
    stage._declare_registers({"level": RegisterSpec(1.0)})
    stage._registers["level"] = 0.3
    stage._declare_registers({"level": RegisterSpec(1.0)})
    assert stage.registers["level"] == 0.3


def test_the_register_views_cannot_be_written_through(engine):
    stage = _Counting(engine)
    stage._declare_registers({"level": RegisterSpec(1.0)})
    with pytest.raises(TypeError):
        stage.registers["level"] = 0.5  # type: ignore[index]
    with pytest.raises(TypeError):
        stage.register_specs["level"] = RegisterSpec(0.5)  # type: ignore[index]


# -- control filtering -------------------------------------------------


def test_an_operator_only_sees_writes_for_registers_it_declared(engine):
    stage = _Counting(engine)
    gain = Gain()
    stage._declare_registers(dict(gain.registers))
    stage._declare_registers({"frequency": RegisterSpec(440.0)})
    engine.writes = (
        RegisterWrite(0, stage, (("level", 0.5), ("frequency", 880.0))),
    )

    stage.control(gain)

    assert gain._writes[0].values == (("level", 0.5),)


def test_a_write_for_no_declared_register_is_dropped_entirely(engine):
    stage = _Counting(engine)
    gain = Gain()
    stage._declare_registers(dict(gain.registers))
    stage._declare_registers({"frequency": RegisterSpec(440.0)})
    engine.writes = (RegisterWrite(0, stage, (("frequency", 880.0),)),)

    stage.control(gain)

    assert gain._writes == ()


def test_writes_addressed_to_another_stage_are_not_delivered(engine):
    stage, other = _Counting(engine), _Counting(engine)
    gain = Gain()
    stage._declare_registers(dict(gain.registers))
    engine.writes = (RegisterWrite(0, other, (("level", 0.5),)),)

    stage.control(gain)

    assert gain._writes == ()


def test_a_lane_write_is_not_delivered_as_a_stage_write(engine):
    stage = _Counting(engine)
    gain = Gain()
    stage._declare_registers(dict(gain.registers))
    engine.writes = (
        RegisterWrite(0, stage, (("level", 0.5),), connection=object()),
    )

    stage.control(gain)

    assert gain._writes == ()


def test_a_signal_driven_register_ignores_writes_for_that_block(engine):
    """The signal overrides the stored value completely; delivering a write
    too would let it briefly win against the thing overriding it."""
    stage = _Counting(engine)
    gain = Gain()
    stage._declare_registers(dict(gain.registers))
    stage.connect_signal(_Counting(engine, value=0.9), to="level")
    engine.writes = (RegisterWrite(0, stage, (("level", 0.5),)),)

    stage.control(gain)

    assert gain._writes == ()
    assert isinstance(gain._registers["level"], np.ndarray)


def test_control_passes_the_absolute_clock(engine):
    engine.clock = 4 * BLOCK_SIZE
    stage = _Counting(engine)
    gain = Gain()
    stage._declare_registers(dict(gain.registers))

    stage.control(gain)

    assert gain._clock == 4 * BLOCK_SIZE


# -- signals and topology ----------------------------------------------


def test_a_signal_connection_needs_a_named_register(engine):
    stage = _Counting(engine)
    with pytest.raises(ValueError):
        stage.connect_signal(_Counting(engine))
    with pytest.raises(ValueError):
        stage.disconnect_signal(_Counting(engine))


def test_disconnecting_a_signal_restores_the_stored_value(engine):
    stage = _Counting(engine)
    stage._declare_registers({"level": RegisterSpec(0.25, accepts_signal=True)})
    source = _Counting(engine, value=0.9)
    stage.connect_signal(source, to="level")
    stage.disconnect_signal(source, to="level")

    assert stage._register_signals.resolve()["level"] == 0.25


def test_a_stage_without_inputs_has_no_lanes(engine):
    stage = _Counting(engine)
    with pytest.raises(ValueError):
        stage.registers_for(_Counting(engine))
    with pytest.raises(ValueError):
        stage.register_specs_for(_Counting(engine))


def test_a_stage_cannot_be_built_while_the_engine_is_driven(engine):
    engine.frozen = True
    with pytest.raises(RuntimeError):
        _Counting(engine)


def test_a_signal_cannot_be_connected_while_the_engine_is_driven(engine):
    stage = _Counting(engine)
    stage._declare_registers({"level": RegisterSpec(1.0, accepts_signal=True)})
    source = _Counting(engine)
    engine.frozen = True

    with pytest.raises(RuntimeError):
        stage.connect_signal(source, to="level")
