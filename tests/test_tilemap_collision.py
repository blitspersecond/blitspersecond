"""The tilemap's mask wired to the tilemap: stamping, scrolling, the switch.

`test_tilemap_collision_mask` proves the mask on its own, with worlds built by
hand. This proves the *wiring* -- that a `TileMap` fills one, moves one, and
reports where it is looking into one -- and it does that against GL, because
the claim worth proving is not that the numbers are self-consistent but that
**the mask and the picture are the same shape in the same place**. So most of
what is here composites the layer for real and compares the framebuffer with
`collider().unpacked`, pixel for pixel, over a camera path.

That is the test the shared `_fine` exists for. Drawing and collision each
take the camera's sub-cell remainder from one property, and a pixel of
disagreement between them would show up here as a one-pixel halo.
"""

from __future__ import annotations

import numpy as np
import pytest

pyglet = pytest.importorskip("pyglet")

from blitspersecond.common import SCREEN  # noqa: E402

CELL = 8
COLS, ROWS = 100, 60


@pytest.fixture(scope="module")
def window():
    """One invisible window the size of the screen, so a readback IS the
    screen and no scaling sits between the picture and the mask."""
    from pyglet.math import Mat4

    try:
        win = pyglet.window.Window(
            width=SCREEN.width, height=SCREEN.height, visible=False
        )
    except Exception as exc:  # pragma: no cover - depends on the machine
        pytest.skip(f"no GL context available: {exc}")
    win.switch_to()
    win.projection = Mat4.orthogonal_projection(
        0, SCREEN.width, 0, SCREEN.height, -1, 1
    )
    yield win
    win.close()


def _sheet():
    """Four 8x8 tiles: a blob, a solid, a ring, and one that is all holes.

    The empty tile is the point of the set. It is a placement like any other
    -- a quad goes to the GPU for it -- and it must contribute nothing to the
    mask, because solidity comes from the pixels and not from a cell being
    occupied.
    """
    from blitspersecond.graphics import Atlas, PixelBuffer

    buffer = PixelBuffer((CELL * 2, CELL * 2), "P")
    palette = buffer.palette
    assert palette is not None
    palette[1] = (255, 0, 0)
    palette[2] = (0, 255, 0)
    palette[3] = (0, 0, 255)
    with buffer.edit() as pixels:
        rng = np.random.default_rng(11)
        # tile 0: a ragged blob, so an off-by-one in either direction shows
        pixels[:CELL, :CELL] = (
            rng.random((CELL, CELL)) > 0.45
        ).astype(np.uint8)
        # tile 1: solid
        pixels[:CELL, CELL:] = 2
        # tile 2: a ring -- a hole in the MIDDLE, which no edge test catches
        pixels[CELL:, :CELL] = 3
        pixels[CELL + 2 : CELL + 6, 2:6] = 0
        # tile 3: all holes
        pixels[CELL:, CELL:] = 0
    return Atlas(buffer, CELL, CELL)


def _map(collidable: bool = True):
    """A map of random tiles and orientations, filled through `cell`.

    The buffer comes off `cell[:, :]`, which is the sanctioned way to build
    one: a fresh map hands back a correctly shaped, all-`EMPTY_TILE` array
    to fill and write back in a single block.
    """
    from blitspersecond.graphics import TileMap

    tilemap = TileMap(_sheet(), COLS, ROWS, collidable=collidable)
    rng = np.random.default_rng(5)
    world = tilemap.cell[:, :]
    world["tile"] = rng.integers(-1, 4, size=(COLS, ROWS))
    world["flags"] = rng.integers(0, 8, size=(COLS, ROWS))
    tilemap.cell[:, :] = world
    return tilemap


def _drawn(window, layer) -> np.ndarray:
    """Composite `layer` over black and return the (360, 640) bool silhouette.

    Row 0 is the quad's y=0: this harness projects y upward and reads back
    bottom row first, so the two cancel and a placement at y=0 lands in row 0
    -- the same row `unpacked` calls 0. The present pass's flip is not in play
    here and would only move both together.
    """
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
    glViewport(0, 0, SCREEN.width, SCREEN.height)
    glClearColor(0.0, 0.0, 0.0, 1.0)
    glClear(GL_COLOR_BUFFER_BIT)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    layer._composite()
    glDisable(GL_BLEND)
    raw = (pyglet.gl.GLubyte * (SCREEN.width * SCREEN.height * 4))()
    glReadPixels(
        0,
        0,
        SCREEN.width,
        SCREEN.height,
        GL_RGBA,
        GL_UNSIGNED_BYTE,
        raw,
    )
    pixels = np.frombuffer(raw, dtype=np.uint8).reshape(
        SCREEN.height, SCREEN.width, 4
    )
    # No palette entry is black, so anything that is not the clear was drawn.
    return pixels[:, :, :3].any(axis=2)


