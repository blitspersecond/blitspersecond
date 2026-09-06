"""The sprite pillar: independently movable, named things over one sheet.

Under construction. `docs/bps/SPRITE_PORT.md` is the port audit and the board
carries one card per step; `docs/bps/SPRITE_FORMAT.md` is the package format
this reads.

**A `Sprite` IS a `Layer`, and there is exactly one per layer** -- because
two things told apart by a collision question must be two layers. A mirror
match is two sprites binding one sheet.

    chun = Sprite(SpriteSheet("chun-li.sprite.json"))
    bps.layers.add(chun)
    chun.play("walk")                   # advances itself on the tick
    chun.position = (160, 200)

    ghosts = chun.before.extend(4)      # a trail, drawn behind her

**Landed:** the sheet and its two loaders; `Sprite` itself; its
presentations -- the signed slot space a trail or a swarm lives in; the frame
plan; the draw; clips; and the silhouette collision plane.

Collision is composed, not retained: a sprite is at a coordinate and every
presentation moves independently, so there is nothing to slide and the plane
is cleared and refilled whenever something moved. It is rebuilt in the render
phase alongside the frame, so the tick that asks about it spends nothing.
Participation is asked three times and they are different questions -- the
sheet is always indexed so a sprite CAN collide; `collidable` switches the
layer; and `collide` decides each presentation, all-or-nothing across a
multipart pose. `visible` is never consulted, so a ghost is drawn and hits
nothing while a cloak hits and is not drawn.

Named planes land alongside it. An assembly authors `attack`, `body_hurt`
and the rest as AABBs in its local frame; each gets a plane of its own and
composing one is filling its rects in, through the same `local_rect` the art
goes through. Nothing downstream knows they were rectangles -- a query is a
bitwise intersection over packed planes, so a box plane and a pixel
silhouette answer the same way and cost the same. They belong to the primary
alone; every slot stamps the silhouette.

A sprite's planes are exactly the screen, with no working border: the
compiled stamp guards every access, so a sprite half off an edge writes what
lands and drops what does not.

**Still to come:** nothing in this pillar. `PlaneOverlay` -- the debug view
that paints the planes -- is the collision system's own remaining card.
"""

from .animation import Animation
from .package import GRID_TILESET, TRANSPARENT_INDEX
from .presentation import Presentation
from .sprite import Sprite
from .sheet import (
    Assembly,
    Clip,
    Frame,
    SpritePart,
    SpriteSheet,
    SpriteTileset,
)

__all__ = [
    "Animation",
    "Assembly",
    "Clip",
    "Frame",
    "GRID_TILESET",
    "TRANSPARENT_INDEX",
    "Presentation",
    "Sprite",
    "SpritePart",
    "SpriteSheet",
    "SpriteTileset",
]
