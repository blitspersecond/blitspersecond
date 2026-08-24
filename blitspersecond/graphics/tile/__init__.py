"""The tile system: everything that addresses an image as a grid and draws
retained placements from it.

TileLayer is the retained tile implementation -- placements over one
TileAtlas, drawn as a single TileBatch, with screen-space collision when
asked. `bps.layers.tile(...)` builds one and puts it in the draw order;
TileEngine is a legacy alias for the class itself, which used to attach on
construction and no longer does.

TileMap is the controller over a TileLayer -- the grid, the sheet it indexes,
and a camera onto a world bigger than the screen -- and is to TileLayer what
Console is to GlyphLayer. TileMaps are minted by a TiledFile,
`ResourceManager().tmx(path).tilemap(name)`, unattached, and placed by
`layers.add`.

Around them live the addressing types the next tile-consuming engine
(sprite/BOB reading anim frames off a sheet) would reuse: Tile (a cheap
interned pointer into an atlas), TileAtlas (the grid reading over a
PixelBuffer) and TileCache (the global interning pool behind Tile()).

Everything else here is THIS engine's path: the TileBatch GPU backend
(axis-aligned quads, destructive compaction) and its CollisionMask (masks
of bounded local placements). The vocabulary all engines share -- PixelBuffer,
the CWORD collision layout -- lives in blitspersecond.graphics.common;
engine machinery users never meet (Point/Vector) lives in
blitspersecond.graphics.internal, and machinery shared with other subsystems
(Shader) in blitspersecond.core. The public faces
(Tile, TileAtlas, TileLayer, TileFlags) are re-exported from
blitspersecond.graphics -- the user's toolbox import.
"""

from .tile import Tile
from .tile_atlas import TileAtlas, TileIndex
from .tile_cache import TileCache
from .tile_engine import TileEngine
from .tile_layer import TileLayer
from .tile_map import TileMap
from .batch import TileBatch, TileFlags
from .collision import CollisionMask, CollisionQuery

__all__ = [
    "Tile",
    "TileAtlas",
    "TileIndex",
    "TileCache",
    "TileEngine",
    "TileLayer",
    "TileMap",
    "TileBatch",
    "TileFlags",
    "CollisionMask",
    "CollisionQuery",
]
