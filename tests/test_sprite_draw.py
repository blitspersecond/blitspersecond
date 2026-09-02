"""The sprite draw, against real pixels.

Everything below this point in the pillar was provable without a window: the
loader, the slot arrays, the chains, the frame plan. The draw is not, and
"the numbers look right" is exactly the claim that has always been made about
renderers that showed nothing.

So these open a real GL context, composite, and read the framebuffer back.
They are skipped where there is no context to make.

Note the projection matches the compositor's -- `ortho(0, w, 0, h)` -- which
puts y=0 at the BOTTOM. The present pass owns the flip that makes a layer
placed at y=0 appear at the top of the screen, and it is not in this pass.
"""

from __future__ import annotations

import numpy as np
import pytest

from blitspersecond.common import PACKAGE_ROOT
from blitspersecond.graphics.sprite import Sprite, SpriteSheet

ASSETS = PACKAGE_ROOT.parent / "assets"
SIZE = 64

pyglet = pytest.importorskip("pyglet")


@pytest.fixture(scope="module")
def window():
    """One invisible window for the module -- contexts are expensive and the
    tests do not disturb each other's state."""
    from pyglet.math import Mat4

    try:
        win = pyglet.window.Window(width=SIZE, height=SIZE, visible=False)
    except Exception as exc:  # pragma: no cover - depends on the machine
        pytest.skip(f"no GL context available: {exc}")
    win.switch_to()
    win.projection = Mat4.orthogonal_projection(0, SIZE, 0, SIZE, -1, 1)
    yield win
    win.close()


def _read(window) -> np.ndarray:
    """The framebuffer as (h, w, 4) uint8, row 0 at the BOTTOM."""
    from pyglet.gl import GL_RGBA, GL_UNSIGNED_BYTE, glReadPixels

    buffer = (pyglet.gl.GLubyte * (SIZE * SIZE * 4))()
    glReadPixels(0, 0, SIZE, SIZE, GL_RGBA, GL_UNSIGNED_BYTE, buffer)
    return np.frombuffer(buffer, dtype=np.uint8).reshape(SIZE, SIZE, 4)


def _compose(window, sprite: Sprite) -> np.ndarray:
    from pyglet.gl import (
        GL_BLEND,
        GL_COLOR_BUFFER_BIT,
        GL_ONE_MINUS_SRC_ALPHA,
        GL_SRC_ALPHA,
        glBlendFunc,
        glClear,
        glClearColor,
        glDisable,
        glEnable,
        glViewport,
    )

    window.switch_to()
    glViewport(0, 0, SIZE, SIZE)
    glClearColor(0.0, 0.0, 0.0, 1.0)
    glClear(GL_COLOR_BUFFER_BIT)
    # Set HERE and not by the layer, exactly as the compositor does it.
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    sprite._composite()
    glDisable(GL_BLEND)
    return _read(window)


@pytest.fixture
def bullets() -> Sprite:
    return Sprite(SpriteSheet(str(ASSETS / "bullets" / "bullets.sprite.json")))


def _lit(pixels: np.ndarray) -> np.ndarray:
    """Where anything was drawn: rows/cols with a non-black pixel."""
    return pixels[:, :, :3].any(axis=2)


def test_a_sprite_actually_draws(window, bullets: Sprite) -> None:
    """The claim the whole pillar has been making without proof."""
    bullets.assembly = "blue"
    bullets.position = (32, 32)
    pixels = _compose(window, bullets)
    assert _lit(pixels).any(), "nothing was drawn"


def test_it_draws_the_right_pixels_in_the_right_place(
    window, bullets: Sprite
) -> None:
    """Stronger than a bounding box: the drawn region is compared to the
    SOURCE art, pixel for pixel.

    The bullet is a circle inside its 8x8 cell, so a bounding-box check would
    pass on art shifted by a texel or sampled with the corners permuted
    wrongly. Comparing the shape catches both -- it is the one test that
    proves the UVs as well as the geometry.

    The package anchors the art at (-4, -4), so a sprite at (32, 32) covers
    28..35: position means the art's centre because the package says so, not
    because the engine assumed it.
    """
    bullets.assembly = "blue"
    bullets.position = (32, 32)
    lit = _lit(_compose(window, bullets))
    drawn = lit[28:36, 28:36]

    tileset = bullets.sheet.tilesets["bullets"]
    part = bullets.sheet.assemblies["blue"].parts[0]
    rect = tileset.atlas.rect(part.tile)
    source = tileset.atlas.buffer.pixels[
        rect.y : rect.y + rect.height, rect.x : rect.x + rect.width
    ]
    # Not flipped: this pass draws the art bottom-up into a framebuffer whose
    # row 0 is the bottom, so the two orders cancel and readback row r is
    # source row r. See `test_each_palette_register_draws_its_own_row`, whose
    # art is asymmetric enough to tell the difference -- this circle is not.
    assert np.array_equal(drawn, source != 0)
    assert not lit[:28].any() and not lit[36:].any()


def test_moving_it_moves_the_pixels(window, bullets: Sprite) -> None:
    """Ties the position surface to the framebuffer, so a plan that staged
    correct numbers into the wrong place cannot pass."""
    bullets.assembly = "blue"
    bullets.position = (20, 32)
    left = np.flatnonzero(_lit(_compose(window, bullets)).any(axis=0))
    bullets.position = (44, 32)
    right = np.flatnonzero(_lit(_compose(window, bullets)).any(axis=0))
    assert int(right.min()) - int(left.min()) == 24


