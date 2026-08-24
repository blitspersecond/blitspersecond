from unittest.mock import Mock

import numpy as np
import pytest

from blitspersecond.colors import ConsolePalette, system_palette
from blitspersecond.graphics.common import Pen
from blitspersecond.graphics.common import Orientation
from blitspersecond.graphics.glyph import CharSet, GlyphEngine
from blitspersecond.graphics.glyph.glyph_layer import _FACING_SHIFT, _GLYPH_MASK
from blitspersecond.display.layer import Layer


def cell_colours(glyphs, x, y):
    """One cell's colour slab, screen-shaped -- (gh, gw, 4)."""
    gw, gh = glyphs._char_width, glyphs._char_height
    return glyphs._colours[y * gh : (y + 1) * gh, x * gw : (x + 1) * gw]


def glyph_ids(glyphs):
    """The glyph half of the packed cell plane, facing masked off."""
    return glyphs._grid & _GLYPH_MASK


def facings(glyphs):
    """The facing half of the packed cell plane, as dense variant indices."""
    return glyphs._grid >> _FACING_SHIFT


@pytest.mark.parametrize(
    ("charset", "char_size", "grid_size"),
    [
        (CharSet.ASCII8X8, (8, 8), (80, 45)),
        (CharSet.ASCII8X12, (8, 12), (80, 30)),
    ],
)
def test_system_charset_geometry(charset, char_size, grid_size):
    glyphs = GlyphEngine(charset)

    assert glyphs._glyphs.shape == (256, char_size[1], char_size[0])
    assert (glyphs.cols, glyphs.rows) == grid_size


@pytest.mark.parametrize("charset", list(CharSet))
def test_ascii_system_charsets_are_byte_addressed(charset):
    glyphs = GlyphEngine(charset)

    assert np.any(glyphs._glyphs[ord("a")] != 0)
    assert np.all(glyphs._glyphs[128:] == 0)


def test_character_size_tuple_builds_blank_charset():
    glyphs = GlyphEngine((10, 9))

    assert glyphs._glyphs.shape == (256, 9, 10)
    assert np.all(glyphs._glyphs == 0)
    assert (glyphs.cols, glyphs.rows) == (64, 40)


@pytest.mark.parametrize("char_size", [(8, 8), (8, 12)])
def test_character_size_must_tile_screen(char_size):
    GlyphEngine(char_size)


def test_character_size_that_does_not_tile_screen_is_rejected():
    with pytest.raises(
        ValueError,
        match="character size 13x19 does not tile the 640x360 screen",
    ):
        GlyphEngine((13, 19))


def test_engine_starts_with_system_palette_and_system_pen():
    glyphs = GlyphEngine(CharSet.ASCII8X8)

    assert glyphs.palette.tobytes() == system_palette().tobytes()
    # The system pen -- slot n -> index n -- so a fresh layer can be written
    # on without choosing colours first. Pen owns what "system" means.
    assert glyphs.pen == Pen()
    assert tuple(glyphs.pen) == (0, 1, 0, 0)
    assert glyphs.pen.bound and glyphs.pen.size == (8, 8)


def test_each_engine_owns_its_pen_and_palette():
    first = GlyphEngine(CharSet.ASCII8X8)
    second = GlyphEngine(CharSet.ASCII8X8)

    assert first.pen is not second.pen
    assert first.palette is not second.palette


def test_palette_can_be_replaced_for_custom_glyph_rendering():
    glyphs = GlyphEngine(CharSet.ASCII8X8)
    palette = ConsolePalette()
    palette[1] = (255, 128, 64, 119)

    glyphs.palette = palette

    assert glyphs.palette is palette
    assert glyphs._image.palette is palette
    assert glyphs.palette[1][3] not in (0, 255)


def test_palette_replacement_requires_a_palette():
    glyphs = GlyphEngine(CharSet.ASCII8X8)

    with pytest.raises(TypeError, match="expected Palette"):
        glyphs.palette = object()


def test_clear_sets_every_cell_to_glyph_zero_in_current_pen():
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    glyphs.pen[0] = 4
    glyphs.pen[1] = 7
    glyphs.pen[2] = 9
    glyphs.pen[3] = 13

    glyphs._grid.fill(ord("a"))
    glyphs._colours.fill(16)
    glyphs.clear()

    assert np.all(glyph_ids(glyphs) == 0)
    assert np.all(glyphs._colours == (4, 7, 9, 13))


def test_clear_snapshots_the_current_pen_into_cells():
    glyphs = GlyphEngine(CharSet.ASCII8X8)
    glyphs.pen[0] = 4
    glyphs.clear()

    glyphs.pen[0] = 7

    assert np.all(glyphs._colours[:, :, 0] == 4)


