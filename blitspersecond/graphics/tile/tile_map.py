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
        self._stamp()

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
        self._layer.scroll = (sx, sy)
        if coarse != self._coarse:
            self._coarse = coarse
            self._stamp()

    def _stamp(self) -> None:
        """Refill the layer with the cells the camera can currently see.

        A full restamp of the window rather than a border-only refill: the
        layer has just culled, and one blit of a plane slice is simple and
        obviously correct. Incremental refill is an optimisation to make when
        a profile asks for it, and it changes nothing outside this method.
        """
        from blitspersecond.system.config import Config

        tw, th = self.tile_size
        disp = Config().display
        ccol, crow = self._coarse
        # The layer draws a tile-aligned viewport plus a one-tile border.
        x0, y0 = max(0, ccol - 1), max(0, crow - 1)
        x1 = min(self.cols, ccol + ceil(disp.width / tw) + 1)
        y1 = min(self.rows, crow + ceil(disp.height / th) + 1)
        self._layer.clear()
        if x1 <= x0 or y1 <= y0:
            return
        # Local space: world cell c sits at (c - coarse) * tile, and the layer
        # applies the sub-cell remainder itself at draw time.
        self._layer.blit(
            self._plane[y0:y1, x0:x1], (x0 - ccol) * tw, (y0 - crow) * th
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
