"""The sprite system: few, rich, named objects -- the other end of the
spectrum from graphics.tile's many-cheap-anonymous.

SpriteSheet is the immutable bundle: tilesets (arbitrary named rects over
indexed PixelBuffers, with the package's authoritative palette tables as
sheet-owned mutable copies), assemblies (timeless poses: ordered parts +
named collision planes in one anchor-local frame) and animation data
(carried, not played). Load one with SpriteSheet.load(path). SpriteEngine
creates and attaches one SpriteLayer; that layer binds one sheet, is the
collision identity and depth position, and draws its instances as
material-coalesced runs -- the cost driver is material transitions, never
instance count.
Sprite is the instance, constructed against the engine
(`engine.sprite("stance-01")`): assembly, position, flip_x, rotation
(quarter-turns about the anchor -- the tile flip bits' D-move made
per-instance state; collision turns in lockstep), per-tileset
palette registers, colour registers, collide -- all per-presentation
state, no clocks anywhere.

Collision is in: every sprite layer answers `collides_with` with a DEFAULT
plane -- the occupied-pixel silhouette -- and assemblies may add named rect
planes selected per question
(`p1.collides_with(p2, mine="attack", theirs="body_hurt").at()`), the
names validated against the sheet's load-time vocabulary. Visible collidable
presentations stamp once into retained packed screen-space masks whenever
their state changes; every query thereafter is a cheap word AND. Cross-engine
comes free -- a sprite layer against a tile/text mask layer is the same
protocol.

PlaneOverlay is the debug view: the retained packed collision planes (named
planes, silhouette tint, anchors) painted onto an RGBA canvas you bind above
the sprite layers -- supercombo.gg's hitbox display. It paints the exact bits
queries test. Deliberately a debug tool, not a frame-path requirement.

Animation closes the loop: the tick sequencer over the sheet's carried
clip data -- internal frame counter, integer holds, loop never/forever,
settable frame, `done` for one-shots. `anim.update(); sprite.assembly =
anim.assembly` is the whole per-tick contract; play(name) is the cancel
primitive; hitstop is not calling update(). Everything smarter -- state
machines, branching, predicates -- is game code, per docs/SPRITES.md. The
layer itself still never reads animation data.

The interchange format and its loader live in resources.sprite_sheet
(resources load, graphics draw); the public faces re-export from
blitspersecond.graphics -- the user's toolbox import.
"""

from .animation import Animation
from .plane_overlay import PlaneOverlay
from .sprite import PaletteRegisters, Sprite
from .sprite_engine import SpriteEngine
from .sprite_layer import SpriteLayer
from .sprite_pool import SpritePlane, SpritePool
from .sprite_sheet import SpriteSheet, SpriteTileset

__all__ = [
    "Animation",
    "PaletteRegisters",
    "PlaneOverlay",
    "Sprite",
    "SpriteEngine",
    "SpriteLayer",
    "SpritePlane",
    "SpritePool",
    "SpriteSheet",
    "SpriteTileset",
]
