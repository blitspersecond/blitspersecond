"""The tilemap's bordered screen-space mask, on its own.

No `TileMap` here: the mask is stamped and scrolled directly, so what it does
is separable from when the camera decides to do it. Worlds are built by hand.
"""

import numpy as np
import pytest

from blitspersecond.common import SCREEN, Size
from blitspersecond.graphics import Atlas, Orientation, PixelBuffer
from blitspersecond.graphics.collision import CWORD, Collider, CollisionQuery
from blitspersecond.graphics.internal.layer import Layer
from blitspersecond.graphics.tilemap.collision import CollisionMask
from blitspersecond.graphics.tilemap.internal import bounded_plane

CELL = 8


def _sheet(seed: int = 3) -> Atlas:
    """One 8x8 tile with no symmetry, so an orientation slip cannot hide."""
    buffer = PixelBuffer((CELL, CELL), "P")
    rng = np.random.default_rng(seed)
    with buffer.edit() as pixels:
        pixels[:] = (rng.random((CELL, CELL)) > 0.4).astype(np.uint8) * 7
    return Atlas(buffer, CELL, CELL)


def _solid_sheet() -> Atlas:
    buffer = PixelBuffer((CELL, CELL), "P")
    with buffer.edit() as pixels:
        pixels[:] = 7
    return Atlas(buffer, CELL, CELL)


def _at(mask: CollisionMask, x: int, y: int) -> np.ndarray:
    """The 8x8 window of the exported interior at (x, y)."""
    return mask.unpacked[y : y + CELL, x : x + CELL]


# -- the store ------------------------------------------------------------


def test_the_store_is_bigger_than_the_screen_and_says_where_it_starts():
    # `mask` is the whole store, apron and all, because a query has to be
    # able to read the border. `offset` is what makes that readable.
    mask = CollisionMask(_sheet())
    assert mask.mask.shape[0] > SCREEN.height
    assert mask.mask.shape[1] > SCREEN.width // 32
    assert mask.mask.dtype == CWORD
    assert mask.offset == (mask._apron[1] * 32, mask._apron[0])
    # `unpacked` is what a query sees, so that IS the screen.
    assert mask.unpacked.shape == (SCREEN.height, SCREEN.width)


def test_the_apron_is_sized_from_the_bounded_plane():
    # NOT from the cell. The plane is the viewport rounded OUTWARD to whole
    # cells plus a border, so at 360 rows and an 8px cell it reaches 368 --
    # and a cell-sized apron would be short. Both this and the stamp guard
    # read `bounded_plane`, which is what makes them agree structurally
    # rather than by coincidence.
    mask = CollisionMask(_sheet())
    left, top, _right, bottom = bounded_plane(Size(CELL, CELL))
    assert mask._apron[0] == max(-top, bottom - SCREEN.height)


@pytest.mark.parametrize("drift", range(32))
def test_the_leading_border_survives_any_sub_word_drift(drift):
    """A border stamp must stay reachable whatever the camera's remainder.

    The store holds one cell of border; the sub-word remainder is carried in
    `offset` instead, and can reach 31 pixels. The extra apron word is what
    covers that, and getting it wrong loses border content SILENTLY -- the
    stamp guard would simply decline and the border would arrive empty.
    """
    mask = CollisionMask(_solid_sheet())
    mask.translate(drift, 0)              # drift with nothing else moving
    mask.stamp(0, -CELL, 40)              # the leftmost placement there is
    assert mask.unpacked[40:48, 0:CELL].sum() == 0   # waiting, not visible

    mask.translate(-CELL, 0)              # the camera walks it in
    assert _at(mask, 0, 40).all()


def test_a_fresh_mask_is_empty():
    assert CollisionMask(_sheet()).unpacked.sum() == 0


# -- stamping -------------------------------------------------------------


def test_a_stamp_lands_where_it_was_put():
    atlas = _sheet()
    mask = CollisionMask(atlas)
    mask.stamp(0, 24, 16)
    assert np.array_equal(_at(mask, 24, 16), atlas.buffer.mask)
    assert mask.unpacked.sum() == atlas.buffer.mask.sum()


@pytest.mark.parametrize("x", [0, 1, 15, 30, 31, 32, 33, 63, 64, 100])
def test_a_stamp_is_exact_at_any_x(x):
    # An unaligned tile straddles two words, and the carry into the second is
    # where a bit gets dropped or duplicated if the split is wrong.
    atlas = _sheet()
    mask = CollisionMask(atlas)
    mask.stamp(0, x, 40)
    assert np.array_equal(_at(mask, x, 40), atlas.buffer.mask)
    assert mask.unpacked.sum() == atlas.buffer.mask.sum()