def test_cell_getter_returns_character_byte():
    glyphs = GlyphEngine(CharSet.ASCII8X8)
    glyphs._grid[3, 5] = ord("a")
    cell_colours(glyphs, 3, 5)[:] = (4, 7, 9, 13)

    assert glyphs.cell[3, 5] == ord("a")


def test_cell_accessor_is_reused_and_reads_live_buffers():
    glyphs = GlyphEngine(CharSet.ASCII8X8)
    cell = glyphs.cell

    glyphs._grid[0, 0] = ord("b")

    assert glyphs.cell is cell
    assert cell[0, 0] == ord("b")


def test_cell_setter_writes_character_with_current_pen():
    glyphs = GlyphEngine(CharSet.ASCII8X8)
    glyphs.pen[0] = 4
    glyphs.pen[1] = 7
    glyphs.pen[2] = 9
    glyphs.pen[3] = 13

    glyphs.cell[3, 5] = ord("a")

    assert glyphs.cell[3, 5] == ord("a")
    # A plain pen paints its four constants across every pixel of the cell.
    assert np.all(cell_colours(glyphs, 3, 5) == (4, 7, 9, 13))


def test_cell_setter_snapshots_current_pen():
    glyphs = GlyphEngine(CharSet.ASCII8X8)
    glyphs.pen[0] = 4
    glyphs.cell[0, 0] = ord("a")

    glyphs.pen[0] = 7

    assert glyphs.cell[0, 0] == ord("a")
    assert np.all(cell_colours(glyphs, 0, 0) == (4, 1, 0, 0))


def test_a_pattern_read_back_is_a_ready_made_template():
    glyphs = GlyphEngine(CharSet.ASCII8X12)

    # No blank() needed: a read hands back the right shape and dtype, so
    # zeros_like is the whole of "give me an empty pattern".
    pattern = np.zeros_like(glyphs.glyph[0])

    assert pattern.shape == (12, 8)      # NumPy order
    assert pattern.dtype == np.uint8
    assert not pattern.any()

    pattern[:6, :4] = 1
    glyphs.glyph[200] = pattern
    assert np.array_equal(glyphs.glyph[200], pattern)


def test_glyph_size_is_screen_order_while_patterns_are_numpy_order():
    # Why the docstring says not to build a pattern from `size`: on a square
    # charset the two agree and a transposed build passes; on 8x12 they do not.
    square = GlyphEngine(CharSet.ASCII8X8)
    tall = GlyphEngine(CharSet.ASCII8X12)

    assert tuple(square.glyph.size) == square.glyph[0].shape
    assert tuple(tall.glyph.size) != tall.glyph[0].shape
    assert tuple(tall.glyph.size) == (8, 12)
    assert tall.glyph[0].shape == (12, 8)


def test_glyph_getter_returns_a_copy_of_the_pattern():
    glyphs = GlyphEngine(CharSet.ASCII8X8)
    pattern = glyphs.glyph[ord("a")]

    assert isinstance(pattern, np.ndarray)
    assert pattern.shape == (8, 8)
    assert np.array_equal(pattern, glyphs._glyphs[ord("a")])

    pattern.fill(0)

    assert np.any(glyphs.glyph[ord("a")])


def test_glyph_accessor_is_reused():
    glyphs = GlyphEngine(CharSet.ASCII8X8)

    assert glyphs.glyph is glyphs.glyph


def test_glyph_setter_copies_a_correctly_sized_pattern():
    glyphs = GlyphEngine((8, 8))
    pattern = np.zeros((8, 8), dtype=np.uint8)
    pattern[2:6, 3:5] = 1

    glyphs.glyph[200] = pattern
    pattern.fill(0)

    expected = np.zeros((8, 8), dtype=np.uint8)
    expected[2:6, 3:5] = 1
    assert np.array_equal(glyphs.glyph[200], expected)


def test_glyph_setter_restamps_cells_and_collision_using_that_id():
    glyphs = GlyphEngine((8, 8))
    glyphs.pen[1] = 7
    glyphs.cell[2, 3] = 200
    assert not glyphs.collision_mask.any()

    pattern = np.zeros((8, 8), dtype=np.uint8)
    pattern[2:6, 3:5] = 1
    glyphs.glyph[200] = pattern

    assert np.array_equal(
        glyphs._image.pixels[24:32, 16:24],
        np.asarray((0, 7, 0, 0), dtype=np.uint8)[pattern],
    )
    unpacked = np.unpackbits(
        glyphs.collision_mask.view(np.uint8), axis=1, bitorder="little"
    )
    assert np.array_equal(unpacked[24:32, 16:24], pattern != 0)


