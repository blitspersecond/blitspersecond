"""The graphics toolbox: everything a game draws with, in one import.

    from blitspersecond.graphics import PixelBuffer, TileAtlas, TileEngine

Three engines produce Layers -- TileEngine (graphics.tile, retained
tile placements over an atlas; also the whole-file path, one buffer as one
giant tile), GlyphEngine (graphics.glyph, a glyph grid at a
position) and SpriteEngine (graphics.sprite, rich named instances over a
SpriteSheet -- one sheet per layer, `layer.sprite("stance-01")` for an
instance). PixelBuffer is the pixel-data primitive they all draw from;
Tile / TileAtlas / TileFlags address and orient tiles; CollisionQuery is
what collision questions return. TileMap is the non-rendering TMX resource:
loaded TileAtlases plus ordinary tile/flags arrays, never compositor Layers.

Engine packages are where things LIVE (each with its own machinery --
TileBatch, TileCache, CollisionMask -- plus specialist faces like
graphics.glyph's cell constants); this package is where users GET the public
faces. Engine internals users never meet (Shader, Point/Vector)
live in graphics.internal; the shared vocabulary modules the
engines consume live in graphics.common. (The console -- teletype semantics
over the GlyphEngine -- is a consumer of this engine, one level up in
blitspersecond.console.)
"""

from .common import CollisionQuery, PixelBuffer
from .glyph import GlyphEngine, GlyphLayer
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
