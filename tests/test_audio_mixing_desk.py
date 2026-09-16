"""`MixingDesk` and `_Pan`: the one place the engine becomes two channels.

**Constant power is the thing worth testing, and it is not constant
amplitude.** A centred signal comes out at `sqrt(0.5)` in each channel, which
reads as "quieter" and is not: the two channels together carry exactly the
power of a hard-panned signal, which is what keeps a sweep steady. So the
tests assert the invariant -- squares sum to one, everywhere -- rather than
any single gain, and there is a separate test for what this law actually
costs, which is +3 dB when the two channels are folded to mono.

**The signal-rate path writes its scratch into the output's own channels**,
so it is tested against the scalar path at the same positions rather than
against hand-computed numbers. If the two agree, the in-place trick has not
corrupted the buffer it is computing in.
"""

import numpy as np
import pytest

from blitspersecond.audio.synth.block import BlockPool
from blitspersecond.audio.synth.bus import Bus, StereoBus
from blitspersecond.audio.constants import BLOCK_SIZE
from blitspersecond.audio.synth.mixing_desk import MixingDesk, _DeskConnection
from blitspersecond.audio.synth.operators import Operator
from blitspersecond.audio.synth.operators.pan import _Pan
from blitspersecond.audio.synth.program import Program
from blitspersecond.audio.synth.registers import RegisterScope, RegisterWrite
from blitspersecond.audio.synth.stages import SourceStage

ROOT_HALF = float(np.sqrt(0.5))


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
    def __init__(self, value: float):
        self.value = value

    def process(self, bus, /):
        bus.current.data.fill(self.value)
        return bus


def _tone(value):
    return lambda: _Tone(value)


@pytest.fixture
def engine() -> _Engine:
    return _Engine()


def _panned(pan, value=1.0, writes=()):
    """One block through a bare _Pan at a given position."""
    source = Bus(BlockPool())
    source.current.data.fill(value)
    output = StereoBus()
    operator = _Pan()
    operator.control(0, {"pan": pan}, tuple(writes))
    operator.route(source, output)
    return output.current.data


# -- constant-power panning --------------------------------------------


def test_centre_is_root_half_in_both_channels(engine):
    stereo = _panned(0.0)
    assert np.allclose(stereo[:, 0], ROOT_HALF)
    assert np.allclose(stereo[:, 1], ROOT_HALF)


def test_hard_left_and_hard_right_are_exact(engine):
    """Spelled out rather than left to cos/sin of a rounded quarter-turn."""
    left = _panned(-1.0)
    assert np.all(left[:, 0] == 1.0)
    assert np.all(left[:, 1] == 0.0)

    right = _panned(1.0)
    assert np.all(right[:, 0] == 0.0)
    assert np.all(right[:, 1] == 1.0)


@pytest.mark.parametrize("pan", [-1.0, -0.6, -0.25, 0.0, 0.25, 0.6, 1.0])
def test_power_is_constant_across_the_image(pan):
    """The invariant, rather than any one gain: squares always sum to one."""
    stereo = _panned(pan)
    power = stereo[:, 0] ** 2 + stereo[:, 1] ** 2
    assert np.allclose(power, 1.0)


def test_a_centred_signal_is_three_decibels_up_when_folded_to_mono():
    """What constant power actually costs, and the reason it is a choice.

    In stereo the pair matches a hard-panned signal exactly. Summed to one
    channel -- a laptop speaker, a phone, a mono TV -- the two halves add as
    amplitude rather than power, so the middle of the image comes back louder
    than its edges.
    """
    centre = _panned(0.0)[0]
    hard = _panned(1.0)[0]

    stereo_power = (centre[0] ** 2 + centre[1] ** 2) / (hard[0] ** 2 + hard[1] ** 2)
    mono_fold = (centre[0] + centre[1]) / (hard[0] + hard[1])

    assert 10 * np.log10(stereo_power) == pytest.approx(0.0, abs=1e-5)
    assert 20 * np.log10(mono_fold) == pytest.approx(3.01, abs=0.01)


def test_panning_left_of_centre_favours_the_left(engine):
    stereo = _panned(-0.5)
    assert stereo[0, 0] > stereo[0, 1]


def test_pan_declares_a_lane_scoped_signal_register():
    spec = _Pan().registers["pan"]
    assert _Pan.scope is RegisterScope.LANE
    assert (spec.minimum, spec.default, spec.maximum) == (-1.0, 0.0, 1.0)
    assert spec.accepts_signal is True


def test_a_write_moves_the_image_at_its_exact_sample():
    cut = 150
    stereo = _panned(
        -1.0, writes=[RegisterWrite(cut, None, (("pan", 1.0),))]
    )
    assert np.all(stereo[:cut, 0] == 1.0)
    assert np.all(stereo[cut:, 1] == 1.0)
    assert stereo[cut - 1, 0] == 1.0
    assert stereo[cut, 0] == 0.0


