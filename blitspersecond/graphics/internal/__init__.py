"""Engine internals: what the renderers rely on, not what users interact
with. The test for living here is "does user code ever import it or meet it
in a hint?" -- if no, it's internal.

  layer.py           Layer: a place in the draw order, and in every
                     subclass the content that place shows. The base every
                     pillar derives from.
  layers.py          Layers: the ordered collection of them, background to
                     foreground. The compositor walks it once a frame.
  palette_texture.py PaletteTexture: the GPU projection of a palette table,
                     the 256xN texture the indexed shader samples on unit 1.

**`Layer` is not exported from `graphics`, though its API is public.** A game
constructs a pillar and calls a layer's methods constantly; what it never does
is name this base class, and exporting it would invite `class MyLayer(Layer)`
-- a bare Layer composites nothing, because the content is what the subclass
IS. `Layers` is not named by a game either: `BlitsPerSecond` builds the one
that exists and lends it out as `bps.layers`, a getter with no setter.

`PaletteTexture` is shared rather than per-engine because a table is: one
`PixelBuffer`'s palette can back a tile layer and a sprite layer at once, and
they should sample one row. Shader PROGRAMS are not shared that way -- each
engine compiles its own.

The design record is `bps/reference/layers.md`.
"""

from .layer import Layer
from .layers import Layers
from .palette_texture import PaletteTexture

__all__ = [
    "Layer",
    "Layers",
    "PaletteTexture",
]
