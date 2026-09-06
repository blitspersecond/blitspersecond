"""The shared collision vocabulary, against hand-built masks.

No tile and no sprite code anywhere in this file, on purpose: the whole
claim of the leaf is that the layout, the participant and the question are
cross-pillar, so anything a test needs from a pillar would be evidence
against it. What plays the part of a pillar here is `_Mask` -- a `Layer`
that keeps packed arrays a test wrote by hand.
"""

import numpy as np
import pytest

from blitspersecond.graphics.collision import (
    CWORD,
    CWORD_BITS,
    Collider,
    CollisionQuery,
    pack,
    unpack,
    words_for,
)
from blitspersecond.common import SCREEN
from blitspersecond.graphics import Atlas, PixelBuffer, TileMap
from blitspersecond.graphics.internal.layer import Layer

#: The test screen: 32 rows of two words, so a word boundary sits at x=32
#: in the middle of the addressable width and both sides of it are testable.
ROWS, WORDS = 32, 2
WIDTH = WORDS * CWORD_BITS


class _Mask(Layer):
    """A collidable layer whose planes a test writes directly.

    Stands in for a pillar: it supplies exactly the three things `Layer`
    asks a subclass for -- `_collision_refusal`, `planes`, `collision_plane`
    -- and nothing else, which is the protocol the leaf claims is
    sufficient. `collidable` itself is the base's, switch and all.
    """

    def __init__(self, *planes: str, tag=None):
        super().__init__(tag=tag)
        self._planes = {name: self._blank() for name in planes}
        self._planes[None] = self._blank()
        self.collidable = True

    @staticmethod
    def _blank():
        return np.zeros((ROWS, WORDS), dtype=CWORD)

    def _collision_refusal(self):
        return None  # a palettised pillar: it may be switched on

    @property
    def planes(self):
        return frozenset(name for name in self._planes if name is not None)

    def collision_plane(self, name=None):
        return self._planes[name]

    # -- test authoring ---------------------------------------------------

    def set(self, x, y, plane=None):
        """Light pixel (x, y) of one plane."""
        mask = self._planes[plane]
        mask[y, x >> 5] |= CWORD(1 << (x & 31))
        return self

    def box(self, x, y, w, h, plane=None):
        for row in range(y, y + h):
            for col in range(x, x + w):
                self.set(col, row, plane)
        return self


class _OffsetMask(_Mask):
    """A collidable layer whose planes sit behind an offset -- the shape a
    bordered, finely-scrolled tilemap presents."""

    def __init__(self, ox: int, oy: int, *planes: str):
        super().__init__(*planes)
        self._offset = (ox, oy)

    @property
    def collision_offset(self):
        return self._offset


# -- the packed layout ----------------------------------------------------


def test_words_for_matches_the_screen():
    # 640 is exactly 20 words with no partial tail -- the property the word
    # size was chosen for.
    assert words_for(640) == 20
    assert words_for(640) * CWORD_BITS == 640


@pytest.mark.parametrize("width", [1, 7, 32, 33, 64, 185])
def test_pack_unpack_round_trips(width):
    rng = np.random.default_rng(width)
    bits = rng.random((9, width)) > 0.5
    packed = pack(bits).astype(CWORD)
    assert np.array_equal(unpack(packed, width), bits)


def test_pack_puts_pixel_x_at_bit_x():
    bits = np.zeros((1, 40), dtype=bool)
    bits[0, 0] = True   # bit 0 of word 0
    bits[0, 31] = True  # bit 31 of word 0
    bits[0, 32] = True  # bit 0 of word 1
    packed = pack(bits)
    assert int(packed[0, 0]) == (1 << 0) | (1 << 31)
    assert int(packed[0, 1]) == 1


def test_pack_stages_wide_enough_to_carry():
    # A pattern shifted to an unaligned x must keep the bits that leave its
    # own word; uint64 staging is what makes them recoverable.
    bits = np.zeros((1, 32), dtype=bool)
    bits[0, 31] = True
    shifted = pack(bits) << np.uint64(1)
    assert int(shifted[0, 0]) == 1 << 32  # the carry survived


# -- the question ---------------------------------------------------------


def test_overlapping_pixel_collides():
    a, b = _Mask().set(3, 9), _Mask().set(3, 9)
    assert a.collides_with(b).at() is True


def test_adjacent_pixel_does_not():
    a, b = _Mask().set(3, 9), _Mask().set(4, 9)
    assert a.collides_with(b).at() is False


