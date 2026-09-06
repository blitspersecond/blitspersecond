"""Per-tile solidity: the packed patterns a stamp shifts into place.

Orientation is the placement's, never the source's, so a buffer cannot
answer this on its own -- it takes a rect and a turn together, which is why
the cache is the atlas's. These tests are about that transform being right
and that cache being honest; nothing here stamps anything.
"""

import numpy as np
import pytest

from blitspersecond.graphics import (
    Atlas,
    Orientation,
    PixelBuffer,
    as_flip_rotation,
)
from blitspersecond.graphics.collision import unpack

ALL_EIGHT = [Orientation(flags) for flags in range(8)]


def _noisy(width: int, height: int, seed: int = 7) -> PixelBuffer:
    """A buffer with no symmetry at all.

    Deliberate: every orientation bug in this engine's history has been
    invisible on a square, symmetric source and wrong on everything else.
    """
    buffer = PixelBuffer((width, height), "P")
    rng = np.random.default_rng(seed)
    with buffer.edit() as pixels:
        pixels[:] = (rng.random((height, width)) > 0.5).astype(np.uint8) * 5
    return buffer


def _bits(atlas: Atlas, key, orientation, width: int) -> np.ndarray:
    return unpack(atlas.solidity(key, orientation).astype(np.uint32), width)


@pytest.mark.parametrize("orientation", ALL_EIGHT)
def test_every_orientation_matches_the_other_spelling(orientation):
    """The engine says these eight turns two ways. They must agree.

    `solidity` composes transpose-then-mirrors, matching `TileBatch`'s image
    transform M = V . H . D and `local_rect`'s documented order. The sprite
    pillar says the same eight as a flip and a quarter-turn count. Checking
    one against the other is stronger than checking either against a
    hand-written expectation, because a hand-written one would be a third
    reading of the same law rather than an independent statement of it.
    """
    buffer = _noisy(8, 8)
    atlas = Atlas(buffer, 8, 8)
    source = buffer.mask.copy()

    flip, rotation = as_flip_rotation(orientation)
    expected = source[:, ::-1] if flip else source
    if rotation:
        expected = np.rot90(expected, -rotation)

    assert np.array_equal(_bits(atlas, 0, orientation, 8), expected)


def test_the_source_itself_is_not_symmetric():
    # Guards the guard: if the fixture ever became symmetric, every
    # orientation test above would pass without testing anything.
    source = _noisy(8, 8).mask
    assert not np.array_equal(source, source[:, ::-1])
    assert not np.array_equal(source, source[::-1, :])
    assert not np.array_equal(source, source.T)


@pytest.mark.parametrize("orientation", ALL_EIGHT)
def test_a_rectangular_tile_packs_what_it_will_occupy(orientation):
    # A transposed rectangular tile swaps its displayed extents, so the
    # pattern has to be the turned shape rather than the source shape --
    # otherwise the packed mask and the drawn quad describe different boxes.
    buffer = _noisy(16, 8)  # one 16x8 tile
    atlas = Atlas(buffer, 16, 8)
    turned = bool(int(orientation) & int(Orientation.TRANSPOSE))
    width, height = (8, 16) if turned else (16, 8)
    assert _bits(atlas, 0, orientation, width).shape == (height, width)


def test_a_named_atlas_keys_solidity_the_same_way_as_rects():
    from blitspersecond.common import Rect

    buffer = _noisy(16, 8)
    atlas = Atlas(buffer, {"left": Rect(0, 0, 8, 8), "right": Rect(8, 0, 8, 8)})
    assert np.array_equal(
        _bits(atlas, "right", Orientation.NONE, 8),
        buffer.mask[0:8, 8:16],
    )


# -- the cache ------------------------------------------------------------


def test_a_pattern_is_computed_once():
    atlas = Atlas(_noisy(8, 8), 8, 8)
    assert atlas.solidity(0) is atlas.solidity(0)
    assert atlas.solidity(0, Orientation.FLIP_H) is atlas.solidity(
        0, Orientation.FLIP_H
    )


def test_uprights_are_primed_and_turns_stay_lazy():
    # Uprights in one pass on the first ask -- the cluster case, where a
    # scene brings many poses into view in one frame, is what that avoids.
    # The other seven are still per tile: a sheet of 1500 tiles has no
    # business packing 12,000 patterns for the handful a level turns.
    atlas = Atlas(_noisy(16, 16), 8, 8)
    atlas.solidity(2, Orientation.FLIP_V)
    assert sorted(atlas._solid) == [0, 1, 2, 3]
    assert all(atlas._solid[row][0] is not None for row in atlas._solid)
    assert sum(entry is not None for entry in atlas._solid[2]) == 2
    assert sum(entry is not None for entry in atlas._solid[0]) == 1


def test_priming_happens_once_not_per_ask():
    atlas = Atlas(_noisy(16, 16), 8, 8)
    first = atlas.solidity(0)
    atlas.solidity(1)
    atlas.solidity(2, Orientation.FLIP_H)
    assert atlas.solidity(0) is first


def test_nothing_is_packed_until_something_asks():
    # An atlas nobody collides against must not pay, and must not force the
    # bool mask -- which is the image's bytes over again.
    atlas = Atlas(_noisy(16, 16), 8, 8)
    assert atlas._solid == {}
    assert atlas.buffer._mask is None


def test_the_cache_is_permanent_because_a_sheet_is_immutable():
    """No invalidation, on purpose.

    A sheet is immutable -- the tilemap states it as the pillar's contract
    and `SpriteSheet` calls itself an immutable bundle -- so a pattern is
    right for as long as the atlas lives.

    An earlier version watched `buffer.mask` for a new array and rebuilt on
    one. The atlas is not the place for that: it holds a REFERENCE to a
    buffer it neither made nor owns, interned by path and shared with every
    other atlas over the same art. And on a tilemap the watch made a broken
    case broken DIFFERENTLY -- patterns re-derived while the already-stamped
    screen mask kept its old bits, so new stamps were right and old ones
    stale. This test pins the absence of that.
    """
    buffer = _noisy(8, 8)
    atlas = Atlas(buffer, 8, 8)
    before = atlas.solidity(0)
    with buffer.edit() as pixels:
        pixels[0, 0] = 0
        pixels[1, 1] = 0
    assert atlas.solidity(0) is before


def test_two_maps_sharing_one_atlas_share_one_pattern_set():
    # The parallax case: hand both layers the same sheet and the transform
    # happens once.
    atlas = Atlas(_noisy(8, 8), 8, 8)
    first = atlas.solidity(0, Orientation.ROT_180)
    assert atlas.solidity(0, Orientation.ROT_180) is first


def test_solidity_needs_a_palettised_sheet():
    atlas = Atlas(PixelBuffer((8, 8), "RGBA"), 8, 8)
    with pytest.raises(ValueError, match="requires an indexed"):
        atlas.solidity(0)


def test_an_unknown_tile_is_refused():
    atlas = Atlas(_noisy(8, 8), 8, 8)
    with pytest.raises(IndexError):
        atlas.solidity(9)
