"""`Mix` and `Gain`: the two Operators the graph builds for itself.

Between them they carry the two patterns the rest of the vocabulary follows.

**Mix is destructive, and that is the contract** -- not an implementation
detail a later version might change quietly. Summing into lane zero and then
swapping that row into the Stage output is what makes mixing allocation-free,
and the cost is that a lane does not hold what it held. Tests here assert the
swap happened by ROW ID, because asserting on samples alone cannot tell a
swap from a copy.

**Gain is the reference segmented write.** A write lands at a sample, not at
a block boundary, so the tests check the exact sample either side of the cut.
Asserting only that "the block got quieter" would pass just as well if the
whole block took the new value, which is the bug worth catching.
"""

import numpy as np
import pytest

from blitspersecond.audio.synth.block import BlockPool
from blitspersecond.audio.synth.bus import Bus, StereoBus
from blitspersecond.audio.constants import BLOCK_SIZE
from blitspersecond.audio.synth.operators import Gain, Mix
from blitspersecond.audio.synth.registers import RegisterSpec, RegisterWrite


@pytest.fixture
def pool() -> BlockPool:
    return BlockPool()


def _lane(pool, value):
    bus = Bus(pool)
    bus.current.data.fill(value)
    return bus


# -- Mix ---------------------------------------------------------------


def test_no_lanes_is_silence_not_the_previous_block(pool):
    output = Bus(pool)
    output.current.data.fill(9.0)
    output.current.quiescent = False

    assert Mix(output).process() is output
    assert np.all(output.current.data == 0.0)
    assert output.current.quiescent is True


def test_lanes_are_summed(pool):
    output = Bus(pool)
    result = Mix(output).process(_lane(pool, 1.0), _lane(pool, 2.0), _lane(pool, 4.0))

    assert result is output
    assert np.all(output.current.data == 7.0)


def test_the_sum_is_swapped_into_the_output_not_copied(pool):
    """Rows are exchanged, so mixing moves no samples."""
    output = Bus(pool)
    output_row = output.current.id
    first = _lane(pool, 1.0)
    first_row = first.current.id

    Mix(output).process(first, _lane(pool, 2.0))

    assert output.current.id == first_row
    assert first.current.id == output_row


def test_lane_zero_receives_the_output_s_old_row_to_reuse(pool):
    """The recycled row is what the lane overwrites next block."""
    output = Bus(pool)
    output.current.data.fill(5.0)
    first = _lane(pool, 1.0)

    Mix(output).process(first, _lane(pool, 2.0))

    assert np.all(first.current.data == 5.0)


@pytest.mark.parametrize(
    "flags, expected",
    [((True, True), True), ((True, False), False), ((False, True), False)],
)
def test_the_mix_settles_only_when_every_lane_has(pool, flags, expected):
    output = Bus(pool)
    lanes = []
    for flag in flags:
        lane = _lane(pool, 0.0)
        lane.current.quiescent = flag
        lanes.append(lane)

    Mix(output).process(*lanes)

    assert output.current.quiescent is expected


def test_mix_sums_stereo_lanes_too(pool):
    """One Mix, not a mono one and a stereo one."""
    output = StereoBus()
    left, right = StereoBus(), StereoBus()
    left.current.data[:, 0] = 1.0
    right.current.data[:, 1] = 2.0

    result = Mix(output).process(left, right)  # type: ignore[arg-type]

    assert result is output
    assert np.all(output.current.data[:, 0] == 1.0)
    assert np.all(output.current.data[:, 1] == 2.0)


def test_a_stereo_mix_swaps_arrays_rather_than_copying():
    output, first = StereoBus(), StereoBus()
    first.current.data.fill(3.0)
    original = first.current.data

    Mix(output).process(first, StereoBus())  # type: ignore[arg-type]

    assert output.current.data is original


# -- Gain --------------------------------------------------------------


def _controlled(gain, writes=(), clock=0, level=None):
    registers = {"level": gain.registers["level"].default if level is None else level}
    gain.control(clock, registers, tuple(writes))
    return gain


def test_gain_declares_one_signal_register():
    spec = Gain(0.5).registers["level"]
    assert isinstance(spec, RegisterSpec)
    assert spec.default == 0.5
    assert spec.unit == "linear"
    assert spec.accepts_signal is True