def test_empty_masks_do_not_collide():
    assert _Mask().collides_with(_Mask()).at() is False


def test_window_is_exact_to_the_pixel():
    a, b = _Mask().set(10, 4), _Mask().set(10, 4)
    query = a.collides_with(b)
    assert query.at(10, 4, 1, 1) is True
    assert query.at(9, 4, 1, 1) is False   # one column left
    assert query.at(11, 4, 1, 1) is False  # one column right
    assert query.at(10, 3, 1, 1) is False  # one row up
    assert query.at(10, 5, 1, 1) is False  # one row down


def test_window_is_half_open():
    a, b = _Mask().set(10, 4), _Mask().set(10, 4)
    query = a.collides_with(b)
    assert query.at(8, 4, 2, 1) is False  # covers 8..9, stops before 10
    assert query.at(8, 4, 3, 1) is True   # covers 8..10


@pytest.mark.parametrize("x", [31, 32, 33])
def test_window_spans_a_word_boundary(x):
    # The trims are applied to two different words here, which is the case
    # the single-word path cannot exercise.
    a, b = _Mask().set(x, 7), _Mask().set(x, 7)
    query = a.collides_with(b)
    assert query.at(x, 7, 1, 1) is True
    assert query.at(30, 7, 4, 1) is True
    assert query.at(0, 7, x, 1) is False       # ends exclusive ON the pixel
    assert query.at(x + 1, 7, 10, 1) is False  # starts past it


def test_overlap_on_the_boundary_word_survives_both_trims():
    a = _Mask().box(28, 2, 8, 3)   # 28..35, straddling the boundary
    b = _Mask().box(34, 2, 8, 3)   # 34..41
    query = a.collides_with(b)
    assert query.at() is True
    assert query.at(34, 2, 2, 3) is True   # the shared columns
    assert query.at(28, 2, 6, 3) is False  # a's half only


def test_window_off_screen_is_false_not_an_error():
    a, b = _Mask().set(5, 5), _Mask().set(5, 5)
    query = a.collides_with(b)
    assert query.at(-100, 5, 10, 1) is False
    assert query.at(WIDTH + 10, 5, 10, 1) is False
    assert query.at(5, ROWS + 10, 1, 1) is False
    assert query.at(5, 5, 0, 0) is False


def test_window_is_clipped_to_the_screen():
    a, b = _Mask().set(1, 1), _Mask().set(1, 1)
    # Straddles the edge; the part on screen still answers.
    assert a.collides_with(b).at(-10, -10, 20, 20) is True


# -- named planes ---------------------------------------------------------


def test_named_plane_is_selected_on_either_side():
    a = _Mask("attack").set(6, 6, "attack")
    b = _Mask("hurt").set(6, 6, "hurt")
    assert a.collider("attack").collides_with(b.collider("hurt")).at() is True
    # The same pixels are NOT in either default plane.
    assert a.collides_with(b).at() is False


def test_a_plane_and_a_silhouette_meet():
    fighter = _Mask("attack").set(12, 3, "attack")
    wall = _Mask().set(12, 3)
    assert fighter.collider("attack").collides_with(wall).at() is True
    assert wall.collides_with(fighter.collider("attack")).at() is True


def test_unknown_plane_raises_where_it_was_named():
    layer = _Mask("attack", "hurt")
    with pytest.raises(KeyError, match="unknown collision plane"):
        layer.collider("attck")


def test_a_layer_with_no_named_planes_says_so():
    # Not the reference's degraded "sheet has []" -- a real sheet whose
    # vocabulary is empty needs to hear that the silhouette is all there is.
    with pytest.raises(KeyError, match="no named collision planes"):
        _Mask().collider("attack")


def test_planes_vocabulary_is_published():
    assert _Mask("attack", "hurt").planes == frozenset({"attack", "hurt"})


# -- the gates and the sugar ----------------------------------------------


def test_a_non_collidable_layer_refuses_loudly():
    # Answering False forever would be a silent bug; this is the loud gate.
    with pytest.raises(ValueError, match="keeps no collision mask"):
        Layer().collider()


def test_a_non_collidable_target_refuses_too():
    with pytest.raises(ValueError, match="keeps no collision mask"):
        _Mask().collides_with(Layer())


def test_a_non_layer_target_is_refused():
    with pytest.raises(TypeError, match="not a collider"):
        _Mask().collides_with(object())