def _disagreement(window, layer) -> int:
    return int(np.count_nonzero(_drawn(window, layer) ^ layer.collider().unpacked))


# -- the picture and the mask ---------------------------------------------


def test_a_stamped_map_collides_where_it_draws(window) -> None:
    tilemap = _map()
    assert _disagreement(window, tilemap) == 0


@pytest.mark.parametrize(
    "scroll",
    [
        (0, 0),
        (1, 0),  # fine only: the mask must not move at all
        (7, 0),  # the last pixel before a coarse step
        (8, 0),  # exactly one cell
        (0, 5),
        (0, 8),
        (13, 21),  # both axes, both parts
        (8 * 20, 8 * 7),  # cell-aligned, well into the world
        (8 * 20 + 3, 8 * 7 + 6),
        (31, 0),  # the word boundary the store's drift is measured against
        (32, 0),
        (33, 0),
        (8 * 61 + 5, 8 * 15 + 2),  # past the right edge of the world
    ],
)
def test_the_mask_follows_the_camera(window, scroll) -> None:
    tilemap = _map()
    tilemap.scroll = scroll
    assert _disagreement(window, tilemap) == 0


def test_the_mask_survives_a_camera_path(window) -> None:
    """The interesting failures are cumulative, not positional.

    Drift accumulates, whole words are absorbed, rows slide, placements are
    culled and refilled -- and every one of those is a chance to lose a bit
    or leave a stale one behind. So this walks a path rather than teleporting:
    forward in fine steps through several word boundaries, back again past
    where it started, and diagonally. Each stop must still draw what it
    collides with.
    """
    tilemap = _map()
    x = y = 0.0
    for step, (dx, dy) in enumerate(
        [(3, 0)] * 24 + [(-5, 2)] * 20 + [(0, -7)] * 12 + [(11, 11)] * 16
    ):
        x, y = x + dx, y + dy
        tilemap.scroll = (x, y)
        assert _disagreement(window, tilemap) == 0, f"step {step} at {(x, y)}"


def test_a_fractional_camera_is_floored_the_same_way_by_both(window) -> None:
    """`scroll` is free to be fractional and nothing drawn ever is. The
    picture floors the remainder to whole pixels; the mask reads the same
    floor out of the same property."""
    tilemap = _map()
    for x in (0.4, 3.5, 7.9, 8.1, 15.6):
        tilemap.scroll = (x, 2.7)
        assert _disagreement(window, tilemap) == 0, x


# -- the switch -----------------------------------------------------------


def test_an_uncollidable_map_builds_no_mask(window) -> None:
    """Off is free, not merely refused. The render skips the plane entirely --
    nothing stamped, nothing allocated -- which is what makes `collidable`
    worth being a switch and not just a capability."""
    tilemap = _map(collidable=False)
    _drawn(window, tilemap)
    assert tilemap._collision is None
    with pytest.raises(ValueError, match="collidable=False"):
        tilemap.collider()


def test_switching_collision_on_picks_up_what_is_already_stamped(
    window,
) -> None:
    """The rebuild path, and the only one there is. A map painted while it
    was out of collision has placements the mask never saw, and the switch
    must not leave it answering about an empty world."""
    tilemap = _map(collidable=False)
    tilemap.scroll = (8 * 9 + 3, 8 * 4 + 5)
    drawn = _drawn(window, tilemap)
    tilemap.collidable = True
    assert np.count_nonzero(drawn ^ tilemap.collider().unpacked) == 0


def test_the_render_builds_the_mask_and_not_the_question(window) -> None:
    """The engine's cycle is: read input, run the game, render the frame AND
    the masks. So a collidable map leaves the compose with its plane already
    filled, and the tick that asks about it spends nothing building one.

    Doing it the other way round -- lazily, on the first question -- gives the
    same answer and bills it to the game, which is the same mistake
    `layers.prepare` runs before the game's tick to avoid.
    """
    tilemap = _map()
    assert tilemap._collision is None, "constructing a map renders nothing"
    _drawn(window, tilemap)
    assert tilemap._collision is not None
    assert tilemap.collider().unpacked.any()