def test_a_signal_pan_matches_the_scalar_path_position_by_position():
    """Confirms the in-place trick does not corrupt its own scratch."""
    positions = np.linspace(-1.0, 1.0, BLOCK_SIZE, dtype=np.float32)
    signal = _panned(positions)

    for index in (0, 1, 100, BLOCK_SIZE // 2, BLOCK_SIZE - 1):
        expected = _panned(float(positions[index]))
        assert np.allclose(signal[index], expected[index], atol=1e-6)


def test_a_signal_pan_is_clipped_rather_than_trusted():
    """A signal is not validated by a RegisterSpec the way a write is."""
    positions = np.full(BLOCK_SIZE, 5.0, dtype=np.float32)
    stereo = _panned(positions)
    assert np.allclose(stereo[:, 0], 0.0, atol=1e-6)
    assert np.allclose(stereo[:, 1], 1.0, atol=1e-6)


def test_panning_preserves_the_lane_s_quiescence():
    source = Bus(BlockPool())
    source.current.quiescent = True
    output = StereoBus()
    operator = _Pan()
    operator.control(0, {"pan": 0.0}, ())

    operator.route(source, output)

    assert output.current.quiescent is True


# -- the desk ----------------------------------------------------------


def test_an_empty_desk_settles_stereo_silence(engine):
    """What lets a Driver hold the device open before a game builds a graph."""
    desk = MixingDesk(engine)
    settled = desk.process()

    assert desk.channels == 2
    assert isinstance(settled, StereoBus)
    assert settled.current.data.shape == (BLOCK_SIZE, 2)
    assert np.all(settled.current.data == 0.0)


def test_a_desk_gives_every_strip_level_and_placement(engine):
    desk = MixingDesk(engine)
    source = SourceStage(engine)
    desk.connect(source)

    strip = desk._connection(source)
    assert isinstance(strip, _DeskConnection)
    assert desk.registers_for(source)["level"] == 1.0
    assert desk.registers_for(source)["pan"] == 0.0


def test_a_centred_strip_reaches_both_channels(engine):
    desk = MixingDesk(engine)
    desk.connect(SourceStage(engine, Program(_tone(1.0))))

    stereo = desk.process().current.data

    assert np.allclose(stereo[:, 0], ROOT_HALF)
    assert np.allclose(stereo[:, 1], ROOT_HALF)


def test_strips_are_placed_independently_and_summed(engine):
    desk = MixingDesk(engine)
    left_source = SourceStage(engine, Program(_tone(1.0)))
    right_source = SourceStage(engine, Program(_tone(1.0)))
    desk.connect(left_source)
    desk.connect(right_source)
    desk._connection(left_source)._registers["pan"] = -1.0
    desk._connection(right_source)._registers["pan"] = 1.0

    stereo = desk.process().current.data

    assert np.all(stereo[:, 0] == 1.0)
    assert np.all(stereo[:, 1] == 1.0)


def test_a_strip_level_applies_before_placement(engine):
    desk = MixingDesk(engine)
    source = SourceStage(engine, Program(_tone(1.0)))
    desk.connect(source)
    desk._connection(source)._registers["level"] = 0.5

    stereo = desk.process().current.data

    assert np.allclose(stereo[:, 0], 0.5 * ROOT_HALF)


def test_the_desk_never_manufactures_stereo_from_one_channel(engine):
    """Two channels that differ, from a mono source, because Pan put it there."""
    desk = MixingDesk(engine)
    source = SourceStage(engine, Program(_tone(1.0)))
    desk.connect(source)
    desk._connection(source)._registers["pan"] = -0.5

    stereo = desk.process().current.data

    assert not np.allclose(stereo[:, 0], stereo[:, 1])


def test_a_sleeping_strip_is_left_out(engine):
    desk = MixingDesk(engine)
    quiet = SourceStage(engine)
    desk.connect(quiet)
    desk.connect(SourceStage(engine, Program(_tone(1.0))))

    desk.process()
    engine.advance()
    stereo = desk.process().current.data

    assert quiet.quiescent is True
    assert np.allclose(stereo[:, 0], ROOT_HALF)


def test_a_desk_cannot_take_another_desk_as_an_input(engine):
    """Stereo stops here; a stereo effect would need its own graph contract."""
    with pytest.raises(ValueError):
        MixingDesk(engine).connect(MixingDesk(engine))


def test_each_strip_owns_its_stereo_block(engine):
    desk = MixingDesk(engine)
    first, second = SourceStage(engine), SourceStage(engine)
    desk.connect(first)
    desk.connect(second)

    assert (
        desk._connection(first).stereo is not desk._connection(second).stereo
    )


def test_a_desk_claims_no_rows_after_the_first_block(engine):
    desk = MixingDesk(engine)
    desk.connect(SourceStage(engine, Program(_tone(1.0))))
    desk.process()
    settled = engine.pool.count

    for _ in range(4):
        engine.advance()
        desk.process()

    assert engine.pool.count == settled
