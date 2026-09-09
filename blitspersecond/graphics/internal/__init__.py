"""Engine internals: what the renderers rely on, not what users interact
with. The test for living here is "does user code ever import it or meet it
in a hint?" -- if no, it's internal.

  layer.py           Layer: a place in the draw order, and in every
                     subclass the content that place shows. The base every
                     pillar derives from.
  layers.py          Layers: the ordered collection of them, background to
                     foreground. The compositor walks it once a frame.

**`Layer` is not exported from `graphics`, though its API is public.** A game
constructs a pillar and calls a layer's methods constantly; what it never does
is name this base class, and exporting it would invite `class MyLayer(Layer)`
-- a bare Layer composites nothing, because the content is what the subclass
IS. `Layers` is not named by a game either: `BlitsPerSecond` builds the one
that exists and lends it out as `bps.layers`, a getter with no setter.

A palette table is NOT here: colour belongs to the layer drawing the art, so
a `PaletteBank` owns its own 256x256 texture and uploads it -- see
`graphics.palette` and `bps/reference/palette.md`. Shader PROGRAMS are not
shared the way a bank is -- each
engine compiles its own.

The design record is `bps/reference/layers.md`.
"""

from .layer import Layer
from .layers import Layers

__all__ = [
    "Layer",
    "Layers",
]