@pytest.mark.parametrize("orientation", [Orientation(f) for f in range(8)])
def test_a_stamp_wears_its_orientation(orientation):
    atlas = _sheet()
    mask = CollisionMask(atlas)
    mask.stamp(0, 48, 24, orientation)
    from blitspersecond.graphics.collision import unpack

    expected = unpack(atlas.solidity(0, orientation).astype(CWORD), CELL)
    assert np.array_equal(_at(mask, 48, 24), expected)


def test_stamps_accumulate_rather_than_replace():
    mask = CollisionMask(_solid_sheet())
    mask.stamp(0, 0, 0)
    mask.stamp(0, 4, 0)  # overlapping
    assert mask.unpacked[0:CELL, 0:12].all()
    assert mask.unpacked.sum() == CELL * 12


def test_a_stamp_off_screen_waits_in_the_apron():
    atlas = _solid_sheet()
    mask = CollisionMask(atlas)
    mask.stamp(0, -CELL, 40)          # one cell left of the screen
    assert mask.unpacked.sum() == 0   # invisible to a query
    mask.translate(-CELL, 0)          # camera walks it in
    assert _at(mask, 0, 40).all()


def test_a_stamp_far_outside_the_apron_is_dropped():
    mask = CollisionMask(_solid_sheet())
    mask.stamp(0, 10_000, 10_000)
    assert mask.unpacked.sum() == 0


# -- translation ----------------------------------------------------------


@pytest.mark.parametrize(
    "dx,dy", [(1, 0), (-1, 0), (0, 1), (0, -1), (7, 0), (-7, 0), (32, 0),
              (-32, 0), (33, -5), (-33, 5), (0, 40), (0, -40)]
)
def test_translation_moves_content_the_other_way(dx, dy):
    # A camera moving right by dx puts the world dx to the left.
    atlas = _sheet()
    mask = CollisionMask(atlas)
    mask.stamp(0, 200, 150)
    mask.translate(dx, dy)
    assert np.array_equal(_at(mask, 200 - dx, 150 - dy), atlas.buffer.mask)
    assert mask.unpacked.sum() == atlas.buffer.mask.sum()


def test_translation_is_the_sum_of_its_steps():
    atlas = _sheet()
    one, many = CollisionMask(atlas), CollisionMask(atlas)
    one.stamp(0, 100, 100)
    many.stamp(0, 100, 100)
    one.translate(30, -18)
    for _ in range(6):
        many.translate(5, -3)
    assert np.array_equal(one.unpacked, many.unpacked)


@pytest.mark.parametrize("edge", ["left", "right", "top", "bottom"])
def test_border_content_arrives_from_every_edge(edge):
    atlas = _solid_sheet()
    mask = CollisionMask(atlas)
    width, height = SCREEN.width, SCREEN.height
    place, move, landing = {
        "left":   ((-CELL, 100), (-CELL, 0), (0, 100)),
        "right":  ((width, 100), (CELL, 0), (width - CELL, 100)),
        "top":    ((100, -CELL), (0, -CELL), (100, 0)),
        "bottom": ((100, height), (0, CELL), (100, height - CELL)),
    }[edge]
    mask.stamp(0, *place)
    assert mask.unpacked.sum() == 0
    mask.translate(*move)
    assert _at(mask, *landing).all()


def test_content_that_leaves_the_store_cannot_come_back():
    # Destructive is the contract: there is no placement list consulted here,
    # which is exactly why a placement is anonymous.
    mask = CollisionMask(_solid_sheet())
    mask.stamp(0, 100, 100)
    mask.translate(10_000, 0)
    mask.translate(-10_000, 0)
    assert mask.unpacked.sum() == 0


def test_a_zero_translation_changes_nothing():
    atlas = _sheet()
    mask = CollisionMask(atlas)
    mask.stamp(0, 60, 60)
    before = mask.unpacked.copy()
    mask.translate(0, 0)
    assert np.array_equal(mask.unpacked, before)


def test_the_exposed_band_is_blank():
    mask = CollisionMask(_solid_sheet())
    for x in range(0, SCREEN.width, CELL):
        mask.stamp(0, x, 0)
    assert mask.unpacked[0].all()
    mask.translate(0, -SCREEN.height)   # push it all off the bottom
    assert mask.unpacked.sum() == 0


