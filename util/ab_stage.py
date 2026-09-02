#!/usr/bin/env python3
"""A/B the sprite staging: one workload, one output, several representations.

    util/ab_stage.py
    util/ab_stage.py --counts 25000,250000 --only current,bulk
    util/ab_stage.py --trail            # does the bulk path tax a trail?

`util/sprite_sweep.py` found the gap and `util/staging_bench.py` sized the
pieces of it on synthetic arrays. This is the controlled experiment: real
`Sprite` state, real `Tables`, real `_emit_chain` as the baseline, and every
candidate **checked against that baseline for identical output before its
time is reported.** A variant that does not reproduce the frame is not a
faster way to draw it, and is printed as a failure rather than a number.

## The hypothesis under test

Kris, closing the measurement session:

    "i suspect it's the shape of the data representation we hold cpu side"
    "or the access pattern we assumed and designed for"
    "we optimised early for the shadow trails which saves a few microsecond
     per trail - but they are bounded usually - and lost the dumb fast bulk"

Two claims, and they need separating, because they have different fixes:

- **access pattern** -- the chain is an arbitrary draw order, so every read
  is a fancy-index. A bulk population's order is a solid ascending run and
  could be a slice.
- **representation** -- what is held per presentation, and what is
  manufactured from it. The plan expands four corners of three floats on the
  CPU, twice, so 16 bytes of state become 96 bytes of vertex stream.

The variants below climb from "drop-in, gives up nothing" to "needs a new
vertex stage", so the trade is chosen against numbers and a build cost rather
than against taste.

## The finding that changes the shape of the fix

`_drawable` filters the chain by `visible`, so a swarm with even one dead
slot hands `_emit_chain` a NON-contiguous order -- and a fountain always has
some, because a bullet that left the screen this tick is refilled at the top
of the next. A fast path conditioned on "the order is contiguous" would
therefore almost never fire on the workload it was written for.

So the dense variants do not filter. They stage the whole span, slice-read,
and collapse a parked slot to a **zero-area quad** by multiplying its width
and height by the keep mask -- which the rasteriser discards for free. Staging
1,250 corpses to avoid gathering 248,750 live ones is the trade, and it is
measured rather than assumed.

## The unit being timed

**One chain's whole contribution to `FramePlan.sync`**, filter included --
`_drawable` and `_emit_chain` together, not `_emit_chain` alone. `_drawable`
gathers three arrays through the order to decide who draws, so timing only
what comes after it would charge the dense variants for a filter the baseline
got for free. They fold that filter into a mask over the span; the comparison
has to contain it on both sides or it is not a comparison.

## What this does NOT measure

Upload and draw. `sprite_sweep --split` puts those at 1.30 ms against
31.21 ms of plan at 250,000, so they are not where the frame goes -- but a
variant that narrows the vertex stream shrinks them too, and that saving is
not counted here. Every figure below is CPU staging alone.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from blitspersecond.graphics.sprite import Sprite, SpriteSheet
from blitspersecond.graphics.sprite.frame import (
    FramePlan,
    Tables,
    _drawable,
    _primary_parts,
)

ROOT = Path(__file__).resolve().parents[1]
SHEET = ROOT / "assets/bullets/bullets.sprite.json"

DEFAULT_COUNTS = (25_000, 100_000, 250_000)
REPEAT = 15

#: The fountain's dynamics, copied from `sprite_sweep.py` so the population
#: this stages has the same spread of positions and the same fraction of
#: parked slots as the run the ceiling was measured on.
GRAVITY = 0.12
SPREAD = 2.2
LIFT = (-7.6, -5.6)
LIFETIME = 115
SCREEN = (640, 360)

#: Roughly what a fountain carries at any moment: a bullet leaves the screen
#: and waits one tick to be refilled. Small, and the whole reason the naive
#: "is the chain contiguous?" fast path does not fire.
DEAD_FRACTION = 0.005


# -- the workload ---------------------------------------------------------


@dataclass
class Scene:
    """A real sprite, a real chain, real tables. What a variant is handed."""

    sprite: Sprite
    tables: Tables
    chain: np.ndarray  # the draw order as authored: every slot, live or not
    span: slice  # the same chain as a slice -- what a dense variant reads
    quads: int  # live quads, so buffers can be sized once

    #: Tables the variants need and the plan does not currently keep. Built
    #: with the scene rather than inside a variant, because building them is
    #: a setup cost the engine would pay once per sheet and charging it to a
    #: per-frame measurement would be dishonest.
    uvbox: np.ndarray = field(default_factory=lambda: np.zeros((0, 4), np.float32))
    uv2: np.ndarray = field(default_factory=lambda: np.zeros((0, 0, 4, 2), np.float32))
    fused: np.ndarray = field(default_factory=lambda: np.zeros((0, 0, 8), np.float32))


def build(count: int, dead: float = DEAD_FRACTION) -> Scene:
    """One swarm, seeded mid-flight, with a realistic scatter of parked slots.

    Positions come from integrating each bullet forward by a random age, the
    same way the sweep seeds, because the gathers being measured are sensitive
    to what the values ARE only through the branch-free arithmetic over them --
    but the parked fraction changes the SHAPE of the work, and that has to be
    right.
    """
    sprite = Sprite(SpriteSheet(str(SHEET)), tag="ab")
    sprite.assembly = None  # the primary draws nothing; this is about the chain

    swarm = sprite.after.extend(count)
    swarm.assemblies[0::2] = sprite.assembly_id("blue")
    swarm.assemblies[1::2] = sprite.assembly_id("pink")

    rng = np.random.default_rng(0xB011E7)
    fx = np.full(count, SCREEN[0] / 2, dtype=np.float32)
    fy = np.full(count, SCREEN[1] - 24, dtype=np.float32)
    vx = rng.uniform(-SPREAD, SPREAD, count).astype(np.float32)
    vy = rng.uniform(*LIFT, count).astype(np.float32)
    age = rng.uniform(0.0, LIFETIME, count).astype(np.float32)
    fx += vx * age
    fy += vy * age + 0.5 * GRAVITY * age * age

    with swarm.edit():
        swarm.positions[:, 0] = np.floor(fx)
        swarm.positions[:, 1] = np.floor(fy)
        swarm.visible[:] = True
        if dead:
            corpses = rng.choice(count, size=int(count * dead), replace=False)
            swarm.visible[corpses] = False

    plan = FramePlan()
    tables = plan._tables_for(sprite)
    chain = sprite._after
    order = _drawable(sprite, chain)
    tables.ensure(sprite._slots.assembly[order], sprite._assembly_names)

    first = int(chain[0])
    if int(np.ptp(chain)) != chain.size - 1:
        raise SystemExit("the chain is not one contiguous run of slot ids")
    scene = Scene(sprite, tables, chain, slice(first, first + chain.size),
                  int(order.size))
    scene.uvbox = uv_boxes(sprite)
    scene.uv2 = np.ascontiguousarray(tables.uv[..., :2])
    scene.fused = _fuse(scene)
    return scene


def uv_boxes(sprite: Sprite) -> np.ndarray:
    """`(assemblies, 4)` float32 of `(u0, v0, du, dv)` -- UNTURNED.

    A table the instanced variants need and the plan does not currently keep.
    It is per assembly ONLY: an orientation permutes which corner reads which
    (u, v), it does not move the box, so the eight-fold axis of the existing
    `(A, 8, 4, 3)` table exists purely to pre-permute the corners.

    That is the representation claim in one array. Today a quad gathers 12
    floats out of a table with 96 per assembly; this gathers 4 out of a table
    with 4, which for the bullet sheet is 32 bytes total and never leaves L1.
    """
    sheet = sprite.sheet
    names = sprite._assembly_names
    boxes = np.zeros((max(1, len(names)), 4), dtype=np.float32)
    for index, name in enumerate(names):
        parts = sheet.assemblies[name].parts
        if len(parts) != 1:
            continue
        part = parts[0]
        tileset = sheet.tilesets[part.tileset]
        source = tileset.atlas.rect(part.tile)
        width, height = tileset.atlas.size
        boxes[index] = (
            source.x / width,
            source.y / height,
            source.width / width,
            source.height / height,
        )
    return boxes


# -- what a variant produces ----------------------------------------------


@dataclass
class Buffers:
    """Staging arrays, allocated once per (variant, count) and rewritten.

    Retained exactly as `FramePlan` retains its own, so no variant is charged
    for an allocation the engine would not make on a steady frame.
    """

    corners3: np.ndarray = field(default_factory=lambda: np.zeros((0, 4, 3), np.float32))
    uvs3: np.ndarray = field(default_factory=lambda: np.zeros((0, 4, 3), np.float32))
    corners2: np.ndarray = field(default_factory=lambda: np.zeros((0, 4, 2), np.float32))
    uvs2: np.ndarray = field(default_factory=lambda: np.zeros((0, 4, 2), np.float32))
    instance: np.ndarray = field(default_factory=lambda: np.zeros((0, 9), np.float32))
    minimal: np.ndarray = field(default_factory=lambda: np.zeros((0, 3), np.float32))
    quads: int = 0

    def reserve(self, n: int) -> None:
        self.corners3 = np.zeros((n, 4, 3), np.float32)
        self.uvs3 = np.zeros((n, 4, 3), np.float32)
        self.corners2 = np.zeros((n, 4, 2), np.float32)
        self.uvs2 = np.zeros((n, 4, 2), np.float32)
        self.instance = np.zeros((n, 9), np.float32)
        self.minimal = np.zeros((n, 3), np.float32)


@dataclass
class Variant:
    name: str
    fn: Callable[[Scene, Buffers], List[tuple]]
    fmt: str
    needs: str
    gives_up: str
    note: str = ""


VARIANTS: Dict[str, Variant] = {}


def variant(name: str, *, fmt: str = "corners3", needs: str = "drop-in",
            gives_up: str = "nothing", note: str = ""):
    def wrap(fn):
        VARIANTS[name] = Variant(name, fn, fmt, needs, gives_up, note)
        return fn

    return wrap


def _cuts(registers: np.ndarray, colors: np.ndarray) -> np.ndarray:
    """Where a run ends: the diff `_emit_chain` does today."""
    n = registers.size
    cuts = np.zeros(n, dtype=bool)
    if n > 1:
        cuts[1:] = (registers[1:] != registers[:-1]) | np.any(
            colors[1:] != colors[:-1], axis=1
        )
    return cuts


def _runs_from_cuts(scene: Scene, cuts: np.ndarray, registers: np.ndarray,
                    colors: np.ndarray) -> List[tuple]:
    sheet = scene.sprite.sheet
    tileset = sheet.tilesets[sheet.primary_tileset]
    starts = np.flatnonzero(cuts)
    edges = [0, *starts.tolist(), cuts.size]
    return [
        (
            id(tileset.atlas.buffer),
            id(tileset.palettes[int(registers[begin])]),
            tuple(colors[begin].tolist()),
            begin,
            end - begin,
        )
        for begin, end in zip(edges, edges[1:])
    ]


def _one_run(scene: Scene, registers: np.ndarray, colors: np.ndarray,
             count: int) -> List[tuple]:
    sheet = scene.sprite.sheet
    tileset = sheet.tilesets[sheet.primary_tileset]
    return [
        (
            id(tileset.atlas.buffer),
            id(tileset.palettes[int(registers[0])]),
            tuple(colors[0].tolist()),
            0,
            count,
        )
    ]


# -- A: what ships today ---------------------------------------------------


@variant("current", note="the shipped `_emit_chain`, verbatim")
def stage_current(scene: Scene, buf: Buffers) -> List[tuple]:
    slots = scene.sprite._slots
    tables = scene.tables
    order = _drawable(scene.sprite, scene.chain)

    ids = slots.assembly[order]
    turns = slots.orientation[order].astype(np.intp)
    rects = tables.rect[ids, turns]
    xy = slots.position[order]

    n = order.size
    x0 = (xy[:, 0] + rects[:, 0]).astype(np.float32)
    y0 = (xy[:, 1] + rects[:, 1]).astype(np.float32)
    x1 = x0 + rects[:, 2]
    y1 = y0 + rects[:, 3]
    verts = buf.corners3[:n]
    verts[:, 0, 0], verts[:, 0, 1] = x0, y0
    verts[:, 1, 0], verts[:, 1, 1] = x1, y0
    verts[:, 2, 0], verts[:, 2, 1] = x1, y1
    verts[:, 3, 0], verts[:, 3, 1] = x0, y1
    buf.uvs3[:n] = tables.uv[ids, turns]
    buf.quads = n

    registers = slots.palette[order]
    colors = slots.color[order]
    return _runs_from_cuts(scene, _cuts(registers, colors), registers, colors)


# -- B: the access pattern -------------------------------------------------


@variant(
    "sliced",
    needs="a contiguous filtered order",
    gives_up="nothing -- but see the note",
    note="fires only when NOTHING is parked; a fountain always has corpses",
)
def stage_sliced(scene: Scene, buf: Buffers) -> List[tuple]:
    """The naive fast path: `order` is a solid run, so read slices.

    Included to be measured and then RULED OUT. It is the obvious reading of
    "the swarm's ids are contiguous", it is a real saving where it applies,
    and it does not apply: `_drawable` has already dropped the parked slots by
    the time the chain gets here, so the order is contiguous only on the frames
    where nothing died. That is why the dense variants below exist.
    """
    slots = scene.sprite._slots
    tables = scene.tables
    order = _drawable(scene.sprite, scene.chain)
    n = order.size
    first = int(order[0])
    if n and int(np.ptp(order)) != n - 1:
        raise Contiguity(f"{n:,} live slots span {int(np.ptp(order)) + 1:,}")
    where = slice(first, first + n)

    ids = slots.assembly[where]
    turns = slots.orientation[where].astype(np.intp)
    rects = tables.rect[ids, turns]
    xy = slots.position[where]

    x0 = (xy[:, 0] + rects[:, 0]).astype(np.float32)
    y0 = (xy[:, 1] + rects[:, 1]).astype(np.float32)
    x1 = x0 + rects[:, 2]
    y1 = y0 + rects[:, 3]
    verts = buf.corners3[:n]
    verts[:, 0, 0], verts[:, 0, 1] = x0, y0
    verts[:, 1, 0], verts[:, 1, 1] = x1, y0
    verts[:, 2, 0], verts[:, 2, 1] = x1, y1
    verts[:, 3, 0], verts[:, 3, 1] = x0, y1
    buf.uvs3[:n] = tables.uv[ids, turns]
    buf.quads = n

    registers = slots.palette[where]
    colors = slots.color[where]
    return _runs_from_cuts(scene, _cuts(registers, colors), registers, colors)


class Contiguity(RuntimeError):
    """A variant's precondition did not hold on this workload."""


