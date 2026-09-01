"""TileMap: the pillar. A world of cells over one sheet, and a Layer.

See the package docstring for the contract this file implements.
"""

from __future__ import annotations

from math import ceil, floor
from typing import Optional, Tuple

import numpy as np
from pyglet.gl import GL_TEXTURE0, GL_TEXTURE1, glActiveTexture, glBindTexture

from blitspersecond.common import SCREEN, Location, Size

from ..atlas import Atlas
from ..internal.layer import Layer
from ..internal.palette_texture import PaletteTexture
from . import shader
from .batch import TileBatch
from ..orientation import Orientation, applies_to
from .internal import TileAccess
from .tile import Tile

#: One cell of a world: which tile of the sheet, and how it is turned.
#:
#: `tile` is a LOCAL index into the map's one sheet, zero-based, with `EMPTY`
#: (-1) meaning nothing is there. `flags` is `Orientation`'s 0..7 bit layout.
#:
#: This lives with the tilemap and not with whatever reads files. The plane is
#: the pillar's own vocabulary for a world; a TMX loader is one thing that
#: happens to produce one, and a game building a world procedurally is
#: another.
PLANE_DTYPE = np.dtype([("tile", np.int32), ("flags", np.uint8)])

#: A cell with nothing in it.
EMPTY = -1

#: The `_window` value meaning "the GPU holds none of the world".
#:
#: An empty rectangle rather than a `None`, so `_refill`'s rect difference can
#: test it with the same `x1 <= x0` it already applies to the new window --
#: nothing painted and nothing visible are one case, and neither needs a
#: branch of its own.
_NOTHING_PAINTED = (0, 0, 0, 0)

