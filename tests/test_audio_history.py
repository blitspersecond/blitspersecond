"""`Bus.delay`: reaching backwards on a lane that keeps no recording.

**Everything here is checked against an oracle, not against the ring.** The
ring is a permutation of pool rows with a fractional blend across two of them,
and a test that re-derives which slot holds what would be the implementation
written twice. So `_Tape` below keeps the entire signal as one flat array and
answers "what was playing `ticks` samples ago" by slicing it -- slow, obvious,
and structurally nothing like the thing it is checking.

The delays under test are deliberately **not** multiples of the block size.
400, 800 and friends exercise only the path where a delayed block is a stored
block handed back whole; the interesting path is the one where the answer
straddles two stored blocks, and a suite of round numbers would never enter
it.

Signals are random rather than ramps or tones. A ramp makes an off-by-one
look like a small value error and a tone makes it look like a phase shift; on
noise, one sample out of place is unmistakable.
"""

import numpy as np
import pytest

from blitspersecond.audio.synth.block import BlockPool
from blitspersecond.audio.synth.bus import Bus, _History
from blitspersecond.audio.constants import BLOCK_SIZE


class _Tape:
    """The whole signal, flat. What a delay line would be if memory were free."""

    def __init__(self):
        self.samples = np.zeros(0, dtype=np.float32)

    def write(self, block):
        self.samples = np.concatenate([self.samples, block])

    def read(self, ticks: int) -> np.ndarray:
        """The block that was playing `ticks` samples before the latest one."""
        stop = len(self.samples) - ticks
        start = stop - BLOCK_SIZE
        out = np.zeros(BLOCK_SIZE, dtype=np.float32)
        if stop <= 0:
            return out                      # entirely before the recording
        head = max(start, 0)
        out[head - start:] = self.samples[head:stop]
        return out


def _run(ticks, blocks=12, seed=0):
    """Push `blocks` random blocks through a delayed Bus and the oracle."""
    rng = np.random.default_rng(seed)
    bus = Bus(BlockPool())
    owner = object()
    tape = _Tape()
    actual, expected = [], []

    for _ in range(blocks):
        signal = rng.standard_normal(BLOCK_SIZE).astype(np.float32)
        bus.current.data = signal
        tape.write(signal)
        bus.delay(owner, ticks)
        actual.append(bus.current.data.copy())
        expected.append(tape.read(ticks))

    return actual, expected


# -- against the oracle ------------------------------------------------


@pytest.mark.parametrize(
    "ticks",
    [0, 1, 7, 399, 400, 401, 600, 799, 800, 801, 1000, 1200, 1999, 4321],
)
def test_delay_matches_a_flat_recording(ticks):
    actual, expected = _run(ticks)
    for index, (got, want) in enumerate(zip(actual, expected)):
        assert np.array_equal(got, want), f"block {index} differs at {ticks=}"


def test_zero_delay_is_the_signal_itself():
    actual, _ = _run(0)
    rng = np.random.default_rng(0)
    for got in actual:
        assert np.array_equal(got, rng.standard_normal(BLOCK_SIZE).astype(np.float32))


def test_before_the_recording_starts_the_lane_is_silent():
    """A delay longer than the signal is silence, not a wrapped-around ring."""
    actual, _ = _run(1200, blocks=2)
    for got in actual:
        assert np.all(got == 0.0)


# -- a delay that moves, because it is a register ----------------------


def test_shrinking_a_delay_still_tracks_the_oracle():
    """A delay time is a register, so depth moves while the graph runs.

    Shortening is the direction that stays exact: the ring keeps its newest
    blocks, and a shorter delay needs nothing older than those.
    """
    rng = np.random.default_rng(3)
    bus = Bus(BlockPool())
    owner = object()
    tape = _Tape()

    # Warm at full depth, then walk it down through the fractional boundary.
    for ticks in (1600,) * 6 + (1200, 900, 401, 250, 3, 0):
        signal = rng.standard_normal(BLOCK_SIZE).astype(np.float32)
        bus.current.data = signal
        tape.write(signal)
        bus.delay(owner, ticks)
        assert np.array_equal(bus.current.data, tape.read(ticks)), f"{ticks=}"


