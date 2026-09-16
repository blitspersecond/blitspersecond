"""Withdrawing a scheduled future: `cancel()` and `schedule(replacing=...)`.

**The graph is the identity.** A queued write already names the Stage it is
addressed to, so a cancellation names that Stage rather than a handle or a
group the caller had to mint. The tests here are mostly about that choice
holding up: that one node's future can be dropped without touching another's,
that a lane and its Stage are separate addresses, and that a node with nothing
queued is not an error.

**Cancellation is a control operation, not a stop button.** It withdraws
instructions that have not been rendered yet. It does not reach into retained
state, so a reverb behind a cancelled source goes on ringing until it drains
-- pinned here, because the opposite behaviour would be easy to implement by
accident and would sound wrong.

**The acceptance proof is Choon-shaped**, at the bottom: ten PCM sources into
one desk, a region part-rendered, replaced atomically with another, checked
against an oracle built by concatenating the regions independently.
"""

import numpy as np
import pytest

from blitspersecond.audio.constants import BLOCK_SIZE, SAMPLE_RATE
from blitspersecond.audio.synth.engine import AudioEngine
from blitspersecond.audio.synth.operators import Echo, Gain, Operator, PCM
from blitspersecond.audio.synth.program import Program
from blitspersecond.audio.synth.registers import RegisterSpec
from blitspersecond.audio.sound import Sound


class _Tone(Operator):
    """A level-controlled constant, so arithmetic is readable in one sample.

    Honours its writes segment by segment, like a real operator, so a `dt=0`
    event lands inside the block it was admitted to rather than at the
    boundary after it. Without that the tests here would be reading the
    settled bank a block late and calling it cancellation.
    """

    registers = {"tone": RegisterSpec(1.0)}

    _level = 1.0
    _clock = 0
    _writes = ()

    def control(self, clock, registers, writes):
        self._clock = clock
        self._level = registers["tone"]
        self._writes = writes

    def process(self, bus, /):
        data = bus.current.data
        level = float(self._level)
        start = 0
        for write in self._writes:
            stop = write.timestamp - self._clock
            data[start:stop] = level
            for _name, value in write.values:
                level = float(value)
            start = stop
        data[start:] = level
        bus.current.quiescent = False
        return bus


@pytest.fixture
def engine() -> AudioEngine:
    return AudioEngine()


def _render(engine, blocks):
    """Settle whole blocks and return the terminal Stage's first channel."""
    out = []
    for _ in range(blocks):
        settled = engine.advance()
        data = settled.current.data
        out.append(np.array(data[:, 0] if data.ndim == 2 else data))
    return np.concatenate(out)


def _tone_source(engine):
    source = engine.source(Program(_Tone))
    engine.output = source
    return source


# --------------------------------------------------------------------------
# What a cancellation reaches


def test_cancelling_before_anything_renders_prevents_the_whole_timeline(engine):
    source = _tone_source(engine)
    engine.schedule([(0, source, {"tone": 5.0}), (BLOCK_SIZE, source, {"tone": 9.0})])
    engine.cancel([source])
    assert np.all(_render(engine, 3) == 1.0)


def test_cancelling_part_way_leaves_what_already_rendered_alone(engine):
    source = _tone_source(engine)
    engine.schedule(
        [
            (0, source, {"tone": 5.0}),
            (BLOCK_SIZE * 2, source, {"tone": 9.0}),
        ]
    )
    first = _render(engine, 1)
    engine.cancel([source])
    rest = _render(engine, 3)
    # The write that already landed is a fact; the one still queued is gone.
    assert np.all(first == 5.0)
    assert np.all(rest == 5.0)


def test_a_cancelled_node_can_be_scheduled_again(engine):
    source = _tone_source(engine)
    engine.schedule([(BLOCK_SIZE, source, {"tone": 9.0})])
    engine.cancel([source])
    engine.schedule([(BLOCK_SIZE, source, {"tone": 3.0})])
    assert np.all(_render(engine, 1) == 1.0)
    assert np.all(_render(engine, 2) == 3.0)


def test_one_node_cannot_cancel_another(engine):
    left, right = engine.source(Program(_Tone)), engine.source(Program(_Tone))
    desk = engine.mixing_desk()
    desk.connect(left)
    desk.connect(right)
    engine.output = desk
    engine.schedule(
        [
            (0, desk, left, {"level": 1.0, "pan": 0.0}),
            (0, desk, right, {"level": 0.0, "pan": 0.0}),
            (BLOCK_SIZE, left, {"tone": 4.0}),
            (BLOCK_SIZE, right, {"tone": 7.0}),
        ]
    )
    engine.cancel([left])
    _render(engine, 3)
    assert float(left._registers["tone"]) == 1.0
    assert float(right._registers["tone"]) == 7.0