@pytest.mark.parametrize("glyph_id", [-1, 256])
def test_glyph_accessor_rejects_id_outside_byte_range(glyph_id):
    glyphs = GlyphEngine(CharSet.ASCII8X8)

    with pytest.raises(ValueError, match="glyph id must be a byte"):
        _ = glyphs.glyph[glyph_id]


def test_glyph_setter_requires_an_ndarray():
    glyphs = GlyphEngine(CharSet.ASCII8X8)

    with pytest.raises(TypeError, match="glyph pattern must be an ndarray"):
        glyphs.glyph[200] = [[0] * 8] * 8


def test_glyph_setter_requires_the_character_shape():
    glyphs = GlyphEngine(CharSet.ASCII8X12)

    with pytest.raises(ValueError, match=r"shape \(12, 8\)"):
        glyphs.glyph[200] = np.zeros((8, 12), dtype=np.uint8)


def test_glyph_setter_requires_integer_plane_indices():
    glyphs = GlyphEngine(CharSet.ASCII8X8)

    with pytest.raises(TypeError, match="integer plane indices"):
        glyphs.glyph[200] = np.zeros((8, 8), dtype=np.float32)


def test_glyph_setter_rejects_plane_outside_pen():
    glyphs = GlyphEngine(CharSet.ASCII8X8)
    pattern = np.zeros((8, 8), dtype=np.uint8)
    pattern[0, 0] = 4

    with pytest.raises(ValueError, match=r"range 0\.\.3"):
        glyphs.glyph[200] = pattern


@pytest.mark.parametrize("value", [-1, 256])
def test_cell_setter_rejects_value_outside_byte_range(value):
    glyphs = GlyphEngine(CharSet.ASCII8X8)

    with pytest.raises(ValueError, match="glyph index must be a byte"):
        glyphs.cell[0, 0] = value


def test_engine_produces_a_layer():
    glyphs = GlyphEngine(CharSet.ASCII8X8)

    assert isinstance(glyphs, Layer)
    assert glyphs.size == (640, 360)
    assert glyphs.indexed
    assert glyphs.palette is glyphs._image.palette


def test_cell_write_renders_glyph_planes_through_cell_colours():
    glyphs = GlyphEngine(CharSet.ASCII8X8)
    glyphs.pen[0] = 4
    glyphs.pen[1] = 7
    glyphs.pen[2] = 9
    glyphs.pen[3] = 13

    glyphs.cell[2, 3] = ord("a")

    expected = np.asarray((4, 7, 9, 13), dtype=np.uint8)[
        glyphs._glyphs[ord("a")]
    ]
    assert np.array_equal(glyphs._image.pixels[24:32, 16:24], expected)
    assert glyphs._image.dirty


def test_clear_refreshes_layerable_pixel_buffer():
    glyphs = GlyphEngine(CharSet.ASCII8X8)
    glyphs.cell[0, 0] = ord("a")
    glyphs._image.dirty = False

    glyphs.clear()

    assert not glyphs._image.pixels.any()
    assert glyphs._image.dirty


def test_prepare_and_draw_delegate_to_picture_engine():
    glyphs = GlyphEngine(CharSet.ASCII8X8)
    glyphs._renderer.prepare = Mock()
    glyphs._renderer.blit = Mock()
    glyphs._renderer._composite = Mock()

    glyphs.prepare()
    glyphs._composite()

    glyphs._renderer.prepare.assert_called_once_with()
    glyphs._renderer.blit.assert_called_once_with(0, 0, 0)
    glyphs._renderer._composite.assert_called_once_with()


def test_cell_write_stamps_rendered_glyph_into_collision_mask():
    glyphs = GlyphEngine(CharSet.ASCII8X8)
    glyphs.pen[1] = 7

    glyphs.cell[2, 3] = ord("a")

    unpacked = np.unpackbits(
        glyphs.collision_mask.view(np.uint8), axis=1, bitorder="little"
    )
    assert np.array_equal(
        unpacked[24:32, 16:24],
        glyphs._glyphs[ord("a")] != 0,
    )
    assert glyphs.collidable


def test_cell_overwrite_replaces_collision_stamp():
    glyphs = GlyphEngine(CharSet.ASCII8X8)
    glyphs.pen[1] = 7
    glyphs.cell[2, 3] = ord("a")
    _ = glyphs.collision_mask

    glyphs.cell[2, 3] = 0

    assert not glyphs.collision_mask.any()


def test_glyph_plane_mapped_to_transparent_pen_is_not_solid():
    glyphs = GlyphEngine(CharSet.ASCII8X8)
    # Every plane on the transparent index -- stated, not inherited from the
    # default, which is the system pen and therefore visible.
    glyphs.pen = Pen(0, 0, 0, 0)

    glyphs.cell[2, 3] = ord("a")

    assert not glyphs.collision_mask.any()


