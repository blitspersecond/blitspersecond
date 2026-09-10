"""TileMap: the pillar. A world of cells over one sheet, and a Layer.

See the package docstring for the contract this file implements.
"""

from __future__ import annotations

from math import ceil, floor
from typing import Hashable, Optional, Tuple

import numpy as np
from pyglet.gl import GL_TEXTURE0, GL_TEXTURE1, glActiveTexture, glBindTexture

from blitspersecond.common import Location, Size

from ..atlas import Atlas
from ..internal.layer import Layer
from ..palette import PaletteBank
from . import shader
from .batch import TileBatch
from .collision import CollisionMask
from ..orientation import Orientation, applies_to
from .internal import (
    EMPTY_TILE,
    PLANE_DTYPE,
    CellAccess,
    TileAccess,
    bounded_plane,
)
from .tile import Tile

# `PLANE_DTYPE` and `EMPTY_TILE` moved to `internal.py` -- the storage
# layout is the pillar's own now that `cell` is the only road in.

#: The `_window` value meaning "the GPU holds none of the world".
#:
#: An empty rectangle rather than a `None`, so `_refill`'s rect difference can
#: test it with the same `x1 <= x0` it already applies to the new window --
#: nothing painted and nothing visible are one case, and neither needs a
#: branch of its own.
_NOTHING_PAINTED = (0, 0, 0, 0)

#: One live placement: where it sits, which cell of the sheet, how it is turned.
#:
#: No `source` and no `size`, and neither may be added: both are derived, and
#: storing a derived value is storing something that can disagree with what it
#: came from. One immutable sheet makes a source rectangle arithmetic from the
#: index, and a placement's displayed size is always the sheet's cell.
_PLACEMENT_DTYPE = np.dtype(
    [
        ("target", np.int32, 2),
        ("tile", np.int32),
        ("flags", np.uint8),
    ]
)


