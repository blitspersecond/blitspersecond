#!/usr/bin/env python3
"""Where restamping cells one at a time stops being cheaper than repainting
the window.

    util/repaint_knee.py
    util/repaint_knee.py --cells 8,16,32 --repeats 200

A write through `cell` restamps in PROPORTION to what it changed
(`bps/reference/tiles.md`, "Stamping a cell"), and a knee must exist because
the two roads have
different shapes: per cell is `N x c` and unbounded, a window repaint is
bounded by one screen whatever N is. Anything unbounded crosses anything
bounded. This measures where.

## The three roads, all through one path

Since that card, a single-cell write and a block write are the same call with
a different rectangle -- `_restamp_region` clips to the window, drops the
placements inside it by rect, clears the mask rect, and rebuilds from the
world with `_stamp_cells`. So this is not comparing two implementations; it is
comparing one implementation at three rectangle sizes:

    per cell    N calls, one cell each      what N scattered edits cost
    block       1 call over N cells         what a slice write costs
    window      1 call over the viewport    the wholesale road

## Why cell size is swept

Two cost laws were argued and only one of them was checked. A quad is four
vertices and one UV row whatever the tile's pixel size, so the DRAW side
should be flat in cell size. Collision writes bits proportional to the tile's
AREA, so it should not be -- `CollisionMask.stamp` measures 8.51/8.50/8.52 us
at cells 8/16/32, which *Batch the collision stamp* reads as dispatch hiding
that cost rather than the absence of one. `--no-collide` separates them.

## The constant this produces has a short life

`_stamp_plane` is a gather rather than a per-cell loop now, and the
collision stamp went the same way, so the numbers below are older than both.
Re-run before quoting them. What should NOT move is the shape: bounded
against unbounded, crossing once.

Warm-up runs are discarded rather than averaged in, and every arm runs in one
process against one map, because session variance on this box has moved a
figure by more than several differences that were nearly decided on.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from time import perf_counter

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pyglet  # noqa: E402
from pyglet.math import Mat4  # noqa: E402

from blitspersecond.common import SCREEN  # noqa: E402
from blitspersecond.graphics import Atlas, PixelBuffer, TileMap  # noqa: E402

WARMUP = 20


def _sheet(cell: int, image=None) -> Atlas:
    """The sheet to cut. Real art if given one, else four synthetic tiles.

    Real art matters here for two reasons the synthetic sheet hides. A
    2048x1536 picture cut at 16px is 12,288 tiles, so the rect table and the
    lazily-filled solidity cache are the size a real game's are; and its cells
    are not uniform, so a replacement changes the picture rather than swapping
    one flat colour for another.
    """
    if image is not None:
        return Atlas(PixelBuffer(image), cell, cell)
    # No palette on the buffer: a buffer carries pixels and a LAYER carries the
    # colours. Indices 1..3 are distinct in the bank every map comes up with.
    buffer = PixelBuffer((cell * 2, cell * 2), "P")
    with buffer.edit() as pixels:
        pixels[:] = 0
        pixels[:cell, :cell] = 1
        pixels[cell:, :cell] = 2
        pixels[:cell, cell:] = 3
    return Atlas(buffer, cell, cell)


def _timed(work, repeats: int) -> float:
    """Seconds per call, warm-up discarded rather than averaged in."""
    for _ in range(WARMUP):
        work()
    best = None
    for _ in range(5):
        started = perf_counter()
        for _ in range(repeats):
            work()
        elapsed = perf_counter() - started
        best = elapsed if best is None else min(best, elapsed)
    return best / repeats


def measure(cell: int, collidable: bool, repeats: int, image=None) -> dict:
    cols = SCREEN.width // cell + 8
    rows = SCREEN.height // cell + 8
    tilemap = TileMap(_sheet(cell, image), cols, rows, collidable=collidable)
    # An incrementing sequence, so every cell on screen holds different art --
    # nothing is shared between placements that would not be in a real world.
    world = tilemap.cell[:, :]
    world["tile"] = (
        np.arange(cols * rows).reshape(cols, rows) % len(tilemap.tileset)
    )
    tilemap.cell[:, :] = world
    tilemap._stream()

    x0, y0, x1, y1 = tilemap._window
    window_cells = (x1 - x0) * (y1 - y0)
    count = len(tilemap.tileset)
    tiles = [tilemap.tile[n % count] for n in (1, 2, 3, 0)]
    step = {"n": 0}

    def one_cell() -> None:
        # A different cell and a different tile each time, so nothing is a
        # no-op and no single rect stays hot in cache.
        step["n"] += 1
        n = step["n"]
        tilemap.cell[x0 + n % (x1 - x0), y0 + (n // 7) % (y1 - y0)] = tiles[n % 4]

    def whole_window() -> None:
        step["n"] += 1
        tilemap.cell[x0:x1, y0:y1] = tiles[step["n"] % 4]

    per_cell = _timed(one_cell, repeats)
    window = _timed(whole_window, max(repeats // 20, 5))

    blocks = {}
    for side in (2, 4, 8, 16):
        if side * side > window_cells:
            break
        bx, by = x0 + 2, y0 + 2

        def block(side=side, bx=bx, by=by) -> None:
            step["n"] += 1
            tilemap.cell[bx : bx + side, by : by + side] = tiles[step["n"] % 4]

        blocks[side * side] = _timed(block, max(repeats // 4, 5))

    return {
        "cell": cell,
        "collidable": collidable,
        "window_cells": window_cells,
        "per_cell": per_cell,
        "window": window,
        "blocks": blocks,
        "knee": window / per_cell,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cells", default="8,16,32")
    parser.add_argument("--repeats", type=int, default=400)
    parser.add_argument(
        "--image",
        default=None,
        help="a sheet to cut instead of the synthetic one; must divide evenly "
        "by every --cells value, and must be palettised to measure collision",
    )
    args = parser.parse_args()

    window = pyglet.window.Window(
        width=SCREEN.width, height=SCREEN.height, visible=False
    )
    window.switch_to()
    window.projection = Mat4.orthogonal_projection(
        0, SCREEN.width, 0, SCREEN.height, -1, 1
    )

    sizes = [int(part) for part in args.cells.split(",")]
    print(f"{'cell':>5} {'coll':>5} {'window':>8} {'per cell':>10} "
          f"{'window rp':>10} {'knee':>8}")
    print("-" * 52)
    results = []
    for cell in sizes:
        for collidable in (False, True):
            row = measure(cell, collidable, args.repeats, args.image)
            results.append(row)
            print(
                f"{row['cell']:>5} {str(row['collidable']):>5} "
                f"{row['window_cells']:>8} "
                f"{row['per_cell'] * 1e6:>9.2f}us "
                f"{row['window'] * 1e3:>9.3f}ms "
                f"{row['knee']:>8.0f}"
            )

    print("\nblock write, us per cell (one call over NxN cells)")
    print(f"{'cell':>5} {'coll':>5} " + " ".join(f"{n:>9}" for n in (4, 16, 64, 256)))
    for row in results:
        cells = [row["blocks"].get(n) for n in (4, 16, 64, 256)]
        shown = " ".join(
            f"{(t / n) * 1e6:>9.3f}" if t else f"{'-':>9}"
            for n, t in zip((4, 16, 64, 256), cells)
        )
        print(f"{row['cell']:>5} {str(row['collidable']):>5} " + shown)


if __name__ == "__main__":
    main()
