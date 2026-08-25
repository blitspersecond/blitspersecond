"""Shared engine machinery that belongs to no one subsystem.

    from blitspersecond.core import Shader

The test for living here is "do two subsystems that must not depend on each
other both need it?" Shader is the founding case: graphics.tile and
graphics.sprite name their programs through it, and so does display's
framebuffer post-pass. While it lived in graphics.internal, `display` had to
import `graphics` to reach it -- which pulled the ENTIRE graphics package into
display's own initialisation for the sake of one class, and made the two
packages mutually dependent (graphics needs display.layer for Layer). That the
arrangement worked at all rested on import ORDER: `display.display` imports
`.internal` before `.layers`, so graphics happened to finish loading first.
Order-dependence at the root of a package is a tripwire, not a design.

This package is a strict leaf in the subsystem graph -- it depends only on
`blitspersecond.resources` (and through it `blitspersecond.common`), never on
graphics, display, audio or input, and nothing here may ever import upward.
That is the whole discipline; it is what makes `core` safe to import from
anywhere, in any order.

Not to be confused with `graphics.internal` (Point/Vector/PaletteTexture,
which only the renderers meet) or `common` (Size/Location/Rect -- pure value
types with no engine dependencies at all). Machinery lands here only once a
second subsystem genuinely needs it; one consumer means it still belongs to
that consumer.
"""

from .shader import Shader

__all__ = [
    "Shader",
]
