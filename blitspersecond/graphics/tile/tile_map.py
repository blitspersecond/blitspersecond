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
texture is lazy -- so reading planes for pathfinding or editing costs no
context. Only stamping into a layer needs one.
"""

from __future__ import annotations

from math import ceil, floor
from typing import Dict, Iterator, Optional, Tuple

import numpy as np

from blitspersecond.graphics.common import PixelBuffer
from blitspersecond.resources import ResourceManager, TiledFile

from .tile_atlas import TileAtlas
from .tile_layer import TileLayer


class TileMap:
    """One named layer of a TiledFile: its world plane, and the TileLayer it
    stamps into.

        maps = TileMap.open("island.tmx", bps.layers)
        island = maps["blue_island"]
        island.scroll = (x, 0)            # streams as the camera moves
        island.layer.after(background)    # the console.glyphs equivalent

    `open()` mints one layer per TMX layer in authored back-to-front order, so
    the artist's stacking IS the draw order, and shares one TileAtlas between
    layers drawn from the same tileset so they stay on one texture.

    The plane stays a plain structured array (PLANE_DTYPE, indexed
    `[row, col]`, tile -1 empty). Pathfinding, editing and collision prep read
    it directly; none of that depends on the layer.
    """

    def __init__(
        self,
        layer: TileLayer,
        plane: np.ndarray,
        tiled: Optional[TiledFile] = None,
    ) -> None:
        if not isinstance(layer, TileLayer):
            raise TypeError(f"expected TileLayer, got {type(layer)}")
        if plane.ndim != 2:
            raise TypeError(f"expected a 2-D map plane, got shape {plane.shape}")
        self._layer = layer
        self._plane = plane
        self._tiled = tiled
        self._scroll: Tuple[float, float] = (0.0, 0.0)
        self._coarse: Tuple[int, int] = (0, 0)
        self._origin: Tuple[int, int] = (0, 0)
        # The cell window currently stamped, so a camera step only pays for
        # what actually came into view. Empty until the first fill.
        self._window: Tuple[int, int, int, int] = (0, 0, 0, 0)
        self._refill()

    @classmethod
    def open(cls, filename: str, layers) -> Dict[str, "TileMap"]:
        """Every layer of a TMX as TileMaps, minted into `layers` in authored
        order (back to front), keyed and tagged by layer name.

        Layers sharing a tileset share one TileAtlas, and therefore one
        texture. Which tileset a layer uses is carried on TiledLayer, proved
        by the loader rather than re-derived here.
        """
        manager = ResourceManager()
        tiled = manager.tmx(filename)
        atlases = {
            tileset.name: TileAtlas(
                PixelBuffer(manager.image(tileset.image)), tileset.w, tileset.h
            )
            for tileset in tiled.tilesets
        }
        return {
            entry.name: cls(
                layers.tile(atlases[entry.tileset], tag=entry.name),
                np.array(entry.plane),
                tiled,
            )
            for entry in tiled.layers
        }

    # -- what it controls ---------------------------------------------------

    @property
    def layer(self) -> TileLayer:
        """The TileLayer this map stamps into -- reorder or tint it there."""
        return self._layer

    @property
    def plane(self) -> np.ndarray:
        """The whole world, `[row, col]`, tile -1 empty."""
        return self._plane

    @property
    def tiled(self) -> Optional[TiledFile]:
        """The file this layer came from, when it came from one."""
        return self._tiled

    # -- the world ----------------------------------------------------------

    @property
    def cols(self) -> int:
        """World width in cells -- the plane's, not the viewport's."""
        return int(self._plane.shape[1])

    @property
    def rows(self) -> int:
        """World height in cells."""
        return int(self._plane.shape[0])

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
        a cell boundary refills the visible window from the world plane, which
        is the whole reason this class exists.
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
        plane."""
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
            self._plane[y0:y1, x0:x1],
            (x0 - ccol) * tw + ox,
            (y0 - crow) * th + oy,
        )

    # -- reading the world --------------------------------------------------

    def __getitem__(self, cell: Tuple[int, int]) -> np.void:
        """`tilemap[col, row]` -> one cell's (tile, flags). Geometry order,
        matching the rest of the API; the plane underneath is `[row, col]`."""
        col, row = cell
        return self._plane[row, col]

    def __iter__(self) -> Iterator[np.ndarray]:
        """Rows of the world, top to bottom."""
        return iter(self._plane)

    def __len__(self) -> int:
        return self.rows
