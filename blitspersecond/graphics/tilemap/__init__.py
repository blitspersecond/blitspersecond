"""The tilemap: a grid of cells indexing one sheet, with a camera onto a world
larger than the screen.

**A `TileMap` IS a `Layer`** -- one class, built and placed in the draw order
like any other, with nothing sitting above it holding one. That is the same
collapse the console went through, and it closes the last place where
`Layer`'s own law was false.

The pillar's contract, from which most of its design follows:

    one TileMap, one sheet, and the sheet is immutable

Not a limitation being accepted -- the assumption `TileBatch` is built around.
One texture means one bind and one `glDrawElements` for the whole layer, at
any quad count. Overlays and parallax are more layers, which is also how you
get more collision identities; sharing art between them is free, because
`PixelBuffer` interns by path.

## The modules

    shader.py     the two programs this layer draws with -- `indexed.frag`
                  for a palette sheet, `direct.frag` for RGBA
    batch.py      TileBatch: the GPU backend -- N axis-aligned quads from a
                  NumPy SoA, one bind and one draw call for the whole layer
    tilemap.py    TileMap: the class, the world's `PLANE_DTYPE`, the
                  validation both doors into a world go through, and the
                  placement SoA that stamping fills
    tile.py       Tile: one cell of the sheet, owned by the map that minted it
    tiled.py      TiledFile: reading a world out of a Tiled file -- one road
                  in among two, and not the privileged one
    internal.py   the `tile[n]` face -- a shape you reach through and never
                  build

## Status

Under construction. `docs/bps/TILEMAP_PORT.md` is the port audit -- what
survives from the reference tree, what does not, and why -- and the board
carries one card per step at the top of `bps-board/tasks/todo`.

**Landed:** `shader.py`, `batch.py`, the shell, placement, the draw, TMX
loading, the camera, and `wrap` -- worlds with no edges, per axis. A `TileMap` you `bps.layers.add` paints its own
window and streams as `scroll` moves -- one texture bind and one draw call
however much of the world is showing, and no buffer written at all between
cell boundaries.

**Still to come:** the colour registers, and collision.
"""

from .tile import Tile
from .tiled import TiledFile, TiledLayer
from .tilemap import EMPTY, PLANE_DTYPE, TileMap, empty_map

__all__ = [
    "EMPTY",
    "PLANE_DTYPE",
    "Tile",
    "TileMap",
    "TiledFile",
    "TiledLayer",
    "empty_map",
]