def test_clear_removes_collision_stamps():
    glyphs = GlyphEngine(CharSet.ASCII8X8)
    glyphs.pen[1] = 7
    glyphs.cell[2, 3] = ord("a")
    _ = glyphs.collision_mask

    glyphs.clear()

    assert not glyphs.collision_mask.any()


def test_collision_query_reads_glyph_stamps():
    first = GlyphEngine(CharSet.ASCII8X8)
    second = GlyphEngine(CharSet.ASCII8X8)
    first.pen[1] = 7
    second.pen[1] = 9
    first.cell[2, 3] = ord("a")
    second.cell[2, 3] = ord("a")

    assert first.collides_with(second).at()

    second.cell[2, 3] = 0

    assert not first.collides_with(second).at()


def test_scroll_moves_cells_without_wrapping_and_preserves_colours():
    glyphs = GlyphEngine(CharSet.ASCII8X8)
    glyphs.pen[1] = 7
    glyphs.cell[2, 3] = ord("a")
    glyphs.pen[1] = 9

    glyphs.scroll((-1, 2))

    assert glyphs.cell[1, 5] == ord("a")
    assert np.all(cell_colours(glyphs, 1, 5) == (0, 7, 0, 0))
    assert glyphs.cell[2, 3] == 0
    assert np.all(cell_colours(glyphs, 2, 0) == (0, 9, 0, 0))
    assert not glyph_ids(glyphs)[-1].any()


def test_scroll_moves_collision_stamp():
    glyphs = GlyphEngine(CharSet.ASCII8X8)
    glyphs.pen[1] = 7
    glyphs.cell[2, 3] = ord("a")
    before = glyphs.collision_mask.copy()

    glyphs.scroll((-1, 2))

    after = glyphs.collision_mask
    assert not np.array_equal(after, before)
    unpacked = np.unpackbits(after.view(np.uint8), axis=1, bitorder="little")
    assert not unpacked[24:32, 16:24].any()
    assert np.array_equal(
        unpacked[40:48, 8:16],
        glyphs._glyphs[ord("a")] != 0,
    )


def test_scroll_beyond_grid_clears_in_current_pen():
    glyphs = GlyphEngine(CharSet.ASCII8X8)
    glyphs.pen[1] = 7
    glyphs.cell[2, 3] = ord("a")
    glyphs.pen[1] = 9

    glyphs.scroll((0, glyphs.rows))

    assert not glyph_ids(glyphs).any()
    assert np.all(glyphs._colours[:, :, 1] == 9)
    assert not glyphs.collision_mask.any()


@pytest.mark.parametrize("amount", [(1,), [1, 2], (1.0, 2), (True, 2)])
def test_scroll_rejects_invalid_amount(amount):
    glyphs = GlyphEngine(CharSet.ASCII8X8)

    with pytest.raises(TypeError):
        glyphs.scroll(amount)


@pytest.mark.parametrize("bad", [(1.0, 2), (1, 2.0), (True, 2), (1, None)])
def test_scroll_rejects_invalid_loose_arguments(bad):
    glyphs = GlyphEngine(CharSet.ASCII8X8)

    with pytest.raises(TypeError):
        glyphs.scroll(*bad)


def test_scroll_takes_loose_x_y_as_well_as_a_pair():
    # A coordinate pair is one idea; it should not need a tuple built round
    # it to be heard, and code holding an (x, y) should not have to unpack.
    loose = GlyphEngine(CharSet.ASCII8X8)
    paired = GlyphEngine(CharSet.ASCII8X8)
    for glyphs in (loose, paired):
        glyphs.pen = Pen(0, 9, size=glyphs.glyph.size)
        glyphs.cell[2, 3] = ord("a")

    loose.scroll(1, 2)
    paired.scroll((1, 2))

    assert loose.cell[3, 5] == ord("a")
    assert np.array_equal(loose._grid, paired._grid)
    assert np.array_equal(loose._image.pixels, paired._image.pixels)


@pytest.mark.parametrize(
    ("charset", "error"),
    [
        ((8,), TypeError),
        ([8, 8], TypeError),
        ((8.0, 8), TypeError),
        ((True, 8), TypeError),
        ((0, 8), ValueError),
        ((8, -1), ValueError),
    ],
)
def test_invalid_character_size_rejected(charset, error):
    with pytest.raises(error):
        GlyphEngine(charset)


# -- fiddled pens: per-pixel ink on the one Pen class -------------------------


def chrome(layer, ramp, slot=1):
    """A pen whose `slot` runs down a vertical ramp -- the copper trick, one
    cell's worth. Bound to the layer so `paint` exists to fiddle."""
    pen = Pen(0, 0, 0, 0, size=layer.glyph.size)
    pen.paint[:, :, slot] = np.asarray(ramp, dtype=np.uint8)[:, None]
    return pen


