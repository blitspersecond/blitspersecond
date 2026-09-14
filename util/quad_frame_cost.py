#!/usr/bin/env python3
"""What one quad layer costs at each station of a frame.

    util/quad_frame_cost.py
    util/quad_frame_cost.py --counts 100,1000,10000 --samples 200
    util/quad_frame_cost.py --json                  # machine-readable

`testsuite/e1m1.py` proves the pillar draws a DOOM level. It cannot price it:
most of that frame is the renderer's BSP traverse and projection -- the world,
not the engine -- so the commit is buried inside a number it does not dominate.
This drives the pillar directly with geometry computed before the clock starts,
so every figure here is the layer's own.

## Eight stations, and they are not one number

    ingest      filling a cleared list      `add()` per command, or one `extend()`
    compact     the validate/cull kernel    Numba, every live command and clip
    uv          the Atlas rows gathered     one `np.take` into retained storage
    map         the inverse homographies    one broadcast NumPy pass
    expand      corners to vertices         NumPy fast path, or the ragged kernel
    upload      the four buffer writes      positions, mapping, regions, indices
    sync        every station above         what a changed command list costs
    draw        the submission              one bind, one `glDrawElements`

They are separated because the pillar's claims are about the differences
between them, not their total. The bulk door exists because `ingest` is where a
per-command Python call lands; the clipped arm exists because `expand` is the
only station that changes shape when a command carries its own clip.

## Two doors and two clip shapes

`add()` and `extend()` are measured on the same geometry, which is what prices
the bulk door rather than asserting it. Unclipped commands take the four-corner
fast path, where the whole expansion is NumPy over a fixed stride; clipped ones
carry a convex polygon each and go through the ragged Numba kernel. A pillar
figure that quoted only the unclipped arm would be quoting the case that made
the kernel unnecessary.

## Nothing is culled, deliberately

Every generated command is valid and on screen. A culled one is cheaper --
`compact` skips its geometry and the survivors compact past it -- so a workload
with dead commands in it prices the cull, not the commit. What a frame pays is
the all-alive case, and it is also the only one that repeats: culling rewrites
the list, so the second sample would not measure the same population as the
first.

The window is invisible, so `draw` is submission and not presentation. That is
the number the budget is spent in: this engine is CPU bound at every load it
can be put under, and a draw call costs the thread that issues it whether or
not anything watches the result.

## `upload` and `draw` are not trustworthy across a multi-population sweep

Every population runs in this one process against one layer, and a GL buffer
grows and never shrinks. So the driver state a large run wants is already paid
for by the time it is measured, and the ordering can invert: 10,000 commands
measured mid-sweep reported 0.735ms unclipped against 0.138ms clipped, and the
same population in a fresh process reported 0.111ms against 0.389ms — the
second is the believable one, since a clipped command ships more vertices.

The CPU stations do not have this problem and reproduce inside 2% between the
two shapes of run. **Quote `ingest`, `compact`, `uv`, `map`, `expand` and
`sync` from a sweep; quote `upload` and `draw` only from a single `--counts`
run, or fix this properly.** The fix is one population per subprocess, which is
what `sprite_sweep.py` does and why it does it: `BlitsPerSecond` and `Metrics`
are singletons and driver state is not worth sharing.
"""

import argparse
import json
from statistics import median
from time import perf_counter
from typing import Callable, Dict, List, Sequence

import numpy as np
import pyglet

from blitspersecond.common import SCREEN
from blitspersecond.graphics import Atlas, PixelBuffer
from blitspersecond.graphics.quads import Quads
from blitspersecond.graphics.quads.layer import _inverse_homographies

WARMUP = 20
COUNTS = (100, 1_000, 10_000, 50_000)
CLIP_POINTS = 5


def _summary(samples: Sequence[float]) -> Dict[str, float]:
    ordered = sorted(samples)
    return {
        "p50": median(ordered),
        "p90": ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))],
        "max": ordered[-1],
        "mean": sum(ordered) / len(ordered),
    }


def _samples(work: Callable[[], None], count: int) -> List[float]:
    """One timing per call, warm-up discarded rather than averaged in."""
    for _ in range(WARMUP):
        work()
    out = []
    for _ in range(count):
        started = perf_counter()
        work()
        out.append(perf_counter() - started)
    return out


