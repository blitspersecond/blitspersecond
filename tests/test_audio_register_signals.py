"""`_RegisterSignals`: a register driven by audio instead of by a value.

**The layering is the subject.** Reading a register falls through signal,
then stored value, then -- for a lane -- the Stage's signal and the Stage's
value. Every test here is really about which layer wins, so the fixtures give
each layer a distinguishable value rather than a realistic one: if two layers
held plausible numbers, a test could pass while reading the wrong one.

**Resolution is cached per block, and that is load-bearing**, not a speed
trick. One control Stage feeding four sockets must advance its phase once. So
`_Source` counts its pulls and the tests assert on the count, which is the
only thing that would actually catch a regression there.
"""

import numpy as np
import pytest

from blitspersecond.audio.synth.block import BlockPool
from blitspersecond.audio.synth.bus import Bus
from blitspersecond.audio.constants import BLOCK_SIZE
from blitspersecond.audio.synth.register_signals import _RegisterSignals
from blitspersecond.audio.synth.registers import RegisterSpec


class _Clock:
    """A BlockClock: the one engine service a resolve needs."""

    def __init__(self):
        self.block = 0


class _Source:
    """A SignalSource that counts how often it was pulled."""

    channels = 1

    def __init__(self, engine, value=1.0):
        self._engine = engine
        self._bus = Bus(BlockPool())
        self._bus.current.data.fill(value)
        self.pulls = 0

    def process(self):
        self.pulls += 1
        return self._bus


SIGNAL = {"level": RegisterSpec(0.0, accepts_signal=True)}
PLAIN = {"gate": RegisterSpec(False)}


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


def _bank(clock, specs=None, stored=None, parent=None):
    stored = {"level": 0.25} if stored is None else stored
    return _RegisterSignals(
        clock,
        SIGNAL if specs is None else specs,
        stored,
        parent=parent,
    )


# -- the stored value, and a signal over it ----------------------------


def test_a_register_with_no_signal_reads_its_stored_value(clock):
    bank = _bank(clock)
    assert bank.resolve()["level"] == 0.25


def test_a_connected_signal_overrides_the_stored_value(clock):
    bank = _bank(clock)
    bank.connect("level", _Source(clock, 0.9))

    resolved = bank.resolve()["level"]
    assert isinstance(resolved, np.ndarray)
    assert np.all(resolved == 0.9)


def test_the_stored_value_survives_underneath_and_returns(clock):
    """Writes keep landing on the value, so disconnecting restores it rather
    than freezing whatever the signal last said."""
    stored = {"level": 0.25}
    bank = _RegisterSignals(clock, SIGNAL, stored)
    source = _Source(clock, 0.9)
    bank.connect("level", source)
    bank.resolve()

    stored["level"] = 0.5  # a scheduled write, landing underneath
    bank.disconnect("level", source)

    assert bank.resolve()["level"] == 0.5


def test_names_reports_which_registers_are_driven(clock):
    bank = _bank(clock)
    assert list(bank.names) == []
    bank.connect("level", _Source(clock))
    assert list(bank.names) == ["level"]


# -- what may be connected ---------------------------------------------


def test_a_register_that_did_not_opt_in_refuses_a_signal(clock):
    """`accepts_signal` is a claim the Operator implements array values."""
    bank = _RegisterSignals(clock, PLAIN, {"gate": False})
    with pytest.raises(ValueError):
        bank.connect("gate", _Source(clock))


def test_an_unknown_register_cannot_be_driven(clock):
    with pytest.raises(ValueError):
        _bank(clock).connect("frequency", _Source(clock))


def test_a_stereo_stage_cannot_drive_a_register(clock):
    source = _Source(clock)
    source.channels = 2
    with pytest.raises(ValueError):
        _bank(clock).connect("level", source)


def test_a_stage_from_another_engine_cannot_drive_a_register(clock):
    with pytest.raises(ValueError):
        _bank(clock).connect("level", _Source(_Clock()))


def test_disconnecting_a_stage_that_is_not_connected_raises(clock):
    bank = _bank(clock)
    bank.connect("level", _Source(clock))
    with pytest.raises(ValueError):
        bank.disconnect("level", _Source(clock))


# -- resolving once per block ------------------------------------------