def test_switching_collision_off_drops_the_mask(window) -> None:
    tilemap = _map()
    _drawn(window, tilemap)
    assert tilemap._collision is not None
    tilemap.collidable = False
    assert tilemap._collision is None
    _drawn(window, tilemap)
    assert tilemap._collision is None, "and the render keeps skipping it"


def test_switching_off_and_on_across_a_scroll_still_agrees(window) -> None:
    """Off for a while is not off for a frame. The camera keeps moving while
    the mask does not exist, so what comes back has to be built at where the
    camera IS -- not where it was when the switch was thrown."""
    tilemap = _map()
    tilemap.scroll = (40, 16)
    _drawn(window, tilemap)
    tilemap.collidable = False
    for x in range(41, 96, 7):
        tilemap.scroll = (x, 16 + x // 3)
        _drawn(window, tilemap)
    tilemap.collidable = True
    assert _disagreement(window, tilemap) == 0


def test_the_switch_still_refuses_an_unpalettised_sheet(window) -> None:
    """The override must not have eaten the base's rule."""
    from blitspersecond.graphics import Atlas, PixelBuffer, TileMap

    rgba = Atlas(PixelBuffer((CELL * 2, CELL * 2), "RGBA"), CELL, CELL)
    tilemap = TileMap(rgba, COLS, ROWS)
    with pytest.raises(ValueError, match="indexed sheet"):
        tilemap.collidable = True
    assert tilemap._collision is None


def test_collidable_is_still_a_boolean(window) -> None:
    tilemap = _map()
    with pytest.raises(ValueError, match="must be a boolean"):
        tilemap.collidable = 1


# -- clear ----------------------------------------------------------------


def test_clear_empties_the_mask_too(window) -> None:
    """`clear` drops the placements, so the bits they wrote have to go with
    them -- and the camera repaints both on the next frame."""
    tilemap = _map()
    _drawn(window, tilemap)
    assert tilemap.collider().unpacked.any()
    tilemap.clear()
    assert not tilemap.collider().unpacked.any()
    assert _disagreement(window, tilemap) == 0


# -- the offset -----------------------------------------------------------


def test_a_fine_scroll_moves_the_offset_and_not_one_bit(window) -> None:
    """The property the whole design turns on. A frame that pans less than a
    cell touches the store not at all -- same buffer, same contents -- and
    moves only the number the query reads through."""
    tilemap = _map()
    _drawn(window, tilemap)
    store = tilemap.collision_plane()
    before = store.copy()
    ox, oy = tilemap.collision_offset

    tilemap.scroll = (3, 2)
    _drawn(window, tilemap)
    assert tilemap.collision_offset == (ox + 3, oy + 2)
    assert tilemap.collision_plane() is store
    assert np.array_equal(store, before)


def test_the_answer_does_not_move_until_a_frame_is_drawn(window) -> None:
    """A collision is a fact about the frame that was PAINTED.

    If a bullet overlapped the ship, it overlapped, and the tick's job is to
    adjudicate that -- so the answer cannot depend on where in the tick it was
    asked. Moving the camera arbitrarily far, in either direction and by
    fractions, must not move the collision window by one pixel: `_coarse` and
    `_fine` are both latched, and `_stream` moves them together at the top of
    the compose.

    Read live off `scroll`, a game that moved the camera and then asked would
    get a different answer from one that asked first, and neither would be
    about a frame that ever existed.
    """
    tilemap = _map()
    tilemap.scroll = (CELL * 2, CELL)
    _drawn(window, tilemap)
    settled = tilemap.collision_offset
    answer = tilemap.collider().unpacked

    for scroll in [(CELL * 2 + 3, CELL), (0.5, 0), (-40.5, 17.5), (900, 700)]:
        tilemap.scroll = scroll
        assert tilemap.collision_offset == settled, scroll
        assert np.array_equal(tilemap.collider().unpacked, answer), scroll


def test_a_coarse_scroll_moves_the_store_and_keeps_the_window(window) -> None:
    """A whole cell is the other half. The store slides, and the offset comes
    back to where it was -- the bits moved instead."""
    tilemap = _map()
    _drawn(window, tilemap)
    ox, oy = tilemap.collision_offset

    tilemap.scroll = (CELL * 4, CELL * 3)
    _drawn(window, tilemap)
    # Vertically the mask moves exactly, so the row offset is unchanged.
    assert tilemap.collision_offset[1] == oy
    # Horizontally it moves in whole words and carries the rest as drift, so
    # 32 pixels of camera have gone into the store and no more.
    assert tilemap.collision_offset[0] == ox + (CELL * 4) % 32
