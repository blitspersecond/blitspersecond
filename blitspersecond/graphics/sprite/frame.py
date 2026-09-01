"""The frame plan: presentation state projected into quads and runs.

No GL here. This module turns "where everything is" into two float arrays and
a list of draw runs; `_composite` in the draw step issues them. Keeping the
projection separate is what makes it testable without a window, and it is
where the pillar's cost actually lives.

## The shape of a frame

    walk the before chain   -- one quad each
    emit the primary        -- one quad per part
    walk the after chain    -- one quad each

In that order, always, with no sort. The chains ARE the draw order (see
`presentation.py`), so there is nothing to decide here about sequence -- only
about where one run of quads ends and the next begins.

## Runs, and why they are cheap

A **run** is a maximal stretch of adjacent quads sharing a material:
`(buffer, palette, colour registers)`. Coalescing is **strictly
order-preserving** -- never reordering across a material boundary -- because
that is exactly where transparency stops commuting.

Boundaries are found by a vectorised diff rather than by walking, which
matters: a population of 25,000 must not be inspected one at a time. And
because instanced presentations are pinned to the sheet's `primary_tileset`,
the buffer is constant across a whole chain by construction, so the only
things that can break a run there are the palette register and the colour.

A uniform population therefore finds ZERO boundaries and emits ONE run --
the same result reference got by holding its material once per pool, reached
here without a second representation.

## The one orientation formula

`local_rect` is shared by draw staging and (later) collision stamping **so the
two cannot drift**. It is one function and stays one function: transpose swaps
the axes, then the two mirrors reflect about the sprite's origin, matching the
order the flag bits are documented in.

The per-assembly tables it fills are computed once per orientation and reused
for every presentation, so a 25,000-bullet population of two kinds fills
sixteen rows and gathers the rest.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Tuple

import numpy as np

from blitspersecond.common import Location, Rect

from ..orientation import Orientation
from .presentation import PRIMARY

if TYPE_CHECKING:
    from .sheet import SpriteSheet
    from .sprite import Sprite


#: `(buffer, palette, colour registers)` -- what a run is constant in, and
#: what the draw binds before issuing it.
Material = Tuple[object, object, Tuple[float, float, float, float]]

#: One draw: its material and the slice of the staging arrays it covers.
Run = Tuple[object, object, Tuple[float, float, float, float], int, int]

#: The eight orientations, as the index the tables are keyed by.
ORIENTATIONS = 8

#: The flag bits as plain ints. See `local_rect` for why they are not used as
#: `Orientation` members in the hot path.
_TRANSPOSE = int(Orientation.TRANSPOSE)
_FLIP_H = int(Orientation.FLIP_H)
_FLIP_V = int(Orientation.FLIP_V)


def local_rect(
    orientation: int,
    offset: Location,
    source: Rect,
    origin: Location,
) -> Tuple[int, int, int, int]:
    """Where one part lands relative to the sprite's position.

        drawn at = position + local_rect(...)

    The part sits at `offset` in the artist's frame; `origin` is the point the
    sprite is placed and turned about, so the local frame is `offset - origin`
    and the orientation acts about `(0, 0)` in it.

    **Order is transpose, then mirrors**, which is what the flag bits already
    document (`TRANSPOSE` is "applied first") and what the tile pillar's flip
    bits speak. Getting it backwards is invisible on a square, symmetric
    source and wrong on everything else.

    Returns `(x, y, width, height)` as plain ints. Width and height swap under
    the four transposing orientations -- which is the whole reason a sprite
    may take all eight where a grid tile may not: a sprite has no cell to
    return to and no neighbours to overflow.
    """
    # Masked as a plain int, not as the flag. `Orientation` is an `IntFlag`
    # and `&` on one routes through the enum machinery -- which showed up as
    # the single largest cost in the small-sprite profile, on a function that
    # does six comparisons.
    turn = int(orientation)
    x = offset.x - origin.x
    y = offset.y - origin.y
    width, height = source.width, source.height
    if turn & _TRANSPOSE:
        x, y = y, x
        width, height = height, width
    if turn & _FLIP_H:
        x = -(x + width)
    if turn & _FLIP_V:
        y = -(y + height)
    return int(x), int(y), int(width), int(height)


class Tables:
    """Per-assembly, per-orientation local rects and texture coordinates.

    Both are static for a given sheet and origin, so each `(assembly,
    orientation)` pair is computed once and gathered thereafter. Rows are
    filled on first use rather than up front: a sheet of 3198 assemblies has
    no business building 25,584 variants for the six a game shows.

    Only SINGLE-PART assemblies get a row. Multipart poses belong to the
    primary alone and are walked directly, because a row here is one quad by
    construction -- which is the same restriction that lets the chains share
    one representation.

    **Rebuilt when the origin moves**, because the origin is inside the
    formula. That is per assembly rather than per presentation, so it is
    cheap even for a large population.
    """

    __slots__ = ("_sheet", "_origin", "_rect", "_uv", "_built")

    def __init__(self, sheet: "SpriteSheet", origin: Location) -> None:
        self._sheet = sheet
        self._origin = origin
        count = max(1, len(sheet.assemblies))
        self._rect = np.zeros((count, ORIENTATIONS, 4), dtype=np.int32)
        # Four corners of (u, v, r) -- pyglet's tex_coords layout, so the draw
        # step can hand these straight to a vertex list with no repacking.
        self._uv = np.zeros((count, ORIENTATIONS, 4, 3), dtype=np.float32)
        self._built = np.zeros(count, dtype=bool)

    @property
    def origin(self) -> Location:
        return self._origin

    @property
    def rect(self) -> np.ndarray:
        return self._rect

    @property
    def uv(self) -> np.ndarray:
        return self._uv

    def ensure(self, ids: np.ndarray, names: Tuple[str, ...]) -> None:
        """Fill any of `ids` not yet computed. Vectorised in the common case
        of "all of them already are"."""
        missing = ids[~self._built[ids]]
        if not missing.size:
            return
        for index in np.unique(missing):
            self._fill(int(index), names)

    def _fill(self, index: int, names: Tuple[str, ...]) -> None:
        assembly = self._sheet.assemblies[names[index]]
        self._built[index] = True
        if len(assembly.parts) != 1:
            # Blank poses and multipart poses both land here. Neither has a
            # single quad to describe, and neither is ever gathered: a blank
            # contributes nothing and a multipart one is the primary's, walked
            # part by part. Marked built so it is not retried every frame.
            return
        part = assembly.parts[0]
        tileset = self._sheet.tilesets[part.tileset]
        source = tileset.atlas.rect(part.tile)
        image_w, image_h = tileset.atlas.size
        for orientation in range(ORIENTATIONS):
            self._rect[index, orientation] = local_rect(
                orientation, part.offset, source, self._origin
            )
            self._uv[index, orientation] = _corners(
                source, orientation, image_w, image_h
            )


def _corners(
    source: Rect, orientation: int, image_w: int, image_h: int
) -> np.ndarray:
    """The four (u, v, r) texture corners for one source rect, turned.

    Computed from the image's dimensions rather than from a `Texture`, so the
    whole plan stays GL-free and testable in a headless run. That holds
    because a `PixelBuffer` maps one-to-one onto its texture; if a backend
    ever pads to a power of two, the draw step is where that has to be caught.

    The turn is applied by PERMUTING the corners rather than by moving them:
    the same four points in a different order is exactly what a mirror or a
    quarter turn is, and it keeps the sampled region identical so no
    half-texel drift can creep in.
    """
    u0 = source.x / image_w
    v0 = source.y / image_h
    u1 = (source.x + source.width) / image_w
    v1 = (source.y + source.height) / image_h
    # Top-left, top-right, bottom-right, bottom-left -- the winding the
    # staging writes its position corners in.
    uv = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
    turn = int(orientation)
    if turn & _TRANSPOSE:
        uv = [uv[0], uv[3], uv[2], uv[1]]
    if turn & _FLIP_H:
        uv = [uv[1], uv[0], uv[3], uv[2]]
    if turn & _FLIP_V:
        uv = [uv[3], uv[2], uv[1], uv[0]]
    return np.array([(u, v, 0.0) for u, v in uv], dtype=np.float32)


class FramePlan:
    """Staging arrays and a run list, rebuilt only when something moved.

    Retained rather than rebuilt from scratch: the corner arrays grow by
    doubling and are rewritten in place, so a steady scene allocates nothing
    at all. `quads` says how much of them is live this frame.
    """

    __slots__ = ("verts", "uvs", "runs", "quads", "_tables")

    def __init__(self) -> None:
        self.verts = np.zeros((0, 4, 3), dtype=np.float32)
        self.uvs = np.zeros((0, 4, 3), dtype=np.float32)
        self.runs: List[Run] = []
        self.quads = 0
        self._tables: Optional[Tables] = None

    def _reserve(self, quads: int) -> None:
        if quads <= self.verts.shape[0]:
            return
        size = max(8, self.verts.shape[0])
        while size < quads:
            size *= 2
        self.verts = np.zeros((size, 4, 3), dtype=np.float32)
        self.uvs = np.zeros((size, 4, 3), dtype=np.float32)

    def _tables_for(self, sprite: "Sprite") -> Tables:
        tables = self._tables
        if tables is None or tables.origin != sprite.origin:
            tables = self._tables = Tables(sprite.sheet, sprite.origin)
        return tables

    def sync(self, sprite: "Sprite") -> None:
        """Rebuild the plan from the sprite's presentations."""
        slots = sprite._slots
        tables = self._tables_for(sprite)
        names = sprite._assembly_names

        before = _drawable(sprite, sprite._before)
        after = _drawable(sprite, sprite._after)
        primary = _primary_parts(sprite)

        tables.ensure(slots.assembly[before], names)
        tables.ensure(slots.assembly[after], names)

        self._reserve(before.size + len(primary) + after.size)
        self.runs = []
        self.quads = 0
        self._emit_chain(sprite, tables, before)
        self._emit_primary(sprite, tables, primary)
        self._emit_chain(sprite, tables, after)

    def _emit_chain(
        self, sprite: "Sprite", tables: Tables, order: np.ndarray
    ) -> None:
        """One chain's quads, staged in bulk and cut into runs by a diff."""
        if not order.size:
            return
        slots = sprite._slots
        ids = slots.assembly[order]
        turns = slots.orientation[order].astype(np.intp)
        rects = tables.rect[ids, turns]
        xy = slots.position[order]

        first = self.quads
        last = first + order.size
        x0 = (xy[:, 0] + rects[:, 0]).astype(np.float32)
        y0 = (xy[:, 1] + rects[:, 1]).astype(np.float32)
        x1 = x0 + rects[:, 2]
        y1 = y0 + rects[:, 3]
        verts = self.verts[first:last]
        verts[:, 0, 0], verts[:, 0, 1] = x0, y0
        verts[:, 1, 0], verts[:, 1, 1] = x1, y0
        verts[:, 2, 0], verts[:, 2, 1] = x1, y1
        verts[:, 3, 0], verts[:, 3, 1] = x0, y1
        self.uvs[first:last] = tables.uv[ids, turns]
        self.quads = last

        # The buffer is constant across a chain -- instanced presentations are
        # pinned to the primary tileset -- so only the palette register and
        # the colour can cut a run, and both are found by diff rather than by
        # walking 25,000 slots one at a time.
        #
        # **This is an invariant the bind DEPENDS on, not a preference.** The
        # buffer and palette are read once, here, and the per-slot assemblies
        # are never consulted for them; off-tileset art in a chain would gather
        # UVs for its real tileset and draw them through this one. The
        # `assembly` setter and presentation seeding both refuse it, and no
        # public door bypasses them -- so it is checked at the WRITE and not
        # here. A staging-time guard was tried and removed: 77 us a frame at
        # 25,000 to defend a door that does not exist. **Anything that adds a
        # bulk write into the state arrays must validate it there.**
        tileset = sprite.sheet.tilesets[sprite.sheet.primary_tileset]
        registers = slots.palette[order]
        colors = slots.color[order]
        cuts = np.zeros(order.size, dtype=bool)
        if order.size > 1:
            cuts[1:] = (registers[1:] != registers[:-1]) | np.any(
                colors[1:] != colors[:-1], axis=1
            )
        starts = np.flatnonzero(cuts)
        edges = [0, *starts.tolist(), order.size]
        for begin, end in zip(edges, edges[1:]):
            self._add_run(
                (
                    tileset.atlas.buffer,
                    tileset.palettes[int(registers[begin])],
                    tuple(colors[begin].tolist()),
                ),
                first + begin,
                end - begin,
            )

    def _emit_primary(self, sprite: "Sprite", tables: Tables, parts) -> None:
        """The primary's parts, walked. One or two of them, so a loop is the
        right shape here and a gather is not.

        A SINGLE-part pose still reads its rect and corners out of the same
        cached table the chains gather from -- it is keyed by assembly, not by
        who is presenting it, so recomputing them every frame was pure waste
        on 3147 of the fixture's 3198 poses. Only a multipart pose, which has
        no single row to describe it, is computed here.
        """
        if not parts:
            return
        slots = sprite._slots
        turn = int(slots.orientation[PRIMARY])
        px, py = (int(v) for v in slots.position[PRIMARY])
        color = tuple(slots.color[PRIMARY].tolist())
        origin = sprite.origin
        cached = None
        if len(parts) == 1:
            cached = int(slots.assembly[PRIMARY])
            tables.ensure(np.array([cached], dtype=np.int32), sprite._assembly_names)
        for part in parts:
            tileset = sprite.sheet.tilesets[part.tileset]
            if cached is not None:
                x, y, width, height = (
                    int(v) for v in tables.rect[cached, turn]
                )
            else:
                source = tileset.atlas.rect(part.tile)
                x, y, width, height = local_rect(
                    turn, part.offset, source, origin
                )
            index = self.quads
            x0, y0 = float(px + x), float(py + y)
            x1, y1 = x0 + width, y0 + height
            self.verts[index, 0, 0], self.verts[index, 0, 1] = x0, y0
            self.verts[index, 1, 0], self.verts[index, 1, 1] = x1, y0
            self.verts[index, 2, 0], self.verts[index, 2, 1] = x1, y1
            self.verts[index, 3, 0], self.verts[index, 3, 1] = x0, y1
            if cached is not None:
                self.uvs[index] = tables.uv[cached, turn]
            else:
                image_w, image_h = tileset.atlas.size
                self.uvs[index] = _corners(source, turn, image_w, image_h)
            self.quads += 1
            self._add_run(
                (
                    tileset.atlas.buffer,
                    tileset.palettes[sprite.palettes[part.tileset]],
                    color,
                ),
                index,
                1,
            )

    def _add_run(self, material: Material, first: int, count: int) -> None:
        """Extend the last run if the material matches, else start a new one.

        Adjacent-only, never reordering: two stretches of the same material
        with a different one between them stay two runs, because merging them
        would move quads past a material boundary and transparency does not
        commute across one.
        """
        if self.runs:
            buffer, palette, color, begin, length = self.runs[-1]
            if (buffer, palette, color) == material and begin + length == first:
                self.runs[-1] = (buffer, palette, color, begin, length + count)
                return
        self.runs.append((*material, first, count))


def _drawable(sprite: "Sprite", order: np.ndarray) -> np.ndarray:
    """The slots in a chain that will actually produce a quad.

    Three ways not to: parked, presenting nothing, or presenting a BLANK pose
    -- one with no parts at all, which is legal everywhere and must contribute
    nothing rather than a zero-area quad. All three are dropped here rather
    than staged and skipped later, so a parked population costs one mask and
    no quads.
    """
    if not order.size:
        return order
    slots = sprite._slots
    ids = slots.assembly[order]
    keep = slots.visible[order] & (ids >= 0)
    keep[keep] &= sprite._assembly_quads[ids[keep]] == 1
    return order[keep]


def _primary_parts(sprite: "Sprite"):
    """The primary's parts in authored order, or `()` if it draws nothing."""
    slots = sprite._slots
    if not slots.visible[PRIMARY]:
        return ()
    index = int(slots.assembly[PRIMARY])
    if index < 0:
        return ()
    return sprite.sheet.assemblies[sprite._assembly_names[index]].parts
