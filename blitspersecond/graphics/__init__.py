"""The graphics toolbox: everything a game draws with, in one import.

    from blitspersecond.graphics import PixelBuffer, TileAtlas

Layers are minted by `bps.layers`, not from here -- `bps.layers.tile(...)`,
`.sprite(sheet)`, `.glyph(charset)` each build one and put it in the draw
order. There are three kinds: TileLayer (graphics.tile, retained tile
placements over an atlas; also the whole-file path, one buffer as one giant
tile), GlyphLayer (graphics.glyph, a glyph grid at a position) and SpriteLayer
(graphics.sprite, rich named instances over a SpriteSheet -- one sheet per
layer, `layer.sprite("stance-01")` for an instance).

What this package supplies is the material those layers draw FROM: PixelBuffer
is the pixel-data primitive; Tile / TileAtlas / TileFlags address and orient
tiles; CharSet names the system character sets; CollisionQuery is what
collision questions return. TileMap is the non-rendering TMX resource: loaded
TileAtlases plus ordinary tile/flags arrays, never compositor Layers.

The layer classes are exported too, for the unattached path -- `TileLayer(...)`
builds one off-screen until `bps.layers.add(layer)` places it. TileEngine,
SpriteEngine and GlyphEngine are legacy aliases for those same three classes;
they used to attach on construction and no longer do.

Engine packages are where things LIVE (each with its own machinery --
TileBatch, TileCache, CollisionMask -- plus specialist faces like
graphics.glyph's cell constants); this package is where users GET the public
faces. Engine internals users never meet (Point/Vector, PaletteTexture)
live in graphics.internal; the shared vocabulary modules the
engines consume live in graphics.common; machinery shared with OTHER
subsystems -- Shader, which display's framebuffer post-pass needs too --
lives in blitspersecond.core. (The console -- teletype semantics
over a GlyphLayer -- is a consumer of this engine, one level up in
blitspersecond.console.)
"""

from .common import CollisionQuery, PixelBuffer
from .glyph import CharSet, GlyphEngine, GlyphLayer
from .sprite import (
    Animation,
    Sprite,
    SpriteEngine,
    SpriteLayer,
    SpritePlane,
    SpritePool,
    SpriteSheet,
)
from .tile import (
    Tile,
    TileAtlas,
    TileEngine,
    TileFlags,
    TileLayer,
    TileMap,
)

__all__ = [
    "PixelBuffer",
    "CharSet",
    "Tile",
    "TileAtlas",
    "TileEngine",
    "TileLayer",
    "TileFlags",
    "TileMap",
    "Animation",
    "Sprite",
    "SpriteEngine",
    "SpriteLayer",
    "SpritePlane",
    "SpritePool",
    "SpriteSheet",
    "GlyphEngine",
    "GlyphLayer",
    "CollisionQuery",
]