def test_unity_with_no_writes_leaves_the_block_alone(pool):
    """The common case: every connection carries a Gain nobody writes to."""
    bus = _lane(pool, 2.0)
    row = bus.current.id

    assert _controlled(Gain()).process(bus) is bus
    assert np.all(bus.current.data == 2.0)
    assert bus.current.id == row


def test_a_scalar_level_scales_the_whole_block(pool):
    bus = _lane(pool, 2.0)
    _controlled(Gain(), level=0.25).process(bus)
    assert np.all(bus.current.data == 0.5)


def test_a_negative_level_inverts_phase(pool):
    """Not a mistake -- it is why `level` has no minimum."""
    bus = _lane(pool, 1.0)
    _controlled(Gain(), level=-1.0).process(bus)
    assert np.all(bus.current.data == -1.0)


def test_a_write_takes_effect_at_its_exact_sample(pool):
    bus = _lane(pool, 1.0)
    cut = 137
    write = RegisterWrite(cut, None, (("level", 0.5),))
    _controlled(Gain(), writes=[write], level=1.0).process(bus)

    assert np.all(bus.current.data[:cut] == 1.0)
    assert np.all(bus.current.data[cut:] == 0.5)
    # The samples either side of the cut, named, so an off-by-one is not
    # hidden by the slices above agreeing with the implementation.
    assert bus.current.data[cut - 1] == 1.0
    assert bus.current.data[cut] == 0.5


def test_several_writes_cut_the_block_into_segments(pool):
    bus = _lane(pool, 1.0)
    writes = [
        RegisterWrite(100, None, (("level", 2.0),)),
        RegisterWrite(250, None, (("level", 4.0),)),
    ]
    _controlled(Gain(), writes=writes, level=1.0).process(bus)

    assert np.all(bus.current.data[:100] == 1.0)
    assert np.all(bus.current.data[100:250] == 2.0)
    assert np.all(bus.current.data[250:] == 4.0)


def test_a_write_at_sample_zero_covers_the_whole_block(pool):
    bus = _lane(pool, 1.0)
    write = RegisterWrite(0, None, (("level", 3.0),))
    _controlled(Gain(), writes=[write], level=1.0).process(bus)
    assert np.all(bus.current.data == 3.0)


def test_writes_are_offset_from_the_engine_clock_not_the_block(pool):
    """Timestamps are absolute samples; the cut is `timestamp - clock`."""
    bus = _lane(pool, 1.0)
    clock = 48_000
    write = RegisterWrite(clock + 200, None, (("level", 0.5),))
    _controlled(Gain(), writes=[write], clock=clock, level=1.0).process(bus)

    assert np.all(bus.current.data[:200] == 1.0)
    assert np.all(bus.current.data[200:] == 0.5)


def test_a_signal_level_multiplies_sample_by_sample(pool):
    """Another Stage's audio driving the register -- a tremolo is this."""
    bus = _lane(pool, 1.0)
    ramp = np.linspace(0.0, 1.0, BLOCK_SIZE, dtype=np.float32)
    _controlled(Gain(), level=ramp).process(bus)
    assert np.allclose(bus.current.data, ramp)


def test_a_signal_level_defeats_the_unity_fast_path(pool):
    """An array is never `== 1.0`, so the shortcut must not swallow it."""
    bus = _lane(pool, 1.0)
    ones = np.ones(BLOCK_SIZE, dtype=np.float32)
    ones[10] = 0.0
    _controlled(Gain(), level=ones).process(bus)
    assert bus.current.data[10] == 0.0


def test_gain_passes_quiescence_through(pool):
    gain = Gain()
    assert gain._output_quiescent(Bus(pool), True) is True
    assert gain._output_quiescent(Bus(pool), False) is False


def test_a_silencing_level_does_not_claim_the_lane_has_settled(pool):
    """Level can come back next block, and nothing upstream was woken."""
    bus = _lane(pool, 1.0)
    gain = _controlled(Gain(), level=0.0)
    gain.process(bus)
    assert np.all(bus.current.data == 0.0)
    assert gain._output_quiescent(bus, False) is False
