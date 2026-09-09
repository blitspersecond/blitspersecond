"""Changing a cell that is already on screen: `cell[x, y]` replaces what is
there, in the world and on the screen.

The bug this closes: `_refill` only stamps cells that newly come into view, so
editing a cell already drawn changed nothing -- the quad still pointed at the
old tile and the mask still held the old bits, and the cell corrected itself
only if the camera walked away far enough to cull it. A raw NumPy write cannot
be observed, so the fix was to remove the raw road: `cell` is the only way in,
and it knows what changed because it is what changed it.

**One target holds one tile.** Kris, 2026-09-05: *"no overlays, a tile is a
tile; if you want two things drawn; use a layer."* Tiled agrees -- a TMX tile
layer holds one gid per cell -- so a map that round-trips a `.tmx` cannot
express a stack.

Two claims carry the weight here, and both need the mask AND the picture:

  * **Replacing takes the old bits away.** A mask only ORs, so `clear_rect` is
    the one path in the engine that removes solidity without removing all of
    it. If it were wrong the old tile would stay solid under the new one --
    invisible, and exactly the stale hitbox this is about.
  * **The quad and the mask rect are built by separate arithmetic**, so
    agreeing is not automatic. A replacement that updated one would leave a
    cell you can see but not touch, or touch but not see.

**The straddling-stamp tests are gone with `blit`.** They covered a hand stamp
sitting across four grid cells, which `_restamp_over` existed to put back
after a clear. Every placement is a grid cell now, so placements are disjoint,
the clear can never carve into a neighbour, and both the case and the pass
that defended it were deleted rather than kept for something that cannot
arise.
"""

from __future__ import annotations

import numpy as np
import pytest

pyglet = pytest.importorskip("pyglet")

from blitspersecond.common import SCREEN  # noqa: E402
from blitspersecond.graphics.collision import unpack  # noqa: E402
from blitspersecond.graphics.orientation import Orientation  # noqa: E402

CELL = 8

#: Tile ids in the sheet built below. `SOLID` fills its cell, `HALF` fills the
#: top half only, and `TEST_EMPTY` draws nothing -- so a replacement can be
#: checked by counting rather than by trusting a shape. `TEST_EMPTY` is a real
#: tile whose pixels are all transparent, which is a different thing from the
#: engine's `EMPTY_TILE`: that one is no tile at all and makes no placement.
SOLID, HALF, TEST_EMPTY = 1, 2, 3


