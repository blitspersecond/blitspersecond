"""The tilemap: a grid of cells indexing one sheet, with a camera onto a
world larger than the screen.

`bps/reference/graphics/tiles.md` is the authority on this pillar -- the contract, the
costs, what refuses, and why the camera splits the way it does.

The contract everything here follows from:

    one TileMap, one sheet, and the sheet is immutable

One texture means one bind and one `glDrawElements` for the whole layer, at
any quad count. Overlays and parallax are more layers, which is also how you
get more collision identities.

**A `TileMap` IS a `Layer`** -- built and placed in the draw order like any
other, with nothing sitting above it holding one.

## The modules

    shader.py     the two programs this layer draws with -- `indexed.frag`
                  for a palette sheet, `direct.frag` for RGBA
    batch.py      TileBatch: the GPU backend -- N axis-aligned quads from a
                  NumPy SoA, one bind and one draw call for the whole layer
    tilemap.py    TileMap: the class, the world's `PLANE_DTYPE`, the
                  validation both doors into a world go through, and the
                  placement SoA that stamping fills
    tile.py       Tile: one cell of the sheet, owned by the map that minted it
    collision.py  CollisionMask: the bit-packed screen-space plane a
                  collidable map stamps into, with the border that makes
                  scrolling it cheap
    tiled.py      TiledFile: reading a world out of a Tiled file -- one road
                  in among two, and not the privileged one
    internal.py   the `tile[n]` and `cell[x, y]` faces -- shapes you reach
                  through and never build
"""

from .tile import Tile
from .tiled import TiledFile, TiledLayer
from .internal import EMPTY_TILE
from .tilemap import TileMap

__all__ = [
    "EMPTY_TILE",
    "Tile",
    "TileMap",
    "TiledFile",
    "TiledLayer",
]