def test_a_sub_word_step_moves_no_memory_at_all():
    # The whole point: content appearing to move IS the screen looking
    # elsewhere, so a step under a word costs an integer add.
    mask = CollisionMask(_solid_sheet())
    mask.stamp(0, 100, 100)
    held = mask.mask
    before = held.copy()
    mask.translate(31, 0)
    assert mask.mask is held              # same buffer
    assert np.array_equal(mask.mask, before)   # untouched
    assert mask.offset[0] == mask._apron[1] * 32 + 31
    assert _at(mask, 100 - 31, 100).all()      # but it has moved on screen


def test_a_whole_word_step_moves_memory_and_swaps():
    # The reason `Collider` resolves the mask on every question instead of
    # holding the array it was given.
    mask = CollisionMask(_solid_sheet())
    mask.stamp(0, 100, 100)
    held = mask.mask
    mask.translate(32, 0)
    assert mask.mask is not held          # swapped
    assert mask.offset[0] == mask._apron[1] * 32   # drift absorbed
    assert _at(mask, 100 - 32, 100).all()


def test_drift_accumulates_across_sub_word_steps():
    mask = CollisionMask(_solid_sheet())
    mask.stamp(0, 100, 100)
    for _ in range(4):
        mask.translate(10, 0)             # 40 total: one word plus 8
    assert mask.offset[0] == mask._apron[1] * 32 + 8
    assert _at(mask, 60, 100).all()


# -- clearing -------------------------------------------------------------


def test_clear_empties_the_apron_too():
    mask = CollisionMask(_solid_sheet())
    mask.stamp(0, -CELL, 40)   # apron only
    mask.stamp(0, 40, 40)      # interior
    mask.clear()
    assert mask.unpacked.sum() == 0
    mask.translate(-CELL, 0)
    assert mask.unpacked.sum() == 0   # nothing waiting outside either


# -- it answers the shared question ---------------------------------------


class _MaskLayer(Layer):
    """The thinnest thing that can put a CollisionMask into a query."""

    def __init__(self, mask: CollisionMask):
        super().__init__()
        self._mask = mask
        self.collidable = True

    def _collision_refusal(self):
        return None

    @property
    def collision_offset(self):
        return self._mask.offset

    def collision_plane(self, name=None):
        return self._mask.mask


def test_two_masks_answer_collides_with():
    atlas = _solid_sheet()
    a, b = CollisionMask(atlas), CollisionMask(atlas)
    a.stamp(0, 100, 100)
    b.stamp(0, 104, 104)
    first, second = _MaskLayer(a), _MaskLayer(b)
    assert first.collides_with(second).at() is True
    assert first.collides_with(second).at(104, 104, 4, 4) is True
    assert first.collides_with(second).at(100, 100, 4, 4) is False


def test_a_query_follows_the_buffer_swap():
    atlas = _solid_sheet()
    a, b = CollisionMask(atlas), CollisionMask(atlas)
    a.stamp(0, 100, 100)
    b.stamp(0, 100, 100)
    query = _MaskLayer(a).collides_with(_MaskLayer(b))
    assert query.at() is True
    a.translate(200, 0)          # walks a's content away from b's
    assert query.at() is False


# -- awkward cell sizes ---------------------------------------------------
#
# The bug these exist for: the apron was sized from the cell, while the plane
# a stamp may land in is the viewport rounded OUTWARD to whole cells plus a
# border. At 360 rows and a 16px cell that plane reaches 384 while the apron
# allowed 376, so trailing stamps were dropped -- silently, because the guard
# simply declined and the border arrived empty. It went unnoticed because
# every test until now stamped near the origin.


AWKWARD = [
    (8, 8),      # the baseline
    (16, 16),    # divides the width, does NOT divide the height
    (17, 17),    # divides neither, and is not a power of two
    (32, 32),    # exactly one word
    (33, 33),    # one word plus one: patterns straddle, always
    (17, 11),    # rectangular and awkward in both axes
    (5, 5),      # smaller than a word by a lot
    (48, 48),    # wider than a word, not a multiple
    (64, 64),    # two whole words
    (3, 7),      # absurd, but nothing forbids it
    (40, 24),    # rectangular, neither axis aligned
]


def _awkward_sheet(width: int, height: int) -> Atlas:
    buffer = PixelBuffer((width, height), "P")
    with buffer.edit() as pixels:
        pixels[:] = 7
    return Atlas(buffer, width, height)


