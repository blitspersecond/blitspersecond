"""Engine internals: what the renderers rely on, not what users interact
with. The test for living here is "does user code ever import it or meet it
in a hint?" -- if no, it's internal.

  layer.py           Layer: a place in the draw order, and in every
                     subclass the content that place shows. The base every
                     pillar derives from.
  layers.py          Layers: the ordered collection of them, background to
                     foreground. The compositor walks it once a frame.
  palette_texture.py PaletteTexture: a Palette's GPU projection, the 256x1
                     row the indexed shader samples on unit 1.

`PaletteTexture` is internal in the plain sense: a game never names a
palette's GPU row, and it needs a current GL context, so it is draw-time
machinery rather than anything to hold. It is shared rather than per-engine
because a Palette is: one `PixelBuffer`'s table can back a tile layer and a
sprite layer at once, and they should sample one 256x1 row. Shader PROGRAMS
are not shared that way -- each engine compiles its own, in its own
`shader.py`; see `graphics/console/shader.py` for why.

The draw-order pair is subtler, and the two halves qualify differently.
`Layers` is never NAMED by a game: `BlitsPerSecond` builds the one that
exists and lends it out as `bps.layers`, a getter with no setter, so there is
no second one to construct and no way to swap this one. `Layer` is
different: a game DOES construct one, because a pillar IS one -- `Console(...)`
is a Layer, and `TileMap(...)` will be -- and calls its methods constantly
(`hud.after(actors)`, `layer.enabled = False`). What a game never does is name
this base class.

Which is why `Layer` is not exported from `graphics` even though its API is
public. Exporting it would invite `class MyLayer(Layer)`, and a bare Layer
composites nothing -- the content is what the subclass IS. You construct the
pillar, never the base.
"""

from .layer import Layer
from .layers import Layers
from .palette_texture import PaletteTexture

__all__ = [
    "Layer",
    "Layers",
    "PaletteTexture",
]
