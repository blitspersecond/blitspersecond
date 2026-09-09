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
`(buffer, palette bank, palette row, colour registers)`. Coalescing is
**strictly order-preserving** -- never reordering across a material boundary
-- because that is exactly where transparency stops commuting.

Boundaries are found by a vectorised diff rather than by walking, which
matters: a population of 25,000 must not be inspected one at a time. And
because instanced presentations are pinned to the sheet's `primary_tileset`,
the buffer and the bank are constant across a whole chain by construction and
the colour belongs to the sprite, so the only thing that can break a run there
is the palette ROW.

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


#: `(buffer, palette bank, palette row, colour registers)` -- what a run is
#: constant in, and what the draw binds or sets before issuing it. The bank
#: and the row are two fields rather than the `Palette` this held until
#: 2026-09-02, because a bank is ONE texture: only the bank is bound, and the
#: row is a number fed to the vertex stage.
Material = Tuple[object, object, int, Tuple[float, float, float, float]]

#: One draw: its material and the slice of the staging arrays it covers.
Run = Tuple[object, object, int, Tuple[float, float, float, float], int, int]

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

    __slots__ = ("_sheet", "_origin", "_rect", "_box", "_quad", "_built")

    def __init__(self, sheet: "SpriteSheet", origin: Location) -> None:
        self._sheet = sheet
        self._origin = origin
        count = max(1, len(sheet.assemblies))
        self._rect = np.zeros((count, ORIENTATIONS, 4), dtype=np.int32)
        # `(u0, v0, du, dv)` per assembly, and NOT per orientation: turning a
        # quad permutes which corner reads which (u, v) without moving the
        # box, so the eight-fold axis this table used to carry existed only to
        # pre-permute corners the CPU should not have been making. The vertex
        # stage permutes now -- see `_source_corner`, which is the definition
        # it mirrors.
        self._box = np.zeros((count, 4), dtype=np.float32)
        # rect and box FUSED, as float32, for the staging gather: eight floats
        # per (assembly, orientation) so a chain reads one row per quad rather
        # than indexing two tables with the same pair. The box is duplicated
        # across the eight orientations to buy that, which for Chun-Li's 3198
        # assemblies is 818 KB of table against a saving measured per frame.
        self._quad = np.zeros((count, ORIENTATIONS, 8), dtype=np.float32)
        self._built = np.zeros(count, dtype=bool)

    @property
    def origin(self) -> Location:
        return self._origin

    @property
    def rect(self) -> np.ndarray:
        return self._rect

    @property
    def box(self) -> np.ndarray:
        return self._box

    @property
    def quad(self) -> np.ndarray:
        """`(assembly, orientation, 8)` of `(x, y, w, h, u0, v0, du, dv)`."""
        return self._quad

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
        image_w, image_h = tileset.atlas.buffer.size
        for orientation in range(ORIENTATIONS):
            self._rect[index, orientation] = local_rect(
                orientation, part.offset, source, self._origin
            )
        self._box[index] = uv_box(source, image_w, image_h)
        self._quad[index, :, :4] = self._rect[index]
        self._quad[index, :, 4:] = self._box[index]


def uv_box(source: Rect, image_w: int, image_h: int) -> Tuple[float, float, float, float]:
    """`(u0, v0, du, dv)` -- one source rect as a normalised box.

    Independent of orientation, which is the point: a turn changes which
    corner reads which (u, v), not where the box is. Computed from the
    image's dimensions rather than from a `Texture`, so the plan stays GL-free
    and testable headless.
    """
    return (
        source.x / image_w,
        source.y / image_h,
        source.width / image_w,
        source.height / image_h,
    )