def test_layer_sugar_equals_the_explicit_default_collider():
    a, b = _Mask().set(2, 2), _Mask().set(2, 2)
    assert a.collides_with(b).at() == a.collides_with(b.collider()).at()
    assert a.collides_with(b).at() == a.collider().collides_with(b).at()
    assert (
        a.collides_with(b).at()
        == a.collider().collides_with(b.collider()).at()
    )


def test_collider_reports_its_layer_and_plane():
    layer = _Mask("attack")
    assert layer.collider().plane is None
    assert layer.collider("attack").plane == "attack"
    assert layer.collider("attack").layer is layer


# -- what the query must NOT consult --------------------------------------


def test_a_disabled_layer_keeps_answering():
    # Drawing is a view decision; solidity is a world one. Turning a layer
    # off must not take its contents out of play.
    a, b = _Mask().set(4, 4), _Mask().set(4, 4)
    a.enabled = False
    b.enabled = False
    assert a.collides_with(b).at() is True


def test_a_handle_resolves_its_plane_on_every_ask():
    # A tile layer swaps mask buffers when it scrolls and a sprite layer
    # recomposes every frame, so a handle that cached an array would answer
    # about a frame that has gone.
    a, b = _Mask().set(4, 4), _Mask().set(4, 4)
    query = a.collides_with(b)
    assert query.at() is True
    a._planes[None] = _Mask._blank()  # the layer swapped its buffer
    assert query.at() is False


def test_the_query_does_not_mutate_the_planes():
    a = _Mask().box(28, 2, 8, 3)
    b = _Mask().box(34, 2, 8, 3)
    before_a = a.collision_plane().copy()
    before_b = b.collision_plane().copy()
    a.collides_with(b).at(30, 2, 6, 3)
    assert np.array_equal(a.collision_plane(), before_a)
    assert np.array_equal(b.collision_plane(), before_b)


def test_unpacked_is_screen_space():
    # Screen space, not stored space: a bordered plane holds content outside
    # the screen, and a debug view showing it would not be showing what a
    # query answers about.
    layer = _Mask().set(3, 9).set(40, 2)
    bits = layer.collider().unpacked
    assert bits.shape == (SCREEN.height, SCREEN.width)
    assert bits[9, 3] and bits[2, 40]
    assert bits.sum() == 2


def test_unpacked_applies_the_offset():
    layer = _OffsetMask(5, 3).set(40, 12)
    bits = layer.collider().unpacked
    assert bits[12 - 3, 40 - 5]
    assert bits.sum() == 1


def test_query_can_be_built_directly_from_two_colliders():
    a, b = _Mask().set(1, 1), _Mask().set(1, 1)
    assert CollisionQuery(Collider(a), Collider(b)).at() is True


# -- the switch -----------------------------------------------------------


def test_collidable_defaults_off_on_the_bare_base():
    assert Layer().collidable is False


def test_a_layer_that_cannot_collide_refuses_to_be_switched_on():
    # Not a switch to flip: with no masks behind it there is nothing to
    # switch on, so the refusal names that rather than storing a lie.
    layer = Layer()
    with pytest.raises(ValueError, match="keeps no collision masks"):
        layer.collidable = True
    assert layer.collidable is False


def test_collidable_takes_a_bool_only():
    with pytest.raises(ValueError, match="must be a boolean"):
        _Mask().collidable = 1


def test_switching_off_makes_a_held_query_raise_not_answer_false():
    # The whole point: a False here is indistinguishable from "these do not
    # touch", so a layer switched off would read as working code forever.
    a, b = _Mask().set(4, 4), _Mask().set(4, 4)
    query = a.collides_with(b)
    assert query.at() is True
    a.collidable = False
    with pytest.raises(ValueError, match="collidable=False"):
        query.at()
    with pytest.raises(ValueError, match="collidable=False"):
        query.at(0, 0, 8, 8)


def test_switching_off_the_far_side_raises_too():
    a, b = _Mask().set(4, 4), _Mask().set(4, 4)
    query = a.collides_with(b)
    b.collidable = False
    with pytest.raises(ValueError, match="collidable=False"):
        query.at()


def test_switching_back_on_restores_the_answer():
    a, b = _Mask().set(4, 4), _Mask().set(4, 4)
    query = a.collides_with(b)
    a.collidable = False
    a.collidable = True
    assert query.at() is True


