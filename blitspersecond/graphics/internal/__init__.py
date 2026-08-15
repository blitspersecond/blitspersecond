"""Engine internals: what the renderers rely on, not what users interact
with. The test for living here is "does user code ever import it or meet it
in a hint?" -- if no, it's internal.

Point and Vector are the mutable integer (x, y) pairs TileCache re-stamps in
place. They are deliberately NOT the public vocabulary: user-facing signatures
use common.Size / common.Location (immutable NamedTuples that unpack and hash
like the tuples they are).

Shader is the compiled-program cache the engines and the framebuffer
post-pass name their programs through (Shader.get(vert, frag)) -- GL
plumbing, never a user concern.

PaletteTexture is palette RAM's GPU projection: Palette identity -> a 256x1
texture the INDEXED shader samples on unit 1, upload gated by the palette's
mutation counter. Engines fetch it per draw; users only ever touch Palette.

(TileCache is NOT here: the whole tile system -- Tile, TileAtlas, TileCache,
TileEngine -- lives together in graphics.tile, which is what keeps this
package a leaf: nothing in internal imports upward.)
"""

from .palette_texture import PaletteTexture
from .point import Point
from .shader import Shader
from .vector import Vector

__all__ = [
    "PaletteTexture",
    "Point",
    "Shader",
    "Vector",
]