def _source_corner(corner: int, orientation: int) -> int:
    """Which of the four (u, v) corners output `corner` reads, when turned.

    **The definition `quads.vert` mirrors.** It is here, in Python, so the
    rule can be tested -- a shader cannot be -- and so there is one statement
    of it rather than two that drift.

    `_corners` composes transpose, then FLIP_H, then FLIP_V, and each of those
    maps an output corner to an input one. Undoing them to find the source
    therefore runs in the OPPOSITE order: V, then H, then transpose. Getting
    that backwards is invisible on a square, symmetric source and wrong on
    everything else.
    """
    turn = int(orientation)
    source = int(corner)
    if turn & _FLIP_V:
        source = 3 - source
    if turn & _FLIP_H:
        source = source ^ 1
    if turn & _TRANSPOSE:
        source = (4 - source) % 4
    return source


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

    __slots__ = ("data", "runs", "quads", "_tables")

    #: Floats per quad in `data`: x, y, w, h, u0, v0, du, dv, turn. The layout
    #: `quads.vert` reads by `gl_VertexID / 4`, and the reason this pillar
    #: stages 36 bytes a quad where it used to manufacture 96.
    RECORD = 9

    def __init__(self) -> None:
        self.data = np.zeros((0, self.RECORD), dtype=np.float32)
        self.runs: List[Run] = []
        self.quads = 0
        self._tables: Optional[Tables] = None

    def _reserve(self, quads: int) -> None:
        if quads <= self.data.shape[0]:
            return
        size = max(8, self.data.shape[0])
        while size < quads:
            size *= 2
        self.data = np.zeros((size, self.RECORD), dtype=np.float32)

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
        xy = slots.position[order]

        first = self.quads
        last = first + order.size
        geometry = tables.quad[ids, turns]
        record = self.data[first:last]
        record[:, 0] = xy[:, 0] + geometry[:, 0]
        record[:, 1] = xy[:, 1] + geometry[:, 1]
        record[:, 2:8] = geometry[:, 2:8]
        record[:, 8] = turns
        self.quads = last

        # The buffer and the bank are constant across a chain -- instanced
        # presentations are pinned to the primary tileset -- and so is the
        # colour, which belongs to the sprite. **So the palette ROW is the
        # only thing left that can cut a run here**, and it is found by a diff
        # over one int32 array rather than by walking 25,000 slots.
        #
        # It used to diff the colour too, over an `(n, 4)` float32: 3.70ms at
        # 250,000 presentations, to discover boundaries a population does not
        # have. That went with the per-presentation colour registers on
        # 2026-09-02 -- see `Presentation.red`.
        #
        # **This is an invariant the bind DEPENDS on, not a preference.** The
        # buffer and bank are read once, here, and the per-slot assemblies
        # are never consulted for them; off-tileset art in a chain would gather
        # UVs for its real tileset and draw them through this one. The
        # `assembly` setter and presentation seeding both refuse it, and no
        # public door bypasses them -- so it is checked at the WRITE and not
        # here. A staging-time guard was tried and removed: 77 us a frame at
        # 25,000 to defend a door that does not exist. **Anything that adds a
        # bulk write into the state arrays must validate it there.**
        tileset = sprite.sheet.tilesets[sprite.sheet.primary_tileset]
        registers = slots.palette[order]
        color = _tint(sprite)
        cuts = np.zeros(order.size, dtype=bool)
        if order.size > 1:
            cuts[1:] = registers[1:] != registers[:-1]
        starts = np.flatnonzero(cuts)
        edges = [0, *starts.tolist(), order.size]
        for begin, end in zip(edges, edges[1:]):
            self._add_run(
                (
                    tileset.atlas.buffer,
                    sprite._bank(sprite.sheet.primary_tileset),
                    int(registers[begin]),
                    color,
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
        color = _tint(sprite)
        origin = sprite.origin
        cached = None
        if len(parts) == 1:
            cached = int(slots.assembly[PRIMARY])
            tables.ensure(np.array([cached], dtype=np.int32), sprite._assembly_names)
        for part in parts:
            tileset = sprite.sheet.tilesets[part.tileset]
            if cached is not None:
                x, y, width, height = (
                    float(v) for v in tables.rect[cached, turn]
                )
                box = tables.box[cached]
            else:
                source = tileset.atlas.rect(part.tile)
                x, y, width, height = local_rect(
                    turn, part.offset, source, origin
                )
                box = uv_box(source, *tileset.atlas.buffer.size)
            index = self.quads
            record = self.data[index]
            record[0], record[1] = px + x, py + y
            record[2], record[3] = width, height
            record[4:8] = box
            record[8] = turn
            self.quads += 1
            self._add_run(
                (
                    tileset.atlas.buffer,
                    sprite._bank(part.tileset),
                    sprite.palettes[part.tileset],
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
            buffer, bank, row, color, begin, length = self.runs[-1]
            if (buffer, bank, row, color) == material and begin + length == first:
                self.runs[-1] = (buffer, bank, row, color, begin, length + count)
                return
        self.runs.append((*material, first, count))


def _tint(sprite: "Sprite") -> Tuple[float, float, float, float]:
    """The sprite's colour registers as the run's `colors` attribute.

    Alpha is pinned at 1.0 and is not a register. The attribute stays a vec4
    because that is what the shared vertex stage declares, and feeding it a
    fourth component the engine never varies is cheaper than a sprite-specific
    stage would be. A pixel's transparency is decided by the palette index and
    the shader's discard, not here.
    """
    r, g, b = sprite._color
    return (float(r), float(g), float(b), 1.0)


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