def _dense_keep(scene: Scene) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The filter `_drawable` does, done over the SPAN and kept as a mask.

    Same three rules -- shown, presenting something, presenting one quad --
    but the answer stays a boolean per slot instead of becoming an index
    array. That is the whole difference: a mask multiplies, an index array
    gathers.
    """
    slots = scene.sprite._slots
    span = scene.span
    ids = slots.assembly[span]
    keep = slots.visible[span] & (ids >= 0)
    safe = np.maximum(ids, 0)
    keep &= scene.sprite._assembly_quads[safe] == 1
    return safe, keep, ids


@variant(
    "dense",
    needs="a contiguous CHAIN (cacheable on the chain, not per frame)",
    gives_up="uploads the parked slots as zero-area quads",
    note="stages 250,000 to avoid gathering 248,750",
)
def stage_dense(scene: Scene, buf: Buffers) -> List[tuple]:
    """Slice-read the whole chain; collapse parked slots to zero area.

    A parked slot keeps its pose and position, so it stages like any other and
    is then multiplied to nothing by the keep mask. The rasteriser discards a
    degenerate quad before it costs a fragment, so the picture is identical and
    the price is the vertex bandwidth of the corpses -- half a percent of the
    stream, against dropping every fancy-index in the function.
    """
    slots = scene.sprite._slots
    tables = scene.tables
    span = scene.span
    ids, keep, _ = _dense_keep(scene)
    turns = slots.orientation[span].astype(np.intp)
    rects = tables.rect[ids, turns]
    xy = slots.position[span]
    n = ids.size

    x0 = (xy[:, 0] + rects[:, 0]).astype(np.float32)
    y0 = (xy[:, 1] + rects[:, 1]).astype(np.float32)
    x1 = x0 + rects[:, 2] * keep
    y1 = y0 + rects[:, 3] * keep
    verts = buf.corners3[:n]
    verts[:, 0, 0], verts[:, 0, 1] = x0, y0
    verts[:, 1, 0], verts[:, 1, 1] = x1, y0
    verts[:, 2, 0], verts[:, 2, 1] = x1, y1
    verts[:, 3, 0], verts[:, 3, 1] = x0, y1
    buf.uvs3[:n] = tables.uv[ids, turns]
    buf.quads = n

    registers = slots.palette[span]
    colors = slots.color[span]
    return _runs_from_cuts(scene, _cuts(registers, colors), registers, colors)


# -- C: the representation, one piece at a time ----------------------------


@variant(
    "no-diff",
    needs="a tracked `uniform` flag on the chain",
    gives_up="nothing when the flag is true; falls back when it is not",
    note="the CEILING on any uniformity tracking, not an implementation",
)
def stage_no_diff(scene: Scene, buf: Buffers) -> List[tuple]:
    """`current`, minus the per-presentation material diff.

    The diff reads 20 bytes per presentation and compares 5 values to discover
    a boundary that a swarm does not have. What it defends is a shadow trail's
    fade -- four ghosts at four alphas -- so it is charged to 250,000 to serve
    four. This measures what removing it is worth; it does not say how the
    flag would be kept.
    """
    slots = scene.sprite._slots
    tables = scene.tables
    order = _drawable(scene.sprite, scene.chain)

    ids = slots.assembly[order]
    turns = slots.orientation[order].astype(np.intp)
    rects = tables.rect[ids, turns]
    xy = slots.position[order]

    n = order.size
    x0 = (xy[:, 0] + rects[:, 0]).astype(np.float32)
    y0 = (xy[:, 1] + rects[:, 1]).astype(np.float32)
    x1 = x0 + rects[:, 2]
    y1 = y0 + rects[:, 3]
    verts = buf.corners3[:n]
    verts[:, 0, 0], verts[:, 0, 1] = x0, y0
    verts[:, 1, 0], verts[:, 1, 1] = x1, y0
    verts[:, 2, 0], verts[:, 2, 1] = x1, y1
    verts[:, 3, 0], verts[:, 3, 1] = x0, y1
    buf.uvs3[:n] = tables.uv[ids, turns]
    buf.quads = n

    first = int(order[0])
    return _one_run(scene, slots.palette[first:first + 1],
                    slots.color[first:first + 1], n)


@variant(
    "bulk",
    needs="a contiguous chain + a tracked uniform flag",
    gives_up="parked slots uploaded as zero-area quads",
    note="dense + no-diff: the fast lane, in the CURRENT vertex format",
)
def stage_bulk(scene: Scene, buf: Buffers) -> List[tuple]:
    """The drop-in fast lane. No gather, no diff, one run, same buffers.

    This is the rung that can ship without touching a shader, so it is the one
    `sprite_sweep --stage bulk` runs end to end.
    """
    slots = scene.sprite._slots
    tables = scene.tables
    span = scene.span
    ids, keep, _ = _dense_keep(scene)
    turns = slots.orientation[span].astype(np.intp)
    rects = tables.rect[ids, turns]
    xy = slots.position[span]
    n = ids.size

    x0 = (xy[:, 0] + rects[:, 0]).astype(np.float32)
    y0 = (xy[:, 1] + rects[:, 1]).astype(np.float32)
    x1 = x0 + rects[:, 2] * keep
    y1 = y0 + rects[:, 3] * keep
    verts = buf.corners3[:n]
    verts[:, 0, 0], verts[:, 0, 1] = x0, y0
    verts[:, 1, 0], verts[:, 1, 1] = x1, y0
    verts[:, 2, 0], verts[:, 2, 1] = x1, y1
    verts[:, 3, 0], verts[:, 3, 1] = x0, y1
    buf.uvs3[:n] = tables.uv[ids, turns]
    buf.quads = n

    return _one_run(scene, slots.palette[span], slots.color[span], n)


@variant(
    "bulk-vec2",
    fmt="corners2",
    needs="a sprite-specific vertex stage (the z is shared with tiles)",
    gives_up="the tilemap's shared shader",
    note="a third off both streams: 96 B/quad -> 64 B",
)
def stage_bulk_vec2(scene: Scene, buf: Buffers) -> List[tuple]:
    """`bulk` without the constant-zero third float on either stream.

    Every quad currently carries eight floats that are always 0.0 -- four in
    the position stream and four in the UVs -- because the vertex shader is
    shared with the tile pillar and declares `vec3`. They are written, cached
    and uploaded every frame.
    """
    slots = scene.sprite._slots
    tables = scene.tables
    span = scene.span
    ids, keep, _ = _dense_keep(scene)
    turns = slots.orientation[span].astype(np.intp)
    rects = tables.rect[ids, turns]
    xy = slots.position[span]
    n = ids.size

    x0 = (xy[:, 0] + rects[:, 0]).astype(np.float32)
    y0 = (xy[:, 1] + rects[:, 1]).astype(np.float32)
    x1 = x0 + rects[:, 2] * keep
    y1 = y0 + rects[:, 3] * keep
    verts = buf.corners2[:n]
    verts[:, 0, 0], verts[:, 0, 1] = x0, y0
    verts[:, 1, 0], verts[:, 1, 1] = x1, y0
    verts[:, 2, 0], verts[:, 2, 1] = x1, y1
    verts[:, 3, 0], verts[:, 3, 1] = x0, y1
    buf.uvs2[:n] = scene.uv2[ids, turns]
    buf.quads = n

    return _one_run(scene, slots.palette[span], slots.color[span], n)


# -- D: stop expanding corners on the CPU ----------------------------------


@variant(
    "instanced",
    fmt="instance",
    needs="glDrawArraysInstanced + a vertex stage that expands the quad",
    gives_up="the shared shader and the index buffer",
    note="9 floats per quad, not 24 -- the corner expansion moves to the GPU",
)
def stage_instanced(scene: Scene, buf: Buffers) -> List[tuple]:
    """Stage the QUAD, not its corners.

    The plan currently manufactures a 6x amplification of its own state every
    frame: 16 bytes of position, pose and orientation become 96 bytes of
    corners and texture coordinates, written strided, cached, and shipped over
    the bus. A vertex shader reading `gl_VertexID & 3` reconstructs all four
    corners from `(x, y, w, h)` and `(u0, v0, du, dv)` for nothing, because it
    is already running per vertex.

    Note what happens to the UV gather. Turning a quad permutes which corner
    reads which (u, v); it does not move the box. So the `(A, 8, 4, 3)` table
    the current path gathers 12 floats from collapses to `(A, 4)` -- 32 bytes
    for the bullet sheet -- and the turn rides along as one float for the
    shader to permute by. **The eight-fold axis of that table exists only to
    pre-permute corners the CPU should not be making.**
    """
    slots = scene.sprite._slots
    tables = scene.tables
    span = scene.span
    ids, keep, _ = _dense_keep(scene)
    turns = slots.orientation[span].astype(np.intp)
    rects = tables.rect[ids, turns]
    xy = slots.position[span]
    n = ids.size

    inst = buf.instance[:n]
    inst[:, 0] = xy[:, 0] + rects[:, 0]
    inst[:, 1] = xy[:, 1] + rects[:, 1]
    inst[:, 2] = rects[:, 2] * keep
    inst[:, 3] = rects[:, 3] * keep
    inst[:, 4:8] = scene.uvbox[ids]
    inst[:, 8] = turns
    buf.quads = n

    return _one_run(scene, slots.palette[span], slots.color[span], n)


@variant(
    "instanced-fused",
    fmt="instance",
    needs="as `instanced`, plus one fused geometry table",
    gives_up="a second copy of the rect table, as float32",
    note="one gather of 8 floats instead of two of 4",
)
def stage_instanced_fused(scene: Scene, buf: Buffers) -> List[tuple]:
    """`instanced` with the rect and UV tables fused into one `(A, 8, 8)`.

    Two gathers over the same index pair read two cache lines per quad from
    two arrays; one gather reads one. The table is tiny either way -- the cost
    being removed is the second indexing pass, not a miss.
    """
    slots = scene.sprite._slots
    span = scene.span
    ids, keep, _ = _dense_keep(scene)
    turns = slots.orientation[span].astype(np.intp)
    geom = scene.fused[ids, turns]
    xy = slots.position[span]
    n = ids.size

    inst = buf.instance[:n]
    inst[:, 0] = xy[:, 0] + geom[:, 0]
    inst[:, 1] = xy[:, 1] + geom[:, 1]
    inst[:, 2] = geom[:, 2] * keep
    inst[:, 3] = geom[:, 3] * keep
    inst[:, 4:8] = geom[:, 4:8]
    inst[:, 8] = turns
    buf.quads = n

    return _one_run(scene, slots.palette[span], slots.color[span], n)


@variant(
    "instanced-minimal",
    fmt="minimal",
    needs="as `instanced`, plus the geometry table in a buffer the shader indexes",
    gives_up="a UBO will not hold it -- needs an SSBO or a buffer texture",
    note="3 floats per quad: the floor of any CPU-side approach",
)
def stage_minimal(scene: Scene, buf: Buffers) -> List[tuple]:
    """Stage the position and a key; the shader looks the geometry up.

    The limit case, and here to settle the argument rather than to be built:
    if this is not much faster than `instanced`, then the remaining cost is
    the position read and the float conversion, and no further narrowing of
    the stream is worth a shader that cannot be read.

    A parked slot is keyed to a reserved zero-area row rather than multiplied,
    since there is no width here to multiply. Real keys therefore start at 1 --
    assembly 0 at orientation 0 is a perfectly ordinary bullet and must not
    collide with "draws nothing".

    **The table does not fit a uniform block.** A row is 32 bytes and there is
    one per (assembly, orientation): the bullet sheet needs 16 of them and
    Chun-Li needs 25,584, which is 800 KB against the 16 KB a UBO is
    guaranteed. So this rung costs an SSBO or a buffer texture, and a
    dependent fetch per vertex -- which is why it is here as a floor to argue
    against rather than as a thing to build.
    """
    slots = scene.sprite._slots
    span = scene.span
    ids, keep, _ = _dense_keep(scene)
    turns = slots.orientation[span]
    xy = slots.position[span]
    n = ids.size

    out = buf.minimal[:n]
    out[:, 0] = xy[:, 0]
    out[:, 1] = xy[:, 1]
    out[:, 2] = (((ids << 3) | turns) + 1) * keep
    buf.quads = n

    return _one_run(scene, slots.palette[span], slots.color[span], n)


def _uniform(registers: np.ndarray, colors: np.ndarray) -> bool:
    """Does this whole span share one material? Conservatively, and in one pass.

    A broadcast compare against row zero, not a neighbour diff. It answers the
    strictly weaker question the lane needs -- "is there ANY variation" rather
    than "where is it" -- so it reduces to one bool instead of building a run
    list.

    **1.4 ms at 250,000, and that is a floor rather than an implementation
    detail.** Every exact form measured lands within 8% of it -- viewing the
    rows as `uint64` pairs, XOR-folding, `count_nonzero` -- because all of them
    are bound by reading a 4 MB array once. `np.ptp(colors, axis=0)` is the
    one to avoid: a strided reduction down a C-contiguous `(n, 4)` costs
    13.4 ms, an order of magnitude worse than reading the same bytes flat.

    So this is the honest version of the `no-diff` ceiling -- no flag, no
    tracking, nothing to keep true -- and what it cannot escape is that
    **merely LOOKING at a per-presentation colour costs 1.4 ms at 250,000.**
    That is the representation claim in its purest form: the cost is the
    array's existence, not the diff over it.
    """
    if registers.size < 2:
        return True
    if int(np.ptp(registers)):
        return False
    return not bool(np.any(colors != colors[0]))


@variant(
    "bulk-checked",
    needs="a contiguous chain -- nothing else",
    gives_up="parked slots uploaded as zero-area quads",
    note="`bulk` paying for its own uniformity test, every frame. Shippable.",
)
def stage_bulk_checked(scene: Scene, buf: Buffers) -> List[tuple]:
    """The lane, gated by a test it pays for rather than by a tracked flag.

    This is the rung that can ship today: it needs no new shader, no new
    invariant and no cooperation from the state arrays. If the span turns out
    to be mixed it falls back, so correctness does not depend on anyone
    remembering to keep a flag true.
    """
    slots = scene.sprite._slots
    span = scene.span
    registers = slots.palette[span]
    colors = slots.color[span]
    if not _uniform(registers, colors):
        return stage_current(scene, buf)
    return stage_bulk(scene, buf)


def install_bulk() -> None:
    """Patch `FramePlan.sync` to take the bulk lane where it can.

    Monkeypatched rather than written into the pillar, because the point of
    this exercise is to get an END-TO-END number for a candidate before
    deciding to build it. `sprite_sweep.py --stage bulk` installs this and
    then measures the engine exactly as it measures the shipped path, so the
    two rows of the comparison are produced by the same instrument.

    Three gates, and each one earns its keep:

    **Contiguity.** The chain must be one solid run of slot ids, or there is
    no slice to read.

    **Occupancy.** The lane's cost is set by the chain's SIZE and the current
    path's by its live count, so below about half full, staging everything is
    more work than gathering the few. `util/ab_stage.py --occupancy` puts the
    crossover between 25% and 50%; the gate sits at 50% so it is on the safe
    side of a curve measured on one workload.

    **Uniformity.** One material, or a trail's four fades collapse into one
    run and the frame is simply wrong.

    Anything that fails a gate falls straight back to `_drawable` +
    `_emit_chain`, so nothing about correctness depends on the lane.

    Then the live EXTENT is trimmed, which is what keeps the pillar's promise
    that a parked or blank presentation stages no quad -- an entirely parked
    chain trims to nothing and emits nothing, rather than a run of degenerate
    quads. Two `argmax` passes over a bool array, and it pays for itself on
    any pool whose dead cluster at one end.
    """

    def chain(self, sprite, tables, names, order):
        if not order.size:
            return
        slots = sprite._slots
        first, last = int(order[0]), int(order[-1])
        span = slice(first, last + 1)
        width = last - first + 1
        if (
            width != order.size
            or np.count_nonzero(slots.visible[span]) * 2 < width
            or not _uniform(slots.palette[span], slots.color[span])
        ):
            live = _drawable(sprite, order)
            tables.ensure(slots.assembly[live], names)
            self._emit_chain(sprite, tables, live)
            return

        ids = slots.assembly[span]
        keep = slots.visible[span] & (ids >= 0)
        safe = np.maximum(ids, 0)
        keep &= sprite._assembly_quads[safe] == 1
        if not keep.any():
            return
        lo = int(np.argmax(keep))
        hi = keep.size - int(np.argmax(keep[::-1]))
        if lo or hi != keep.size:
            keep = keep[lo:hi]
            safe = safe[lo:hi]
            span = slice(first + lo, first + hi)
        tables.ensure(safe, names)

        turns = slots.orientation[span].astype(np.intp)
        rects = tables.rect[safe, turns]
        xy = slots.position[span]
        n = safe.size
        begin = self.quads
        end = begin + n
        x0 = (xy[:, 0] + rects[:, 0]).astype(np.float32)
        y0 = (xy[:, 1] + rects[:, 1]).astype(np.float32)
        x1 = x0 + rects[:, 2] * keep
        y1 = y0 + rects[:, 3] * keep
        verts = self.verts[begin:end]
        verts[:, 0, 0], verts[:, 0, 1] = x0, y0
        verts[:, 1, 0], verts[:, 1, 1] = x1, y0
        verts[:, 2, 0], verts[:, 2, 1] = x1, y1
        verts[:, 3, 0], verts[:, 3, 1] = x0, y1
        self.uvs[begin:end] = tables.uv[safe, turns]
        self.quads = end

        tileset = sprite.sheet.tilesets[sprite.sheet.primary_tileset]
        self._add_run(
            (
                tileset.atlas.buffer,
                tileset.palettes[int(slots.palette[first])],
                tuple(slots.color[first].tolist()),
            ),
            begin,
            n,
        )

    def sync(self, sprite):
        names = sprite._assembly_names
        tables = self._tables_for(sprite)
        primary = _primary_parts(sprite)
        self._reserve(sprite._before.size + len(primary) + sprite._after.size)
        self.runs = []
        self.quads = 0
        chain(self, sprite, tables, names, sprite._before)
        self._emit_primary(sprite, tables, primary)
        chain(self, sprite, tables, names, sprite._after)

    FramePlan.sync = sync


# -- equivalence -----------------------------------------------------------


def _permute(uv: np.ndarray, turn: int) -> np.ndarray:
    """The corner permutation `_corners` applies, as a reorder of four rows."""
    order = [0, 1, 2, 3]
    if turn & 1:  # TRANSPOSE
        order = [order[0], order[3], order[2], order[1]]
    if turn & 2:  # FLIP_H
        order = [order[1], order[0], order[3], order[2]]
    if turn & 4:  # FLIP_V
        order = [order[3], order[2], order[1], order[0]]
    return uv[order]


def canonical(scene: Scene, buf: Buffers, fmt: str) -> Tuple[np.ndarray, np.ndarray]:
    """One variant's output as `(corners, uvs)`, whatever it staged.

    Every variant is judged on the picture it would draw, not on the bytes it
    wrote. A dense variant's zero-area quads are dropped here, because a quad
    the rasteriser discards is not part of the picture -- that is exactly the
    claim being checked.
    """
    n = buf.quads
    if fmt == "corners3":
        corners = buf.corners3[:n, :, :2].copy()
        uvs = buf.uvs3[:n, :, :2].copy()
    elif fmt == "corners2":
        corners = buf.corners2[:n].copy()
        uvs = buf.uvs2[:n].copy()
    elif fmt in ("instance", "minimal"):
        if fmt == "minimal":
            keys = buf.minimal[:n, 2].astype(np.int64)
            live = keys != 0
            index = np.maximum(keys - 1, 0)
            ids, turns = index >> 3, index & 7
            geom = scene.fused[ids, turns].copy()
            x0 = buf.minimal[:n, 0] + geom[:, 0]
            y0 = buf.minimal[:n, 1] + geom[:, 1]
            w, h = geom[:, 2] * live, geom[:, 3] * live
            box = geom[:, 4:8]
        else:
            inst = buf.instance[:n]
            x0, y0, w, h = inst[:, 0], inst[:, 1], inst[:, 2], inst[:, 3]
            box = inst[:, 4:8]
            turns = inst[:, 8].astype(np.int64)
        corners = np.empty((n, 4, 2), np.float32)
        corners[:, 0] = np.stack((x0, y0), axis=1)
        corners[:, 1] = np.stack((x0 + w, y0), axis=1)
        corners[:, 2] = np.stack((x0 + w, y0 + h), axis=1)
        corners[:, 3] = np.stack((x0, y0 + h), axis=1)
        u0, v0, du, dv = box[:, 0], box[:, 1], box[:, 2], box[:, 3]
        flat = np.empty((n, 4, 2), np.float32)
        flat[:, 0] = np.stack((u0, v0), axis=1)
        flat[:, 1] = np.stack((u0 + du, v0), axis=1)
        flat[:, 2] = np.stack((u0 + du, v0 + dv), axis=1)
        flat[:, 3] = np.stack((u0, v0 + dv), axis=1)
        uvs = flat
        for turn in np.unique(turns):
            if turn:
                rows = turns == turn
                uvs[rows] = _permute(flat[rows].transpose(1, 0, 2),
                                     int(turn)).transpose(1, 0, 2)
    else:
        raise ValueError(fmt)

    # Drop the degenerate quads: zero width or zero height draws nothing.
    area = (corners[:, 2, 0] - corners[:, 0, 0]) * (
        corners[:, 2, 1] - corners[:, 0, 1]
    )
    live = area != 0
    return corners[live], uvs[live]


def compare(baseline, candidate) -> Optional[str]:
    """Why two staged frames differ, or None if they do not."""
    (bc, bu), (cc, cu) = baseline, candidate
    if bc.shape != cc.shape:
        return f"{cc.shape[0]:,} drawn quads against {bc.shape[0]:,}"
    if not np.array_equal(bc, cc):
        worst = float(np.abs(bc - cc).max())
        return f"corners differ, worst {worst:g} px"
    if not np.allclose(bu, cu, atol=1e-6):
        worst = float(np.abs(bu - cu).max())
        return f"texture coordinates differ, worst {worst:g}"
    return None


def runs_match(baseline: List[tuple], candidate: List[tuple]) -> Optional[str]:
    """Runs are compared on their MATERIAL SEQUENCE, not on quad counts.

    Coalesced first, because "one run of 24,875" and "five adjacent runs of
    the same material" draw the same picture and only one of them is a
    difference worth reporting. Counts are deliberately dropped: a dense
    variant covers more quads than the baseline by exactly the parked ones it
    staged as degenerate, and those draw nothing -- `canonical` has already
    established that.

    What this catches is the failure that matters: a fast lane that collapses
    a trail's four fades into one run. That is not a faster frame, it is a
    different one.
    """
    def fold(runs):
        out = []
        for buffer, palette, color, first, count in runs:
            material = (buffer, palette, color)
            if not out or out[-1] != material:
                out.append(material)
        return out

    a, b = fold(baseline), fold(candidate)
    if a != b:
        return f"{len(b)} material run(s) against {len(a)}"
    return None


# -- driving ---------------------------------------------------------------


def measure(scene: Scene, name: str, repeat: int) -> dict:
    var = VARIANTS[name]
    buf = Buffers()
    buf.reserve(scene.chain.size)
    try:
        runs = var.fn(scene, buf)
    except Contiguity as why:
        return {"name": name, "skipped": str(why)}
    out = canonical(scene, buf, var.fmt)

    samples = []
    for _ in range(repeat):
        started = perf_counter()
        var.fn(scene, buf)
        samples.append((perf_counter() - started) * 1000.0)
    return {
        "name": name,
        "best": min(samples),
        "median": statistics.median(samples),
        "runs": runs,
        "out": out,
        "quads": buf.quads,
    }


def sweep(counts, names, repeat: int, dead: float) -> None:
    for count in counts:
        scene = build(count, dead)
        parked = scene.chain.size - scene.quads
        print(
            f"\n{count:,} presentations "
            f"({scene.quads:,} drawn, {parked:,} parked), best of {repeat}\n"
        )
        base = measure(scene, "current", repeat)
        rows = [base] + [
            measure(scene, name, repeat) for name in names if name != "current"
        ]
        print(f"  {'variant':<20} {'best':>8} {'median':>8} {'vs current':>11}"
              f"  {'quads':>9}  equivalent")
        print("  " + "-" * 76)
        for row in rows:
            if "skipped" in row:
                print(f"  {row['name']:<20} {'--':>8} {'--':>8} {'--':>11}"
                      f"  {'--':>9}  precondition: {row['skipped']}")
                continue
            verdict = (
                compare(base["out"], row["out"])
                or runs_match(base["runs"], row["runs"])
                or "yes"
            )
            delta = (
                "baseline"
                if row is base
                else f"{(1 - row['best'] / base['best']) * 100:+5.1f}%"
            )
            print(
                f"  {row['name']:<20} {row['best']:7.2f}m {row['median']:7.2f}m"
                f" {delta:>11}  {row['quads']:>9,}  {verdict}"
            )
        print()
        for row in rows:
            if "skipped" in row:
                continue
            var = VARIANTS[row["name"]]
            print(f"  {row['name']:<20} needs {var.needs}")
            print(f"  {'':<20} gives up {var.gives_up}")
            if var.note:
                print(f"  {'':<20} -- {var.note}")


def _fuse(scene: Scene) -> np.ndarray:
    """`(assemblies, 8, 8)` float32: the rect and the UV box in one row."""
    rect = scene.tables.rect.astype(np.float32)
    boxes = scene.uvbox
    fused = np.zeros(rect.shape[:2] + (8,), np.float32)
    fused[..., :4] = rect
    fused[..., 4:] = boxes[:, None, :]
    return np.ascontiguousarray(fused)


def trail(repeat: int) -> None:
    """Does the fast lane tax the case it was taken away from?

    The whole trade is "make the trail scrappier, make the bulk a monster", so
    the cost of DECIDING which lane to take has to be small against a trail's
    own staging -- otherwise four ghosts pay for twenty-five thousand bullets'
    optimisation, which is the mistake being undone, pointing the other way.
    """
    print("\na four-ghost trail: what the lane check costs the small case\n")
    scene = build(4, dead=0.0)
    # Four ghosts at four alphas: the population the current path is for, and
    # the one it charges 250,000 bullets to defend.
    scene.sprite._slots.color[scene.span, 3] = [1.0, 0.75, 0.5, 0.25]

    base = measure(scene, "current", repeat * 20)
    rows = [base] + [
        measure(scene, name, repeat * 20) for name in ("dense", "bulk")
    ]
    print(f"  {'variant':<20} {'best':>9}  {'runs':>4}  equivalent")
    print("  " + "-" * 58)
    for row in rows:
        verdict = (
            compare(base["out"], row["out"])
            or runs_match(base["runs"], row["runs"])
            or "yes"
        )
        print(f"  {row['name']:<20} {row['best'] * 1000:8.1f}u"
              f"  {len(row['runs']):>4}  {verdict}")
    print()
    print("  `bulk` is WRONG here, and that is the point: a trail's four fades")
    print("  are four materials, so collapsing them to one run draws a")
    print("  different picture. The uniform flag is a correctness gate, not a")
    print("  tuning knob -- and the fade is exactly what the general path is")
    print("  FOR. Costing it a few microseconds on four presentations is the")
    print("  trade; costing it to 250,000 bullets is what the sweep found.\n")


def occupancy(count: int, repeat: int) -> None:
    """Where the dense lane stops paying: a big pool, mostly parked.

    The lane stages the whole chain and multiplies the corpses to nothing, so
    its cost is set by the chain's SIZE while the current path's is set by the
    live count. There is therefore an occupancy below which staging everything
    is more work than gathering the few -- and a gate that does not know it
    would make a mostly-idle pool slower, which is the exact mistake this
    whole exercise is unwinding, pointing the other way.

    A swarm sits at the top of this table by construction: it exists to be
    full, and a fountain runs at 99.5%. A game that allocates 250,000 slots to
    use 10,000 of them is the case the gate has to catch.
    """
    print(f"\na {count:,}-slot chain at falling occupancy, best of {repeat}\n")
    print(f"  {'occupancy':>9}  {'drawn':>9}  {'current':>9}  {'bulk':>9}"
          f"  {'vs current':>11}")
    print("  " + "-" * 58)
    for fraction in (1.0, 0.75, 0.5, 0.25, 0.1, 0.05, 0.02):
        scene = build(count, dead=1.0 - fraction)
        base = measure(scene, "current", repeat)
        lane = measure(scene, "bulk", repeat)
        verdict = compare(base["out"], lane["out"]) or "ok"
        delta = (1 - lane["best"] / base["best"]) * 100
        flag = "" if delta > 0 else "   <- the lane now COSTS"
        print(
            f"  {fraction * 100:8.0f}%  {scene.quads:>9,}"
            f"  {base['best']:8.2f}m  {lane['best']:8.2f}m"
            f"  {delta:+10.1f}%{flag}"
            + ("" if verdict == "ok" else f"  [{verdict}]")
        )
    print("\n  A fountain runs at 99.5%. The gate is not about the common case;")
    print("  it is about not making an idle pool worse than it is today.\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--counts", default=",".join(str(c) for c in DEFAULT_COUNTS))
    parser.add_argument("--only", default="", help="comma-separated variant names")
    parser.add_argument("--repeat", type=int, default=REPEAT)
    parser.add_argument("--dead", type=float, default=DEAD_FRACTION,
                        help="fraction of the chain parked (0 makes `sliced` fire)")
    parser.add_argument("--trail", action="store_true",
                        help="only the four-ghost case")
    parser.add_argument("--occupancy", type=int, metavar="SLOTS",
                        help="sweep how full a chain of this size is")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)

    if args.list:
        for name, var in VARIANTS.items():
            print(f"{name:<20} {var.note}")
        return 0
    if args.trail:
        trail(args.repeat)
        return 0
    if args.occupancy:
        occupancy(args.occupancy, args.repeat)
        return 0

    names = [n for n in args.only.split(",") if n] or list(VARIANTS)
    unknown = [n for n in names if n not in VARIANTS]
    if unknown:
        raise SystemExit(f"no such variant: {', '.join(unknown)}")
    counts = [int(c) for c in args.counts.split(",")]
    sweep(counts, names, args.repeat, args.dead)
    trail(args.repeat)
    return 0


if __name__ == "__main__":
    sys.exit(main())
