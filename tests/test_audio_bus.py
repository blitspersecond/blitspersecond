"""`Bus`, `StereoBus`, `StereoBlock`: the wire between stages.

Two things here are easy to test in a way that proves nothing, so both are
tested by their failure mode instead.

**`ingest` copies.** Asserting the samples match afterwards passes just as
well if the bus kept a reference to the source array -- the values are equal
either way. So the source is mutated after the ingest and the consumer is
required not to have noticed, which is the case that matters: one settled
stage feeding two lanes, each of which then writes in place.

**Release is not tested by calling release twice.** That catches a flag, not
a bug. What matters is that a released bus stops answering at all, so its
pool row cannot be read after it has been handed to somebody else.
"""

import numpy as np
import pytest

from blitspersecond.audio.synth.block import BlockPool, StereoBlock
from blitspersecond.audio.synth.bus import Bus, StereoBus
from blitspersecond.audio.constants import BLOCK_SIZE


@pytest.fixture
def pool() -> BlockPool:
    return BlockPool()


# -- a mono bus --------------------------------------------------------


def test_a_new_bus_owns_a_silent_block(pool):
    bus = Bus(pool)
    assert bus.current.data.shape == (BLOCK_SIZE,)
    assert np.all(bus.current.data == 0.0)
    assert bus.current.quiescent is True


def test_buses_from_one_pool_own_different_rows(pool):
    first, second = Bus(pool), Bus(pool)
    first.current.data.fill(1.0)
    second.current.data.fill(2.0)
    assert np.all(first.current.data == 1.0)


def test_ingest_takes_a_copy_not_a_reference(pool):
    source, consumer = Bus(pool), Bus(pool)
    source.current.data.fill(3.0)

    consumer.ingest(source)
    source.current.data.fill(9.0)

    assert np.all(consumer.current.data == 3.0)


def test_two_lanes_ingesting_one_source_do_not_share_it(pool):
    """The reason ingest copies: one settled stage feeding two lanes."""
    source = Bus(pool)
    source.current.data.fill(1.0)
    left, right = Bus(pool), Bus(pool)

    left.ingest(source)
    right.ingest(source)
    left.current.data *= 10.0

    assert np.all(right.current.data == 1.0)
    assert np.all(source.current.data == 1.0)


def test_ingest_carries_quiescence(pool):
    source, consumer = Bus(pool), Bus(pool)
    source.current.quiescent = False

    consumer.ingest(source)

    assert consumer.current.quiescent is False


def test_clear_silences_and_settles(pool):
    bus = Bus(pool)
    bus.current.data.fill(5.0)
    bus.current.quiescent = False

    bus.clear()

    assert np.all(bus.current.data == 0.0)
    assert bus.current.quiescent is True


def test_clear_keeps_the_same_row(pool):
    """Clearing is a write, not a reallocation; nothing downstream rebinds."""
    bus = Bus(pool)
    row_id = bus.current.id
    bus.clear()
    assert bus.current.id == row_id


# -- release -----------------------------------------------------------


def test_release_returns_the_row_to_the_pool(pool):
    bus = Bus(pool)
    row_id = bus.current.id

    bus.release()

    assert Bus(pool).current.id == row_id


def test_a_released_bus_refuses_every_operation(pool):
    bus = Bus(pool)
    other = Bus(pool)
    bus.release()

    for attempt in (bus.clear, bus.release, lambda: bus.ingest(other)):
        with pytest.raises(RuntimeError):
            attempt()


# -- stereo ------------------------------------------------------------


def test_a_stereo_bus_is_two_silent_channels():
    bus = StereoBus()
    assert bus.current.data.shape == (BLOCK_SIZE, 2)
    assert bus.current.data.dtype == np.float32
    assert bus.current.quiescent is True


def test_stereo_channels_are_independent():
    """Nothing manufactures stereo from mono; the two carry different audio."""
    bus = StereoBus()
    bus.current.data[:, 0] = 1.0
    bus.current.data[:, 1] = -1.0
    assert np.all(bus.current.data[:, 0] == 1.0)
    assert np.all(bus.current.data[:, 1] == -1.0)


def test_stereo_clear_silences_and_settles():
    bus = StereoBus()
    bus.current.data.fill(4.0)
    bus.current.quiescent = False

    bus.clear()

    assert np.all(bus.current.data == 0.0)
    assert bus.current.quiescent is True


def test_stereo_swap_exchanges_arrays_and_quiescence():
    first, second = StereoBlock(), StereoBlock()
    first.data.fill(1.0)
    second.data.fill(2.0)
    first.quiescent = False

    first.swap(second)

    assert np.all(first.data == 2.0)
    assert np.all(second.data == 1.0)
    assert (first.quiescent, second.quiescent) == (True, False)


def test_a_stereo_block_will_not_swap_with_a_mono_one(pool):
    with pytest.raises(TypeError):
        StereoBlock().swap(Bus(pool).current)  # type: ignore[arg-type]