def test_a_lane_and_its_stage_are_different_addresses(engine):
    source = engine.source(Program(_Tone))
    desk = engine.mixing_desk()
    desk.connect(source)
    engine.output = desk
    engine.schedule(
        [
            (BLOCK_SIZE, source, {"tone": 4.0}),
            (BLOCK_SIZE, desk, source, {"level": 0.25}),
        ]
    )
    # Cancelling the strip leaves the voice's own future untouched.
    engine.cancel([(desk, source)])
    _render(engine, 3)
    assert float(source._registers["tone"]) == 4.0
    assert float(desk.registers_for(source)["level"]) == 1.0


def test_cancelling_a_stage_does_not_reach_its_lanes(engine):
    source = engine.source(Program(_Tone))
    desk = engine.mixing_desk()
    desk.connect(source)
    engine.output = desk
    engine.schedule([(BLOCK_SIZE, desk, source, {"level": 0.25})])
    # The desk names the lane's OWNER, not the lane. Addressing is the same
    # as it is for a write: a Stage and one of its inputs are two places.
    engine.cancel([desk])
    _render(engine, 3)
    assert float(desk.registers_for(source)["level"]) == 0.25


def test_cancelling_a_node_with_nothing_queued_is_not_an_error(engine):
    source = _tone_source(engine)
    engine.cancel([source])
    engine.cancel([source])
    engine.schedule([(0, source, {"tone": 2.0})])
    _render(engine, 1)
    engine.cancel([source])
    engine.cancel([source])
    assert np.all(_render(engine, 1) == 2.0)


def test_a_cancelled_source_leaves_the_tail_behind_it_ringing(engine):
    # Cancellation is control, not a mute. The echo has audio in its ring
    # that was already produced, and no instruction can un-produce it.
    source = engine.source(Program(_Tone))
    tail = engine.apply(Program(lambda: Echo(200, feedback=0.7, mix=1.0)))
    tail.connect(source)
    engine.output = tail
    engine.schedule(
        [(0, source, {"tone": 1.0}), (BLOCK_SIZE * 2, source, {"tone": 0.0})]
    )
    _render(engine, 2)
    engine.cancel([source])
    after = _render(engine, 2)
    assert np.abs(after).max() > 0.1


# --------------------------------------------------------------------------
# Replacement, which is the one that has to be atomic


def test_a_replacement_supersedes_only_the_targets_it_names(engine):
    left, right = engine.source(Program(_Tone)), engine.source(Program(_Tone))
    desk = engine.mixing_desk()
    desk.connect(left)
    desk.connect(right)
    engine.output = desk
    engine.schedule(
        [
            (BLOCK_SIZE * 4, left, {"tone": 4.0}),
            (BLOCK_SIZE * 4, right, {"tone": 7.0}),
        ]
    )
    engine.schedule([(0, left, {"tone": 2.0})], replacing=[left])
    _render(engine, 6)
    assert float(left._registers["tone"]) == 2.0
    assert float(right._registers["tone"]) == 7.0


def test_a_replacement_does_not_withdraw_the_timeline_it_arrived_with(engine):
    source = _tone_source(engine)
    engine.schedule([(BLOCK_SIZE * 4, source, {"tone": 9.0})])
    engine.schedule(
        [(BLOCK_SIZE, source, {"tone": 3.0})],
        replacing=[source],
    )
    _render(engine, 6)
    assert float(source._registers["tone"]) == 3.0


def test_a_rejected_replacement_leaves_the_existing_timeline_intact(engine):
    source = _tone_source(engine)
    engine.schedule([(BLOCK_SIZE, source, {"tone": 6.0})])
    with pytest.raises(ValueError, match="no register"):
        engine.schedule([(0, source, {"nonsense": 1.0})], replacing=[source])
    _render(engine, 3)
    assert float(source._registers["tone"]) == 6.0


def test_replacing_names_the_same_addresses_schedule_does(engine):
    source = engine.source(Program(_Tone))
    desk = engine.mixing_desk()
    desk.connect(source)
    engine.output = desk
    engine.schedule(
        [
            (BLOCK_SIZE * 4, source, {"tone": 9.0}),
            (BLOCK_SIZE * 4, desk, source, {"level": 0.1}),
        ]
    )
    engine.schedule(
        [(0, desk, source, {"level": 0.5})],
        replacing=[source, (desk, source)],
    )
    _render(engine, 6)
    assert float(source._registers["tone"]) == 1.0
    assert float(desk.registers_for(source)["level"]) == 0.5