def test_a_pen_binds_to_the_layer_it_is_built_against():
    glyphs = GlyphEngine(CharSet.ASCII8X12)

    pen = Pen(size=glyphs.glyph.size)

    assert pen.bound
    assert pen.size == (8, 12)
    width, height = pen.size            # a real Size: unpacks, names its parts
    assert (pen.size.width, pen.size.height) == (width, height) == (8, 12)
    assert pen.paint.shape == (12, 8, 4)


def test_a_flat_pen_is_sizeless_until_it_is_used():
    pen = Pen(1, 2, 3, 4)

    assert not pen.bound
    assert pen.size is None
    with pytest.raises(RuntimeError, match="not bound"):
        pen.paint

    GlyphEngine(CharSet.ASCII8X12).pen = pen

    assert pen.size == (8, 12)


def test_an_unbound_pen_refuses_a_plane_but_still_takes_indices():
    pen = Pen(1, 2)

    # A plane needs a cell to be shaped against, and a flat pen has none.
    with pytest.raises(RuntimeError, match="no cell to shape a plane against"):
        pen[1] = np.arange(12, dtype=np.uint8)
    with pytest.raises(RuntimeError, match="no cell to shape a plane against"):
        pen.foreground = np.arange(12, dtype=np.uint8)

    # Scalars are still fine -- that is what a sizeless pen is for.
    pen[1] = 7
    assert tuple(pen) == (1, 7, 0, 0)

    # And once a layer binds it, the same plane lands.
    GlyphEngine(CharSet.ASCII8X12).pen = pen
    pen[1] = np.arange(12, dtype=np.uint8)
    assert pen[1].shape == (12, 8)


def test_a_pen_is_sized_by_dimensions_not_by_a_layer():
    pen = Pen(1, 2, size=(8, 12))

    assert pen.bound and pen.size == (8, 12)
    assert pen.paint.shape == (12, 8, 4)


def test_one_pen_serves_every_layer_whose_cells_match():
    ramp = np.arange(12, dtype=np.uint8)
    shared = Pen(0, 1, size=(8, 12))
    shared[1] = ramp

    first = GlyphEngine(CharSet.ASCII8X12)
    second = GlyphEngine(CharSet.ASCII8X12)
    first.pen = shared
    second.pen = shared

    # One instrument, two surfaces -- a pen is not owned by a layer.
    assert first.pen is second.pen is shared
    first.cell[0, 0] = ord("A")
    second.cell[0, 0] = ord("A")
    assert np.array_equal(
        first._colours[0:12, 0:8, 1], second._colours[0:12, 0:8, 1]
    )
    assert (first._colours[0:12, 0:8, 1][:, 0] == ramp).all()


def test_a_bound_pen_is_frozen_and_refuses_another_cell_size():
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    pen = Pen(size=glyphs.glyph.size)

    with pytest.raises(ValueError, match="bound to 8x12"):
        GlyphEngine(CharSet.ASCII8X8).pen = pen


def test_layer_rejects_a_pen_that_is_neither():
    glyphs = GlyphEngine(CharSet.ASCII8X8)

    with pytest.raises(TypeError):
        glyphs.pen = (1, 2, 3, 4)


def test_a_fiddled_pen_paints_its_array_verbatim_into_the_cell():
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    ramp = range(20, 32)
    glyphs.pen = chrome(glyphs, ramp)

    glyphs.cell[3, 5] = ord("a")

    slab = cell_colours(glyphs, 3, 5)
    assert np.array_equal(slab[:, :, 1], np.array(ramp, dtype=np.uint8)[:, None]
                          .repeat(8, axis=1))


def test_a_fiddled_pen_gives_each_scanline_of_a_glyph_its_own_colour():
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    glyphs.pen = chrome(glyphs, range(20, 32))

    glyphs.cell[3, 5] = ord("a")

    lit = glyphs._image.pixels[5 * 12 : 6 * 12, 3 * 8 : 4 * 8]
    per_row = {int(row[row != 0][0]) for row in lit if np.any(row)}
    # The point of the whole exercise: one glyph, many colours down its height.
    assert len(per_row) > 1


def test_a_plain_pen_still_paints_one_flat_colour_per_cell():
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    glyphs.pen = Pen(4, 7, 9, 13)

    glyphs.cell[3, 5] = ord("a")

    assert np.all(cell_colours(glyphs, 3, 5) == (4, 7, 9, 13))


def test_clear_under_a_fiddled_pen_tiles_it_across_every_cell():
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    pen = chrome(glyphs, range(20, 32))
    glyphs.pen = pen

    glyphs.clear()

    assert np.array_equal(cell_colours(glyphs, 0, 0), pen.paint)
    assert np.array_equal(cell_colours(glyphs, 7, 9), pen.paint)


