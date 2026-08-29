"""The graphics toolbox: everything a game draws with, in one import.

    from blitspersecond.graphics import PixelBuffer, Palette, RED

`PixelBuffer` is a picture: `PixelBuffer("art.png")` reads it, validates it,
and hands back THE buffer for that file -- so two layers over one tileset
share one texture and one tile pool. `PixelBuffer((64, 64), "P")` is a blank
canvas instead, shared with nobody. `copy()` is how you own art that arrived
shared.

`Atlas` cuts one of those into equal cells -- `Atlas(buffer, 16, 16)` -- and
hands them back by index with a `len()`. A charset's glyphs, a tilemap's
tiles and a sprite sheet's frames are all that shape, so the cutting is here
once rather than in each engine, and each engine brings its own rule about
how many cells it needs.

`Palette` is the 256-entry table an indexed image carries, and the names
(`RED`, `CYAN`, `TRANSPARENT`) are indices INTO one, not colours in
themselves. See `palette.py` for why a colour being an index is what puts
this package, rather than the framebuffer, in charge of it.

Loading lives here rather than in a package of its own. An earlier tree had
`resources` -- a loading dock holding images, sounds and tile maps together
on the grounds that they all came off a disk. Reading a file is not what any
of them are FOR: an image belongs with the things that draw it, a sound with
the things that play it, and what they share is caching behaviour, which is
`common.CacheableResource` and needs no address of its own.

`TileMap` is a world of cells over one sheet, and IS a layer: build one,
`bps.layers.add` it, and it draws. `Tile` is a cell of its sheet, which only
that map will draw; `PLANE_DTYPE`, `EMPTY` and `empty_map` are how you author
a world by hand rather than loading one, which is a first-class road and not a
workaround.

`TiledFile` is the other road -- `TiledFile("island.tmx").tilemap("trees")` --
and it is a convenience, not the way in. It fills the same array `empty_map`
hands you empty, so a game generating its own world never imports it and loses
nothing by not doing so.

The remaining engine -- sprite -- and the rest of the material they draw from
land here as the rebuild brings them back; this file is the list of what a
game is meant to reach for, so a name arrives on it when its engine does.

The draw order is NOT on that list. `Layer` and `Layers` live in
`graphics.internal`: the engine owns the one collection there is and lends it
out as `bps.layers`, and a pillar IS a layer, so you build a `Console` or a
`TileMap` and hand it over rather than naming the base class. See that
package's docstring for why the public-looking half is still internal.
"""

from .atlas import Atlas
from .pixel_buffer import PixelBuffer
from .tilemap import (
    EMPTY,
    PLANE_DTYPE,
    Tile,
    TileMap,
    TiledFile,
    TiledLayer,
    empty_map,
)
from .orientation import (
    FLIP_OF,
    ROTATION_OF,
    SHAPE_PRESERVING,
    Orientation,
    as_flip_rotation,
    from_flip_rotation,
    applies_to,
    swaps_axes,
)
from .palette import (
    ConsolePalette,
    Palette,
    system_palette,
    TRANSPARENT,
    WHITE,
    SILVER,
    GRAY,
    BLACK,
    RED,
    MAROON,
    YELLOW,
    OLIVE,
    LIME,
    GREEN,
    CYAN,
    TEAL,
    BLUE,
    NAVY,
    MAGENTA,
    PURPLE,
)

__all__ = [
    "Atlas",
    "PixelBuffer",
    "TileMap",
    "Tile",
    "TiledFile",
    "TiledLayer",
    "PLANE_DTYPE",
    "EMPTY",
    "empty_map",
    "Orientation",
    "SHAPE_PRESERVING",
    "swaps_axes",
    "applies_to",
    "as_flip_rotation",
    "from_flip_rotation",
    "FLIP_OF",
    "ROTATION_OF",
    "Palette",
    "ConsolePalette",
    "system_palette",
    "TRANSPARENT",
    "WHITE",
    "SILVER",
    "GRAY",
    "BLACK",
    "RED",
    "MAROON",
    "YELLOW",
    "OLIVE",
    "LIME",
    "GREEN",
    "CYAN",
    "TEAL",
    "BLUE",
    "NAVY",
    "MAGENTA",
    "PURPLE",
]