#: One live placement: where it sits, which cell of the sheet, how it is turned.
#:
#: Reference carried `source` and `size` here too. Both are derived and neither
#: survives the contract: the map has ONE sheet that cannot change, so a source
#: rectangle is arithmetic from the index; and a placement's displayed size is
#: always the sheet's cell, because a rectangular source may not be turned
#: diagonally and turning a square one leaves its shape alone. Storing either
#: is storing something that can disagree with what it was derived from.
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

        island = TileMap(Atlas(PixelBuffer("island.png"), 16, 16), world)
        bps.layers.add(island)
        island.after(sky)
        island.scroll = (x, 0)      # and it streams

    Two faces onto it, and both hand back things this map owns:

        island.tile[42]     one cell of the SHEET  -> a Tile
        island.map[x, y]    one cell of the WORLD  -> (tile, flags)

    ## One sheet, and it cannot be changed

    The sheet is a constructor argument and there is no way to replace it.
    That is not a restriction being tolerated -- it is the assumption
    `TileBatch` is built around. One texture means one bind and one
    `glDrawElements` for the whole layer at any quad count; a second sheet
    would be two of each and the SoA design would stop paying for itself.

    Wanting two sheets on screen means wanting two TileMaps, which is also how
    you get two collision identities and two places in the draw order. Sharing
    the art between them is free: `PixelBuffer` interns by path, so two maps
    over one tileset genuinely hold one texture. **Identity is not art**, and
    it is identity a layer is the unit of.

    So there is no `bind()`, no `unbind()`, and -- more to the point -- no
    "unbound" state for any method here to know about.

    ## `map` is a real array, and that is the point

    `map[x, y]` is X FIRST, stored x-major, so the order a person says a
    position in is the order the memory is in and a whole-column write is
    contiguous. Unlike the console's `cell[x, y]`, **this looks like a NumPy
    index because it is one** -- slice it, `argwhere` it, write a column into
    it. `map[:, 5]` is every cell on row 5.

    The console needs an access face there and this does not, and the reason
    is extent rather than taste: a console's grid IS the screen, so a position
    in it is a screen position. A map is bigger than the screen, so a position
    in it is a world position and the camera is what relates the two. The same
    difference is why `scroll` means something different on each pillar.

    **The array is taken, not copied.** `map` reads and writes the one you
    handed over, which is what makes a game's own world data and this map the
    same thing rather than two that drift.

    ## The map holds a window, not a world

    A world is any size; what this holds on the GPU is the viewport plus one
    cell of border, and no more. `scroll` moves the camera over the world and
    the map streams: the placements that left are culled, the cells that
    arrived are stamped, and everything in between is one vertex attribute.

    So **you never stamp a level.** Set `origin` to say where the world sits,
    `scroll` to say where you are looking, and the cells appear as they come
    into view. `blit` is for putting something in the plane that the world
    does not contain -- and the camera will cull it like anything else, so it
    is not a way to pin a decoration to the screen.

    This is Commander Keen's arrangement rather than AMOS's `SCROLL`: pan the
    view and redraw the newly exposed edge, instead of blitter-copying the
    screen and blanking the gap. The deciding fact is that there IS a world
    behind the screen here, so a destructive copy would be strictly more work
    for the same picture. `Console.scroll` is the other lineage, and means the
    other thing, for the same reason in reverse.

    `wrap` says whether the world has edges at all. A wrapped axis is read
    modulo its own size, so the camera can pan along it forever and a
    one-column strip is a complete repeating sky. Per axis, because a world
    that joins up along one and not the other is the normal case rather than
    an exotic one -- see the property.
    """

    def __init__(
        self,
        tileset: Atlas,
        map: np.ndarray,
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
        if collidable and not tileset.indexed:
            raise ValueError(
                "a collidable tilemap needs an indexed sheet, and this one is "
                f"mode {tileset.mode!r}. Collision is derived from palette "
                "index 0 meaning transparent, so an RGBA sheet has nothing to "
                "derive a mask from -- it is unbuildable here, not switched "
                "off. Drop collidable, or author the sheet indexed."
            )
        _validate(map, tileset)

        # Assigned once. There is no rebinding, so these are never None and no
        # method below has to ask whether they are.
        self._tileset: Atlas = tileset
        self._map: np.ndarray = map
        self._collidable: bool = collidable
        self._wrap: Tuple[bool, bool] = (bool(wrap[0]), bool(wrap[1]))
        self._tile: TileAccess = TileAccess(self, len(tileset))

        # Both constant for this map's life, because the sheet is. Reference
        # recomputed the bounds on every stamp -- and reached through a
        # deferred `Config` import to do it, which `common.SCREEN` exists to
        # make unnecessary.
        cell = tileset.cell
        self._bounds: Tuple[int, int, int, int] = (
            -cell.width,
            -cell.height,
            ceil(SCREEN.width / cell.width) * cell.width + cell.width,
            ceil(SCREEN.height / cell.height) * cell.height + cell.height,
        )
        # The live placements, and the permanent scratch a cull compacts into.
        # `_count` rows are live; the rest is capacity.
        self._placements: np.ndarray = np.zeros(0, dtype=_PLACEMENT_DTYPE)
        self._placements_scratch: np.ndarray = np.zeros(0, dtype=_PLACEMENT_DTYPE)
        self._verts: np.ndarray = np.zeros((0, 4, 3), dtype=np.float32)
        self._count: int = 0
        self._dirty: bool = True
        self._batch = None

        # The camera. `_scroll` is where the game is looking, in world pixels
        # and free to be fractional; `_coarse` is the whole-cell part of it
        # that the CURRENT placements were baked at, and `_window` the world
        # cells they cover. The last two describe what the GPU holds, not
        # where the camera is -- `_stream` is what brings them back together.
        self._scroll: Tuple[float, float] = (0.0, 0.0)
        self._origin: Location = Location(0, 0)
        self._coarse: Tuple[int, int] = (0, 0)
        self._window: Tuple[int, int, int, int] = _NOTHING_PAINTED

    # -- what it is made of ----------------------------------------------

    @property
    def tileset(self) -> Atlas:
        """The sheet this map's indices mean. Fixed for the map's whole life.

        Tiled's word, and the reference's, for the same reason `map` is `map`:
        name the thing. The TYPE is the shared `Atlas` -- a sprite sheet and a
        tileset are one shape, cut in one place -- and this is what this
        pillar calls the one it was given."""
        return self._tileset

    @property
    def map(self) -> np.ndarray:
        """The world: `map[x, y]` -> `(tile, flags)`, x-major, live.

        Writable. Editing it edits this map, which is what a level editor, a
        procedural generator and a game streaming its own world all want."""
        return self._map

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

        Off by default on both axes, because a world that silently repeats
        when you scroll past its end is a surprise, and running out of world
        is a thing a game may legitimately want to detect.

        **Per axis, because the useful cases need them different.** Civ's
        map wraps east to west and not north to south: the globe joins up
        along one axis and the poles are a genuine edge, and that is a world
        topology rather than a trick. A side scroller is the same shape --
        the level loops, while the sky wrapping round into the ground is not
        a level but a bug you can see. One flag for both would make both
        unreachable.

        This costs nothing to have on. The world is not copied, tiled or
        grown: a wrapped axis simply stops clamping the visible window, and
        the cells are read modulo the world's size. So a one-column strip is
        a legitimate world -- it is the whole of a repeating sky -- and
        covering a 640px screen with it is the same work as covering it with
        anything else.

        The alternative a caller reaches for otherwise is `np.tile`, and it
        is worse in three ways: it allocates N copies of the art's indices,
        it only extends as far as it was tiled, and the seam is baked in at
        build time so the world can never be edited as one thing again.
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

    @property
    def collidable(self) -> bool:
        """Whether this map maintains a collision mask.

        Read-only, and settled at construction: a collidable map needs an
        indexed sheet, and the sheet cannot change, so this cannot become
        false-by-circumstance later."""
        return self._collidable

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

        The whole of the wrong-sheet guard, and one `is`. Reference needed two
        comparisons here -- same buffer, same tile size -- because an
        atlas-bound handle could not be asked who made it."""
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

    def blit(
        self,
        tile: Tile,
        x: int,
        y: int,
        orientation: Orientation = Orientation.NONE,
    ) -> None:
        """Stamp `tile` at `(x, y)` in local pixels, turned by `orientation`.

            island.blit(island.tile[42], 32, 16, Orientation.FLIP_H)

        The tile must be one this map minted -- `tile[n]` off this map's own
        sheet. A tile from another map is refused rather than drawn wrong.

        **Placements are retained but anonymous.** This returns no handle, and
        a stamp cannot afterwards be moved, hidden or re-dressed. Those would
        make the map a sprite pool, and they cannot be reconciled with the
        destructive cull that scrolling performs -- a placement that leaves the
        bordered plane is gone, and a handle to it would be a handle to
        nothing. Change the world (`map[x, y]`) and let the camera restamp.

        Needs a live GL context, because the placement's texture rectangle goes
        to the GPU as it is made. Constructing a map does not; stamping into
        one does.
        """
        self._require_own(tile)
        orientation = Orientation(int(orientation) & 7)
        cell = self._tileset.cell
        if not applies_to(orientation, (cell.width, cell.height)):
            raise ValueError(
                f"{orientation!r} turns a tile diagonally, and this sheet's "
                f"tiles are {cell.width}x{cell.height}. A quarter turn would "
                f"make them {cell.height}x{cell.width}, which either overflows "
                f"the neighbouring cells or has to be squeezed -- and squeezing "
                f"stops the pixels being square. Rectangular sheets take NONE, "
                f"FLIP_H, FLIP_V and ROT_180; square sheets take all eight."
            )
        self._stamp(tile.index, int(x), int(y), orientation)

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
        row = self._placements[slot]
        row["target"] = (x, y)
        row["tile"] = index
        row["flags"] = int(orientation)
        self._count = slot + 1
        self._dirty = True

        batch = self._ensure_batch()
        # Where the cell sits, asked of the sheet rather than multiplied out
        # here. The sprite pillar's grid road needs the same four numbers, and
        # two engines each doing the arithmetic is how they come to disagree
        # about it -- so `Atlas` owns it and both ask.
        source = self._tileset.rect(index)
        region = self._tileset.buffer.texture.get_region(
            source.x, source.y, source.width, source.height
        )
        batch.set_quad(slot, region.tex_coords, orientation)

    def _stamp_plane(self, plane: np.ndarray, x: int, y: int) -> None:
        """Stamp every occupied cell of `plane`, its top-left at `(x, y)`.

        `plane` is a slice of a world -- x-major, `PLANE_DTYPE` -- so
        `argwhere` yields `(x, y)` pairs directly and nothing is transposed on
        this path. Every cell carries its own orientation, which is why this
        takes none.

        The refill path: the camera crosses a cell boundary and the newly
        exposed column arrives through here. It runs on cell boundaries rather
        than per frame.
        """
        if plane.dtype != PLANE_DTYPE:
            raise TypeError(
                f"expected a PLANE_DTYPE world slice; got {plane.dtype}"
            )
        cell = self._tileset.cell
        for gx, gy in np.argwhere(plane["tile"] != EMPTY):
            entry = plane[gx, gy]
            self._stamp(
                int(entry["tile"]),
                x + int(gx) * cell.width,
                y + int(gy) * cell.height,
                Orientation(int(entry["flags"])),
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

    # -- the camera ------------------------------------------------------

    @property
    def scroll(self) -> Tuple[float, float]:
        """Where the game is looking, in world pixels. Assignment is free.

            island.scroll = (x, 0)

        **Setting this stamps nothing.** It stores two floats; the next frame
        works out what that means. Sub-cell movement turns out to mean one
        generic vertex attribute and no buffer write at all, and crossing a
        cell boundary means culling what left and stamping what arrived --
        which is why a map holds only the viewport and one cell of border
        rather than the whole world.

        Reference did the work in this setter, so a game that nudged the
        camera twice in a tick paid for two culls and two refills to arrive
        where one would have put it. Here the reconciliation happens once, in
        the frame, from wherever the camera ended up.

        **A camera, not a move.** `Console.scroll` shifts its contents and
        blanks the gap, because a console's grid IS the screen and there is
        nothing behind it to look at. A tilemap has a world larger than the
        screen, so moving the view over it is what scrolling can mean -- and
        copying pixels would be strictly more work for the same picture. Same
        word, different extents; see the class docstring.

        Fractional values are welcome -- a game accumulating `speed * dt`
        produces them -- but the view lands on a whole pixel, because a pixel
        engine that sampled between them would not be one.
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
        self._refill()

    def _cull(self, dx: int, dy: int) -> None:
        """Bake one coarse camera step into the placements and drop the ones
        it pushed off the bounded plane.

        The survivors are compacted into the permanent scratch and the two
        mirrors swap roles, so a cull allocates nothing however often the
        camera crosses a boundary. `TileBatch.compact` performs the identical
        `take` over the texture rectangles, which is what keeps a placement
        and its quad at the same index.
        """
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
                shader.program(self._tileset.indexed),
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

        Reference had this as a public `prepare()`. It was called from exactly
        one place -- the next line of its own `_composite` -- in all three
        pillars, so it was never a frame phase, just a named section wearing
        public clothes. It is private and named for what it does.
        """
        # The camera first, because it decides WHAT the placements are before
        # this decides where their corners go. It is also the only reason
        # this pillar needs a frame at all: setting `scroll` is a float
        # assignment, and everything it implies is settled here.
        self._stream()

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
        if self._tileset.indexed:
            palette = self._tileset.palette
            # Indexed implies a table: a file brings one and a blank canvas
            # gets `system_palette()`. Asserted rather than guarded, because
            # skipping the bind would draw the sheet's indices as colours --
            # a picture that is wrong rather than absent.
            assert palette is not None
            # Palette RAM on unit 1, as a 256x1 texture rather than a uniform
            # array: see PaletteTexture for why. Unit 0 is left active because
            # the batch binds the sheet there.
            program["palette_texture"] = 1
            row = PaletteTexture.get(palette)
            glActiveTexture(GL_TEXTURE1)
            glBindTexture(row.target, row.id)
            glActiveTexture(GL_TEXTURE0)

        # The fine camera: the sub-cell remainder the coarse step left
        # behind, as ONE generic vertex attribute shifting every quad. No
        # buffer is written, so panning between cell boundaries costs the
        # same whether the layer holds ten quads or ten thousand.
        #
        # Negated, because the view moving right moves the content left. And
        # floored to whole pixels: the remainder is already in [0, cell), so
        # truncation is a floor, and a pixel engine that sampled between
        # pixels would stop being one.
        cell = self._tileset.cell
        self._batch.draw(
            self._tileset.buffer.texture,
            self._count,
            translate=(
                -float(int(self._scroll[0] - self._coarse[0] * cell.width)),
                -float(int(self._scroll[1] - self._coarse[1] * cell.height)),
            ),
        )

    def __repr__(self) -> str:
        cw, ch = self._tileset.cell
        return (
            f"<TileMap {self.cols}x{self.rows} cells of {cw}x{ch} "
            f"[{self._tag}]>"
        )


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
    bad = (tiles != EMPTY) & ((tiles < 0) | (tiles >= count))
    if bad.any():
        x, y = (int(i) for i in np.argwhere(bad)[0])
        raise ValueError(
            f"cell [{x}, {y}] names tile {int(tiles[x, y])}, but this sheet "
            f"holds {count} ({tileset.cols}x{tileset.rows} cells). Use "
            f"{EMPTY} for an empty cell."
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


def empty_map(cols: int, rows: int) -> np.ndarray:
    """A world of `cols` x `rows` empty cells, ready to write into.

    The starting point for a game building its own world rather than loading
    one: `world = empty_map(256, 32)`, then `world["tile"][x, y] = n`. Empty
    is `EMPTY`, not zero -- zero is a real tile, the sheet's first cell."""
    if cols < 1 or rows < 1:
        raise ValueError(f"a world cannot be empty; got {cols}x{rows}")
    plane = np.zeros((int(cols), int(rows)), dtype=PLANE_DTYPE)
    plane["tile"] = EMPTY
    return plane