# --------------------------------------------------------------------------
# What a caller may say


def test_an_ordinary_caller_sees_no_change(engine):
    source = _tone_source(engine)
    assert engine.schedule([(0, source, {"tone": 3.0})]) is None
    assert np.all(_render(engine, 1) == 3.0)


def test_cancel_needs_at_least_one_target(engine):
    with pytest.raises(ValueError, match="no targets"):
        engine.cancel([])


def test_a_target_must_be_a_stage_or_a_lane(engine):
    source = _tone_source(engine)
    with pytest.raises(TypeError, match="Stage, or a"):
        engine.cancel([object()])
    with pytest.raises(TypeError, match="Stage, or a"):
        engine.cancel([(source, source, source)])


def test_a_target_must_belong_to_this_engine(engine):
    foreign = AudioEngine().source(Program(_Tone))
    with pytest.raises(ValueError, match="foreign Stage"):
        engine.cancel([foreign])
    desk = engine.mixing_desk()
    with pytest.raises(ValueError, match="foreign input Stage"):
        engine.cancel([(desk, foreign)])


def test_a_lane_target_must_actually_be_connected(engine):
    source = engine.source(Program(_Tone))
    desk = engine.mixing_desk()
    with pytest.raises(ValueError, match="no input connected"):
        engine.cancel([(desk, source)])


# --------------------------------------------------------------------------
# The acceptance proof: a transport over ten stems


def _stems(engine, count=10):
    """Ten PCM voices into one desk, as a stem player is shaped."""
    sounds = [
        Sound(f"stem{index}", np.linspace(-1.0, 1.0, 40_000) * (index + 1) / 10, 48_000)
        for index in range(count)
    ]
    desk = engine.mixing_desk()
    voices = []
    for sound in sounds:
        voice = engine.source(Program(lambda s=sound: PCM(s), lambda: Gain(1.0)))
        desk.connect(voice)
        voices.append(voice)
    engine.output = desk
    engine.schedule(
        [(0, desk, voice, {"level": 1.0, "pan": 0.0}) for voice in voices]
    )
    return desk, voices, sounds


def _region_oracle(sounds, regions, length):
    """Every stem summed, played from each cue in turn, built independently."""
    gain = float(np.cos(np.pi / 4))  # a centred strip, in one channel
    expected = np.zeros(length, dtype=np.float64)
    at = 0
    for cue, span in regions:
        for sound in sounds:
            start = int(round(cue * sound.rate))
            expected[at : at + span] += sound.data[start : start + span] * gain
        at += span
    return expected


def test_a_queued_transport_can_be_replaced_mid_flight(engine):
    desk, voices, sounds = _stems(engine)
    span = BLOCK_SIZE * 3

    # Region A, queued as several exact repetitions into the future.
    engine.schedule(
        [
            (index * span, voice, {"playhead": 0.1, "gate": True})
            for index in range(4)
            for voice in voices
        ]
    )
    part_of_a = _render(engine, 2)

    # Region B, atomically superseding every stem's unresolved future.
    engine.schedule(
        [(0, voice, {"playhead": 0.5, "gate": True}) for voice in voices],
        replacing=list(voices),
    )
    then_b = _render(engine, 3)

    played = np.concatenate([part_of_a, then_b])
    expected = _region_oracle(
        sounds, [(0.1, BLOCK_SIZE * 2), (0.5, BLOCK_SIZE * 3)], len(played)
    )
    assert played == pytest.approx(expected, abs=1e-5)


def test_cancelling_a_transport_is_permanent_silence_after_the_boundary(engine):
    desk, voices, sounds = _stems(engine)
    span = BLOCK_SIZE * 3
    engine.schedule(
        [
            (index * span, voice, {"playhead": 0.1, "gate": True})
            for index in range(4)
            for voice in voices
        ]
    )
    _render(engine, 2)

    engine.schedule(
        [(0, voice, {"gate": False}) for voice in voices],
        replacing=list(voices),
    )
    # Nothing queued can bring it back: the repetitions are gone, so the
    # stop is not undone three blocks later by a write from the old future.
    assert not np.any(_render(engine, 12))


def test_a_transport_over_one_stem_leaves_the_others_running(engine):
    desk, voices, sounds = _stems(engine)
    engine.schedule(
        [(BLOCK_SIZE * 2, voice, {"playhead": 0.2, "gate": True}) for voice in voices]
    )
    engine.cancel([voices[3]])
    _render(engine, 4)
    assert voices[3]._program.operators[0].active is False
    assert all(
        voice._program.operators[0].active for index, voice in enumerate(voices)
        if index != 3
    )
