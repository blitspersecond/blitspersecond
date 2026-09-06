"""One fragment stage, three pillars, drawn for real.

`indexed.frag` is shared by the sprite, tilemap and console pillars, and each
pairs it with a vertex stage of its own. The sprite pillar has had pixel
tests since it landed; the other two had none, so a change to the shared
stage -- the palette row varying that made a tileset's bank ONE 256xN table
-- could break either of them with the whole suite still green.

These are deliberately thin. They prove the program links, the units are
bound the way each pillar thinks they are, and the palette lookup resolves an
index to the colour the table holds. What the console composes and where the
tilemap scrolls are answered by their own GL-free tests.
"""

from __future__ import annotations

import numpy as np
import pytest

SIZE = 64

pyglet = pytest.importorskip("pyglet")


@pytest.fixture(scope="module")
def window():
    """One invisible window for the module, as `test_sprite_draw` does."""
    from pyglet.math import Mat4

    try:
        win = pyglet.window.Window(width=SIZE, height=SIZE, visible=False)
    except Exception as exc:  # pragma: no cover - depends on the machine
        pytest.skip(f"no GL context available: {exc}")
    win.switch_to()
    win.projection = Mat4.orthogonal_projection(0, SIZE, 0, SIZE, -1, 1)
    yield win
    win.close()


def _compose(window, layer) -> np.ndarray:
    """Composite one layer over black and read the framebuffer back as
    (h, w, 4) uint8, row 0 at the bottom. Blend is set HERE and not by the
    layer, exactly as the compositor does it."""
    from pyglet.gl import (
        GL_BLEND,
        GL_COLOR_BUFFER_BIT,
        GL_ONE_MINUS_SRC_ALPHA,
        GL_RGBA,
        GL_SRC_ALPHA,
        GL_UNSIGNED_BYTE,
        glBlendFunc,
        glClear,
        glClearColor,
        glDisable,
        glEnable,
        glReadPixels,
        glViewport,
    )

    window.switch_to()
    glViewport(0, 0, SIZE, SIZE)
    glClearColor(0.0, 0.0, 0.0, 1.0)
    glClear(GL_COLOR_BUFFER_BIT)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    layer._composite()
    glDisable(GL_BLEND)
    buffer = (pyglet.gl.GLubyte * (SIZE * SIZE * 4))()
    glReadPixels(0, 0, SIZE, SIZE, GL_RGBA, GL_UNSIGNED_BYTE, buffer)
    return np.frombuffer(buffer, dtype=np.uint8).reshape(SIZE, SIZE, 4)


def _colours(pixels: np.ndarray) -> set:
    """Every non-black colour on screen. Black is the clear, and index 0 is
    the hole, so what is left is what the palette resolved."""
    flat = pixels[:, :, :3].reshape(-1, 3)
    return {tuple(int(c) for c in row) for row in flat if row.any()}


def test_a_tilemap_draws_through_its_palette(window) -> None:
    """A tile layer's sheet is indices and its colour comes from unit 1. One
    sheet means one table and row 0, which is the case the shared stage must
    keep answering now that the row is a varying."""
    from blitspersecond.graphics import Atlas, PixelBuffer, TileMap

    sheet = PixelBuffer((16, 16), "P")
    sheet.pixels[:8, :8] = 1  # tile 0
    sheet.pixels[8:, 8:] = 2  # tile 3
    palette = sheet.palette
    assert palette is not None
    palette[1] = (255, 0, 0)
    palette[2] = (0, 255, 0)

    tilemap = TileMap(Atlas(sheet, 8, 8), 4, 4)
    tilemap.cell[:, :] = tilemap.tile[0]
    tilemap.cell[0, 0] = tilemap.tile[3]

    seen = _colours(_compose(window, tilemap))
    assert seen == {(255, 0, 0), (0, 255, 0)}


def test_a_console_draws_through_its_palette(window) -> None:
    """The console composes one indexed buffer and draws it as one quad, so
    what this proves is `picture.vert` + `indexed.frag`: the pen's colour is
    an INDEX and it must come back as the palette's red."""
    from blitspersecond.graphics import RED
    from blitspersecond.graphics.console import Console

    console = Console()
    console.pen[1] = RED
    console.print("HELLO")

    pixels = _compose(window, console)
    assert pixels[:, :, :3].any(), "the console drew nothing"
    assert _colours(pixels) == {(255, 0, 0)}


def test_a_recoloured_palette_reaches_the_screen(window) -> None:
    """The upload is version-gated, and a table that is a ROW of a bank is
    still gated on its own counter. Writing an entry between two composes
    must repaint what is already on screen."""
    from blitspersecond.graphics import Atlas, PixelBuffer, TileMap

    sheet = PixelBuffer((8, 8), "P")
    sheet.pixels[:, :] = 1
    palette = sheet.palette
    assert palette is not None
    palette[1] = (255, 0, 0)

    tilemap = TileMap(Atlas(sheet, 8, 8), 2, 2)
    tilemap.cell[:, :] = tilemap.tile[0]
    assert _colours(_compose(window, tilemap)) == {(255, 0, 0)}

    palette[1] = (0, 0, 255)
    assert _colours(_compose(window, tilemap)) == {(0, 0, 255)}