@pytest.mark.parametrize(("cw", "ch"), AWKWARD, ids=lambda v: str(v))
@pytest.mark.parametrize("drift", [0, 1, 17, 31])
def test_every_placement_in_the_plane_is_retained(cw, ch, drift):
    """No stamp anywhere in the bounded plane may be dropped, at any drift.

    This is the whole apron contract, said exhaustively rather than at a few
    hand-picked coordinates. It walks every cell position the stamp guard
    would accept -- including the trailing edges, which is where the cell-
    sized apron failed -- with the store carrying each sub-word remainder it
    can hold.
    """
    atlas = _awkward_sheet(cw, ch)
    mask = CollisionMask(atlas)
    mask.translate(drift, 0)
    left, top, right, bottom = bounded_plane(Size(cw, ch))
    dropped = []
    for x in range(left, right, cw):
        for y in range(top, bottom, ch):
            before = int(mask._mask.sum())
            mask.stamp(0, x, y)
            if int(mask._mask.sum()) == before:
                dropped.append((x, y))
    assert not dropped, f"{cw}x{ch} drift={drift} dropped {len(dropped)}: {dropped[:4]}"


@pytest.mark.parametrize(("cw", "ch"), AWKWARD, ids=lambda v: str(v))
def test_an_awkward_tile_stamps_exactly_where_it_was_put(cw, ch):
    atlas = _awkward_sheet(cw, ch)
    mask = CollisionMask(atlas)
    mask.stamp(0, 37, 41)          # a deliberately unhelpful position
    assert mask.unpacked[41 : 41 + ch, 37 : 37 + cw].all()
    assert mask.unpacked.sum() == cw * ch


@pytest.mark.parametrize(("cw", "ch"), AWKWARD, ids=lambda v: str(v))
def test_an_awkward_border_stamp_arrives_with_the_camera(cw, ch):
    atlas = _awkward_sheet(cw, ch)
    mask = CollisionMask(atlas)
    mask.stamp(0, -cw, 100)
    assert mask.unpacked[100 : 100 + ch, 0:cw].sum() == 0
    mask.translate(-cw, 0)
    assert mask.unpacked[100 : 100 + ch, 0:cw].all()


@pytest.mark.parametrize(("cw", "ch"), AWKWARD, ids=lambda v: str(v))
def test_an_awkward_tile_survives_a_drifting_camera(cw, ch):
    # Sub-word steps move nothing and whole-word steps move memory; a tile
    # whose size divides neither must come out in the same place either way.
    atlas = _awkward_sheet(cw, ch)
    one, many = CollisionMask(atlas), CollisionMask(atlas)
    one.stamp(0, 200, 150)
    many.stamp(0, 200, 150)
    one.translate(35, 12)
    for _ in range(5):
        many.translate(7, 0)
    many.translate(0, 12)
    assert np.array_equal(one.unpacked, many.unpacked)
    assert one.unpacked[150 - 12 : 150 - 12 + ch, 200 - 35 : 200 - 35 + cw].all()


# -- 33x33: a tile wider than a DWORD -------------------------------------
#
# The sharpest size in the set. A 33px pattern packs to two words and needs
# THREE once shifted, so `stamp`'s carry slice is at full stretch at every
# alignment; and 33 divides neither 640, 360 nor 32, so no arithmetic
# anywhere gets to be lucky. Every case here is exact-pixel against the
# source, not merely "something was retained".

WIDE = 33


@pytest.fixture(scope="module")
def wide():
    """A 33x33 sheet with no symmetry, and its bounded plane."""
    buffer = PixelBuffer((WIDE, WIDE), "P")
    rng = np.random.default_rng(WIDE)
    with buffer.edit() as pixels:
        pixels[:] = (rng.random((WIDE, WIDE)) > 0.45).astype(np.uint8) * 7
    atlas = Atlas(buffer, WIDE, WIDE)
    return atlas, buffer.mask.copy(), bounded_plane(Size(WIDE, WIDE))


def test_a_wide_tile_needs_three_words_once_shifted(wide):
    atlas, _source, _plane = wide
    # The premise of everything below: two words at rest, three in flight.
    assert atlas.solidity(0).shape[1] == 2


@pytest.mark.parametrize("x", list(range(0, 66)))
def test_a_wide_tile_is_exact_at_every_alignment(x, wide):
    atlas, source, _plane = wide
    mask = CollisionMask(atlas)
    mask.stamp(0, x, 50)
    assert np.array_equal(mask.unpacked[50 : 50 + WIDE, x : x + WIDE], source)
    assert mask.unpacked.sum() == source.sum()   # nothing smeared elsewhere