def test_minting_a_handle_on_a_switched_off_layer_raises():
    layer = _Mask("attack")
    layer.collidable = False
    with pytest.raises(ValueError, match="collidable=False"):
        layer.collider()
    with pytest.raises(ValueError, match="collidable=False"):
        layer.collider("attack")


def test_the_two_refusals_read_differently():
    # Art that can never collide wants authoring differently; a switch you
    # turned off wants turning back on. Same gate, different fix.
    off = _Mask()
    off.collidable = False
    with pytest.raises(ValueError, match="Set collidable=True"):
        off.collider()
    with pytest.raises(ValueError, match="keeps no collision masks"):
        Layer().collider()


def test_enabled_and_collidable_are_independent():
    a, b = _Mask().set(4, 4), _Mask().set(4, 4)
    a.enabled = False           # a view decision
    assert a.collides_with(b).at() is True
    a.enabled = True
    a.collidable = False        # a world decision
    with pytest.raises(ValueError):
        a.collides_with(b)


# -- the switch on a real pillar ------------------------------------------
#
# The rule that decides whether a layer may be switched on is per pillar,
# and TileMap is the only one where it can answer no: a sprite sheet is
# indexed by construction and a console's charset is too, while a tilemap
# takes an RGBA sheet quite deliberately.


def _filled(sheet, cols=4, rows=4, **kwargs):
    """A map whose every cell holds tile 0."""
    tilemap = TileMap(sheet, cols, rows, **kwargs)
    tilemap.cell[:, :] = tilemap.tile[0]
    return tilemap


def test_an_indexed_tilemap_may_be_collidable():
    sheet = Atlas(PixelBuffer((32, 32), "P"), 16, 16)
    assert _filled(sheet, collidable=True).collidable is True


def test_an_rgba_tilemap_may_not_be():
    # Unbuildable rather than switched off: solidity is palette index 0, and
    # an RGBA sheet has no index 0 to derive it from.
    sheet = Atlas(PixelBuffer((32, 32), "RGBA"), 16, 16)
    with pytest.raises(ValueError, match="needs an indexed sheet"):
        _filled(sheet, collidable=True)


def test_an_rgba_tilemap_is_fine_when_it_does_not_ask_to_collide():
    sheet = Atlas(PixelBuffer((32, 32), "RGBA"), 16, 16)
    tilemap = _filled(sheet)
    assert tilemap.collidable is False
    with pytest.raises(ValueError, match="needs an indexed sheet"):
        tilemap.collidable = True


def test_a_tilemap_switch_flips_both_ways():
    sheet = Atlas(PixelBuffer((32, 32), "P"), 16, 16)
    tilemap = _filled(sheet, collidable=True)
    tilemap.collidable = False
    with pytest.raises(ValueError, match="collidable=False"):
        tilemap.collider()
    tilemap.collidable = True
    assert tilemap.collidable is True


def test_a_tilemap_defaults_to_not_colliding():
    sheet = Atlas(PixelBuffer((32, 32), "P"), 16, 16)
    assert _filled(sheet).collidable is False


# -- offsets --------------------------------------------------------------


def test_a_layer_reports_no_offset_by_default():
    # A sprite is at a coordinate and a console is screen space; neither has
    # anywhere else for screen (0, 0) to be.
    assert _Mask().collision_offset == (0, 0)
    assert _Mask().collider().offset == (0, 0)


def test_an_offset_moves_a_plane_under_the_screen():
    # The plane's pixel at (40, 12) shows at screen (35, 9).
    shifted = _OffsetMask(5, 3).set(40, 12)
    plain = _Mask().set(35, 9)
    assert plain.collides_with(shifted).at() is True
    assert plain.collides_with(shifted).at(35, 9, 1, 1) is True
    assert plain.collides_with(shifted).at(40, 12, 1, 1) is False


def test_two_offsets_are_resolved_against_each_other():
    # Different parallax scrolls: neither side is the reference frame, both
    # resolve into screen space.
    a = _OffsetMask(5, 0).set(40, 6)    # shows at screen 35
    b = _OffsetMask(9, 0).set(44, 6)    # shows at screen 35
    assert a.collides_with(b).at() is True
    c = _OffsetMask(9, 0).set(45, 6)    # shows at screen 36
    assert a.collides_with(c).at() is False


def test_an_offset_can_be_negative():
    shifted = _OffsetMask(-4, -2).set(10, 10)
    assert _Mask().set(14, 12).collides_with(shifted).at() is True