def test_scroll_under_a_fiddled_pen_moves_slabs_intact():
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    glyphs.pen = chrome(glyphs, range(20, 32))
    glyphs.cell[2, 3] = ord("a")
    moved = cell_colours(glyphs, 2, 3).copy()

    glyphs.scroll((-1, 2))

    assert glyphs.cell[1, 5] == ord("a")
    assert np.array_equal(cell_colours(glyphs, 1, 5), moved)


def test_fiddled_pen_paint_is_live_but_written_cells_are_snapshots():
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    pen = chrome(glyphs, range(20, 32))
    glyphs.pen = pen

    glyphs.cell[0, 0] = ord("a")
    pen.paint[:, :, 1] = 99

    assert not np.any(cell_colours(glyphs, 0, 0)[:, :, 1] == 99)
    glyphs.cell[1, 0] = ord("a")
    assert np.all(cell_colours(glyphs, 1, 0)[:, :, 1] == 99)


# -- facing: carried by the pen, not a mode on the layer ---------------------


def cell_pixels(glyphs, x, y):
    """One cell's composed palette indices -- (gh, gw)."""
    gw, gh = glyphs._char_width, glyphs._char_height
    return glyphs._image.pixels[y * gh : (y + 1) * gh, x * gw : (x + 1) * gw]


def test_orientation_starts_upright():
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    assert glyphs.pen.facing is Orientation.NONE


def test_flipped_r_is_a_passable_cyrillic_ya():
    # The whole feature in one line: a mirrored R is Я. Composed pixels, not
    # the variant table -- this proves the flip survives the render path.
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    glyphs.pen = Pen(0, 15)
    glyphs.clear()
    glyphs.cell[0, 0] = ord("R")
    glyphs.pen = Pen(*tuple(glyphs.pen), size=glyphs.glyph.size, facing=Orientation.FLIP_H)
    glyphs.cell[1, 0] = ord("R")

    upright = cell_pixels(glyphs, 0, 0)
    assert np.array_equal(cell_pixels(glyphs, 1, 0), upright[:, ::-1])
    assert not np.array_equal(cell_pixels(glyphs, 1, 0), upright)  # R is not symmetric


@pytest.mark.parametrize(
    ("orientation", "mirror"),
    [
        (Orientation.FLIP_H, lambda a: a[:, ::-1]),
        (Orientation.FLIP_V, lambda a: a[::-1]),
        (Orientation.ROT_180, lambda a: a[::-1, ::-1]),
    ],
)
def test_each_legal_orientation_mirrors_the_composed_cell(orientation, mirror):
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    glyphs.pen = Pen(0, 15)
    glyphs.clear()
    glyphs.cell[0, 0] = ord("R")
    glyphs.pen = Pen(*tuple(glyphs.pen), size=glyphs.glyph.size, facing=orientation)
    glyphs.cell[1, 0] = ord("R")
    assert np.array_equal(cell_pixels(glyphs, 1, 0), mirror(cell_pixels(glyphs, 0, 0)))


@pytest.mark.parametrize(
    "orientation",
    [Orientation.TRANSPOSE, Orientation.ROT_90, Orientation.ROT_270],
)
@pytest.mark.parametrize("charset", [CharSet.ASCII8X12, CharSet.ASCII8X8])
def test_quarter_turns_are_refused_even_on_a_square_charset(charset, orientation):
    # 8x8 could take all eight; it does not get to. A rule that holds for
    # every charset beats a capability that holds for some of them.
    glyphs = GlyphEngine(charset)
    with pytest.raises(ValueError, match="quarter turn"):
        glyphs.pen = Pen(*tuple(glyphs.pen), size=glyphs.glyph.size, facing=orientation)
    assert glyphs.pen.facing is Orientation.NONE  # rejected, not half-applied


def test_written_cells_keep_their_facing_when_the_brush_turns():
    # Exactly how the pen behaves: the brush describes the NEXT write.
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    glyphs.pen = Pen(0, 15)
    glyphs.clear()
    glyphs.cell[0, 0] = ord("R")
    before = cell_pixels(glyphs, 0, 0).copy()
    glyphs.pen = Pen(*tuple(glyphs.pen), size=glyphs.glyph.size, facing=Orientation.FLIP_H)
    assert np.array_equal(cell_pixels(glyphs, 0, 0), before)


def test_clear_uses_the_current_orientation_like_it_uses_the_pen():
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    glyphs.pen = Pen(*tuple(glyphs.pen), size=glyphs.glyph.size, facing=Orientation.ROT_180)
    glyphs.clear()
    assert np.all(facings(glyphs) == Orientation.ROT_180 >> 1)


