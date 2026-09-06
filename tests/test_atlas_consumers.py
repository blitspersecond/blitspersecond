"""The pillars that need an AUTOGRID say so, at construction.

An atlas is a table of rects, and a cell size is a generator that fills it.
Two of the three pillars need the generator's parameters back:

  * a `TileMap`'s world cell size IS its sheet's cell size -- the camera, the
    bounds and every placement are measured in it;
  * a `Console` measures its screen in glyphs and packs a glyph id into a
    byte, so its cells are one size by definition.

Neither can work over rects that have no common size. Before these guards
both accepted such an atlas and then failed several frames of setup later,
with `Atlas`'s message about autogrid parameters rather than the pillar's own
reason -- which is the failure class the tree calls the worst available: the
thing that goes wrong is not the thing that gets reported.

The sprite pillar is the one that takes either, which is the whole point of
the unification, and is tested with the sheet when it lands.
"""

import pytest

from blitspersecond.common import Rect
from blitspersecond.graphics import Atlas, PixelBuffer, TileMap
from blitspersecond.graphics.console import CharSet, Console

GLYPHS = 256


@pytest.fixture
def buffer() -> PixelBuffer:
    return PixelBuffer((128, 128), "P")


@pytest.fixture
def given(buffer: PixelBuffer) -> Atlas:
    """Enough rects that a count check cannot be what refuses it."""
    return Atlas(buffer, {f"r{i}": Rect(0, 0, 8, 8) for i in range(GLYPHS)})


def test_tilemap_refuses_rects_given_outright(given: Atlas) -> None:
    with pytest.raises(ValueError) as excinfo:
        TileMap(given, 4, 4)
    message = str(excinfo.value)
    assert "autogrid" in message
    # It gives the pillar's OWN reason, not the atlas's.
    assert "world cell size" in message
    assert "Atlas(buffer, w, h)" in message


def test_console_refuses_rects_given_outright(given: Atlas) -> None:
    with pytest.raises(ValueError) as excinfo:
        Console(given)
    message = str(excinfo.value)
    assert "autogrid" in message
    assert "glyph" in message
    assert "Atlas(buffer, w, h)" in message


def test_the_refusal_beats_the_cell_read(given: Atlas) -> None:
    """Console reads `charset.cell` while resolving its arguments, which is
    itself one of the things a given-rects atlas cannot answer. The guard has
    to run first or the console never gets to state its own standard."""
    with pytest.raises(ValueError, match="glyph sheet"):
        Console(given)


def test_tilemap_still_takes_an_autogrid(buffer: PixelBuffer) -> None:
    tilemap = TileMap(Atlas(buffer, 16, 16), 4, 4)
    assert tilemap.tileset.gridded
    assert tuple(tilemap.tile_size) == (16, 16)


def test_console_still_takes_an_autogrid_and_a_charset(
    buffer: PixelBuffer,
) -> None:
    assert Console(Atlas(buffer, 8, 8)).size is not None  # 256 cells exactly
    assert Console(CharSet.ASCII8X12).size is not None


def test_a_shipped_charset_is_an_autogrid() -> None:
    """The road every default console takes -- pinned because the guard would
    otherwise be one edit away from refusing the fonts BPS ships."""
    for charset in CharSet:
        assert charset.atlas.gridded


def test_an_undersized_autogrid_still_fails_on_the_count(
    buffer: PixelBuffer,
) -> None:
    """The guard is additional, not a replacement: a real grid with too few
    cells is refused for the reason it always was."""
    with pytest.raises(ValueError, match=f"{GLYPHS} glyphs"):
        Console(Atlas(buffer, 32, 32))  # 16 cells
