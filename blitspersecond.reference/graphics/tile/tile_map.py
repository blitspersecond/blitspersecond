"""One layer of a Tiled map, and the TileLayer it drives.

TileMap is to TileLayer what Console is to GlyphLayer: a semantic controller
over a layer somebody else placed on screen. It adds no *rendering* state --
palette, tint and background still belong to the layer -- but it holds state
of its own, the way Console holds a cursor. Console's cursor is where the next
write lands; TileMap's camera is which window of the world is showing. Both
are a position into a space larger than any single operation.

That division exists because a TileLayer is deliberately finite: a local
tilemap around the 640x360 viewport, holding the visible cells plus a one-tile
border, culling stamps as the camera moves and leaving the exposed edge empty
"for a map streamer to refill". TileMap is that streamer. A TMX map is any
size; the layer only ever holds what can be seen.

Building one is GL-free -- TileAtlas is arithmetic over a PixelBuffer whose
texture is lazy -- so reading grids for pathfinding or editing costs no
context. Only stamping into a layer needs one.
"""

# ---------------------------------------------------------------------------
# API DECISIONS (2026-08-24, branch `inversion`) -- why this surface is shaped
# the way it is. Companion to the block in console/console.py; the same three
# fields, and the same warning: this is the SPEC, not a description of the
# current internals. The graphics component is to be rebuilt against these
# decisions, so everything here must survive its implementation being deleted.
#
#   DECISION    -- what the surface promises.
#   REQUIREMENT -- what that promise is IN SERVICE OF.
#   EVIDENCE    -- a measurement, an observed failure, or a standing
#                  principle. Implementation convenience is NOT evidence.
#
# 1. DECISION: the analogy IS the definition, and it is exact:
#
#        layer <---> console <---> glyph engine
#        layer <---> tilemap <---> tile engine
#
#    A TileMap is a grid of cells indexing a sheet, a reference to that sheet,
#    and methods to blit from it. `tiles` is the console's `cell`; `tileset`
#    is its charset; `blit` is its `write`; `layers.add` places both.
#    REQUIREMENT: the three pillars must be layer-first siblings speaking one
#    language, so a caller who has learned one has learned the others. This is
#    the whole point of the exercise -- judge changes by whether the pillars
#    spell the same idea the same way, never by local convenience on one.
#    EVIDENCE: the name was interrogated and kept on these grounds after
#    `Playfield` was put up and rejected. `TileMap` has two real collisions --
#    Tiled calls the whole FILE a map, and general usage calls the index grid
#    a tilemap -- but the sibling match outweighed both, and the collisions
#    are answered by the factory being `TiledFile` and the grid being `tiles`.
#
# 2. DECISION: `tiles` is indexed `[x, y]`, X FIRST, and is stored x-major so
#    that order is native rather than wrapped. There is no row/col-ordered
#    sibling on this pillar and no `plane` in the public surface.
#    REQUIREMENT: one addressing order per surface, matching the order a
#    person says a screen position in.
#    EVIDENCE: the rule is already written down in _CellAccess/_PaintAccess --
#    "the grid is the engine's to define, the image belongs to the screen, and
#    the screen is row-major down to the GPU". `paint_buffer[row, col]` is the
#    ONLY reason the console has two orders: a transposed whole-plane write
#    measured 1.2ms against 21us native, so its image side is not negotiable
#    and the NAMES carry the warning. The tile engine has no such image --
#    tiles carry their own palette indices straight from the sheet, so there
#    is nothing to paint them with and no per-pixel maths to force row-major.
#    It therefore inherits none of it. The old `plane` was row-major only
#    because TMX decodes that way, which is an accident of the file format,
#    not a constraint; the transpose now happens ONCE at load.
#
# 3. DECISION: `TiledFile` mints TileMaps -- `maps.tilemap("blue_island")` --
#    memoised per layer name, and mints them UNATTACHED. `layers.add(tilemap)`
#    places them, finding the layer through the public `.layer` property.
#    The `layers` parameter that `TileMap.open(path, layers)` took is gone.
#    REQUIREMENT: ONE construction law across every graphics type -- build
#    unattached, `add` places -- so a caller never has to know which types
#    defer, and so minting is never entangled with drawing.
#    EVIDENCE: `TileMap.open` was the last holdout; display/layers.py's `add`
#    docstring already promised this ("Console has one; TileMap will"). The
#    old shape also had a real cost: `open()` built its atlases per CALL, so
#    two opens of one TMX produced two PixelBuffers, two textures and two
#    interned tile pools for the same PNG -- measured, `PixelBuffer(spec)`
#    twice gives ids 0 and 1, because identity IS sharing for a buffer.
#
# 4. DECISION: a TMX is an ASSET (immutable, shared, one per path in the
#    ResourceManager memo); a TileMap is a GAME OBJECT (its own camera, its
#    own writable grid). The factory is the seam. Sheets are shared through
#    `ResourceManager.buffer()`; `copy()` is how a caller opts out.
#    REQUIREMENT: shared things must be safe to share, and mutable things must
#    be owned. A camera is per-instance state that must never be silently
#    shared; a sheet is 22KB of pixels that has no business being duplicated.
#    EVIDENCE: audited -- NOTHING in the engine writes to a loaded sheet.
#    Every `.edit()`/`.pixels[...]` site in the tree writes to a buffer the
#    writer minted for itself (GlyphLayer's composite target, PlaneOverlay,
#    SpritePool). Recolouring goes through the palette to the shader, which
#    `TileLayer.palette` states outright: "recolouring by swapping the table
#    instead of the pixels is the whole point of an indexed surface." The
#    sprite pillar already runs this exact law -- SpriteSheet takes per-sheet
#    mutable copies while "the spec stays pristine in the ResourceManager
#    cache" -- so tile is converging onto it, not inventing it.
#
# 5. DECISION: `copy()` gives a fully independent TileMap -- its own writable
#    grid AND its own PixelBuffer, hence its own texture and its own interned
#    tile pool -- on a fresh unattached layer.
#    REQUIREMENT: destructive per-game edits (eating the dirt in a Boulder
#    Dash, blowing up a wall) must stay local to the game that made them.
#    EVIDENCE: buffer granularity is forced, not chosen. Copying the TileAtlas
#    alone would still share pixels, because an atlas is arithmetic OVER a
#    buffer; and sharing is keyed on buffer identity (`PixelBuffer.id` names
#    the texture in a Tile and keys its pool in the TileCache). So a private
#    buffer is the smallest unit at which "blow up a tile" cannot leak.
#
# 6. DECISION: `blit(tile, x, y, flags)` stays CONTENT-FIRST, matching
#    `console.write(text, at=...)`. Position-first was sketched and declined.
#    REQUIREMENT: see #1 -- the pillars spell one idea one way.
# ---------------------------------------------------------------------------

