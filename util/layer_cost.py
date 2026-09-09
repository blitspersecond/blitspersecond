#!/usr/bin/env python3
"""What a drawn tile layer costs per frame, and what the collection costs.

    util/layer_cost.py
    util/layer_cost.py --counts 1,4,16,64 --tmx path/to/level.tmx

**Two different questions, and keeping them apart is the point of the arms.**
"Layers are cheap" is a claim about the COLLECTION -- that putting a thing in
the draw order costs the walk and a dispatch, so a game can stop counting
layers. What a layer costs once it draws is the PILLAR's, and belongs in that
pillar's chapter.

The design-log figure this replaces conflated them: ~47us of compose per
additional layer, measured 2026-08-24 on the Sonic parallax scene, is a
tilemap's draw cost quoted as a layers cost.

## Three arms

    disabled   attached, `enabled = False` -- the walk reaches it, the settle
               runs, nothing draws. THE COLLECTION'S QUESTION.
    sparse     drawn, one placement
    full       drawn, a real level's window of placements

`disabled` is the collection plus whatever the pillar's settle does, so it is
an upper bound on the walk: a tilemap's settle alone measures ~1.7us, which is
essentially the whole of it. The collection's own per-layer cost does not
separate from noise.

`sparse` against `full` is the tile pillar's answer, and for this pillar the
difference is **nothing** -- a tilemap draws in one bind and one
`glDrawElements` at any quad count, so a layer holding one tile and a layer
holding a level's window issue the same calls. A tile layer's marginal cost is
FIXED, and counting placements is the wrong way to budget one.

Do not generalise either result to the other pillars. A sprite layer stages
its quads every frame, so its marginal cost is not flat, and saying so needs
this pointed at that pillar.

## What is measured

`Layers._draw()`, which is the walk: blend transition tracking and
`composite()` on every attached layer. Not `commit()`, because that adds the
framebuffer clear -- ONE per frame however many layers there are, constant in
N, and so not part of a marginal cost.

## Why the layers share a sheet

They are built from one `TiledFile`, so they share an interned `Atlas` and one
texture -- what parallax over one sheet does. It also isolates the question:
distinct textures measured 4.784ms against 4.801ms for 100 layers on
2026-08-24, noise, so rebinding is not what a marginal layer costs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from time import perf_counter
from typing import List

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pyglet  # noqa: E402
from pyglet.math import Mat4  # noqa: E402

from blitspersecond.common import SCREEN  # noqa: E402
from blitspersecond.graphics import Atlas, PixelBuffer, TileMap, TiledFile  # noqa: E402
from blitspersecond.graphics.internal.layers import Layers  # noqa: E402

WARMUP = 20
_TESTSUITE = Path(__file__).resolve().parents[2] / "testsuite/assets/sonic"


def _sparse_map(sheet: Atlas) -> TileMap:
    """A drawn layer holding one placement: the machinery, and a real draw."""
    tilemap = TileMap(sheet, 1, 1)
    tilemap.cell[0, 0] = tilemap.tile[0]
    return tilemap


def _timed(work, repeats: int) -> float:
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


def measure(build, count: int, arm: str, repeats: int) -> float:
    layers = Layers()
    maps: List[TileMap] = []
    for _ in range(count):
        tilemap = build()
        if arm == "disabled":
            tilemap.enabled = False
        layers.add(tilemap)
        maps.append(tilemap)
    # One compose off the metric ring, so the first frame's whole-window fill
    # is not billed to the steady-state walk this is asking about.
    layers._draw()
    return _timed(layers._draw, repeats)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--counts", default="1,2,4,8,16,32")
    parser.add_argument("--tmx", default=str(_TESTSUITE / "Sonic_md_fg1.tmx"))
    parser.add_argument("--repeats", type=int, default=200)
    args = parser.parse_args()

    window = pyglet.window.Window(
        width=SCREEN.width, height=SCREEN.height, visible=False
    )
    window.switch_to()
    window.projection = Mat4.orthogonal_projection(
        0, SCREEN.width, 0, SCREEN.height, -255, 255
    )

    tiled = TiledFile(args.tmx)
    sheet = tiled.tilemap(tiled.names[0]).tileset

    def full():
        tilemap = tiled.tilemap(tiled.names[0])
        tilemap.wrap = (True, False)
        return tilemap

    counts = [int(c) for c in args.counts.split(",")]
    print(f"{'layers':>7} {'disabled':>11} {'sparse':>11} {'full':>11}")
    rows = {}
    for count in counts:
        row = []
        for arm, build in (
            ("disabled", full),
            ("sparse", lambda: _sparse_map(sheet)),
            ("full", full),
        ):
            row.append(measure(build, count, arm, args.repeats))
        rows[count] = row
        print(
            f"{count:>7} "
            + " ".join(f"{v * 1e6:>10.2f}u" for v in row)
        )

    lo, hi = counts[0], counts[-1]
    span = hi - lo
    print(f"\nmarginal cost of one more layer, {lo} -> {hi}:")
    print("  (disabled is the collection's question; the other two are the "
          "tile pillar's)")
    for index, arm in enumerate(("disabled", "sparse", "full")):
        per = (rows[hi][index] - rows[lo][index]) / span
        print(f"  {arm:>9} {per * 1e6:>8.2f}us")
    sparse = (rows[hi][1] - rows[lo][1]) / span
    full = (rows[hi][2] - rows[lo][2]) / span
    print(
        f"\n  one placement vs a level's worth: {sparse * 1e6:.2f}us against "
        f"{full * 1e6:.2f}us -- {abs(full - sparse) / sparse * 100:.1f}% apart"
    )
    print(
        f"  {hi} drawn layers are {rows[hi][2] * 1e3:.2f}ms, "
        f"{rows[hi][2] / 0.01667 * 100:.1f}% of a 60Hz frame"
    )


if __name__ == "__main__":
    main()
