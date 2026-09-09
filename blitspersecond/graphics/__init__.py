"""The graphics toolbox: everything a game draws with, in one import.

    from blitspersecond.graphics import PixelBuffer, PaletteBank, RED

`PixelBuffer` is a picture: `PixelBuffer("art.png")` reads it, validates it,
and hands back THE buffer for that file -- so two layers over one tileset
share one texture and one tile pool. `PixelBuffer((64, 64), "P")` is a blank
canvas instead, shared with nobody. `copy()` is how you own art that arrived
shared.

`Atlas` is a table of rects over one of those, addressed by key. A charset's
glyphs, a tilemap's tiles and a sprite's tiles are one thing -- a large image
cut into rects that ends up as quads on the GPU -- so it lives here once
rather than in each engine.

**Giving a cell size is an AUTOGRID: a way of generating that table, not a
second kind of atlas.** `Atlas(buffer, 16, 16)` fills it by dividing the image
and keys by index; `Atlas(buffer, {"hit-01": Rect(1, 1, 83, 101)})` is handed
an authored package's rectangles and keys by name. Same table, same `len()`,
`atlas[key]` and `atlas.rect(key)` -- which is the whole of what a draw path
needs. `cell`, `cols` and `rows` are the autogrid's own parameters, kept
because the consumer that supplied them usually needs them back. Each engine
brings its own rule about how many rects it needs and what a key means.

`PaletteBank` is 256 colour tables as one block, and the names
(`RED`, `CYAN`, `TRANSPARENT`) are indices INTO one, not colours in
themselves. See `palette.py` for why a colour being an index is what puts
this package, rather than the framebuffer, in charge of it. `PaletteBank` is
256 palettes of 256 colours, which the GPU holds as a single 256x256
texture the bank owns and uploads.

Loading lives here rather than in a package of its own. An earlier tree had
`resources` -- a loading dock holding images, sounds and tile maps together
on the grounds that they all came off a disk. Reading a file is not what any
of them are FOR: an image belongs with the things that draw it, a sound with
the things that play it, and what they share is caching behaviour, which is
`common.CacheableResource` and needs no address of its own.

`TileMap` is a world of cells over one sheet, and IS a layer: build one,
`bps.layers.add` it, and it draws. `Tile` is a cell of its sheet, which only
that map will draw. `TileMap(sheet, cols, rows)` makes an empty world and
`tilemap.cell[x, y]` is the only road into it -- for reading, for writing, and
for a whole block in one write. `EMPTY_TILE` is the word for a cell holding
nothing, needed when you fill a buffer taken off `cell[:, :]` by hand.

`TiledFile` is the other road -- `TiledFile("island.tmx").tilemap("trees")` --
and it is a convenience, not the way in. A `.tmx` layer arrives as a world
already made, so it has a door of its own; a game generating its own world
writes through `cell` and never imports it.

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
    EMPTY_TILE,
    Tile,
    TileMap,
    TiledFile,
    TiledLayer,
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
from .color import ColorQuad
from .palette import (
    PaletteBank,
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
    "EMPTY_TILE",
    "Orientation",
    "SHAPE_PRESERVING",
    "swaps_axes",
    "applies_to",
    "as_flip_rotation",
    "from_flip_rotation",
    "FLIP_OF",
    "ROTATION_OF",
    "ColorQuad",
    "PaletteBank",
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