def test_two_presentations_both_draw(window, bullets: Sprite) -> None:
    """One sprite, two drawings of it, one draw call -- the pillar's whole
    premise, checked where it can actually be seen."""
    ghost = bullets.before.append()
    ghost.assembly = "blue"
    ghost.position = (16, 32)
    ghost.visible = True
    bullets.assembly = "blue"
    bullets.position = (48, 32)
    lit = _lit(_compose(window, bullets))
    cols = np.flatnonzero(lit.any(axis=0))
    # The art is a circle inset one pixel inside its 8x8 cell, so a ghost
    # centred on 16 lights 13..18 and a primary on 48 lights 45..50.
    assert int(cols.min()) == 13 and int(cols.max()) == 50
    assert len(bullets._plan.runs) == 1  # and it took ONE run to do it


def test_a_parked_presentation_draws_nothing(window, bullets: Sprite) -> None:
    ghost = bullets.before.append()
    ghost.assembly = "blue"
    ghost.position = (16, 32)
    ghost.visible = False
    bullets.assembly = "blue"
    bullets.position = (48, 32)
    cols = np.flatnonzero(_lit(_compose(window, bullets)).any(axis=0))
    assert int(cols.min()) == 45  # the primary alone, art inset by one


def test_alpha_zero_draws_nothing_visible(window, bullets: Sprite) -> None:
    """The colour registers reach the GPU. `visible` is not the only way for
    a presentation to contribute nothing, and this is the one that still
    costs a quad -- so it proves the attribute arrived rather than that the
    plan skipped it."""
    bullets.assembly = "blue"
    bullets.position = (32, 32)
    bullets.alpha = 0.0
    pixels = _compose(window, bullets)
    assert bullets._plan.quads == 1
    assert not _lit(pixels).any()


def test_a_sprite_presenting_nothing_is_a_clear_screen(
    window, bullets: Sprite
) -> None:
    bullets.assembly = None
    assert not _lit(_compose(window, bullets)).any()


# -- the palette bank ---------------------------------------------------
#
# fumiko rather than the bullets: the bullet sheet carries one palette, so
# there is no row to pick wrong. Hers are four, authored in one package from
# one document, which is exactly the case the bank exists for.


@pytest.fixture
def fumiko() -> Sprite:
    return Sprite(SpriteSheet(str(ASSETS / "fumiko" / "fumiko.sprite.json")))


def _resolved(bank, row: int, indices: np.ndarray) -> np.ndarray:
    """The art coloured by hand: source indices through one row of a bank."""
    table = np.array([bank[row][i][:3] for i in range(256)], dtype=np.uint8)
    return table[indices]


def test_a_bank_is_one_texture_with_a_row_each(window, fumiko: Sprite) -> None:
    """The shape of the thing, before what it draws: four palettes are four
    ROWS of one 256x4 table, not four textures."""
    from blitspersecond.graphics.internal import PaletteTexture

    window.switch_to()
    bank = fumiko.sheet.tilesets["character"].palettes
    assert len(bank) == 4
    table = PaletteTexture.get(bank)
    assert (table.width, table.height) == (256, 4)
    assert PaletteTexture.get(bank) is table  # memoised on the bank


def test_each_palette_register_draws_its_own_row(window, fumiko: Sprite) -> None:
    """The card's whole claim, against real pixels: the register picks a row
    of one table and the art comes back in that row's colours.

    Every drawn pixel is compared to the source index resolved through the
    palette by hand, for all four -- so a row selected off by one, or an
    upload that stacked the bank upside down, fails here rather than looking
    plausible.
    """
    tileset = fumiko.sheet.tilesets["character"]
    rect = tileset.atlas.rect("idle-0")
    source = tileset.atlas.buffer.pixels[
        rect.y : rect.y + rect.height, rect.x : rect.x + rect.width
    ]
    opaque = source != 0
    fumiko.assembly = "idle-0"
    fumiko.position = (16, 16)

    seen = []
    for row in range(len(tileset.palettes)):
        fumiko.palettes["character"] = row
        pixels = _compose(window, fumiko)
        # No flip. Row 0 of a readback is the bottom of the framebuffer, and
        # this pass draws the art bottom-up into it -- the present pass owns
        # the flip that turns that the right way round, and is not here. So
        # readback row r IS source row r, which asymmetric art proves and the
        # bullet's symmetric circle cannot.
        drawn = pixels[16 : 16 + rect.height, 16 : 16 + rect.width, :3]
        assert np.array_equal(
            drawn[opaque], _resolved(tileset.palettes, row, source)[opaque]
        )
        seen.append(drawn[opaque].copy())

    # And the four are genuinely four pictures. fumiko's tables differ in the
    # skin and hair ramps, so a shader that ignored the row and read row 0
    # four times would pass every comparison above only if they were equal.
    assert not any(np.array_equal(seen[0], other) for other in seen[1:])


def test_a_costume_swap_is_a_row_not_a_bind(window, fumiko: Sprite) -> None:
    """Two presentations of one tileset on different registers still cut a
    run -- the row is per draw, not yet per quad -- but they cut it onto ONE
    bank, which is the bind this card was about."""
    ghost = fumiko.before.append()
    ghost.assembly = "idle-0"
    ghost.position = (0, 16)
    ghost.visible = True
    ghost.palettes["character"] = 1
    fumiko.assembly = "idle-0"
    fumiko.position = (36, 16)
    fumiko.palettes["character"] = 3
    fumiko._sync()

    runs = fumiko._plan.runs
    assert len(runs) == 2
    assert runs[0][1] is runs[1][1]  # one bank
    assert (runs[0][2], runs[1][2]) == (1, 3)  # two rows

    # Both drew, and in different colours: the row survives the run loop, not
    # just the plan.
    pixels = _compose(window, fumiko)
    left = pixels[16:48, 0:24, :3]
    right = pixels[16:48, 36:60, :3]
    assert _lit(left).any() and _lit(right).any()
    assert not np.array_equal(left, right)