from __future__ import annotations

from math import ceil, floor
from typing import Optional, Tuple, Union

import numpy as np

from blitspersecond.display.controller import Controller
from blitspersecond.resources import TiledFile

from .tile_atlas import TileAtlas
from .tile_layer import TileLayer


class TileMap(Controller):
    """One tile layer of a world: the grid, the sheet it indexes, and the
    TileLayer it stamps into.

        maps = ResourceManager().tmx("island.tmx")
        island = maps.tilemap("blue_island")
        bps.layers.add(island)
        island.scroll = (x, 0)            # streams as the camera moves
        island.layer.after(background)    # the console.layer equivalent

    Built unattached and drawn only once `layers.add` places it -- the same
    law every graphics type here obeys.

    `TileMap(atlas, tiles)` is the normal road. `TileMap(layer, tiles)` is the
    advanced one, for when you have already minted and configured the layer --
    the direct analogue of `Console(GlyphLayer(...))` beside `Console(80, 45)`.
    """

    def __init__(
        self,
        source: Union[TileAtlas, TileLayer],
        tiles: np.ndarray,
        tmx: Optional[TiledFile] = None,
    ) -> None:
        if isinstance(source, TileLayer):
            layer = source
        elif isinstance(source, TileAtlas):
            layer = TileLayer(source)
        else:
            raise TypeError(
                f"TileMap takes a TileAtlas (the normal road) or a TileLayer "
                f"you built yourself; got {type(source)}"
            )
        if tiles.ndim != 2:
            raise TypeError(f"expected a 2-D tile grid, got shape {tiles.shape}")
        self._layer = layer
        # Ours, and writable: the factory hands over a copy so the cached TMX
        # record stays pristine (same law as PixelBuffer copying ImageSpec).
        self._tiles = tiles
        self._tmx = tmx
        self._scroll: Tuple[float, float] = (0.0, 0.0)
        self._coarse: Tuple[int, int] = (0, 0)
        self._origin: Tuple[int, int] = (0, 0)
        # The cell window currently stamped, so a camera step only pays for
        # what actually came into view. Empty until the first fill.
        self._window: Tuple[int, int, int, int] = (0, 0, 0, 0)
        self._refill()

    # -- what it controls ---------------------------------------------------

    @property
    def layer(self) -> TileLayer:
        """The TileLayer this map stamps into -- tint or repaint it there.

        The generic name every controller shares, so `layers.add` can find
        the layer inside one without knowing what kind it holds. You do NOT
        need it to reorder: `tilemap.before(sprites)` proxies through, same
        as Console.
        """
        return self._layer

    @property
    def tiles(self) -> np.ndarray:
        """The world grid, `tiles[x, y]` -- X FIRST, screen order.

        The console's `cell` for this pillar: one sheet index and one
        orientation per cell, `tile` -1 meaning empty. Stored x-major, so this
        order is native and a whole-column write is contiguous.

        This looks like a NumPy index and is one -- but read `tiles[a, b]` as
        "column a, row b", the order `Size`, `blit` and `scroll` already use.
        `tiles[:, 5]` is every cell on ROW 5.
        """
        return self._tiles

    @property
    def tileset(self) -> TileAtlas:
        """The sheet this map's indices mean -- read-through to the layer.

        Shared by every map over the same image unless you took `copy()`.
        """
        return self._layer.atlas

    @property
    def tmx(self) -> Optional[TiledFile]:
        """The file this layer came from, when it came from one."""
        return self._tmx

    def copy(self) -> "TileMap":
        """An independent TileMap: its own grid and its own sheet.

        Everything about the returned map is private to it -- a fresh
        PixelBuffer means a fresh texture and a fresh interned tile pool -- so
        a game can blow up tiles, eat the dirt, or repaint the sheet without
        any of it reaching the shared original. Unattached, like any freshly
        built layer.
        """
        atlas = self._layer.atlas
        private = TileAtlas(atlas.buffer.copy(), *atlas.tile_size)
        twin = TileMap(private, np.array(self._tiles), self._tmx)
        twin.origin = self._origin
        twin.scroll = self._scroll
        return twin

    # -- the world ----------------------------------------------------------

    @property
    def cols(self) -> int:
        """World width in cells -- the grid's, not the viewport's."""
        return int(self._tiles.shape[0])

    @property
    def rows(self) -> int:
        """World height in cells."""
        return int(self._tiles.shape[1])

    @property
    def size(self) -> Tuple[int, int]:
        """World size in cells, `(cols, rows)`."""
        return self.cols, self.rows

    @property
    def tile_size(self) -> Tuple[int, int]:
        tw, th = self._layer.tile_size
        return int(tw), int(th)

    @property
    def pixel_size(self) -> Tuple[int, int]:
        """World size in pixels."""
        tw, th = self.tile_size
        return self.cols * tw, self.rows * th

    # -- the camera ---------------------------------------------------------

    @property
    def scroll(self) -> Tuple[float, float]:
        """Camera position in world pixels.

        Sub-cell movement is one GPU translate and restamps nothing. Crossing
        a cell boundary refills the visible window from the world grid, which
        is the whole reason this class exists.

        Measured: 100 maps scrolling sub-cell hold 60fps with compose
        unchanged; the cost is entirely the refill at column crossings.
        """
        return self._scroll

    @scroll.setter
    def scroll(self, value: Tuple[float, float]) -> None:
        sx, sy = float(value[0]), float(value[1])
        tw, th = self.tile_size
        coarse = (floor(sx / tw), floor(sy / th))
        self._scroll = (sx, sy)
        # The layer bakes the coarse step into its local origins and culls the
        # stamps that left, so afterwards it holds exactly the intersection of
        # the old and new windows. Only the difference needs painting.
        self._layer.scroll = (sx, sy)
        if coarse != self._coarse:
            self._coarse = coarse
            self._refill()

    @property
    def origin(self) -> Tuple[int, int]:
        """Where the world's top-left cell sits, in pixels, before the camera.

        A map smaller than the screen is centred with this rather than by
        parking the camera at a negative offset -- the camera is where you are
        looking, the origin is where the world is.
        """
        return self._origin

    @origin.setter
    def origin(self, value: Tuple[int, int]) -> None:
        origin = (int(value[0]), int(value[1]))
        if origin != self._origin:
            self._origin = origin
            self._window = (0, 0, 0, 0)
            self._layer.clear()
            self._refill()

    def _visible(self) -> Tuple[int, int, int, int]:
        """The cell window the layer can currently hold: the tile-aligned
        viewport plus its one-tile border, in world cells, clamped to the
        grid."""
        from blitspersecond.system.config import Config

        tw, th = self.tile_size
        disp = Config().display
        ccol, crow = self._coarse
        ox, oy = self._origin
        x0 = ccol + floor((-tw - ox) / tw)
        y0 = crow + floor((-th - oy) / th)
        x1 = ccol + ceil((ceil(disp.width / tw) * tw + tw - ox) / tw)
        y1 = ceil((ceil(disp.height / th) * th + th - oy) / th) + crow
        return (
            max(0, x0),
            max(0, y0),
            min(self.cols, x1),
            min(self.rows, y1),
        )

    def _refill(self) -> None:
        """Paint the cells that just came into view, and only those.

        The layer culls what left; this stamps what arrived. Scrolling along
        one axis therefore costs one column or row of blits, not a whole
        screen of them -- which is the difference between a 3840px level
        streaming and repainting ~2,800 cells every eight pixels.
        """
        new = self._visible()
        old = self._window
        self._window = new
        nx0, ny0, nx1, ny1 = new
        if nx1 <= nx0 or ny1 <= ny0:
            return
        ox0, oy0, ox1, oy1 = old
        if ox1 <= ox0 or oy1 <= oy0:  # nothing held: paint it all
            self._blit_cells(nx0, ny0, nx1, ny1)
            return
        # Rect difference: the columns that entered, then the rows that
        # entered over the columns that were already there (so the corner is
        # not stamped twice -- a doubled stamp is invisible but not free).
        if nx0 < ox0:
            self._blit_cells(nx0, ny0, min(ox0, nx1), ny1)
        if nx1 > ox1:
            self._blit_cells(max(ox1, nx0), ny0, nx1, ny1)
        cx0, cx1 = max(nx0, ox0), min(nx1, ox1)
        if cx0 < cx1:
            if ny0 < oy0:
                self._blit_cells(cx0, ny0, cx1, min(oy0, ny1))
            if ny1 > oy1:
                self._blit_cells(cx0, max(oy1, ny0), cx1, ny1)

    def _blit_cells(self, x0: int, y0: int, x1: int, y1: int) -> None:
        """Stamp one world-cell rectangle at its place in the layer's local
        space: world cell c sits at (c - coarse) * tile + origin, and the
        layer applies the sub-cell remainder itself at draw time."""
        if x1 <= x0 or y1 <= y0:
            return
        tw, th = self.tile_size
        ccol, crow = self._coarse
        ox, oy = self._origin
        self._layer.blit(
            self._tiles[x0:x1, y0:y1],
            (x0 - ccol) * tw + ox,
            (y0 - crow) * th + oy,
        )

    # -- reading the world --------------------------------------------------

    def __getitem__(self, cell: Tuple[int, int]) -> np.void:
        """`tilemap[x, y]` -> one cell's (tile, flags). Same order as
        `tiles[x, y]`, which is what this reads."""
        return self._tiles[cell]
