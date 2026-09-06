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
    collision.py  CollisionMask: the bit-packed screen-space plane a
                  collidable map stamps into, with the border that makes
                  scrolling it cheap
    tiled.py      TiledFile: reading a world out of a Tiled file -- one road
                  in among two, and not the privileged one
    internal.py   the `tile[n]` face -- a shape you reach through and never
                  build

## Status

Under construction. `docs/bps/TILEMAP_PORT.md` is the port audit -- what
survives from the reference tree, what does not, and why -- and the board
carries one card per step at the top of `bps-board/tasks/todo`.

**Landed:** `shader.py`, `batch.py`, the shell, placement, the draw, TMX
loading, the camera, `wrap` -- worlds with no edges, per axis -- and
collision. A `TileMap` you `bps.layers.add` paints its own window and streams
as `scroll` moves -- one texture bind and one draw call however much of the
world is showing, and no buffer written at all between cell boundaries.

Collision rides the same split, and lands in the same phase. A tile's bits
are stamped when its quad is, so the mask is filled by the three occasions a
tile is drawn at all: the first frame's whole screen, a hand `blit`, and the
gutter a scroll exposes. A coarse step slides the stored plane in whole words
and whole rows; a fine one moves nothing on either side of the machine,
because `_fine` is one number and both the vertex attribute and the query's
offset read it -- latched, because a collision is a fact about the frame that
was painted.

**Still to come:** the colour registers.
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