def test_scroll_carries_facing_with_the_cells():
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    glyphs.pen = Pen(0, 15)
    glyphs.clear()
    glyphs.pen = Pen(*tuple(glyphs.pen), size=glyphs.glyph.size, facing=Orientation.FLIP_H)
    glyphs.cell[0, 0] = ord("R")
    flipped = cell_pixels(glyphs, 0, 0).copy()

    glyphs.pen = Pen(*tuple(glyphs.pen), size=glyphs.glyph.size, facing=Orientation.NONE)
    glyphs.scroll((3, 2))
    # The cell moved with its glyph, its ink AND its facing.
    assert np.array_equal(cell_pixels(glyphs, 3, 2), flipped)
    # Newly exposed cells take the current brush.
    assert facings(glyphs)[0, 0] == Orientation.NONE >> 1


@pytest.mark.parametrize("facing", [Orientation.FLIP_H, Orientation.FLIP_V,
                                    Orientation.ROT_180])
def test_reading_a_cell_masks_the_packed_facing_off(facing):
    # A cell packs facing above the glyph id, but a read hands back the glyph
    # ALONE -- so a value can make the round trip. If a read carried the
    # facing, writing it back would set a facing the pen never chose, and
    # there would be two places facing comes from.
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    glyphs.pen = Pen(0, 15, size=glyphs.glyph.size, facing=facing)
    glyphs.cell[3, 5] = ord("R")
    glyphs.cell[10:15, 7] = np.frombuffer(b"HELLO", np.uint8)

    assert glyphs.cell[3, 5] == ord("R")
    assert list(glyphs.cell[10:15, 7]) == list(b"HELLO")
    assert glyphs.cell[10:15, 7].dtype == np.uint8
    # ...and the facing really is in there, just not in what a read returns.
    assert facings(glyphs)[3, 5] == facing >> 1


def test_a_bulk_run_carries_the_pens_facing_like_a_single_write():
    # One packed plane means a run sets glyph and facing in one store; a run
    # is the same operation as a single write, wider, in facing too.
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    glyphs.pen = Pen(0, 15, size=glyphs.glyph.size, facing=Orientation.FLIP_H)
    glyphs.cell[0, 0] = ord("R")
    expected = cell_pixels(glyphs, 0, 0).copy()

    glyphs.cell[10:15, 7] = ord("R")

    assert np.all(facings(glyphs)[10:15, 7] == Orientation.FLIP_H >> 1)
    for x in range(10, 15):
        assert np.array_equal(cell_pixels(glyphs, x, 7), expected)


def test_paint_buffer_writes_land_and_recompose():
    # The colour plane is per PIXEL, so a write to it can vary INSIDE a cell
    # -- which is the whole reason it is screen-shaped rather than cell-shaped.
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    # A solid block of our own, so the test does not depend on the font
    # happening to ship one: every pixel selects plane 1.
    solid = 1
    glyphs.glyph[solid] = np.ones_like(glyphs.glyph[0])
    glyphs.pen = Pen(0, 1, size=glyphs.glyph.size)
    glyphs.cell[0, 0] = solid

    gradient = np.arange(12, dtype=np.uint8)[:, None]
    glyphs.paint_buffer[0:12, 0:8, 1] = gradient

    # Each scanline of the cell resolved to its own palette index.
    assert np.array_equal(cell_pixels(glyphs, 0, 0), np.broadcast_to(gradient, (12, 8)))


def test_paint_buffer_reads_are_read_only_views():
    # A view, because copying 900KB to look at it would be absurd. Read-only,
    # because a write through a captured view could not tell the layer to
    # recompose and would show as a frame that silently did not change.
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    view = glyphs.paint_buffer[:]

    assert view.base is not None, "should be a view, not a copy"
    with pytest.raises(ValueError):
        view[0, 0, 0] = 9


def test_paint_buffer_partial_write_recomposes_only_the_rows_it_touched():
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    calls = []
    glyphs._refresh_rows = lambda first, last: calls.append((first, last))

    glyphs.paint_buffer[24:36] = 0  # pixel rows 24..36 == cell row 2
    glyphs.paint_buffer[30:40] = 0  # straddles cell rows 2 and 3
    glyphs.paint_buffer[..., 1] = 0  # no row information -> the whole screen
    glyphs.paint_buffer[13] = 0  # one pixel row, inside cell row 1

    assert calls == [(2, 3), (2, 4), (0, 30), (1, 2)]


def test_paint_buffer_shape_is_the_screen_not_the_grid():
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    height, width, slots = glyphs.paint_buffer.shape

    assert (width, height) == tuple(glyphs.size)
    assert slots == 4
    # ...and emphatically not the cell grid, which is the point of the name.
    assert (width, height) != (glyphs.cols, glyphs.rows)


