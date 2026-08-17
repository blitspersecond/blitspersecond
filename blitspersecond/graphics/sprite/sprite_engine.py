"""The auto-attaching SpriteLayer factory."""

from blitspersecond.display.layer import attach_to_display

from .sprite_layer import SpriteLayer
from .sprite_sheet import SpriteSheet


class SpriteEngine:
    """The static factory: make a SpriteLayer and put it on screen.

        chun_layer = SpriteEngine(sheet)
        chun = chun_layer.sprite("stance-01")

    Calling it returns a SpriteLayer (__new__ hands back the layer; no
    SpriteEngine instance ever exists), appended at the front of the running
    engine's draw order. Placement and layer.enabled are the caller's
    business from there. If no BlitsPerSecond engine exists yet (GL-free
    tests, offline tools), the layer is returned unattached, exactly as
    SpriteLayer(sheet) would build it.
    """

    def __new__(cls, sheet: SpriteSheet) -> SpriteLayer:
        return attach_to_display(SpriteLayer(sheet))
