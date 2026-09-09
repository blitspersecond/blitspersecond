#!/usr/bin/env python3
"""What one tile layer costs at each station of a frame.

    util/tile_frame_cost.py --tmx ../testsuite/assets/sonic/Sonic_md_fg1.tmx
    util/tile_frame_cost.py --tmx A.tmx --tmx B.tmx --samples 400
    util/tile_frame_cost.py --json                  # machine-readable

`repaint_knee.py` prices the three restamp roads against each other -- what an
EDIT costs. This prices the frame itself: what a tile layer spends when the
camera moves, and when it does not.

## Five stations, and they are five different costs

    load        the first compose                the whole window, from empty
    rest        the camera did not move          four divisions and a compare
    fine        moved within a cell              latches one number
    coarse      crossed a cell boundary          cull, compact, refill, mask
    sync        the corner expansion             one glBufferSubData
    draw        the submission                   one bind, one glDrawElements
    frame       coarse + draw                    the worst ordinary frame
    repaint     `origin` moved                   every placement, from empty

They are separated because the pillar's whole design is that they are not the
same number. A chapter that quotes one figure for "scrolling" would hide the
fact that panning between cell boundaries writes no buffer at all, which is
the property the coarse/fine split exists to produce.

`draw` contains `sync`, because `_composite` calls it -- the submission
alone is the difference between the two rows. `frame` is the pair with the
camera in front of them, which is what one in `cell.width` frames costs while
a level is scrolling; every other frame is `fine` plus `draw`.

The window is invisible, so `draw` is submission and not presentation. That
is the number the budget is spent in: this engine is CPU bound at every load
it can be put under, and a draw call costs the thread that issues it whether
or not anything watches the result.

## The real layers, not a synthetic sheet

Green Hill Zone's three layers are the workload because a synthetic sheet
gets two of these wrong. A repeating sky groups 79 distinct patterns out of
630 placements where the ground groups 7.4x, so a mask build measured on flat
colour is measuring the wrong distribution; and a 12,288-tile sheet has a
rect table and a lazily-filled solidity cache the size a real game's are.
Point `--tmx` at whatever else should be checked.

The layers are loaded exactly as `green_hill_zone.py` loads them, wrapping on
x, which is also what lets the coarse arm step the camera forever without
running off the end of the level.

## Method

Every sample is one call, timed on its own, and the distribution is reported
rather than a mean: a coarse step's cost depends on how many cells the gutter
turned out to hold, and a p50 with a max beside it says that where an average
does not. Warm-up samples are discarded rather than averaged in.

Both collision arms run in ONE session against one map, per the finding that
session variance on this box has moved a figure by more than several
differences that were nearly decided on. Each LAYER gets its own process,
which isolates GL and driver state the way `sprite_sweep.py` does; measured,
it is worth under 3% here.

**If a figure moves 25%, look at the camera before the box.** The repaint arm
did exactly that until it was pinned, and the process isolation was added
first and did not fix it: a level has empty sky, so a window holds anywhere
between 1168 and 1832 placements depending where you are standing, and a
per-placement cost sampled wherever the scrolling arms happened to stop is a
different workload each run rather than a noisy one.

A repaint is measured with the camera pinned at the world's origin, because
its cost is per placement and a level's window does not hold a fixed number of
them -- see the comment on that arm.

**A repaint's ~2.4us a placement is Python call overhead into the kernel plus
a `solidity` lookup, and it stays.** Grouping on `(tile, orientation)` would
collapse it to about 0.7ms, and nothing asks for that: a rebuild is a
one-frame 3.6ms against a 16.67ms budget that a real scene leaves 15.8ms of.
See *Batch the collision stamp* in `implemented` for the costing. If a slower
part ever makes one rebuild drop a frame, this harness is what says so.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pyglet  # noqa: E402
from pyglet.math import Mat4  # noqa: E402

from blitspersecond.common import SCREEN  # noqa: E402
from blitspersecond.graphics import Atlas, PixelBuffer, TileMap  # noqa: E402
from blitspersecond.graphics import TiledFile  # noqa: E402

WARMUP = 20

#: The default workload: Green Hill Zone's three layers, back to front. Found
#: relative to this file, so the testsuite checkout is expected beside the
#: engine -- the same layout `pip install -e ../bps` implies. Missing is not an
#: error; the synthetic sheet is the fallback and says so.
_TESTSUITE = Path(__file__).resolve().parents[2] / "testsuite/assets/sonic"
_DEFAULT_TMX = ("Base.tmx", "Sonic_md_bg1.tmx", "Sonic_md_fg1.tmx")


def _synthetic(cell: int = 16) -> TileMap:
    """Four flat tiles over a world big enough to scroll. The fallback."""
    # No palette on the buffer: a buffer carries pixels and a LAYER carries the
    # colours. Indices 1..3 are distinct in the bank every map comes up with.
    buffer = PixelBuffer((cell * 2, cell * 2), "P")
    with buffer.edit() as pixels:
        pixels[:] = 0
        pixels[:cell, :cell] = 1
        pixels[cell:, :cell] = 2
        pixels[:cell, cell:] = 3
    atlas = Atlas(buffer, cell, cell)
    cols = SCREEN.width // cell + 16
    rows = SCREEN.height // cell + 16
    tilemap = TileMap(atlas, cols, rows, tag="synthetic")
    world = tilemap.cell[:, :]
    world["tile"] = np.arange(cols * rows).reshape(cols, rows) % len(atlas)
    tilemap.cell[:, :] = world
    return tilemap


def _load(path: Path) -> TileMap:
    """One TMX layer as one horizontally-wrapping map, as the demo loads it."""
    tiled = TiledFile(str(path))
    tilemap = tiled.tilemap(tiled.names[0], tag=path.name)
    tilemap.wrap = (True, False)
    return tilemap


def _bytes(obj) -> int:
    """Every NumPy array reachable through an object's own slots.

    Slots rather than `__dict__` because everything measured here has them,
    and a dict or list of arrays is counted through -- `Atlas._solid` is a
    lazily filled cache of packed patterns and is the point of asking.
    """
    total = 0
    seen = set()

    def add(value) -> None:
        nonlocal total
        if isinstance(value, np.ndarray):
            if id(value) not in seen:
                seen.add(id(value))
                total += value.nbytes
        elif isinstance(value, dict):
            for item in value.values():
                add(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                add(item)

    for name in getattr(type(obj), "__slots__", ()):
        add(getattr(obj, name, None))
    return total


def _samples(work: Callable[[int], None], count: int) -> List[float]:
    """One timing per call, warm-up discarded rather than averaged in."""
    for index in range(WARMUP):
        work(-index - 1)
    out = []
    for index in range(count):
        started = perf_counter()
        work(index)
        out.append(perf_counter() - started)
    return out


def _summary(samples: Sequence[float]) -> Dict[str, float]:
    ordered = sorted(samples)
    return {
        "p50": median(ordered),
        "p90": ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))],
        "max": ordered[-1],
        "mean": sum(ordered) / len(ordered),
    }


def _load_cost(build, collidable: bool) -> float:
    """What a map's very first compose costs -- the level-load figure.

    A fresh map each time, because this is the one station that cannot be
    repeated on the same object: after it, the window is painted.
    """
    best = None
    for _ in range(7):
        tilemap = build()
        tilemap.collidable = collidable
        started = perf_counter()
        tilemap._stream()
        elapsed = perf_counter() - started
        best = elapsed if best is None else min(best, elapsed)
    return best


def measure(tilemap: TileMap, collidable: bool, count: int, build=None) -> dict:
    """Every station, for one map, at one collision setting."""
    # FIRST, on fresh maps, because a first compose is the first thing a map
    # does. It is measured COLD, and deliberately: `build` keeps no reference
    # to the `TiledFile`, the interning is weak, so each map re-cuts its atlas
    # and packs every solidity pattern from scratch -- which is what a level
    # load actually is. `Sonic_md_fg1` is 6.2ms cold against 3.7ms over art
    # already packed, and the 2.5ms between them is 352 patterns.
    #
    # That difference is a fact about the ART, not about this harness: two
    # maps sharing one atlas share its pattern cache, and two maps that each
    # cut their own from the same picture do not. See `Atlas.solidity`.
    load = _load_cost(build, collidable) if build is not None else None

    cell = tilemap.tile_size
    tilemap.collidable = collidable
    tilemap.scroll = (0.0, 0.0)
    tilemap._stream()  # the first frame's whole-screen fill, timed separately

    placements = tilemap._count
    window = tilemap._window
    distinct = _distinct_patterns(tilemap)

    # rest: the camera has not moved. Idempotent by design, so the same call
    # repeated IS the workload.
    def rest(_: int) -> None:
        tilemap._stream()

    # fine: inside one cell, never crossing. The remainder cycles rather than
    # advancing, so no sample is secretly a coarse step.
    base = float(tilemap.scroll[0])

    def fine(index: int) -> None:
        tilemap.scroll = (base + 1 + (index % (cell.width - 1)), 0.0)
        tilemap._stream()

    # coarse: exactly one cell per sample, forever, because the layer wraps.
    step = {"x": base}

    def coarse(_: int) -> None:
        step["x"] += cell.width
        tilemap.scroll = (step["x"], 0.0)
        tilemap._stream()

    # frame: everything a coarse step costs, camera and draw together. The
    # worst ordinary frame this layer has -- one in `cell.width` of them.
    def frame(_: int) -> None:
        step["x"] += cell.width
        tilemap.scroll = (step["x"], 0.0)
        tilemap._stream()
        tilemap._composite()

    # repaint: `origin` drops every placement, so the next stream fills the
    # whole window from empty. Alternated, so every sample is a real move.
    #
    # The camera is PINNED first, and that is not tidiness. A repaint's cost is
    # per placement and a window does not hold a fixed number of them: a level
    # has empty sky, so `Sonic_md_fg1` holds between 1168 and 1832 depending
    # where you are standing. Left wherever the scrolling arms finished, this
    # figure moved 25% between runs and looked like box variance.
    origin = tilemap.origin

    def repaint(index: int) -> None:
        tilemap.origin = (origin[0] + (index % 2), origin[1])
        tilemap._stream()

    out = {
        "collidable": collidable,
        "placements": placements,
        "window": window,
        "distinct": distinct,
        "rest": _summary(_samples(rest, count)),
        "fine": _summary(_samples(fine, count)),
        "coarse": _summary(_samples(coarse, count)),
        "frame": _summary(_samples(frame, count)),
    }

    tilemap.scroll = (0.0, 0.0)
    tilemap._stream()
    out["repaint_placements"] = tilemap._count
    out["repaint"] = _summary(_samples(repaint, max(count // 10, 10)))

    # `sync` and `draw` are timed with the camera step OUTSIDE the timing, so
    # the upload and the submission can be told from the streaming that made
    # work for them. `_composite` calls `_sync` itself, so `draw` contains it:
    # the submission alone is the difference.
    sync_samples: List[float] = []
    draw_samples: List[float] = []
    for index in range(WARMUP + count):
        step["x"] += cell.width
        tilemap.scroll = (step["x"], 0.0)
        tilemap._stream()
        started = perf_counter()
        tilemap._sync()
        synced = perf_counter()
        tilemap._composite()
        drawn = perf_counter()
        if index >= WARMUP:
            sync_samples.append(synced - started)
            draw_samples.append(drawn - started)
    out["sync"] = _summary(sync_samples)
    out["draw"] = _summary(draw_samples)

    if load is not None:
        out["load"] = {"p50": load, "p90": load, "max": load, "mean": load}

    out["bytes"] = {
        "placements": tilemap._placements.nbytes + tilemap._placements_scratch.nbytes,
        "verts": tilemap._verts.nbytes,
        "world": tilemap._map.nbytes,
        "mask": _bytes(tilemap._collision),
        "sheet": _bytes(tilemap.tileset),
        # A layer carries its colours: one PaletteBank, 256x256 RGBA, and a
        # map mints its own unless one is assigned to it.
        "palette": tilemap.palette.texels.nbytes,
    }
    return out


def _distinct_patterns(tilemap: TileMap) -> int:
    """How many `(tile, orientation)` pairs the painted window holds.

    The ratio against the placement count is what decides whether a repaint's
    stamps would group -- see *Batch the collision stamp*.
    """
    count = tilemap._count
    if count == 0:
        return 0
    live = tilemap._placements[:count]
    keys = live["tile"].astype(np.int64) * 8 + live["flags"]
    return int(len(np.unique(keys)))


def _worker(path: Path, samples: int) -> list:
    """One layer, in a process of its own. See the module docstring."""
    out = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--run",
            str(path),
            "--samples",
            str(samples),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--tmx",
        action="append",
        default=None,
        help="a TMX file to measure; repeatable. Defaults to Green Hill Zone.",
    )
    parser.add_argument("--samples", type=int, default=300)
    parser.add_argument(
        "--run",
        default=None,
        help="worker mode: measure ONE layer in this process and print JSON. "
        "Spawned per layer by the parent -- see the module docstring.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    window = pyglet.window.Window(
        width=SCREEN.width, height=SCREEN.height, visible=False
    )
    window.switch_to()
    window.projection = Mat4.orthogonal_projection(
        0, SCREEN.width, 0, SCREEN.height, -255, 255
    )

    if args.tmx:
        paths = [Path(p) for p in args.tmx]
    else:
        paths = [_TESTSUITE / name for name in _DEFAULT_TMX]
        paths = [p for p in paths if p.exists()]

    if args.run or not paths:
        build = (lambda: _load(Path(args.run))) if args.run else _synthetic
        tilemap = build()
        name = Path(args.run).name if args.run else "synthetic (no testsuite)"
        results = []
        for collidable in (False, True):
            row = measure(tilemap, collidable, args.samples, build)
            row["layer"] = name
            row["cell"] = tuple(tilemap.tile_size)
            row["world"] = (tilemap.cols, tilemap.rows)
            results.append(row)
        if args.json or args.run:
            print(json.dumps(results, indent=2))
            return
    else:
        results = []
        for path in paths:
            results.extend(_worker(path, args.samples))
        if args.json:
            print(json.dumps(results, indent=2))
            return

    for row in results:
        cw, ch = row["cell"]
        wc, wr = row["world"]
        print(
            f"\n{row['layer']}  {wc}x{wr} cells of {cw}x{ch}, "
            f"collidable={row['collidable']}"
        )
        print(
            f"  window holds {row['placements']} placements, "
            f"{row['distinct']} distinct (tile,orientation) "
            f"-- {row['placements'] / max(row['distinct'], 1):.1f}x"
        )
        print(f"  {'station':>10} {'p50':>10} {'p90':>10} {'max':>10}")
        stations = ("load", "rest", "fine", "coarse", "sync", "draw", "frame", "repaint")
        for station in stations:
            stat = row.get(station)
            if stat is None:
                continue
            note = ""
            if station == "repaint":
                per = stat["p50"] / max(row["repaint_placements"], 1)
                note = (
                    f"   {row['repaint_placements']} placements, "
                    f"{per * 1e6:.2f}u each"
                )
            print(
                f"  {station:>10} "
                f"{stat['p50'] * 1e6:>9.2f}u "
                f"{stat['p90'] * 1e6:>9.2f}u "
                f"{stat['max'] * 1e6:>9.2f}u{note}"
            )
        total = sum(row["bytes"].values())
        parts = "  ".join(f"{k} {v / 1024:.1f}K" for k, v in row["bytes"].items())
        print(f"  {parts}   = {total / 1024:.1f}K")


if __name__ == "__main__":
    main()