class TileMap(Layer):
    """A grid of cells indexing one sheet, with a camera onto a world larger
    than the screen -- and a `Layer`, so the thing you stamp into is the thing
    you place in the draw order.

        island = TileMap(Atlas(PixelBuffer("island.png"), 16, 16), 256, 32)
        bps.layers.add(island)
        island.after(sky)
        island.scroll = (x, 0)      # and it streams

    Two faces onto it, and both hand back things this map owns:

        island.tile[42]     one cell of the SHEET  -> a Tile
        island.cell[x, y]   one cell of the WORLD  -> (Tile, Orientation)

    `bps/reference/graphics/tiles.md` is the authority: the contract, the costs, what
    refuses, the camera split, and what this pillar is not.

    Three rules that constrain edits here:

    - **One sheet, assigned once, with no setter.** There is no `bind()`, no
      `unbind()`, and no "unbound" state for any method to know about.
    - **`cell` is the only road into the world.** There is no public array,
      and a proxy over one could not work: `map["tile"][x, y] = 7` is two
      operations and only the first is the map's.
    - **The map holds a window, not a world** -- the viewport plus one cell of
      border, and no more. Nothing can be put in the plane that the world does
      not contain.

    `cell[x, y]` is X FIRST, and here that is also the storage order, so
    nothing is transposed on any road.
    """

    def __init__(
        self,
        tileset: Atlas,
        cols: int,
        rows: int,
        *,
        wrap: Tuple[bool, bool] = (False, False),
        collidable: bool = False,
        tag: Optional[str] = None,
    ) -> None:
        super().__init__(tag=tag)
        if not isinstance(tileset, Atlas):
            raise TypeError(
                f"a tilemap draws from an Atlas; got {type(tileset).__name__}. "
                f"Cut one from a picture with Atlas(PixelBuffer(path), w, h)."
            )
        if not tileset.gridded:
            raise ValueError(
                f"a tilemap needs an autogrid sheet, and this atlas was given "
                f"its {len(tileset)} rects outright. A tilemap's world cell "
                f"size IS its sheet's cell size -- the camera, the bounds "
                f"and every placement are measured in it -- so a world cannot "
                f"be laid over rects that have no common size. Cut the sheet "
                f"with Atlas(buffer, w, h); a sprite is the pillar for "
                f"arbitrary rects."
            )
        if int(cols) < 1 or int(rows) < 1:
            raise ValueError(f"a world cannot be empty; got {cols}x{rows}")

        # Assigned once. There is no rebinding, so these are never None and no
        # method below has to ask whether they are.
        self._tileset: Atlas = tileset
        # Made here rather than taken. A world handed IN leaves the caller
        # holding a live reference to `_map`, which is a private array in name
        # only -- `world["tile"][x, y] = 5` would still reach it with nothing
        # noticing, which is the exact hole `cell` exists to close.
        self._map: np.ndarray = np.zeros((int(cols), int(rows)), dtype=PLANE_DTYPE)
        self._map["tile"] = EMPTY_TILE
        # Allocated here and never rebound, so every write site asks about
        # `collidable` rather than about the buffer's existence -- ONE question
        # on the stamp path, and the same one the switch itself is.
        #
        # Unconditional, including for an unpalettised sheet that can never be
        # switched on. The two planes are 41KB each; making that case save the
        # bytes would put an `Optional` back through every stamp, every cull
        # and both reads to buy 82KB from the one map that cannot use them.
        self._collision: CollisionMask = CollisionMask(tileset)
        # The plane does not match the placements. Set here because there are
        # no placements yet, and cleared by whichever of the two roads fills
        # it: the refill, when it paints a whole window, or `_rebuild_collision`
        # when there was nothing for the refill to do. See
        # `bps/reference/graphics/collision.md`, "When a question is answered".
        #
        # Only a tick can set it and only the commit reads it, so the two
        # never interleave -- the plane is built after the game's code has
        # run, and nothing can invalidate one in the frame that built it.
        self._collision_dirty: bool = True
        # Through the base's setter, so the one rule about palettised art
        # fires here and wherever a game flips the switch later.
        self.collidable = bool(collidable)
        self._wrap: Tuple[bool, bool] = (bool(wrap[0]), bool(wrap[1]))
        self._tile: TileAccess = TileAccess(self, len(tileset))
        self._cell: CellAccess = CellAccess(self)

        # Both constant for this map's life, because the sheet is. Reference
        # recomputed the bounds on every stamp -- and reached through a
        # deferred `Config` import to do it, which `common.SCREEN` exists to
        # make unnecessary.
        cell = tileset.cell
        self._bounds: Tuple[int, int, int, int] = bounded_plane(cell)
        # The live placements, and the permanent scratch a cull compacts into.
        # `_count` rows are live; the rest is capacity.
        self._placements: np.ndarray = np.zeros(0, dtype=_PLACEMENT_DTYPE)
        self._placements_scratch: np.ndarray = np.zeros(0, dtype=_PLACEMENT_DTYPE)
        self._verts: np.ndarray = np.zeros((0, 4, 3), dtype=np.float32)
        self._count: int = 0
        self._dirty: bool = True
        self._batch = None
        # This layer's colours. Seeded from what the tileset image carried,
        # in row 0 -- the art's own table. The buffer does not own a palette:
        # two maps over one tileset image are two layers with two banks, and
        # a table on the image would make them share it with no road to part.
        # The other 255 rows are the system palette until something fills
        # them, and moving `palette.active` is how a map changes scheme.
        self._palette = PaletteBank()
        embedded = self._tileset.buffer.embedded_palette
        if embedded is not None:
            self._palette.texels[0, : len(embedded), :3] = embedded
            self._palette.texels[0, 0, 3] = 0
            self._palette.dirty = True

        # The camera. `_scroll` is where the game is looking, in world pixels
        # and free to be fractional. The other three describe THE FRAME THAT
        # WAS PAINTED, not where the camera is now: `_coarse` is the whole-cell
        # part the placements were baked at, `_fine` the sub-cell remainder
        # they were drawn at, and `_window` the world cells they cover.
        # `_stream` is what brings all three back in line with `_scroll`, and
        # it runs at the top of the compose.
        #
        # `_fine` is latched rather than derived on demand because a collision
        # question is a question about the painted frame. See
        # `collision_offset`.
        self._scroll: Tuple[float, float] = (0.0, 0.0)
        self._origin: Location = Location(0, 0)
        self._coarse: Tuple[int, int] = (0, 0)
        self._fine: Tuple[int, int] = (0, 0)
        self._window: Tuple[int, int, int, int] = _NOTHING_PAINTED

    @classmethod
    def _from_plane(cls, tileset: Atlas, plane: np.ndarray, **kwargs) -> "TileMap":
        """A map over a world that already exists. **The loader's door.**

        `TiledFile` is the caller, and the only one. A `.tmx` layer IS a
        world -- the file says what every cell holds -- so making an empty map
        and then writing the whole thing through `cell` would be building a
        thing in order to immediately overwrite it.

        **This is not the public constructor wearing a different hat.** What
        makes it safe is that `TiledFile` builds its plane with `np.array(...)`
        and does not keep it: nothing outside can reach the world afterwards.
        A caller handing over an array it still holds would keep a live handle
        on `_map`, which is the hole `cell` exists to close -- so do not widen
        this door.

        `_validate` runs here and only here -- a file is untrusted input, and
        this is the one door a world arrives through fully formed.
        """
        _validate(plane, tileset)
        tilemap = cls(tileset, int(plane.shape[0]), int(plane.shape[1]), **kwargs)
        tilemap._map = plane
        return tilemap

    # -- what it is made of ----------------------------------------------

    @property
    def tileset(self) -> Atlas:
        """The sheet this map's indices mean. Fixed for the map's whole life.

        Tiled's word, and the reference's: name the thing. The TYPE is the
        shared `Atlas` -- a sprite sheet and a tileset are one shape, cut in
        one place -- and this is what this pillar calls the one it was
        given."""
        return self._tileset

    @property
    def cell(self) -> CellAccess:
        """The world: `cell[x, y]` -> `None`, or `(Tile, Orientation)`.

        The only road into a map's cells, for reading and for writing, and
        the reason there is no public array. See `CellAccess`.
        """
        return self._cell

    @property
    def tile(self) -> TileAccess:
        """The sheet's cells: `tile[n]` -> a `Tile` this map owns."""
        return self._tile

    @property
    def wrap(self) -> Tuple[bool, bool]:
        """Whether the world repeats when the camera runs off its edge,
        per axis: `(x, y)`.

            sky.wrap = (True, False)        # a strip that goes on forever
            level.wrap = (True, False)      # and a level that loops

        Off by default on both axes. Per axis because the useful worlds need
        them different -- see `bps/reference/graphics/tiles.md`, "Worlds with no edges".

        This costs nothing to have on. The world is not copied, tiled or
        grown: a wrapped axis stops clamping the visible window, and the cells
        are read modulo the world's size. So a one-column strip is a
        legitimate world.
        """
        return self._wrap

    @wrap.setter
    def wrap(self, value: Tuple[bool, bool]) -> None:
        wrap = (bool(value[0]), bool(value[1]))
        if wrap != self._wrap:
            self._wrap = wrap
            # What is visible changes, so what is stamped has to be rebuilt:
            # the window was clamped under the old answer.
            self.clear()

    def _collision_refusal(self) -> Optional[str]:
        """A map may collide iff its sheet is palettised.

        The sheet cannot change, so this answer is constant for the map's
        life -- but it is asked rather than cached because the switch it
        guards is not: `collidable` is a live toggle, and a game turning a
        parallax layer's collision off and on again must meet the same rule
        it met at construction.
        """
        if self._tileset.buffer.mode == "P":
            return None
        return (
            f"a collidable tilemap needs an indexed sheet, and this one is "
            f"mode {self._tileset.buffer.mode!r}. Collision is derived from "
            f"index 0 meaning transparent, so an RGBA sheet has nothing to "
            f"derive a mask from -- it is unbuildable here, not switched off. "
            f"Author the sheet indexed"
        )

    @Layer.collidable.setter  # type: ignore[misc]
    def collidable(self, value: bool) -> None:
        Layer.collidable.fset(self, value)  # type: ignore[misc]
        if value:
            # Whatever is in the buffers describes a frame this map was not
            # masking, so the next compose rebuilds from the placements it
            # kept drawing meanwhile. Until then the plane reads empty, which
            # is the honest answer: this layer was not collidable when the
            # last frame was painted.
            self._collision_dirty = True
        else:
            # Switching off ZEROES rather than drops. What `collidable` buys
            # is the WORK -- no stamp on a blit, no translate on a scroll --
            # and every write path asks that one question directly now, so
            # handing the buffers back would buy nothing and cost an `is None`
            # everywhere. Zeroed rather than left stale so a read between here
            # and the next compose finds nothing rather than history.
            self._collision.clear()

    def _rebuild_collision(self) -> None:
        """Refill the whole plane from the placements. Called by `_stream`.

        **One moment in a game's life: a game switched `collidable` back on
        without the camera having repainted since.** The engine's cycle renders
        the frame and the masks together, and after the first fill the plane is
        maintained by the drawing itself.

        The first compose is NOT this moment, though the flag is set for it.
        A map with no window painted refills the whole of one, and a collidable
        map's bits go in with its quads, so the plane is complete before this
        would run -- `_refill` says so and this is skipped. Running it anyway
        was the same walk twice, and cost more the second time than the first:
        6.7ms against 3.3ms, because iterating the placement SoA row by row
        and pulling each field out of a structured scalar is dearer than the
        refill's road to the same stamps.

        NOT reachable from a read. A query answers about the frame that was
        painted, so a build on the read path would answer about a frame nobody
        has seen and bill the game 3.6ms for the privilege.

        The build is not a per-frame path and never becomes one -- and it is
        bounded anyway, because the placements are at most one bordered
        screenful. Every other write is incremental: `_stamp` adds one tile as
        it is drawn, `_cull` slides the whole plane, `_refill` stamps the
        gutter a scroll exposed.

        It reads `_placements` rather than the world because the placements
        are what is actually on screen: they already carry the camera, the
        wrap resolution and the clamp to the world's edges, and re-deriving
        those here would be a second statement of `_refill`.
        """
        mask = self._collision
        mask.clear()
        for row in self._placements[: self._count]:
            mask.stamp(
                int(row["tile"]),
                int(row["target"][0]),
                int(row["target"][1]),
                Orientation(int(row["flags"])),
            )
        self._collision_dirty = False

    def collision_plane(self, name: Optional[Hashable] = None) -> np.ndarray:
        """The stored plane, apron and all. A tilemap authors no named planes,
        so `collider()` has already refused any `name` but `None`.

        **A read builds nothing.** It hands back what the last compose left,
        which is the frame that was painted. `collider()` has already refused
        an uncollidable layer, so the buffer this reaches is one `_stream` has
        been filling.
        """
        return self._collision.mask

    @property
    def collision_offset(self) -> Tuple[int, int]:
        """Where screen `(0, 0)` sits in the stored plane.

        Two displacements added: the mask's own -- its apron, plus whatever
        sub-word drift it has not moved for -- and `_fine`, which is the same
        number `_composite` hands the GPU as a vertex attribute. The CPU and
        the GPU are looking at the same offset into the same content, which is
        the whole reason a fine scroll costs a collidable layer nothing on
        either side.

        **Both halves are latched to the painted frame, and that is the
        point.** A collision is a fact about the frame that was drawn -- if a
        bullet overlapped the ship, it overlapped, and the tick's job is to
        adjudicate it -- so the answer must not depend on where in the tick it
        was asked. Reading `scroll` live here would have made it: a game that
        moved the camera and then asked would get a different answer from one
        that asked first, and neither would be about a frame that ever
        existed. `_stream` moves both halves at once, at the top of the
        compose, and nothing between two frames moves either.
        """
        ox, oy = self._collision.offset
        fx, fy = self._fine
        return (ox + fx, oy + fy)

    # -- shape -----------------------------------------------------------

    @property
    def cols(self) -> int:
        """The world's width in cells."""
        return int(self._map.shape[0])

    @property
    def rows(self) -> int:
        """The world's height in cells."""
        return int(self._map.shape[1])

    @property
    def size(self) -> Size:
        """The world in CELLS, `(cols, rows)`."""
        return Size(self.cols, self.rows)

    @property
    def tile_size(self) -> Size:
        """One cell in PIXELS -- the sheet's, since every placement matches
        it."""
        return self._tileset.cell

    @property
    def pixel_size(self) -> Size:
        """The whole world in PIXELS. What a camera's range is measured
        against; the game decides its own bounds from it."""
        return Size(
            self.cols * self._tileset.cell.width,
            self.rows * self._tileset.cell.height,
        )

    # -- ownership -------------------------------------------------------

    def owns(self, tile: Tile) -> bool:
        """Whether this map minted `tile`, and therefore will draw it."""
        return isinstance(tile, Tile) and tile.owner is self

    def _require_own(self, tile: Tile) -> Tile:
        """`tile`, or raise saying it belongs to another map.

        The whole of the wrong-sheet guard, and one `is`."""
        if not isinstance(tile, Tile):
            raise TypeError(
                f"expected a Tile from this map's tile[n]; got "
                f"{type(tile).__name__}"
            )
        if tile.owner is not self:
            raise ValueError(
                f"{tile!r} was minted by a different tilemap and this one will "
                f"not draw it. Take the cell from this map's own sheet: "
                f"tile[{tile.index}]."
            )
        return tile

    # -- placement -------------------------------------------------------

    @property
    def bounds(self) -> Tuple[int, int, int, int]:
        """Where a stamp may land: `(left, top, right, bottom)` in pixels.

        The viewport rounded outward to whole cells, plus one cell of border
        on every side. The border is what makes scrolling cheap -- the row or
        column about to come on screen is already stamped, waiting just
        outside, so the camera moves without a refill until it crosses a cell
        boundary.

        Constant, because it is derived from the screen and the sheet's cell,
        and neither changes. An awkward cell size leaves harmless extra pixels
        rather than a partial cell.
        """
        return self._bounds

    def _restamp_region(self, x0: int, y0: int, x1: int, y1: int) -> None:
        """Put world cells `[x0:x1, y0:y1]` back on screen, where they are on it.

        The whole of what a write to `cell` costs beyond the array write, and
        it is proportional to the write: a cell not in the painted window
        costs nothing at all, because `_refill` will read the world as the
        camera arrives.

        **Drop, clear, restamp -- no search.** Every placement is a grid cell
        now that `blit` is gone, so placements are cell-aligned and disjoint:
        the rect a cell occupies contains that cell's placement and no other.
        So the region's placements are dropped by RECTANGLE rather than found
        by target, the mask rect is cleared, and `_stamp_cells` builds the
        region again from the world -- which is the same call a refill makes,
        so a written cell and a scrolled-to cell are stamped by one road.

        An emptied cell needs no case of its own: `_stamp_plane` skips
        `EMPTY_TILE`, so the placement is dropped and simply not replaced.

        Restamping a cell that did not change is harmless -- the same quad
        over the same bits -- which is what lets a block write pass its
        bounding box rather than its exact selection.
        """
        wx0, wy0, wx1, wy1 = self._window
        wrap_x, wrap_y = self._wrap
        for ax0, ax1 in _appearances(x0, x1, wx0, wx1, self.cols, wrap_x):
            for ay0, ay1 in _appearances(y0, y1, wy0, wy1, self.rows, wrap_y):
                self._restamp_rect(ax0, ay0, ax1, ay1)

    def _restamp_rect(self, x0: int, y0: int, x1: int, y1: int) -> None:
        """One rectangle of the painted window, rebuilt from the world.

        Coordinates are the window's own, so they are raw on a wrapped axis --
        `_stamp_cells` and `_block` resolve them modulo the world, exactly as
        the refill path does.
        """
        if x1 <= x0 or y1 <= y0:
            return
        cell = self._tileset.cell
        ccol, crow = self._coarse
        ox, oy = self._origin
        px = (x0 - ccol) * cell.width + ox
        py = (y0 - crow) * cell.height + oy
        width = (x1 - x0) * cell.width
        height = (y1 - y0) * cell.height
        self._drop_in(px, py, width, height)
        # Only collision needs the clear. A quad is opaque geometry already
        # sitting there, so re-pointing it replaces what it drew; a mask
        # accumulates with OR, so the old bits have nowhere else to go.
        if self._collidable:
            self._collision.clear_rect(px, py, width, height)
        self._stamp_cells(x0, y0, x1, y1)

    def _drop_in(self, x: int, y: int, width: int, height: int) -> None:
        """Drop every placement whose target sits inside the local pixel rect.

        The same compaction a cull performs, and for the same reason it is
        safe: `TileBatch.compact` takes the identical indices, so a placement
        and its quad stay at one index.

        Targets are compared, not extents, because a placement is exactly one
        cell of the sheet and the rect is a whole number of those -- a target
        inside it puts the whole placement inside it.
        """
        count = self._count
        if count == 0:
            return
        targets = self._placements["target"][:count]
        inside = (
            (targets[:, 0] >= x)
            & (targets[:, 0] < x + width)
            & (targets[:, 1] >= y)
            & (targets[:, 1] < y + height)
        )
        if not inside.any():
            return
        indices = np.flatnonzero(~inside)
        kept = len(indices)
        if kept:
            np.take(
                self._placements,
                indices,
                axis=0,
                out=self._placements_scratch[:kept],
            )
        self._placements, self._placements_scratch = (
            self._placements_scratch,
            self._placements,
        )
        assert self._batch is not None  # a placement implies a batch
        self._batch.compact(indices)
        self._count = kept
        self._dirty = True

    def _stamp(self, index: int, x: int, y: int, orientation: Orientation) -> None:
        """One placement into the SoA and one quad into the batch.

        The private road, so the refill path can stamp a whole plane without
        re-checking ownership per cell -- it is reading this map's own world.
        """
        cell = self._tileset.cell
        left, top, right, bottom = self._bounds
        if (
            x + cell.width <= left
            or y + cell.height <= top
            or x >= right
            or y >= bottom
        ):
            raise ValueError(
                f"a stamp at {(x, y)} lies outside the bounded local plane "
                f"{self._bounds}. A tilemap retains only the viewport plus one "
                f"cell of border; move the camera with `scroll` rather than "
                f"stamping the whole world."
            )
        slot = self._count
        self._ensure_capacity(slot + 1)
        self._write_placement(slot, index, x, y, orientation)
        self._count = slot + 1

        # Last, so a stamp that could not reach the GPU leaves no bits behind
        # either. The switch itself is the question, so an uncollidable map
        # pays nothing here -- not even the branch's other half. `_stamp` has already refused anything outside the bounded
        # plane, which is the plane the apron was sized to hold.
        #
        # OR, and that is right for an APPEND: two placements overlapping are
        # both solid. Only a REPLACEMENT has to take bits away, which is
        # `_place`'s job and not this one's.
        if self._collidable:
            self._collision.stamp(index, x, y, orientation)

    def _write_placement(
        self, slot: int, index: int, x: int, y: int, orientation: Orientation
    ) -> None:
        """Put one placement in `slot` -- the SoA row and its quad.

        Split out of `_stamp` so that replacing a placement and appending one
        write the same two things the same way. Touches neither `_count` nor
        the mask: the caller decides whether a slot is new, and the mask has
        its own arithmetic to do either way.
        """
        row = self._placements[slot]
        row["target"] = (x, y)
        row["tile"] = index
        row["flags"] = int(orientation)
        self._dirty = True

        batch = self._ensure_batch()
        # Where the cell sits, asked of the sheet rather than multiplied out
        # here. The sprite pillar's grid road needs the same numbers, and two
        # engines each doing the arithmetic is how they come to disagree about
        # it -- so `Atlas` owns it and both ask.
        #
        # A row of `uvs`, not `texture.get_region(...).tex_coords`. That built
        # and discarded a full `Texture` subclass per placement to read twelve
        # constant floats -- 1.66us a tile against 0.03 from the table, and a
        # per-quad Python object in the one pillar built to have none.
        batch.set_quad(slot, self._tileset.uvs[index], orientation)

    def _stamp_plane(self, plane: np.ndarray, x: int, y: int) -> None:
        """Stamp every occupied cell of `plane`, its top-left at `(x, y)`.

        `plane` is a slice of a world -- x-major, `PLANE_DTYPE` -- so
        `argwhere` yields `(x, y)` pairs directly and nothing is transposed on
        this path. Every cell carries its own orientation, which is why this
        takes none.

        The refill path: the camera crosses a cell boundary and the newly
        exposed column arrives through here, and so does every cell of a
        window repaint.

        **Built as a block, not a loop.** Everything this does per cell is
        uniform across the whole plane -- the same sheet, the same cell size,
        one contiguous run of destination slots -- so it is a gather and four
        broadcasts: the targets are two, the SoA rows are three slice
        assignments, and the UVs are one gather off `Atlas.uvs` turned by
        `TileBatch.set_quads`.

        Collision stays per placement, deliberately. Grouping it is costed in
        the board card *Batch the collision stamp* and nothing asks for it.
        """
        if plane.dtype != PLANE_DTYPE:
            raise TypeError(f"expected a PLANE_DTYPE world slice; got {plane.dtype}")
        occupied = np.argwhere(plane["tile"] != EMPTY_TILE)
        if not len(occupied):
            return
        cell = self._tileset.cell
        gx, gy = occupied[:, 0], occupied[:, 1]
        targets = np.empty((len(occupied), 2), dtype=np.int32)
        targets[:, 0] = x + gx * cell.width
        targets[:, 1] = y + gy * cell.height

        # One vectorised test where `_stamp` branched per cell. It should never
        # reject on this path -- the block comes from `_visible`, which is
        # derived from the same `bounds` -- so a rejection is the two having
        # come apart, and it says so rather than silently dropping cells.
        left, top, right, bottom = self._bounds
        tx, ty = targets[:, 0], targets[:, 1]
        outside = (
            (tx + cell.width <= left)
            | (ty + cell.height <= top)
            | (tx >= right)
            | (ty >= bottom)
        )
        if outside.any():
            first = int(np.argmax(outside))
            raise ValueError(
                f"a stamp at {(int(tx[first]), int(ty[first]))} lies outside "
                f"the bounded local plane {self._bounds}. A tilemap retains "
                f"only the viewport plus one cell of border."
            )

        entries = plane[gx, gy]
        tiles = entries["tile"].astype(np.int32)
        flags = entries["flags"]
        count = len(occupied)
        slot = self._count
        self._ensure_capacity(slot + count)

        rows = self._placements[slot : slot + count]
        rows["target"] = targets
        rows["tile"] = tiles
        rows["flags"] = flags
        self._ensure_batch().set_quads(slot, self._tileset.uvs[tiles], flags)
        self._count = slot + count
        self._dirty = True

        # Last, as `_stamp` has it, so a block that could not reach the GPU
        # leaves no bits behind either.
        if self._collidable:
            stamp = self._collision.stamp
            for index in range(count):
                stamp(
                    int(tiles[index]),
                    int(tx[index]),
                    int(ty[index]),
                    Orientation(int(flags[index])),
                )

    def clear(self) -> None:
        """Drop every placement, keeping the sheet, the world and the camera.

        The cheap one. `_count` goes to zero so the next stamp refills from
        slot zero; the GPU buffers and the sheet survive untouched.

        **The camera will paint its window again on the next frame**, because
        painting the window is what the camera is for and this says nothing
        about where it is looking. That makes this a way to discard hand
        stamps, not a way to make a map show nothing -- for that, `enabled`.
        """
        self._count = 0
        self._dirty = True
        self._window = _NOTHING_PAINTED
        if self._collidable:
            self._collision.clear()

    # -- the camera ------------------------------------------------------

    @property
    def palette(self) -> PaletteBank:
        """This layer's colour tables, live.

        Row 0 holds what the tileset image carried; the rest are the system
        palette until something fills them, and `palette.active` picks which
        one the whole map draws through -- a day/night switch is a write to
        that rather than a rewrite of a table.

        The colours belong to the LAYER, not to the art: two maps over one
        tileset image have two banks and can be lit differently. Hand one
        map's bank to another and they share it, deliberately; `copy()` is how
        one of them stops.
        """
        return self._palette

    @palette.setter
    def palette(self, value: PaletteBank) -> None:
        if not isinstance(value, PaletteBank):
            raise TypeError(f"expected PaletteBank, got {type(value)}")
        self._palette = value

    @property
    def scroll(self) -> Tuple[float, float]:
        """Where the game is looking, in world pixels. Assignment is free.

            island.scroll = (x, 0)

        **Setting this stamps nothing.** It stores two floats; the next frame
        works out what that means, once, from wherever the camera ended up --
        so a game that nudges the camera twice in a tick pays for one
        reconciliation rather than two. Sub-cell movement turns out to mean
        one generic vertex attribute and no buffer write at all.

        Fractional values are welcome -- a game accumulating `speed * dt`
        produces them -- but the view lands on a whole pixel.

        **A camera, not a move.** `Console.scroll` shifts its contents and
        blanks the gap; this moves a view over a world that is already there.
        Same word, different extents; `bps/reference/graphics/tiles.md` says why each
        is right for its pillar.
        """
        return self._scroll

    @scroll.setter
    def scroll(self, value: Tuple[float, float]) -> None:
        self._scroll = (float(value[0]), float(value[1]))

    @property
    def origin(self) -> Location:
        """Where the world's first cell sits on screen, in pixels.

            island.origin = (144, 44)       # centre a small map

        The camera is where you are LOOKING; the origin is where the world
        IS. A map smaller than the screen is placed with this rather than by
        parking the camera at a negative scroll, which would say the game is
        looking somewhere outside its own world.

        This is also what a Tiled layer offset becomes -- see
        `TiledFile.tilemap`.

        Changing it drops every placement and repaints, because every stamp
        was positioned relative to the old value. Rare by nature: a world is
        put somewhere once, and then looked at.
        """
        return self._origin

    @origin.setter
    def origin(self, value: Tuple[int, int]) -> None:
        origin = Location(int(value[0]), int(value[1]))
        if origin != self._origin:
            self._origin = origin
            self.clear()

    # -- streaming -------------------------------------------------------

    def _settle_collision(self) -> None:
        """The camera, and everything it drags with it. The `Layer` hook.

        The whole of `_stream`, not a collision half of it, because this
        pillar has no such half: `_refill` writes a tile's bits as it writes
        its quad, which is the property that makes the mask and the picture
        provably the same shape. So a hidden collidable map streams -- it
        writes quads nobody draws, and that is the price of the two never
        being able to disagree.

        Reached on every attached map, drawn or not and collidable or not,
        because `Layer.composite` gates only the drawing half. A map that is
        neither therefore still streams -- the cost of one camera compare and
        whatever a coarse step drags with it. That is deliberate at the base:
        a layer must behave the same whether or not it is visible, and a
        camera that froze while hidden and caught up on reappearing would be
        a second behaviour where the point is that there is one.
        """
        self._stream()

    def _stream(self) -> None:
        """Bring the stamped window in line with where the camera is looking.

        Idempotent and cheap when nothing moved: four divisions and a tuple
        compare. Called once per frame from `_sync`, which is the only place
        this pillar touches GL, so the camera itself never needs a context.

        Two steps, and they are different jobs. The COARSE part of the camera
        has moved the world under the placements, so they are translated and
        the ones that left the bounded plane are culled -- destructively, and
        that is what makes a placement anonymous. Then whatever cells that
        exposed are stamped. The FINE part is not here at all: it reaches the
        GPU as one attribute in `_composite` and moves nothing.
        """
        cell = self._tileset.cell
        coarse = (
            floor(self._scroll[0] / cell.width),
            floor(self._scroll[1] / cell.height),
        )
        if coarse != self._coarse:
            self._cull(
                (coarse[0] - self._coarse[0]) * cell.width,
                (coarse[1] - self._coarse[1]) * cell.height,
            )
            self._coarse = coarse
        # The sub-cell remainder, latched in whole pixels. Two things read it
        # and they must not each work it out: `_composite` shifts every quad
        # by it, and `collision_offset` shifts the mask's window by it. A pixel
        # of disagreement there is a layer whose collision is not where its
        # picture is.
        cell = self._tileset.cell
        self._fine = (
            int(self._scroll[0] - self._coarse[0] * cell.width),
            int(self._scroll[1] - self._coarse[1] * cell.height),
        )
        self._refill()
        # The masks are part of the render, not of whoever asks about them.
        # A collidable map therefore leaves the compose with its plane already
        # filled, and the game's tick spends nothing building one -- the same
        # reason `layers.prepare` runs before the game's tick. Last,
        # because by here the placements are this frame's and a fresh plane
        # built from them needs no translate.
        # Only when the refill did not already do it. A repaint stamps the
        # bits with the quads, so the common road to a full plane is the one
        # above; this is what is left when the placements were already there
        # and only the switch moved.
        if self._collidable and self._collision_dirty:
            self._rebuild_collision()

    def _cull(self, dx: int, dy: int) -> None:
        """Bake one coarse camera step into the placements and drop the ones
        it pushed off the bounded plane.

        The survivors are compacted into the permanent scratch and the two
        mirrors swap roles, so a cull allocates nothing however often the
        camera crosses a boundary. `TileBatch.compact` performs the identical
        `take` over the texture rectangles, which is what keeps a placement
        and its quad at the same index.

        The mask moves first, and for a collidable map unconditionally. Its
        content is not the placements -- bits that left the plane are still in
        the apron and slide out on their own -- so an empty SoA is no reason to
        leave it behind.
        """
        if self._collidable:
            self._collision.translate(dx, dy)
        count = self._count
        if count == 0:
            return
        live = self._placements[:count]
        live["target"][:, 0] -= dx
        live["target"][:, 1] -= dy
        # Every placement is the sheet's cell -- there is no per-row size,
        # for the reason `_PLACEMENT_DTYPE` records.
        cell = self._tileset.cell
        left, top, right, bottom = self._bounds
        x = live["target"][:, 0]
        y = live["target"][:, 1]
        keep = (
            (x + cell.width > left)
            & (y + cell.height > top)
            & (x < right)
            & (y < bottom)
        )
        indices = np.flatnonzero(keep)
        kept = len(indices)
        if kept != count:
            if kept:
                np.take(
                    self._placements,
                    indices,
                    axis=0,
                    out=self._placements_scratch[:kept],
                )
            self._placements, self._placements_scratch = (
                self._placements_scratch,
                self._placements,
            )
            assert self._batch is not None  # a placement implies a batch
            self._batch.compact(indices)
            self._count = kept
        # Unconditional: even a cull that dropped nothing moved every
        # surviving corner, so the position upload is stale either way.
        self._dirty = True

    def _visible(self) -> Tuple[int, int, int, int]:
        """The world cells the bounded plane can currently hold, clamped to
        the world: `(x0, y0, x1, y1)`, half-open.

        `bounds` says the same thing in local pixels. This converts it, rather
        than deriving the viewport a second time -- reference had both
        spellings, each doing its own screen arithmetic through a deferred
        `Config` import, and they had to agree or the refill would stamp cells
        the stamp guard then refused. Sharing one constant makes agreeing
        structural instead of a coincidence that holds.
        """
        cell = self._tileset.cell
        left, top, right, bottom = self._bounds
        ccol, crow = self._coarse
        ox, oy = self._origin
        x0 = ccol + floor((left - ox) / cell.width)
        y0 = crow + floor((top - oy) / cell.height)
        x1 = ccol + ceil((right - ox) / cell.width)
        y1 = crow + ceil((bottom - oy) / cell.height)
        # Clamping to the world is what makes an edge an EDGE. A wrapped axis
        # has no edge, so the window keeps its raw world coordinates -- which
        # may be negative, or past the end -- and `_stamp_cells` resolves them
        # modulo the world. Keeping them raw here is what lets the rect
        # difference in `_refill` stay ordinary arithmetic across the seam: it
        # never sees a window jump back to zero.
        wrap_x, wrap_y = self._wrap
        if not wrap_x:
            x0, x1 = max(0, x0), min(self.cols, x1)
        if not wrap_y:
            y0, y1 = max(0, y0), min(self.rows, y1)
        return (x0, y0, x1, y1)

    def _refill(self) -> None:
        """Stamp the cells that just came into view, and only those.

        `_cull` dropped what left; this paints what arrived. Scrolling along
        one axis therefore costs one column or one row of stamps rather than a
        screen of them, which is the difference between streaming a level and
        repainting all of it every few pixels.

        The rect difference below is why: the entering columns in full, then
        the entering rows over only the columns that were already there, so
        the corner where the two meet is not stamped twice. A doubled stamp is
        invisible but not free -- it is a placement, a quad, and a slot that
        the next cull has to consider.
        """
        new = self._visible()
        old = self._window
        self._window = new
        nx0, ny0, nx1, ny1 = new
        if nx1 <= nx0 or ny1 <= ny0:
            return
        ox0, oy0, ox1, oy1 = old
        if ox1 <= ox0 or oy1 <= oy0:
            self._stamp_cells(nx0, ny0, nx1, ny1)
            # Nothing was painted, so this just stamped the whole window --
            # and a collidable map's bits go in with its quads, which means
            # the plane is now exactly what a rebuild would build. Say so, or
            # `_stream` walks all of it a second time: 3054 stamps for 1527
            # placements on the first compose of a real level, 10.6ms where
            # 3.9ms builds the identical plane.
            #
            # Safe because the mask was zeroed by whatever emptied the window.
            # `_NOTHING_PAINTED` is set in exactly two places -- construction,
            # where the mask is fresh, and `clear()`, which takes the bits with
            # it -- so a full-window fill never lands on top of stale bits.
            self._collision_dirty = False
            return
        if nx0 < ox0:
            self._stamp_cells(nx0, ny0, min(ox0, nx1), ny1)
        if nx1 > ox1:
            self._stamp_cells(max(ox1, nx0), ny0, nx1, ny1)
        cx0, cx1 = max(nx0, ox0), min(nx1, ox1)
        if cx0 < cx1:
            if ny0 < oy0:
                self._stamp_cells(cx0, ny0, cx1, min(oy0, ny1))
            if ny1 > oy1:
                self._stamp_cells(cx0, max(oy1, ny0), cx1, ny1)

    def _stamp_cells(self, x0: int, y0: int, x1: int, y1: int) -> None:
        """Stamp one rectangle of the world where the camera puts it.

        World cell `c` sits at `(c - coarse) * cell + origin` in the local
        plane; the sub-cell remainder is the GPU's to add at draw time, which
        is what keeps fine scrolling free.
        """
        if x1 <= x0 or y1 <= y0:
            return
        cell = self._tileset.cell
        ccol, crow = self._coarse
        ox, oy = self._origin
        self._stamp_plane(
            self._block(x0, y0, x1, y1),
            (x0 - ccol) * cell.width + ox,
            (y0 - crow) * cell.height + oy,
        )

    def _block(self, x0: int, y0: int, x1: int, y1: int) -> np.ndarray:
        """The world cells `[x0:x1, y0:y1]`, resolved through any wrapping.

        Unwrapped, this is a plain slice and therefore a VIEW -- the ordinary
        case pays nothing for the ability to wrap. A wrapped axis takes the
        indices modulo the world instead, which copies, because a window
        straddling the seam is not contiguous in the world's memory however
        contiguous it is on screen.
        """
        wrap_x, wrap_y = self._wrap
        if not wrap_x and not wrap_y:
            return self._map[x0:x1, y0:y1]
        xs = np.arange(x0, x1)
        ys = np.arange(y0, y1)
        if wrap_x:
            xs %= self.cols
        if wrap_y:
            ys %= self.rows
        return self._map[np.ix_(xs, ys)]

    def _ensure_capacity(self, needed: int) -> None:
        """Grow the placement SoA and its GPU projection to hold `needed` rows.

        Doubles, so stamping amortises O(1). The scratch mirror is reallocated
        rather than copied -- it holds nothing between culls.
        """
        capacity = len(self._placements)
        if needed <= capacity:
            return
        grown = max(8, capacity * 2, needed)
        placements = np.zeros(grown, dtype=_PLACEMENT_DTYPE)
        placements[:capacity] = self._placements
        self._placements = placements
        self._placements_scratch = np.zeros(grown, dtype=_PLACEMENT_DTYPE)
        verts = np.zeros((grown, 4, 3), dtype=np.float32)
        verts[: len(self._verts)] = self._verts
        self._verts = verts
        if self._batch is not None:
            self._batch.resize(grown)

    def _ensure_batch(self):
        """This map's `TileBatch`, built on first stamp.

        Lazy because it needs a current GL context and constructing a map must
        not. The program comes from the module-level pair, compiled once per
        process; nothing about it is held per map.
        """
        if self._batch is None:
            self._batch = TileBatch(
                shader.program(self._tileset.buffer.mode == "P"),
                max(8, len(self._placements)),
            )
        return self._batch

    # -- drawing ---------------------------------------------------------

    def _sync(self) -> None:
        """Bring the GPU's copy of the placements up to date.

        Positions are expanded from the SoA in one vectorised pass and sent as
        a single `glBufferSubData` -- one upload for the whole live range, not
        one per placement. Texture rectangles are already on the GPU; they were
        written at stamp time and are static until a cull reorders them.

        The corner expansion is where a placement's extent comes from, and it
        needs no per-row size because there isn't one: every placement is the
        sheet's cell, since a rectangular source may not be turned diagonally
        and turning a square one leaves its shape alone.

        Private, and not a frame phase: its only caller is the next line of
        `_composite`.
        """
        # The camera has already run, in `_settle_collision` -- it decides
        # WHAT the placements are before this decides where their corners go,
        # and it is not behind the `enabled` gate because a hidden collider
        # still has to follow it. It is the only reason this pillar needs a
        # frame at all: setting `scroll` is a float assignment, and everything
        # it implies is settled there.

        # Touching the texture re-uploads it iff the pixels were edited. The id
        # is stable, so nothing downstream rebuilds: this is what makes a
        # writable sheet cost nothing on the frames it did not change.
        _ = self._tileset.buffer.texture

        count = self._count
        if count == 0 or not self._dirty or self._batch is None:
            return
        self._dirty = False

        cell = self._tileset.cell
        target = self._placements["target"][:count]
        x1 = target[:, 0]
        y1 = target[:, 1]
        x2 = x1 + cell.width
        y2 = y1 + cell.height
        verts = self._verts[:count]
        verts[:, 0, 0] = x1
        verts[:, 0, 1] = y1
        verts[:, 1, 0] = x2
        verts[:, 1, 1] = y1
        verts[:, 2, 0] = x2
        verts[:, 2, 1] = y2
        verts[:, 3, 0] = x1
        verts[:, 3, 1] = y2
        self._batch.upload_positions(verts)

    def _composite(self) -> None:
        """Draw this map: sync, bind the palette if indexed, one batch draw.

        Called by the compositor with the framebuffer bound and a 1:1 ortho
        already set. This sets no projection and no viewport -- those are the
        compositor's, and the present pass owns the flip that makes a quad
        placed at y=0 appear at the top of the screen.
        """
        self._sync()
        if self._batch is None or self._count == 0:
            return

        # Asked of the batch rather than of `shader` again: the batch was
        # constructed with it, so one place decides which program this map
        # uses.
        program = self._batch.program
        program["sprite_texture"] = 0
        if self._tileset.buffer.mode == "P":
            # Palette RAM on unit 1, as a texture rather than a uniform
            # array: see `bps/reference/graphics/palette.md`. The upload happens here, at
            # the head of the composite, so it cannot land mid-pass and
            # disturb the unit the sheet is bound to. Unit 0 is left active
            # because the batch binds the sheet there.
            program["palette_texture"] = 1
            table = self._palette.texture
            glActiveTexture(GL_TEXTURE1)
            glBindTexture(table.target, table.id)
            glActiveTexture(GL_TEXTURE0)

        # The fine camera: the sub-cell remainder the coarse step left
        # behind, as ONE generic vertex attribute shifting every quad. No
        # buffer is written, so panning between cell boundaries costs the
        # same whether the layer holds ten quads or ten thousand.
        #
        # Negated, because the view moving right moves the content left. The
        # remainder itself comes from `_fine`, which the collision mask reads
        # too -- the picture and the mask are shifted by one number, so they
        # cannot end up a pixel apart.
        fx, fy = self._fine
        self._batch.draw(
            self._tileset.buffer.texture,
            self._count,
            translate=(-float(fx), -float(fy)),
            palette_row=self._palette.active,
        )

    def __repr__(self) -> str:
        cw, ch = self._tileset.cell
        return f"<TileMap {self.cols}x{self.rows} cells of {cw}x{ch} " f"[{self._tag}]>"