@pytest.mark.parametrize("orientation", [Orientation(f) for f in range(8)])
@pytest.mark.parametrize("x", [0, 1, 15, 31, 32, 33, 47, 64, 65])
def test_a_wide_tile_is_exact_turned_and_unaligned(orientation, x, wide):
    from blitspersecond.graphics.collision import unpack

    atlas, _source, _plane = wide
    expected = unpack(atlas.solidity(0, orientation).astype(CWORD), WIDE)
    mask = CollisionMask(atlas)
    mask.stamp(0, x, 50, orientation)
    assert np.array_equal(
        mask.unpacked[50 : 50 + WIDE, x : x + WIDE], expected
    )
    assert mask.unpacked.sum() == expected.sum()


@pytest.mark.parametrize("drift", list(range(32)))
def test_drift_does_not_displace_a_later_wide_stamp(drift, wide):
    # Drift cancels: stamping and querying convert placement coordinates
    # through the SAME offset, so a stamp lands where it says whatever the
    # store has not moved for. Drift only moves what was already there.
    atlas, source, _plane = wide
    mask = CollisionMask(atlas)
    mask.translate(drift, 0)
    mask.stamp(0, 200, 50)
    assert np.array_equal(mask.unpacked[50 : 50 + WIDE, 200 : 200 + WIDE], source)


@pytest.mark.parametrize("drift", list(range(1, 32)))
def test_drift_does_displace_an_earlier_wide_stamp(drift, wide):
    atlas, source, _plane = wide
    mask = CollisionMask(atlas)
    mask.stamp(0, 200, 50)
    mask.translate(drift, 0)
    at = 200 - drift
    assert np.array_equal(mask.unpacked[50 : 50 + WIDE, at : at + WIDE], source)


@pytest.mark.parametrize("drift", list(range(32)))
def test_the_wide_trailing_corner_survives_any_drift(drift, wide):
    from blitspersecond.graphics.collision import unpack

    atlas, source, plane = wide
    _left, _top, right, bottom = plane
    mask = CollisionMask(atlas)
    mask.translate(drift, 0)
    mask.stamp(0, right - WIDE, bottom - WIDE)
    held = unpack(mask._mask, mask._mask.shape[1] * 32).sum()
    assert held == source.sum()


@pytest.mark.parametrize("drift", list(range(32)))
def test_the_wide_leading_corner_arrives_at_any_drift(drift, wide):
    atlas, source, plane = wide
    left, top, _right, _bottom = plane
    mask = CollisionMask(atlas)
    mask.translate(drift, 0)
    mask.stamp(0, left, top)
    assert mask.unpacked.sum() == 0          # waiting outside
    mask.translate(left, top)                 # the camera walks it in
    assert np.array_equal(mask.unpacked[0:WIDE, 0:WIDE], source)


def test_two_wide_stamps_overlap_across_a_word_boundary(wide):
    atlas, source, _plane = wide
    mask = CollisionMask(atlas)
    mask.stamp(0, 20, 50)
    mask.stamp(0, 45, 50)     # 25 apart: overlapping, and straddling word 2
    expected = np.zeros((WIDE, 58), dtype=bool)
    expected[:, 0:WIDE] |= source
    expected[:, 25 : 25 + WIDE] |= source
    assert np.array_equal(mask.unpacked[50 : 50 + WIDE, 20:78], expected)


def test_a_stamp_whose_carry_would_fall_outside_is_dropped_whole():
    """The guard's exact bound, which the apron otherwise keeps unreachable.

    A tile lands as two word-slice writes -- the pattern, then its carry one
    word along. If the guard admitted a stamp whose carry word is off the end
    of the store, NumPy would not raise: it would clamp the second slice to
    nothing and write a tile with its right edge silently missing. So the
    check has to include the carry word, and this pins the position where
    including it is the only thing that matters.

    No legal placement can reach here -- the apron is sized from the bounded
    plane -- so this is about the guard being right rather than about a case a
    game produces.
    """
    atlas = _solid_sheet()
    mask = CollisionMask(atlas)
    _rows, words = mask._apron
    nwords = atlas.solidity(0).shape[1]
    # The x whose pattern fits but whose CARRY word is one past the end.
    word = mask._mask.shape[1] - nwords
    x = (word - words) << 5
    mask.stamp(0, x, 40)
    assert mask._mask.sum() == 0, "wrote a truncated tile instead of dropping it"
