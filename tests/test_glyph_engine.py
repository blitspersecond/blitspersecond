from unittest.mock import Mock

import numpy as np
import pytest

from blitspersecond.colors import ConsolePalette, MagicPen, Pen, system_palette
from blitspersecond.graphics.glyph import CharSet, GlyphEngine
from blitspersecond.display.layer import Layer


def cell_colours(glyphs, x, y):
    """One cell's colour slab, screen-shaped -- (gh, gw, 4)."""
    gw, gh = glyphs._char_width, glyphs._char_height
    return glyphs._colours[y * gh : (y + 1) * gh, x * gw : (x + 1) * gw]


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


def test_engine_starts_with_system_palette_and_blank_pen():
    glyphs = GlyphEngine(CharSet.ASCII8X8)

    assert glyphs.palette.tobytes() == system_palette().tobytes()
    assert glyphs.pen == Pen(0, 0, 0, 0)


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

    glyphs._chars.fill(ord("a"))
    glyphs._colours.fill(16)
    glyphs.clear()

    assert np.all(glyphs._chars == 0)
    assert np.all(glyphs._colours == (4, 7, 9, 13))


def test_clear_snapshots_the_current_pen_into_cells():
    glyphs = GlyphEngine(CharSet.ASCII8X8)
    glyphs.pen[0] = 4
    glyphs.clear()

    glyphs.pen[0] = 7

    assert np.all(glyphs._colours[:, :, 0] == 4)


def test_cell_getter_returns_character_byte():
    glyphs = GlyphEngine(CharSet.ASCII8X8)
    glyphs._chars[3, 5] = ord("a")
    cell_colours(glyphs, 3, 5)[:] = (4, 7, 9, 13)

    assert glyphs.cell[3, 5] == ord("a")


def test_cell_accessor_is_reused_and_reads_live_buffers():
    glyphs = GlyphEngine(CharSet.ASCII8X8)
    cell = glyphs.cell

    glyphs._chars[0, 0] = ord("b")

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
    assert np.all(cell_colours(glyphs, 0, 0) == (4, 0, 0, 0))


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
    assert glyphs.transparency_index == 0


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
    assert not glyphs._chars[-1].any()


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

    assert not glyphs._chars.any()
    assert np.all(glyphs._colours[:, :, 1] == 9)
    assert not glyphs.collision_mask.any()


@pytest.mark.parametrize("amount", [(1,), [1, 2], (1.0, 2), (True, 2)])
def test_scroll_rejects_invalid_amount(amount):
    glyphs = GlyphEngine(CharSet.ASCII8X8)

    with pytest.raises(TypeError):
        glyphs.scroll(amount)


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


# -- magic pens ---------------------------------------------------------------


def chrome(width, height, ramp, slot=1):
    """A magic pen whose `slot` runs down a vertical ramp -- the copper trick,
    one cell's worth of it."""
    pixels = np.zeros((height, width, 4), dtype=np.uint8)
    pixels[:, :, slot] = np.asarray(ramp, dtype=np.uint8)[:, None]
    return MagicPen(pixels)


def test_magic_pen_reports_its_cell_size_in_screen_order():
    pen = MagicPen(np.zeros((12, 8, 4), dtype=np.uint8))

    assert pen.size == (8, 12)


@pytest.mark.parametrize(
    ("pixels", "error"),
    [
        (np.zeros((12, 8), dtype=np.uint8), ValueError),
        (np.zeros((12, 8, 3), dtype=np.uint8), ValueError),
        (np.zeros((12, 8, 4), dtype=np.float32), TypeError),
        (np.full((12, 8, 4), 300, dtype=np.int16), ValueError),
        ([[[0, 0, 0, 0]]], TypeError),
    ],
)
def test_magic_pen_rejects_bad_pixels(pixels, error):
    with pytest.raises(error):
        MagicPen(pixels)


def test_layer_rejects_a_magic_pen_of_the_wrong_cell_size():
    glyphs = GlyphEngine(CharSet.ASCII8X12)

    with pytest.raises(ValueError):
        glyphs.pen = MagicPen(np.zeros((8, 8, 4), dtype=np.uint8))


def test_layer_rejects_a_pen_that_is_neither():
    glyphs = GlyphEngine(CharSet.ASCII8X8)

    with pytest.raises(TypeError):
        glyphs.pen = (1, 2, 3, 4)


def test_magic_pen_paints_its_array_verbatim_into_the_cell():
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    ramp = range(20, 32)
    glyphs.pen = chrome(8, 12, ramp)

    glyphs.cell[3, 5] = ord("a")

    slab = cell_colours(glyphs, 3, 5)
    assert np.array_equal(slab[:, :, 1], np.array(ramp, dtype=np.uint8)[:, None]
                          .repeat(8, axis=1))


def test_magic_pen_gives_each_scanline_of_a_glyph_its_own_colour():
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    glyphs.pen = chrome(8, 12, range(20, 32))

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


def test_clear_under_a_magic_pen_tiles_it_across_every_cell():
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    pen = chrome(8, 12, range(20, 32))
    glyphs.pen = pen

    glyphs.clear()

    assert np.array_equal(cell_colours(glyphs, 0, 0), pen.pixels)
    assert np.array_equal(cell_colours(glyphs, 7, 9), pen.pixels)


def test_scroll_under_a_magic_pen_moves_slabs_intact():
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    glyphs.pen = chrome(8, 12, range(20, 32))
    glyphs.cell[2, 3] = ord("a")
    moved = cell_colours(glyphs, 2, 3).copy()

    glyphs.scroll((-1, 2))

    assert glyphs.cell[1, 5] == ord("a")
    assert np.array_equal(cell_colours(glyphs, 1, 5), moved)


def test_magic_pen_pixels_are_live_but_written_cells_are_snapshots():
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    pen = chrome(8, 12, range(20, 32))
    glyphs.pen = pen

    glyphs.cell[0, 0] = ord("a")
    pen.pixels[:, :, 1] = 99

    assert not np.any(cell_colours(glyphs, 0, 0)[:, :, 1] == 99)
    glyphs.cell[1, 0] = ord("a")
    assert np.all(cell_colours(glyphs, 1, 0)[:, :, 1] == 99)
