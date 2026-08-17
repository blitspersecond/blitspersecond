"""The auto-attaching TileLayer factory."""

from typing import Optional, Union

from blitspersecond.display.layer import attach_to_display
from blitspersecond.graphics.common import PixelBuffer

from .tile_atlas import TileAtlas
from .tile_layer import TileLayer


class TileEngine:
    """The static factory: make a TileLayer and put it on screen.

        background = TileEngine("tiles/background.png",
                                tilewidth=8, tileheight=8)
        background.blit(1, x, y)
        background.blit(tile_map.layers["ground"], x, y)
        background.before(foreground)

    Calling it returns a TileLayer (__new__ hands back the layer; there is
    never a TileEngine instance), appended at the front of the running
    engine's draw order -- making one IS showing it. Placing it and toggling
    layer.enabled from there is the caller's business.

    The factory carries the making; TileLayer carries the data. If no
    BlitsPerSecond engine exists yet (GL-free tests, offline tools), the
    layer is returned unattached, exactly as TileLayer(...) would build it.
    """

    def __new__(
        cls,
        source: Union[str, PixelBuffer, TileAtlas],
        tilewidth: Optional[int] = None,
        tileheight: Optional[int] = None,
        collidable: bool = False,
    ) -> TileLayer:
        return attach_to_display(
            TileLayer(source, tilewidth, tileheight, collidable)
        )