def _geometry(count: int, clipped: bool):
    """Valid, on-screen corners and packed clips, built before any clock runs.

    Quads march across the screen and wrap, so a large population overlaps
    heavily rather than leaving the viewport -- overlap costs the GPU, which is
    not what is being timed here, and leaving the viewport would be a cull.
    """
    index = np.arange(count, dtype=np.float32)
    x = (index * 17.0) % (SCREEN.width - 40.0)
    y = ((index * 29.0) // (SCREEN.width - 40.0) * 13.0) % (SCREEN.height - 40.0)
    corners = np.empty((count, 4, 2), dtype=np.float32)
    corners[:, 0, 0], corners[:, 0, 1] = x, y
    corners[:, 1, 0], corners[:, 1, 1] = x + 36.0, y + 2.0
    corners[:, 2, 0], corners[:, 2, 1] = x + 33.0, y + 31.0
    corners[:, 3, 0], corners[:, 3, 1] = x + 3.0, y + 29.0
    if not clipped:
        return corners, None

    # A convex pentagon per command, inside its own quad so the coverage is
    # real rather than degenerate, and wound the same way throughout.
    angles = np.linspace(0.0, 2.0 * np.pi, CLIP_POINTS, endpoint=False)
    centre_x = (x + 18.0)[:, None]
    centre_y = (y + 16.0)[:, None]
    clips = np.empty((count * CLIP_POINTS, 2), dtype=np.float32)
    clips[:, 0] = (centre_x + 14.0 * np.cos(angles)[None, :]).ravel()
    clips[:, 1] = (centre_y + 12.0 * np.sin(angles)[None, :]).ravel()
    return corners, clips


def _fill_add(layer: Quads, row: int, corners, clips) -> None:
    layer.clear()
    if clips is None:
        for one in corners:
            layer.add(texture=row, corners=one)
        return
    for index, one in enumerate(corners):
        start = index * CLIP_POINTS
        layer.add(
            texture=row,
            corners=one,
            clip=clips[start : start + CLIP_POINTS],
        )


def _fill_extend(layer: Quads, row: int, corners, clips) -> None:
    layer.clear()
    count = len(corners)
    counts = None
    if clips is not None:
        counts = np.full(count, CLIP_POINTS, dtype=np.uint32)
    block = layer.extend(count, clip_counts=counts)
    block.texture_ids[:] = row
    block.corners[:] = corners
    if clips is not None:
        block.clip_points[:] = clips


def measure(layer: Quads, row: int, count: int, clipped: bool, samples: int) -> dict:
    """Every station, for one population, at one clip shape."""
    corners, clips = _geometry(count, clipped)
    out: Dict[str, object] = {"count": count, "clipped": clipped}

    for name, fill in (("add", _fill_add), ("extend", _fill_extend)):
        # `add()` is a Python call per command; at a large population it is the
        # station under test, so it gets fewer samples rather than a shortcut.
        taken = samples if name == "extend" else max(3, samples // 20)
        out[f"ingest.{name}"] = _summary(
            _samples(lambda: fill(layer, row, corners, clips), taken)
        )

    _fill_extend(layer, row, corners, clips)
    layer._sync()
    if len(layer) != count:
        raise SystemExit(
            f"{count - len(layer)} of {count} commands were culled; the "
            f"workload is not measuring a live population"
        )

    out["compact"] = _summary(_samples(layer._compact, samples))
    out["uv"] = _summary(
        _samples(
            lambda: np.take(
                layer._atlas.uvs,
                layer._rows[:count],
                axis=0,
                out=layer._uvs[:count],
            ),
            samples,
        )
    )
    out["map"] = _summary(
        _samples(
            lambda: _inverse_homographies(layer._corners[:count]),
            samples,
        )
    )
    # The two arms run different expansions, so each is timed on the one it
    # actually takes. Timing `expand_geometry` against unclipped commands
    # would price a kernel that path never calls.
    expansion = (
        layer._sync_clipped_geometry if clipped else layer._sync_unclipped_geometry
    )
    out["expand"] = _summary(_samples(expansion, samples))

    vertex_count = layer._vertex_count
    index_count = layer._index_count
    out["upload"] = _summary(
        _samples(
            lambda: layer._batch.upload(
                layer._positions[:vertex_count],
                layer._projective[:vertex_count],
                layer._regions[:vertex_count],
                layer._indices[:index_count],
            ),
            samples,
        )
    )

    def one_sync() -> None:
        layer._dirty = True
        layer._sync()

    out["sync"] = _summary(_samples(one_sync, samples))
    out["draw"] = _summary(_samples(layer._composite, samples))
    return out


def _table(rows: Sequence[dict]) -> None:
    stations = (
        "ingest.add",
        "ingest.extend",
        "compact",
        "uv",
        "map",
        "expand",
        "upload",
        "sync",
        "draw",
    )
    width = max(len(name) for name in stations) + 2
    for row in rows:
        shape = "clipped (5-gon)" if row["clipped"] else "unclipped (4 corners)"
        print(f"\n{row['count']:,} commands, {shape} — p50 ms, and per command")
        print("-" * (width + 26))
        for name in stations:
            ms = row[name]["p50"] * 1_000.0
            each = row[name]["p50"] * 1_000_000.0 / row["count"]
            print(f"  {name:<{width}} {ms:8.3f} ms   {each:7.3f} us")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--counts",
        default=",".join(str(value) for value in COUNTS),
        help="comma-separated command populations",
    )
    parser.add_argument("--samples", type=int, default=120)
    parser.add_argument(
        "--clipped",
        choices=("both", "only", "never"),
        default="both",
        help="which clip shapes to measure",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    counts = [int(value) for value in args.counts.split(",") if value]
    shapes = {"both": (False, True), "only": (True,), "never": (False,)}[
        args.clipped
    ]

    window = pyglet.window.Window(
        width=SCREEN.width, height=SCREEN.height, visible=False
    )
    window.switch_to()

    atlas = Atlas(PixelBuffer((256, 256), "RGBA"), 64, 64)
    layer = Quads(atlas)
    row = layer.texture_id(0)

    rows = [
        measure(layer, row, count, clipped, args.samples)
        for count in counts
        for clipped in shapes
    ]

    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        _table(rows)


if __name__ == "__main__":
    main()