def test_growing_a_delay_fills_from_silence_not_from_the_past():
    """A delay line knows only what it was storing at the time.

    This is the one place the oracle and the ring legitimately disagree, and
    it is a property rather than a defect. While the delay was short the ring
    was short, so the blocks that scroll into range as it lengthens were
    never kept -- and they come back as silence rather than as the audio that
    really did play then. Audible as a gap when a game automates a delay time
    upwards.

    Written as a test so that a future ring which DOES retain the deeper past
    fails here and has to say so, rather than changing the sound quietly.
    """
    bus = Bus(BlockPool())
    owner = object()

    # A block of real signal, while the delay is too short to store it.
    bus.current.data.fill(1.0)
    bus.delay(owner, 0)

    # Now reach back for it. The ring never had it.
    bus.current.data.fill(2.0)
    bus.delay(owner, BLOCK_SIZE)

    assert np.all(bus.current.data == 0.0)


def test_shrinking_to_zero_empties_the_ring():
    """`ordered[-0:]` is the whole list; a ring shrinking to nothing must not
    keep every row it had."""
    pool = BlockPool()
    bus = Bus(pool)
    owner = object()
    bus.delay(owner, 1600)
    bus.delay(owner, 0)

    history = bus._histories[owner]
    assert history.ids == []
    assert history.scratch is None


# -- rows are returned, not leaked -------------------------------------


def test_a_shrinking_ring_returns_its_rows(pool_size=None):
    pool = BlockPool()
    bus = Bus(pool)
    owner = object()
    for _ in range(8):
        bus.delay(owner, 2000)
    deep = pool.count

    for _ in range(8):
        bus.delay(owner, 400)
    # Nothing new was claimed to shrink, and the rows dropped are reusable.
    assert pool.count == deep
    assert len(pool._free) > 0


def test_releasing_a_bus_releases_its_histories():
    pool = BlockPool()
    bus = Bus(pool)
    owner = object()
    for _ in range(4):
        bus.delay(owner, 1000)

    bus.release()

    assert not np.any(pool.claimed[: pool.count])


def test_a_long_line_grows_the_slab_in_one_go():
    """Depth 5 claims growth to 8, not five separate reallocations."""
    pool = BlockPool()
    bus = Bus(pool)
    bus.delay(object(), 5 * BLOCK_SIZE)
    assert len(pool.slab) >= 8


# -- one ring per operator ---------------------------------------------


def test_two_operators_on_one_lane_keep_separate_rings():
    """The second sees what the first did, so they cannot share a ring."""
    rng = np.random.default_rng(11)
    bus = Bus(BlockPool())
    first, second = object(), object()

    for _ in range(6):
        bus.current.data = rng.standard_normal(BLOCK_SIZE).astype(np.float32)
        bus.delay(first, 400)
        bus.delay(second, 800)

    assert len(bus._histories) == 2
    assert bus._histories[first] is not bus._histories[second]


def test_history_is_built_once_and_reused():
    bus = Bus(BlockPool())
    owner = object()
    bus.delay(owner, 400)
    history = bus._histories[owner]
    bus.delay(owner, 400)
    assert bus._histories[owner] is history
    assert isinstance(history, _History)


# -- quiescence --------------------------------------------------------


def test_a_delayed_lane_stays_awake_while_the_ring_holds_sound():
    """Silence now is not settled: what is in the ring has yet to come out."""
    bus = Bus(BlockPool())
    owner = object()

    bus.current.data.fill(1.0)
    bus.current.quiescent = False
    bus.delay(owner, 1200)

    bus.current.data.fill(0.0)
    bus.current.quiescent = True
    bus.delay(owner, 1200)

    assert bus.current.quiescent is False


def test_a_ring_of_silence_settles():
    bus = Bus(BlockPool())
    owner = object()
    for _ in range(8):
        bus.current.data.fill(0.0)
        bus.current.quiescent = True
        bus.delay(owner, 1200)
    assert bus.current.quiescent is True


# -- what delay refuses ------------------------------------------------


def test_delay_refuses_non_integer_and_negative_ticks():
    bus = Bus(BlockPool())
    for bad in (1.5, "400", True, None):
        with pytest.raises(TypeError):
            bus.delay(object(), bad)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        bus.delay(object(), -1)


def test_a_released_bus_will_not_delay():
    bus = Bus(BlockPool())
    bus.release()
    with pytest.raises(RuntimeError):
        bus.delay(object(), 400)
