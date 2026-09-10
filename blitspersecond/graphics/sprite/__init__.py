"""The sprite pillar: independently movable, named things over one sheet.

    chun = Sprite(SpriteSheet("chun-li.sprite.json"))
    bps.layers.add(chun)
    chun.play("walk")                   # advances itself on the tick
    chun.position = (160, 200)

    ghosts = chun.before.extend(4)      # a trail, drawn behind her

**A `Sprite` IS a `Layer`, and there is exactly one per layer** -- because two
things told apart by a collision question must be two layers. A mirror match
is two sprites binding one interned sheet. `before` and `after` hold further
DRAWINGS of this sprite, sharing its art, its bind and its collision plane.

**`bps/reference/graphics/sprites.md` is the chapter**: what a presentation is and what
only the primary has, the palette register, the frame plan and its runs, the
collision composition, the population curve, and every limit and refusal by
name. `bps/reference/graphics/sprite-package.md` is the `.sprite.json` format.
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
