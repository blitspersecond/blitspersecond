"""`BlockPool` and `Block`: one slab, and stable handles into it.

The pool's whole job is that **a row ID means the same row forever**, and the
two ways that can break are growth and reuse. So the tests here are mostly
about what happens either side of a reallocation.

**Growth is forced, never assumed.** The slab starts empty and grows by one
row per claim by default, so claiming N rows reallocates N times -- every
test that holds a handle across a claim is exercising the case the ID
indirection exists for. `test_data_is_not_a_view_that_survives_growth` is the
one that would catch its removal: it fails only if something cached the array.

**Silence is not the same as quiescence**, and no test here asserts they
match. `quiescent` is a claim about the FUTURE -- nothing more is coming --
which only an operator that knows its own retained state can make. The pool
just carries the flag, so that is all these check.
"""

import numpy as np
import pytest

from blitspersecond.audio.synth.block import Block, BlockPool
from blitspersecond.audio.constants import BLOCK_SIZE


@pytest.fixture
def pool() -> BlockPool:
    return BlockPool()


# -- rows, and what a claim promises -----------------------------------


def test_a_claimed_row_is_block_size_float32_zeros(pool):
    block = Block(pool)
    assert block.data.shape == (BLOCK_SIZE,)
    assert block.data.dtype == np.float32
    assert np.all(block.data == 0.0)


def test_claims_hand_out_distinct_rows(pool):
    blocks = [Block(pool) for _ in range(8)]
    ids = [block.id for block in blocks]
    assert len(set(ids)) == len(ids)
    # Distinct IDs would also be satisfied by distinct views onto one row,
    # which is why the rows are written rather than just counted.
    for value, block in enumerate(blocks, start=1):
        block.data.fill(value)
    for value, block in enumerate(blocks, start=1):
        assert np.all(block.data == value)


def test_claim_growth_must_be_positive(pool):
    with pytest.raises(ValueError):
        pool.claim(0)


def test_growth_buys_rows_ahead_without_handing_them_out(pool):
    """`growth` sizes the reallocation; a claim is still exactly one row."""
    first = pool.claim(16)
    assert first == 0
    assert pool.count == 1
    assert len(pool.slab) == 16
    assert pool.claim() == 1


# -- the reason IDs exist ----------------------------------------------


def test_ids_and_contents_survive_growth(pool):
    first = Block(pool)
    first.data.fill(7.0)
    followers = [Block(pool) for _ in range(8)]

    assert first.id == 0
    assert np.all(first.data == 7.0)
    assert len(set(block.id for block in followers)) == 8


def test_data_is_not_a_view_that_survives_growth(pool):
    """The failure this class exists to prevent, written out.

    A held array is not merely stale after growth -- it is a live, writable
    array nothing reads. Writing through it looks like it worked and is
    silent, so the assertion is that the handle disagrees with the stale
    view, not that the view raises.
    """
    block = Block(pool)
    stale = block.data
    [Block(pool) for _ in range(4)]  # forces reallocation

    stale.fill(3.0)
    assert np.all(block.data == 0.0)
    assert stale is not block.data


# -- release and reuse -------------------------------------------------


def test_released_rows_are_reissued(pool):
    blocks = [Block(pool) for _ in range(8)]
    released_id = blocks[3].id
    blocks[3].release()

    assert Block(pool).id == released_id


def test_reuse_is_last_released_first(pool):
    """Stack order, so a graph's ID sequence reproduces between runs."""
    blocks = [Block(pool) for _ in range(4)]
    blocks[1].release()
    blocks[2].release()

    assert Block(pool).id == blocks[2].id
    assert Block(pool).id == blocks[1].id


def test_a_released_block_cannot_be_read_or_written(pool):
    block = Block(pool)
    block.release()

    with pytest.raises(RuntimeError):
        block.data
    with pytest.raises(RuntimeError):
        block.data = np.ones(BLOCK_SIZE, dtype=np.float32)


def test_a_block_cannot_be_released_twice(pool):
    block = Block(pool)
    block.release()
    with pytest.raises(RuntimeError):
        block.release()


def test_a_raw_row_cannot_be_released_twice(pool):
    """The guard for rows owned without a Block -- delay histories.

    Missing this is not a crash but a corruption: the row lands on the free
    stack twice, two later claims are handed the same memory, and two voices
    write into one buffer.
    """
    block_id = pool.claim()
    pool.release(block_id)
    with pytest.raises(ValueError):
        pool.release(block_id)


def test_releasing_a_row_that_was_never_claimed_raises(pool):
    pool.claim()
    with pytest.raises(IndexError):
        pool.release(4)
    with pytest.raises(IndexError):
        pool.release(-1)


# -- swap --------------------------------------------------------------


def test_swap_exchanges_rows_and_moves_no_samples(pool):
    first, second = Block(pool), Block(pool)
    first_id, second_id = first.id, second.id
    first.data.fill(2.0)
    second.data.fill(5.0)

    first.swap(second)

    assert (first.id, second.id) == (second_id, first_id)
    assert np.all(first.data == 5.0)
    assert np.all(second.data == 2.0)


def test_swap_carries_quiescence_with_the_row(pool):
    first, second = Block(pool), Block(pool)
    first.quiescent = False

    first.swap(second)

    assert first.quiescent is True
    assert second.quiescent is False


def test_swap_across_pools_raises(pool):
    with pytest.raises(ValueError):
        Block(pool).swap(Block(BlockPool()))


# -- data setter -------------------------------------------------------


def test_assigning_data_copies_into_the_owned_row(pool):
    block = Block(pool)
    row_id = block.id
    block.data = np.full(BLOCK_SIZE, 4.0, dtype=np.float32)

    assert block.id == row_id
    assert np.all(pool.slab[row_id] == 4.0)
