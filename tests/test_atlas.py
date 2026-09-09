"""`Atlas`: one image, a table of rects, addressed by key.

An atlas is a set of rects over one image. Giving a cell size is an AUTOGRID
-- a way of GENERATING that table -- not a second kind of atlas. So these are
one file: the same questions are asked of a generated table and a given one,
and they are expected to answer identically.

Two things are tested that a naive reading would skip.

**`rect(key)` is not checked by redoing its arithmetic.** Testing a method
that multiplies four numbers by multiplying the same four numbers back proves
only that the test and the implementation were typed by the same person.
Instead every rect is painted a distinguishable value and the region
`rect(key)` names is asserted byte-identical to the pixels `atlas[key]` hands
back. That fails if either accessor moves, and in the direction that matters
-- the two disagreeing about which rect a key means.

**The autogrid fixture is deliberately awkward**: 7x5 cells of 11x13 pixels,
so nothing is square, nothing is a power of two, and a row-major/column-major
slip cannot hide behind a symmetry.
"""

import numpy as np
import pytest

from blitspersecond.common import Rect
from blitspersecond.graphics import Atlas, PixelBuffer


# -- an autogrid ------------------------------------------------------

COLS, ROWS = 7, 5
CELL_W, CELL_H = 11, 13


@pytest.fixture
def grid() -> Atlas:
    """A 77x65 indexed canvas, each cell filled with its own index + 1.

    Index + 1 because a blank buffer is already zeros: filling cell 0 with 0
    would make "painted correctly" and "never painted" the same picture.
    """
    buffer = PixelBuffer((COLS * CELL_W, ROWS * CELL_H), "P")
    with buffer.edit() as pixels:
        for n in range(COLS * ROWS):
            y = (n // COLS) * CELL_H
            x = (n % COLS) * CELL_W
            pixels[y : y + CELL_H, x : x + CELL_W] = n + 1
    return Atlas(buffer, CELL_W, CELL_H)


def test_rect_locates_the_pixels_getitem_returns(grid: Atlas) -> None:
    """The whole point: slicing the image by `rect(n)` gives `grid[n]`."""
    pixels = grid.buffer.pixels
    for n in range(len(grid)):
        r = grid.rect(n)
        region = pixels[r.y : r.y + r.height, r.x : r.x + r.width]
        assert np.array_equal(region, grid[n]), f"cell {n} disagrees"
        # And it is the cell we meant, not merely a consistent wrong one.
        assert np.all(region == n + 1)


def test_rect_is_a_Rect_of_the_cell_size(grid: Atlas) -> None:
    assert grid.rect(0) == Rect(0, 0, CELL_W, CELL_H)
    assert grid.rect(1) == Rect(CELL_W, 0, CELL_W, CELL_H)
    # First cell of the second row -- the one a row/column slip gets wrong.
    assert grid.rect(COLS) == Rect(0, CELL_H, CELL_W, CELL_H)
    assert all(grid.rect(n)[2:] == grid.cell for n in range(len(grid)))


def test_every_cell_lies_inside_the_image(grid: Atlas) -> None:
    width, height = grid.buffer.size
    for n in range(len(grid)):
        r = grid.rect(n)
        assert 0 <= r.x and r.x + r.width <= width
        assert 0 <= r.y and r.y + r.height <= height


def test_the_cells_tile_the_image_exactly(grid: Atlas) -> None:
    """No overlap and no gap -- the rects cover every pixel exactly once."""
    width, height = grid.buffer.size
    covered = np.zeros((height, width), dtype=np.int32)
    for n in range(len(grid)):
        r = grid.rect(n)
        covered[r.y : r.y + r.height, r.x : r.x + r.width] += 1
    assert np.all(covered == 1)


def test_negative_indices_count_from_the_end_like_getitem(grid: Atlas) -> None:
    count = len(grid)
    for n in (-1, -2, -count):
        assert grid.rect(n) == grid.rect(n + count)
        assert np.array_equal(grid[n], grid[n + count])


@pytest.mark.parametrize("index", [35, 36, -36, 100, -100])
def test_out_of_range_raises_the_same_way_as_getitem(
    grid: Atlas, index: int
) -> None:
    """Both accessors reject the same indices, so a cell that can be read
    can always be located and vice versa."""
    with pytest.raises(IndexError) as from_rect:
        grid.rect(index)
    with pytest.raises(IndexError) as from_getitem:
        grid[index]
    assert str(from_rect.value) == str(from_getitem.value)


def test_a_single_cell_atlas_is_the_whole_image() -> None:
    """The degenerate grid: one cell, and it is the picture."""
    buffer = PixelBuffer((5, 9), "P")
    one = Atlas(buffer, 5, 9)
    assert len(one) == 1
    assert one.rect(0) == Rect(0, 0, 5, 9)


def test_rect_does_not_read_pixels() -> None:
    """The rect table is fixed at construction and owes nothing to the
    contents of the image, so repainting the sheet moves no rect.

    Worth pinning because the tilemap asks `rect()` per stamp inside a refill
    loop, on a sheet a game may be editing.
    """
    buffer = PixelBuffer((32, 32), "P")
    sixteens = Atlas(buffer, 16, 16)
    before = [sixteens.rect(n) for n in range(len(sixteens))]
    with buffer.edit() as pixels:
        pixels[:] = 7
    assert [sixteens.rect(n) for n in range(len(sixteens))] == before


# -- rects given outright ---------------------------------------------

# Deliberately overlapping, abutting and gapped -- an authored package's rects
# owe nothing to a grid, and the atlas takes them as given.
RECTS = {
    "blue": Rect(0, 0, 8, 8),
    "pink": Rect(8, 0, 8, 8),
    "wide": Rect(0, 8, 16, 4),
    "overlaps-wide": Rect(2, 9, 3, 2),
}


@pytest.fixture
def sheet() -> PixelBuffer:
    """A 16x16 canvas where every pixel encodes its own position."""
    buffer = PixelBuffer((16, 16), "P")
    with buffer.edit() as pixels:
        pixels[:] = np.arange(16 * 16, dtype=np.uint8).reshape(16, 16)
    return buffer


@pytest.fixture
def named(sheet: PixelBuffer) -> Atlas:
    return Atlas(sheet, RECTS)


def test_gridded_says_whether_an_autogrid_made_the_rects(
    named: Atlas, sheet: PixelBuffer
) -> None:
    assert named.gridded is False
    assert Atlas(sheet, 8, 8).gridded is True


def test_len_counts_cuts_either_way(named: Atlas, sheet: PixelBuffer) -> None:
    """`len()` is NOT a grid-only member: it means the same thing on both."""
    assert len(named) == len(RECTS) == 4
    assert len(Atlas(sheet, 8, 8)) == 4


def test_rect_returns_the_cut_it_was_given(named: Atlas) -> None:
    for name, rect in RECTS.items():
        assert named.rect(name) == rect


def test_getitem_returns_the_pixels_rect_points_at(
    named: Atlas, sheet: PixelBuffer
) -> None:
    """The same agreement the grid cutting is held to: slicing the image by
    `rect(key)` gives `atlas[key]`."""
    pixels = sheet.pixels
    for name in RECTS:
        r = named.rect(name)
        region = pixels[r.y : r.y + r.height, r.x : r.x + r.width]
        assert np.array_equal(region, named[name])
        assert named[name].shape == (r.height, r.width)


def test_cuts_may_overlap(named: Atlas, sheet: PixelBuffer) -> None:
    """Nothing about a given rect table implies a partition of the image."""
    inner = named["overlaps-wide"]
    outer = named["wide"]
    assert np.array_equal(inner, outer[1:3, 2:5])


def test_getitem_is_a_view_into_the_image(named: Atlas, sheet: PixelBuffer) -> None:
    with sheet.edit() as pixels:
        pixels[0:8, 0:8] = 99
    assert np.all(named["blue"] == 99)
    assert not np.all(named["pink"] == 99)


def test_unknown_cut_raises_naming_the_vocabulary(named: Atlas) -> None:
    with pytest.raises(KeyError) as excinfo:
        named.rect("nope")
    message = str(excinfo.value)
    assert "nope" in message
    assert "blue" in message  # it says what IS there


def test_contains_is_the_loaders_question(named: Atlas, sheet: PixelBuffer) -> None:
    assert "blue" in named
    assert "nope" not in named
    grid = Atlas(sheet, 8, 8)
    assert 0 in grid and 3 in grid
    assert 4 not in grid
    assert "blue" not in grid


def test_keys_preserve_the_authors_order(named: Atlas, sheet: PixelBuffer) -> None:
    """A package is an authored file; its order is the author's."""
    assert list(named.keys()) == list(RECTS)
    assert list(Atlas(sheet, 8, 8).keys()) == [0, 1, 2, 3]


def test_iter_yields_pixels_in_key_order(named: Atlas) -> None:
    assert [c.shape for c in named] == [
        (r.height, r.width) for r in RECTS.values()
    ]


@pytest.mark.parametrize("member", ["cell", "cols", "rows", "cells"])
def test_autogrid_parameters_refuse_and_explain(named: Atlas, member: str) -> None:
    """Not a mode check -- the rects are the same rects. There is simply no
    honest number to return for a generator this atlas never used."""
    with pytest.raises(TypeError) as excinfo:
        getattr(named, member)
    message = str(excinfo.value)
    assert member in message
    assert "autogrid" in message
    assert "given" in message


@pytest.mark.parametrize(
    "bad, why",
    [
        ({"x": Rect(0, 0, 0, 8)}, "non-positive"),
        ({"x": Rect(0, 0, 8, -1)}, "non-positive"),
        ({"x": Rect(-1, 0, 8, 8)}, "negative origin"),
        ({"x": Rect(10, 0, 8, 8)}, "runs outside"),
        ({"x": Rect(0, 10, 8, 8)}, "runs outside"),
    ],
)
def test_a_rect_that_will_not_fit_is_refused_at_construction(
    sheet: PixelBuffer, bad: dict, why: str
) -> None:
    """Not caught later: a rect running off the edge is a bad package, and
    the alternative is a NumPy slice quietly returning fewer rows than the
    rect said."""
    with pytest.raises(ValueError) as excinfo:
        Atlas(sheet, bad)
    assert why in str(excinfo.value)
    assert "'x'" in str(excinfo.value)


def test_an_empty_table_is_refused(sheet: PixelBuffer) -> None:
    with pytest.raises(ValueError, match="at least one rect"):
        Atlas(sheet, {})


def test_the_two_roads_cannot_be_mixed(sheet: PixelBuffer) -> None:
    with pytest.raises(TypeError, match="not both"):
        Atlas(sheet, RECTS, 8)  # pyright: ignore[reportArgumentType]
    with pytest.raises(TypeError, match="cell height"):
        Atlas(sheet, 8)  # pyright: ignore[reportCallIssue]


def test_the_image_is_asked_of_the_buffer_and_not_the_atlas(
    sheet: PixelBuffer,
) -> None:
    """An atlas answers about the CUTTING; the image answers about itself.

    `buffer` is public and one word away, so proxying `size`, `mode`,
    `indexed` and `palette` onto the atlas would be a second surface saying
    what this one already says. Pinned, because a proxy is the kind of
    convenience that grows back.
    """
    named, grid = Atlas(sheet, RECTS), Atlas(sheet, 8, 8)
    for atlas in (named, grid):
        assert atlas.buffer is sheet
        for proxied in ("size", "mode", "indexed", "palette"):
            assert not hasattr(atlas, proxied), (
                f"Atlas.{proxied} is the buffer's question: "
                f"atlas.buffer.{proxied}"
            )


def test_repr_says_how_the_table_was_filled(
    named: Atlas, sheet: PixelBuffer
) -> None:
    assert "rects given" in repr(named)
    assert "cells" in repr(Atlas(sheet, 8, 8))