def test_one_source_feeding_several_sockets_is_pulled_once(clock):
    """The reason resolution is cached: a shared LFO advances once."""
    specs = {
        "level": RegisterSpec(0.0, accepts_signal=True),
        "depth": RegisterSpec(0.0, accepts_signal=True),
    }
    bank = _RegisterSignals(clock, specs, {"level": 0.0, "depth": 0.0})
    source = _Source(clock)
    bank.connect("level", source)
    bank.connect("depth", source)

    bank.resolve()
    bank.resolve()

    assert source.pulls == 2  # once per socket, once for the block


def test_resolving_twice_in_one_block_pulls_nothing_twice(clock):
    bank = _bank(clock)
    source = _Source(clock)
    bank.connect("level", source)

    bank.resolve()
    bank.resolve()
    bank.resolve()

    assert source.pulls == 1


def test_a_new_block_resolves_again(clock):
    bank = _bank(clock)
    source = _Source(clock)
    bank.connect("level", source)

    bank.resolve()
    clock.block += 1
    bank.resolve()

    assert source.pulls == 2


def test_connecting_invalidates_a_resolution_from_this_block(clock):
    """Otherwise a signal connected after a read would not be seen until the
    next block, which is a silently late modulation."""
    bank = _bank(clock)
    assert bank.resolve()["level"] == 0.25

    bank.connect("level", _Source(clock, 0.9))

    assert np.all(bank.resolve()["level"] == 0.9)


def test_the_resolved_array_is_the_source_s_own_block_not_a_copy(clock):
    bank = _bank(clock)
    source = _Source(clock, 0.9)
    bank.connect("level", source)

    resolved = bank.resolve()["level"]
    assert resolved.shape == (BLOCK_SIZE,)
    # Not `is`: `Block.data` builds a fresh view per access by design, so
    # identity would be false for two reads of the same row.
    assert np.shares_memory(resolved, source._bus.current.data)


def test_a_held_view_detaches_when_the_slab_grows(clock):
    """Pins a hazard that is currently harmless, so it stays visible.

    `resolve` holds a view for the rest of the block, which `Block` otherwise
    forbids. A mid-block claim -- a delay ring filling does this -- replaces
    the slab and detaches the view. The samples are still right, because
    growth copies them forward and a settled Stage is not rewritten within
    its own block. This test asserts BOTH halves: that it detaches, and that
    the audio is nonetheless correct. If the second assertion ever fails, the
    invariant this relies on has moved.
    """
    from blitspersecond.audio.synth.block import Block

    pool = BlockPool()
    source = _Source(clock, 0.9)
    source._bus = Bus(pool)
    source._bus.current.data.fill(0.9)
    bank = _bank(clock)
    bank.connect("level", source)

    held = bank.resolve()["level"]
    [Block(pool) for _ in range(6)]  # forces reallocation

    assert not np.shares_memory(held, source._bus.current.data)
    assert np.all(held == 0.9)


# -- a lane underneath a Stage -----------------------------------------


def test_a_lane_falls_back_to_the_stage_value(clock):
    stage = _bank(clock, stored={"level": 0.25})
    lane = _RegisterSignals(clock, SIGNAL, {}, parent=stage)

    assert lane.resolve()["level"] == 0.25


def test_a_lane_value_shadows_the_stage_value(clock):
    stage = _bank(clock, stored={"level": 0.25})
    lane = _RegisterSignals(clock, SIGNAL, {"level": 0.5}, parent=stage)

    assert lane.resolve()["level"] == 0.5


def test_a_lane_signal_beats_everything_below_it(clock):
    stage = _bank(clock, stored={"level": 0.25})
    stage.connect("level", _Source(clock, 0.9))
    lane = _RegisterSignals(clock, SIGNAL, {"level": 0.5}, parent=stage)
    lane.connect("level", _Source(clock, 0.1))

    assert np.all(lane.resolve()["level"] == 0.1)


def test_a_stage_signal_reaches_a_lane_that_has_neither(clock):
    stage = _bank(clock, stored={"level": 0.25})
    stage.connect("level", _Source(clock, 0.9))
    lane = _RegisterSignals(clock, SIGNAL, {}, parent=stage)

    assert np.all(lane.resolve()["level"] == 0.9)


def test_resolving_a_lane_resolves_the_stage_beneath_it(clock):
    """A lane read must not see last block's Stage signal."""
    stage = _bank(clock, stored={"level": 0.25})
    source = _Source(clock, 0.9)
    stage.connect("level", source)
    lane = _RegisterSignals(clock, SIGNAL, {}, parent=stage)

    lane.resolve()

    assert source.pulls == 1