def test_defer_recomposes_once_at_the_end_not_per_write():
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    calls = []
    glyphs._refresh_rows = lambda first, last: calls.append((first, last))

    with glyphs.defer():
        glyphs.cell[:, :] = 7
        glyphs.paint_buffer[:] = 3
        assert calls == [], "nothing should recompose inside the block"

    assert calls == [(0, glyphs.rows)]


def test_defer_result_is_identical_to_writing_eagerly():
    # The whole point: same screen, fewer recomposes. If these ever diverge
    # the optimisation is a lie.
    eager = GlyphEngine(CharSet.ASCII8X12)
    deferred = GlyphEngine(CharSet.ASCII8X12)
    codes = np.arange(eager.cols * eager.rows, dtype=np.uint8).reshape(
        eager.cols, eager.rows
    )
    field = np.random.default_rng(0).integers(
        0, 255, eager.paint_buffer.shape, dtype=np.uint8
    )

    eager.cell[:, :] = codes
    eager.paint_buffer[:] = field
    with deferred.defer():
        deferred.cell[:, :] = codes
        deferred.paint_buffer[:] = field

    assert np.array_equal(eager._image.pixels, deferred._image.pixels)


def test_defer_spans_only_the_rows_touched():
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    calls = []
    glyphs._refresh_rows = lambda first, last: calls.append((first, last))

    with glyphs.defer():
        glyphs.cell[3, 5] = ord("a")
        glyphs.cell[9, 7] = ord("b")

    # A span, not a set -- rows 5 and 7 pull row 6 along with them.
    assert calls == [(5, 8)]


def test_defer_nests_so_a_helper_need_not_know():
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    calls = []
    glyphs._refresh_rows = lambda first, last: calls.append((first, last))

    with glyphs.defer():
        with glyphs.defer():
            glyphs.cell[:, 4] = ord("a")
        assert calls == [], "the inner block must not flush the outer one"
        glyphs.cell[:, 9] = ord("b")

    assert calls == [(4, 10)]


def test_defer_recomposes_even_when_the_block_raises():
    # Leaving the screen out of step with the buffers would turn a handled
    # error into a rendering bug a long way from its cause.
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    calls = []
    glyphs._refresh_rows = lambda first, last: calls.append((first, last))

    with pytest.raises(RuntimeError):
        with glyphs.defer():
            glyphs.cell[:, 4] = ord("a")
            raise RuntimeError("boom")

    assert calls == [(4, 5)]


def test_collision_mask_flushes_a_pending_defer():
    # Collision is answered off the COMPOSED pixels. Mid-defer that would be
    # a wrong answer, not merely a late one.
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    glyphs.glyph[1] = np.ones_like(glyphs.glyph[0])
    glyphs.pen = Pen(0, 15, size=glyphs.glyph.size)
    glyphs.clear()
    assert not glyphs.collision_mask.any()

    with glyphs.defer():
        glyphs.cell[2, 3] = 1
        assert glyphs.collision_mask.any(), "must see the write it was asked about"


def test_cell_is_indexed_x_first_not_numpy_order():
    # `cell[x, y]` LOOKS like a NumPy index and means the transpose of one:
    # `cell[:, 5]` is a row where `array[:, 5]` would be a column. This is
    # the trap the docstring warns about, pinned so the warning stays true.
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    glyphs.cell[:, :] = ord(".")

    glyphs.cell[:, 5] = ord("R")  # a ROW
    glyphs.cell[3, :] = ord("C")  # a COLUMN

    assert len(glyphs.cell[:, 5]) == glyphs.cols
    assert len(glyphs.cell[3, :]) == glyphs.rows
    assert all(glyphs.cell[x, 5] == ord("R") for x in range(glyphs.cols) if x != 3)
    assert all(glyphs.cell[3, y] == ord("C") for y in range(glyphs.rows))
    assert glyphs.cell[10, 9] == ord(".")
    # ...and the storage really is x-major, so none of that costs a transpose.
    assert glyphs._grid.shape == (glyphs.cols, glyphs.rows)


def test_replacing_a_pattern_rederives_its_mirrors():
    # A font edited at runtime must stay consistent in every orientation --
    # the variant table is derived, never a second source of truth.
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    glyphs.pen = Pen(0, 15)
    glyphs.clear()
    pattern = np.zeros((12, 8), dtype=np.uint8)
    pattern[0, 0] = 1  # a lone pixel in the top-left corner
    glyphs.glyph[7] = pattern

    glyphs.pen = Pen(*tuple(glyphs.pen), size=glyphs.glyph.size, facing=Orientation.FLIP_H)
    glyphs.cell[0, 0] = 7
    composed = cell_pixels(glyphs, 0, 0)
    assert composed[0, -1] != composed[0, 0]  # the pixel mirrored to the right edge
