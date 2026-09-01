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

**Landed:** the sheet and its two loaders; `Sprite` itself; and its
presentations -- the signed slot space a trail or a swarm lives in.

**Still to come:** the frame plan, the draw, clips, and collision. It does not draw yet -- adding one to a draw order is
legal and shows nothing, exactly as `TileMap` did between its shell and its
draw.
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