@pytest.fixture(scope="module")
def window():
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
    from blitspersecond.graphics import Atlas, PixelBuffer

    buffer = PixelBuffer((CELL * 2, CELL * 2), "P")
    # No palette here any more: a buffer carries pixels, a LAYER carries the
    # colours. Indices 1..3 are white/silver/gray in the bank every map comes
    # up with, which is all this fixture needs -- opaque and distinguishable.
    with buffer.edit() as pixels:
        pixels[:] = 0
        pixels[:CELL, CELL:] = SOLID            # tile 1: the whole cell
        pixels[CELL:, :CELL][: CELL // 2] = HALF  # tile 2: the top half
        pixels[CELL:, CELL:] = 0                # tile 3: nothing at all
    return Atlas(buffer, CELL, CELL)


def _map(collidable: bool = True):
    from blitspersecond.graphics import TileMap

    tilemap = TileMap(_sheet(), 120, 90, collidable=collidable)
    tilemap._stream()
    return tilemap


def _solid(tilemap) -> int:
    return int(unpack(tilemap.collision_plane(), SCREEN.width).sum())


# -- one target holds one tile ---------------------------------------------


def test_writing_the_same_cell_replaces_rather_than_stacks() -> None:
    tilemap = _map()
    tilemap.cell[2, 2] = tilemap.tile[SOLID]
    assert (tilemap._count, _solid(tilemap)) == (1, CELL * CELL)

    tilemap.cell[2, 2] = tilemap.tile[HALF]
    assert (tilemap._count, _solid(tilemap)) == (1, CELL * CELL // 2)


def test_replacing_with_an_empty_tile_removes_the_solidity() -> None:
    """The case a mask that only ORs cannot express, and the reason
    `clear_rect` had to exist."""
    tilemap = _map()
    tilemap.cell[2, 2] = tilemap.tile[SOLID]
    tilemap.cell[2, 2] = tilemap.tile[TEST_EMPTY]
    assert _solid(tilemap) == 0
    assert tilemap._count == 1


def test_a_cell_can_be_changed_back() -> None:
    tilemap = _map()
    for tile, expected in (
        (SOLID, CELL * CELL),
        (TEST_EMPTY, 0),
        (HALF, CELL * CELL // 2),
        (SOLID, CELL * CELL),
    ):
        tilemap.cell[2, 2] = tilemap.tile[tile]
        assert _solid(tilemap) == expected


def test_repeated_writes_do_not_grow_the_placements() -> None:
    """A door opened and shut a thousand times must not leak slots -- the
    old road's only alternative was `clear()`, which repaints the window."""
    tilemap = _map()
    for i in range(200):
        tilemap.cell[2, 2] = tilemap.tile[SOLID if i % 2 else HALF]
    assert tilemap._count == 1


def test_changing_only_the_orientation_replaces_too() -> None:
    """A cell is `(tile, flags)`, and flags is as much of a change as tile:
    a flipped ramp is solid somewhere else."""
    tilemap = _map()
    tilemap.cell[2, 2] = tilemap.tile[HALF]
    top = np.nonzero(unpack(tilemap.collision_plane(), SCREEN.width))[0]
    tilemap.cell[2, 2] = (tilemap.tile[HALF], Orientation.FLIP_V)
    flipped = np.nonzero(unpack(tilemap.collision_plane(), SCREEN.width))[0]
    assert tilemap._count == 1
    assert top.min() < flipped.min()
    assert len(top) == len(flipped)


def test_a_different_cell_still_appends() -> None:
    tilemap = _map()
    tilemap.cell[2, 2] = tilemap.tile[SOLID]
    tilemap.cell[4, 2] = tilemap.tile[SOLID]
    assert tilemap._count == 2
    assert _solid(tilemap) == 2 * CELL * CELL


# -- neighbours ------------------------------------------------------------


def test_an_adjacent_cell_is_untouched() -> None:
    """Touching edges are not an overlap: clearing one cell's rect must leave
    the neighbour whole."""
    tilemap = _map()
    tilemap.cell[2, 2] = tilemap.tile[SOLID]
    tilemap.cell[3, 2] = tilemap.tile[SOLID]
    assert _solid(tilemap) == 2 * CELL * CELL
    tilemap.cell[2, 2] = tilemap.tile[TEST_EMPTY]
    assert _solid(tilemap) == CELL * CELL


# -- the scroll path is untouched -------------------------------------------


def test_refill_still_appends_without_searching() -> None:
    """`_stamp_plane` drives `_stamp` a column at a time, so the replace
    search must not sit on that road -- it would make streaming quadratic.

    Stated as behaviour rather than as a call count: a scrolled-in column of
    identical cells lands as one placement each, which a replace-on-match
    `_stamp` could not do, since they share no target only by virtue of
    position."""
    tilemap = _map()
    tilemap.cell[:, :] = tilemap.tile[SOLID]
    tilemap.clear()
    tilemap._stream()
    before = tilemap._count
    assert before > 100
    tilemap.scroll = (float(CELL * 3), 0.0)
    tilemap._stream()
    assert tilemap._count > before - 100


def test_an_edit_ahead_of_the_camera_arrives_on_its_own() -> None:
    """The bulk road still works, and costs nothing per cell: `_refill` reads
    the world as the camera reaches it."""
    tilemap = _map()
    # Well past the right edge of a 640px viewport, and inside a 120-cell
    # world -- so the camera has somewhere to travel to reach it.
    far = 110
    tilemap.cell[far, 4] = tilemap.tile[SOLID]
    assert _solid(tilemap) == 0
    tilemap.scroll = (float(far * CELL - 320), 0.0)
    tilemap._stream()
    assert _solid(tilemap) == CELL * CELL


# -- the picture and the mask ----------------------------------------------


def _drawn(window, layer) -> np.ndarray:
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
    layer.composite()
    glDisable(GL_BLEND)
    raw = (pyglet.gl.GLubyte * (SCREEN.width * SCREEN.height * 4))()
    glReadPixels(
        0, 0, SCREEN.width, SCREEN.height, GL_RGBA, GL_UNSIGNED_BYTE, raw
    )
    pixels = np.frombuffer(raw, dtype=np.uint8).reshape(
        SCREEN.height, SCREEN.width, 4
    )
    return pixels[:, :, :3].any(axis=2)


def _disagreement(window, layer) -> int:
    return int(
        np.count_nonzero(_drawn(window, layer) ^ layer.collider().unpacked)
    )


def test_a_replaced_cell_draws_what_it_collides_as(window) -> None:
    """**The claim worth proving.** The quad is re-pointed and the mask rect
    is rebuilt by two entirely separate pieces of arithmetic; this is what
    says they agree. A replacement that updated only one of them would leave
    a cell you can see but not touch, or touch but not see."""
    tilemap = _map()
    tilemap.cell[2, 2] = tilemap.tile[SOLID]
    assert _disagreement(window, tilemap) == 0

    tilemap.cell[2, 2] = tilemap.tile[HALF]
    assert _disagreement(window, tilemap) == 0

    tilemap.cell[2, 2] = tilemap.tile[TEST_EMPTY]
    assert _disagreement(window, tilemap) == 0


def test_replacement_agrees_with_the_picture_under_a_fine_scroll(window) -> None:
    """A fine scroll moves no bits -- the mask carries the sub-word remainder
    as drift instead -- so a clear during one is where the two arithmetics are
    most likely to part company."""
    tilemap = _map()
    for offset in (0.0, 3.0, 7.0, 11.0):
        tilemap.scroll = (offset, offset)
        tilemap._stream()
        tilemap.cell[5, 5] = tilemap.tile[SOLID]
        assert _disagreement(window, tilemap) == 0
        tilemap.cell[5, 5] = tilemap.tile[HALF]
        assert _disagreement(window, tilemap) == 0


# -- clear_rect on its own --------------------------------------------------


def _mask_for():
    from blitspersecond.graphics.tilemap.collision import CollisionMask

    return CollisionMask(_sheet())


def test_clear_rect_matches_a_bool_reference_at_every_drift() -> None:
    """Fuzzed across all 32 sub-word camera positions, because the drift is
    the part `stamp` and `clear_rect` have to agree about -- a clear that
    landed a word away from its stamp would be invisible in aggregate counts
    and obvious only here."""
    rng = np.random.default_rng(7)
    for drift in range(32):
        mask = _mask_for()
        mask._drift = drift
        rows, words = mask._apron
        span = mask.mask.shape[1] * 32
        for _ in range(12):
            mask.clear()
            for i in range(0, 200, CELL):
                mask.stamp(SOLID, i, CELL)
            before = unpack(mask.mask, span).copy()

            x = int(rng.integers(-20, 220))
            y = int(rng.integers(-4, 20))
            w = int(rng.integers(1, 70))
            h = int(rng.integers(1, 12))
            mask.clear_rect(x, y, w, h)

            expected = before.copy()
            expected[
                max(y + rows, 0) : max(y + rows + h, 0),
                max(x + drift + words * 32, 0) : max(
                    x + drift + w + words * 32, 0
                ),
            ] = False
            assert np.array_equal(unpack(mask.mask, span), expected), (
                drift,
                (x, y, w, h),
            )


def test_clear_rect_clips_rather_than_dropping() -> None:
    """`stamp` drops what falls outside; this clips. A stamp that does not
    appear leaves a picture that agrees with itself, but a clear that silently
    did nothing leaves bits lit under a cell that no longer holds them."""
    mask = _mask_for()
    mask.stamp(SOLID, 0, 0)
    span = mask.mask.shape[1] * 32
    assert unpack(mask.mask, span).sum() == CELL * CELL
    mask.clear_rect(-4, -4, CELL + 4, CELL + 4)
    assert unpack(mask.mask, span).sum() == 0


def test_clear_rect_of_nothing_is_nothing() -> None:
    mask = _mask_for()
    mask.stamp(SOLID, 0, 0)
    span = mask.mask.shape[1] * 32
    lit = unpack(mask.mask, span).copy()
    for width, height in ((0, 8), (8, 0), (-1, 8), (8, -1)):
        mask.clear_rect(0, 0, width, height)
    assert np.array_equal(unpack(mask.mask, span), lit)


def test_clear_rect_leaves_its_neighbours_alone() -> None:
    """The bit either side of the rect is the one an off-by-one moves."""
    mask = _mask_for()
    span = mask.mask.shape[1] * 32
    for x in (0, CELL, CELL * 2):
        mask.stamp(SOLID, x, 0)
    mask.clear_rect(CELL, 0, CELL, CELL)
    lit = unpack(mask.mask, span)
    rows, words = mask._apron
    base = words * 32
    assert lit[rows : rows + CELL, base : base + CELL].all()
    assert not lit[rows : rows + CELL, base + CELL : base + CELL * 2].any()
    assert lit[rows : rows + CELL, base + CELL * 2 : base + CELL * 3].all()


# -- the accessor's own rules ------------------------------------------------


def test_none_empties_a_cell_and_drops_its_placement() -> None:
    """`None` is the accessor's word for an empty cell, and emptying one takes
    the placement away rather than pointing it at nothing."""
    tilemap = _map()
    tilemap.cell[2, 2] = tilemap.tile[SOLID]
    assert (tilemap._count, _solid(tilemap)) == (1, CELL * CELL)

    tilemap.cell[2, 2] = None
    assert (tilemap._count, _solid(tilemap)) == (0, 0)
    assert tilemap.cell[2, 2] is None


def test_the_accessor_refuses_the_arrays_own_word() -> None:
    """`EMPTY_TILE` is what the ARRAY holds. Surfacing it through the accessor
    would be surfacing a storage constraint as vocabulary, so `None` is the
    only spelling here and the other one is refused by name."""
    from blitspersecond.graphics import EMPTY_TILE

    tilemap = _map()
    with pytest.raises(TypeError) as excinfo:
        tilemap.cell[2, 2] = EMPTY_TILE
    assert "None" in str(excinfo.value)


def test_an_index_is_refused_where_a_tile_is_wanted() -> None:
    tilemap = _map()
    with pytest.raises(TypeError) as excinfo:
        tilemap.cell[2, 2] = SOLID
    assert f"tile[{SOLID}]" in str(excinfo.value)


def test_a_tile_from_another_map_is_refused() -> None:
    """A `Tile` is an owner and an INDEX -- no sheet, no rectangle -- so one
    minted elsewhere is an index into art this map has never seen. Stamping it
    would draw a plausible wrong cell in silence."""
    tilemap = _map()
    stranger = _map()
    with pytest.raises(ValueError) as excinfo:
        tilemap.cell[2, 2] = stranger.tile[SOLID]
    assert "different tilemap" in str(excinfo.value)


def test_orientation_round_trips_through_the_accessor() -> None:
    """A read hands back what a write takes, so a flip survives a copy with no
    special form. The console's `cell` deliberately does the opposite, because
    a facing is the PEN's and an orientation is the CELL's."""
    tilemap = _map()
    tilemap.cell[2, 2] = (tilemap.tile[HALF], Orientation.FLIP_V)
    tilemap.cell[3, 2] = tilemap.cell[2, 2]

    tile, orientation = tilemap.cell[3, 2]
    assert (tile.index, orientation) == (HALF, Orientation.FLIP_V)


def test_a_block_read_is_a_copy_not_a_view() -> None:
    """A view would put the world back in public hands through the side door:
    `cell[0:2, 0:2]["tile"] = n` would reach it with nothing noticing, which
    is the whole hole this accessor closes."""
    tilemap = _map()
    block = tilemap.cell[0:2, 0:2]
    block["tile"] = SOLID
    assert tilemap.cell[0, 0] is None


def test_a_block_write_round_trips_through_a_read() -> None:
    tilemap = _map()
    tilemap.cell[0, 0] = (tilemap.tile[SOLID], Orientation.FLIP_H)
    tilemap.cell[1, 0] = tilemap.tile[HALF]

    tilemap.cell[4:6, 7] = tilemap.cell[0:2, 0]

    assert tilemap.cell[4, 7][0].index == SOLID
    assert tilemap.cell[4, 7][1] == Orientation.FLIP_H
    assert tilemap.cell[5, 7][0].index == HALF


def test_a_block_write_bounds_checks_its_ids_off_screen() -> None:
    """The array road has no `Tile` and so cannot check ownership. It checks
    RANGE, because an id off the end is a certain `IndexError` from the atlas
    LATER -- inside `_refill`, whenever the camera reaches that cell, with
    nothing in the traceback naming the line that wrote it.

    **Off screen deliberately.** An on-screen write stamps immediately, so it
    raises from the atlas whether or not this check exists -- a test there
    passes with the check deleted, which is how this one was found to be
    vacuous. Off screen nothing stamps, so only the check can fire, and that
    is the whole claim: the failure moves back to its cause."""
    tilemap = _map()
    far = (slice(110, 112), slice(80, 82))
    assert tilemap._window[2] < 110  # genuinely outside the painted window

    block = tilemap.cell[far]
    block["tile"] = 999
    with pytest.raises(IndexError) as excinfo:
        tilemap.cell[far] = block
    assert "999" in str(excinfo.value)


def test_an_id_off_the_end_never_reaches_the_world() -> None:
    """A refused write leaves nothing behind to detonate later."""
    tilemap = _map()
    block = tilemap.cell[110:112, 80:82]
    block["tile"] = 999
    with pytest.raises(IndexError):
        tilemap.cell[110:112, 80:82] = block
    assert tilemap.cell[110, 80] is None


def test_a_block_write_restamps_only_the_visible_part() -> None:
    """The world is bigger than the screen, so filling all of it must not
    stamp all of it -- the retained plane is the viewport plus one cell."""
    tilemap = _map()
    tilemap.cell[:, :] = tilemap.tile[SOLID]

    x0, y0, x1, y1 = tilemap._window
    assert tilemap._count == (x1 - x0) * (y1 - y0)
    assert tilemap._count < 120 * 90


def test_negative_indices_count_from_the_end() -> None:
    tilemap = _map()
    tilemap.cell[-1, -1] = tilemap.tile[SOLID]
    assert tilemap.cell[119, 89][0].index == SOLID


def test_a_write_out_of_range_raises() -> None:
    """Reading past the edge is what a camera does when a world loops; writing
    past it is a cell being named and arithmetic being got instead."""
    tilemap = _map()
    with pytest.raises(IndexError):
        tilemap.cell[120, 0] = tilemap.tile[SOLID]
    with pytest.raises(IndexError):
        tilemap.cell[0, 90] = tilemap.tile[SOLID]


def test_a_read_wraps_only_on_a_wrapped_axis() -> None:
    from blitspersecond.graphics import TileMap

    tilemap = TileMap(_sheet(), 4, 4, wrap=(True, False))
    tilemap.cell[0, 0] = tilemap.tile[SOLID]

    assert tilemap.cell[4, 0][0].index == SOLID  # wrapped: cell 0 again
    with pytest.raises(IndexError):
        tilemap.cell[0, 4]  # unwrapped axis still raises

    # ...and a WRITE raises on both, wrapped or not.
    with pytest.raises(IndexError):
        tilemap.cell[4, 0] = tilemap.tile[SOLID]


def test_the_raw_roads_are_gone() -> None:
    """`blit` and the public array were the two ways to change a map without
    the map knowing. Neither exists."""
    from blitspersecond.graphics import TileMap

    assert not hasattr(TileMap, "blit")
    assert not hasattr(TileMap, "map")
    assert not hasattr(TileMap, "_slot_at")


def test_writing_a_cell_of_a_narrow_wrapped_world_restamps_every_appearance(
    window,
) -> None:
    """A wrapped world can be narrower than the viewport, so one cell is on
    screen many times over -- a one-column repeating sky is the normal case
    rather than the exotic one.

    `_window` keeps RAW world coordinates on a wrapped axis, so a canonical
    cell has to be projected back into the window at every multiple of the
    world's width. Restamping only one of them would leave the same cell drawn
    differently across the screen.

    Counted against the window rather than against solidity: appearances in
    the one-cell border are outside the screen, so the mask clips them and a
    bit count is smaller than the placement count by design.
    """
    from blitspersecond.graphics import TileMap

    tilemap = TileMap(_sheet(), 4, 4, wrap=(True, False), collidable=True)
    tilemap._stream()

    x0, y0, x1, y1 = tilemap._window
    appearances = sum(1 for column in range(x0, x1) if column % 4 == 0)
    assert appearances > 1  # the case is real at this world size

    tilemap.cell[0, 0] = tilemap.tile[SOLID]
    assert tilemap._count == appearances * sum(
        1 for row in range(y0, y1) if row == 0
    )
    # And every one of them draws what it collides as.
    assert _disagreement(window, tilemap) == 0


def test_a_wrapped_write_and_a_refill_agree_on_the_same_cell() -> None:
    """The written cell and the scrolled-to cell must be stamped by one road.
    Scrolling a whole world's width brings the same cells back around; what is
    on screen afterwards must match what the write put there."""
    from blitspersecond.graphics import TileMap

    tilemap = TileMap(_sheet(), 4, 4, wrap=(True, False), collidable=True)
    tilemap._stream()
    tilemap.cell[1, 1] = tilemap.tile[SOLID]
    written = _solid(tilemap)

    tilemap.clear()
    tilemap._stream()
    assert _solid(tilemap) == written