def _appearances(lo, hi, window_lo, window_hi, size, wraps):
    """Where world cells `[lo, hi)` appear inside a painted window.

    One rectangle on an unwrapped axis: the plain intersection. On a WRAPPED
    axis the window keeps raw world coordinates -- `_visible` says why -- so
    the same canonical cells can appear more than once on screen, and a world
    narrower than the viewport appears many times. A one-column repeating sky
    is that case, and it is the normal one rather than the exotic one.

    Yields half-open ranges in the window's own coordinates.
    """
    if not wraps:
        yield (max(lo, window_lo), min(hi, window_hi))
        return
    step = (window_lo - hi) // size + 1
    while lo + step * size < window_hi:
        first = max(lo + step * size, window_lo)
        last = min(hi + step * size, window_hi)
        if first < last:
            yield (first, last)
        step += 1


def _validate(plane: np.ndarray, tileset: Atlas) -> None:
    """Check a world against the sheet it indexes, at construction.

    A map arrives from a TMX file, which validates its own profile, or is
    built by a game with NumPy, which validates nothing. Both doors lead here,
    so the rules live here once and the loader does not repeat them.

    Everything checked is cheap and vectorised, and every failure is one a
    later refill would otherwise report as a crash or a wrong picture, far
    from the line that caused it.
    """
    if not isinstance(plane, np.ndarray):
        raise TypeError(
            f"a tilemap's world is a NumPy array of PLANE_DTYPE; got "
            f"{type(plane).__name__}"
        )
    if plane.dtype != PLANE_DTYPE:
        raise TypeError(
            f"a tilemap's world has dtype PLANE_DTYPE "
            f"[('tile', int32), ('flags', uint8)]; got {plane.dtype}"
        )
    if plane.ndim != 2:
        raise ValueError(
            f"a tilemap's world is two-dimensional and indexed [x, y]; got "
            f"shape {plane.shape}"
        )
    if plane.shape[0] < 1 or plane.shape[1] < 1:
        raise ValueError(f"a tilemap's world cannot be empty; got {plane.shape}")

    tiles = plane["tile"]
    count = len(tileset)
    bad = (tiles != EMPTY_TILE) & ((tiles < 0) | (tiles >= count))
    if bad.any():
        x, y = (int(i) for i in np.argwhere(bad)[0])
        raise ValueError(
            f"cell [{x}, {y}] names tile {int(tiles[x, y])}, but this sheet "
            f"holds {count} ({tileset.cols}x{tileset.rows} cells). Use "
            f"{EMPTY_TILE} for an empty cell."
        )

    flags = plane["flags"]
    if (flags > 7).any():
        x, y = (int(i) for i in np.argwhere(flags > 7)[0])
        raise ValueError(
            f"cell [{x}, {y}] carries orientation bits {int(flags[x, y])}; "
            f"only 0..7 are defined -- see Orientation."
        )

    # The diagonal bit swaps a source's axes, so it is legal only where that
    # leaves the shape alone. `applies_to` is the rule; do not restate it as a
    # bit test plus a branch, which is how it ends up written twice and
    # disagreeing with itself.
    cell = tileset.cell
    if not applies_to(Orientation.TRANSPOSE, (cell.width, cell.height)):
        turned = flags & int(Orientation.TRANSPOSE)
        if turned.any():
            x, y = (int(i) for i in np.argwhere(turned)[0])
            raise ValueError(
                f"cell [{x}, {y}] is turned diagonally, and this sheet's tiles "
                f"are {cell.width}x{cell.height}. A quarter turn would make "
                f"them {cell.height}x{cell.width}: it would either overflow "
                f"the neighbouring cells or have to be squeezed, and squeezing "
                f"stops the pixels being square. Square tilesets take all "
                f"eight orientations; rectangular ones take the four that "
                f"leave the shape alone (NONE, FLIP_H, FLIP_V, ROT_180). This "
                f"is a BPS restriction, not a broken map -- Tiled lets you "
                f"author it."
            )
