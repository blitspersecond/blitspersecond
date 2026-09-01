"""Isolate the array work inside FramePlan._emit_chain: no GL, no engine.

    util/staging_bench.py

`util/sprite_sweep.py` says the frame-plan rebuild is 96% of a sprite frame.
This says which part of the rebuild, by replicating exactly what the chain
path materialises per frame and re-timing the same result computed
differently. It reproduces the in-engine figure closely enough to be trusted
-- 27.1ms here against 31.2ms measured at 250,000 -- so a saving measured here
is a saving worth writing.

**Measurement only.** Nothing below is a proposed patch; it sizes candidates
so the optimisation is chosen from numbers rather than from taste. It models a
SWARM specifically -- one tileset, two assemblies, no rotation, uniform colour
-- because that is the population the ceiling is set by. A trail or a
multipart principal has different proportions.

See the board card *Close the sprite compose gap against the reference*.
"""
import numpy as np
from time import perf_counter

N = 250_000
REPEAT = 20
ORIENTATIONS = 8
ASSEMBLIES = 2

rng = np.random.default_rng(7)
# The state a swarm actually holds.
order = np.arange(N, dtype=np.int32)          # contiguous: a swarm never relinks
assembly = rng.integers(0, ASSEMBLIES, N).astype(np.int32)
orientation = np.zeros(N, dtype=np.uint8)     # a bullet never turns
position = rng.integers(0, 640, (N, 2)).astype(np.int32)
palette = np.zeros(N, dtype=np.int32)
color = np.ones((N, 4), dtype=np.float32)
rect_table = rng.integers(0, 16, (ASSEMBLIES, ORIENTATIONS, 4)).astype(np.int32)
uv_table = rng.random((ASSEMBLIES, ORIENTATIONS, 4, 3)).astype(np.float32)

verts3 = np.zeros((N, 4, 3), dtype=np.float32)
uvs3 = np.zeros((N, 4, 3), dtype=np.float32)
verts2 = np.zeros((N, 4, 2), dtype=np.float32)
uvs2 = np.zeros((N, 4, 2), dtype=np.float32)
uv_table2 = np.ascontiguousarray(uv_table[..., :2])


def timed(label, fn):
    fn()
    best = min(
        (lambda s=perf_counter(): (fn(), perf_counter() - s)[1])() for _ in range(REPEAT)
    )
    print(f"  {label:<44} {best * 1000:7.2f} ms")
    return best


def as_is():
    ids = assembly[order]
    turns = orientation[order].astype(np.intp)
    rects = rect_table[ids, turns]
    xy = position[order]
    x0 = (xy[:, 0] + rects[:, 0]).astype(np.float32)
    y0 = (xy[:, 1] + rects[:, 1]).astype(np.float32)
    x1 = x0 + rects[:, 2]
    y1 = y0 + rects[:, 3]
    verts3[:, 0, 0], verts3[:, 0, 1] = x0, y0
    verts3[:, 1, 0], verts3[:, 1, 1] = x1, y0
    verts3[:, 2, 0], verts3[:, 2, 1] = x1, y1
    verts3[:, 3, 0], verts3[:, 3, 1] = x0, y1
    uvs3[:] = uv_table[ids, turns]
    registers = palette[order]
    colors = color[order]
    cuts = np.zeros(N, dtype=bool)
    cuts[1:] = (registers[1:] != registers[:-1]) | np.any(
        colors[1:] != colors[:-1], axis=1
    )
    np.flatnonzero(cuts)


def sliced():
    """Same output, but the chain is contiguous so nothing is gathered."""
    ids = assembly
    turns = orientation.astype(np.intp)
    rects = rect_table[ids, turns]
    xy = position
    x0 = (xy[:, 0] + rects[:, 0]).astype(np.float32)
    y0 = (xy[:, 1] + rects[:, 1]).astype(np.float32)
    x1 = x0 + rects[:, 2]
    y1 = y0 + rects[:, 3]
    verts3[:, 0, 0], verts3[:, 0, 1] = x0, y0
    verts3[:, 1, 0], verts3[:, 1, 1] = x1, y0
    verts3[:, 2, 0], verts3[:, 2, 1] = x1, y1
    verts3[:, 3, 0], verts3[:, 3, 1] = x0, y1
    uvs3[:] = uv_table[ids, turns]
    registers = palette
    colors = color
    cuts = np.zeros(N, dtype=bool)
    cuts[1:] = (registers[1:] != registers[:-1]) | np.any(
        colors[1:] != colors[:-1], axis=1
    )
    np.flatnonzero(cuts)


def vec2():
    """Sliced, and without the constant-zero third float on both arrays."""
    ids = assembly
    turns = orientation.astype(np.intp)
    rects = rect_table[ids, turns]
    x0 = (position[:, 0] + rects[:, 0]).astype(np.float32)
    y0 = (position[:, 1] + rects[:, 1]).astype(np.float32)
    x1 = x0 + rects[:, 2]
    y1 = y0 + rects[:, 3]
    verts2[:, 0, 0], verts2[:, 0, 1] = x0, y0
    verts2[:, 1, 0], verts2[:, 1, 1] = x1, y0
    verts2[:, 2, 0], verts2[:, 2, 1] = x1, y1
    verts2[:, 3, 0], verts2[:, 3, 1] = x0, y1
    uvs2[:] = uv_table2[ids, turns]
    cuts = np.zeros(N, dtype=bool)
    cuts[1:] = (palette[1:] != palette[:-1]) | np.any(
        color[1:] != color[:-1], axis=1
    )
    np.flatnonzero(cuts)


print(f"\n{N:,} presentations, best of {REPEAT}\n")
base = timed("as-is (gathered through `order`)", as_is)
s = timed("contiguous chain: slices, no gather", sliced)
v = timed("...and vec2 verts/uvs (no constant z)", vec2)

print(f"\n  slices alone            {(1 - s / base) * 100:5.1f}% off")
print(f"  slices + vec2           {(1 - v / base) * 100:5.1f}% off")

print("\n  component costs, as-is:")
for label, fn in (
    ("gathers through order", lambda: (assembly[order], orientation[order],
                                       position[order], palette[order], color[order])),
    ("uv table gather -> (N,4,3)", lambda: uv_table[assembly, orientation.astype(np.intp)]),
    ("8 strided writes into (N,4,3)", lambda: [
        verts3.__setitem__((slice(None), k, j), 1.0)
        for k in range(4) for j in range(2)]),
    ("colour diff (N,4 float32)", lambda: np.any(color[1:] != color[:-1], axis=1)),
):
    timed(label, fn)

footprint = {
    "slot state": 8 + 1 + 4 + 1 + 1 + 16 + 4 + 1,
    "verts (N,4,3) f32": 48,
    "uvs (N,4,3) f32": 48,
}
per = sum(footprint.values())
print(f"\n  resident bytes per presentation: {per}")
for name, size in footprint.items():
    print(f"    {name:<22} {size:>4} B")
print(f"\n  at {N:,}: {per * N / 2**20:.1f} MiB resident")
for l3 in (96, 32, 16, 8):
    print(f"    spills a {l3:>2} MiB L3 above {l3 * 2**20 // per:>9,} presentations")
