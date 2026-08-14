"""Colour: the palette table and the names that index it.

    from blitspersecond.colors import Palette, RED, system_palette

`Palette` is the 256-entry RGBA table indexed images carry and the engines
upload -- stored as authored, index 0 always transparent (`ConsolePalette` is the
one sanctioned exception, honouring per-entry alpha for the console's stunts).
`system_palette()` builds the stock 16-colour map, and the constants (`RED`,
`WHITE`, `CYAN`, ...) are its indices -- write `console.foreground = RED` and
mean it. This is the bottom of the dependency stack: a leaf everything paints
with.
"""

from .palette import ConsolePalette, Palette
from .pen import Pen
from .magic_pen import MagicPen
from .colors import (
    system_palette,
    TRANSPARENT,
    WHITE,
    SILVER,
    GRAY,
    BLACK,
    RED,
    MAROON,
    YELLOW,
    OLIVE,
    LIME,
    GREEN,
    CYAN,
    TEAL,
    BLUE,
    NAVY,
    MAGENTA,
    PURPLE,
)

__all__ = [
    "Palette",
    "ConsolePalette",
    "Pen",
    "MagicPen",
    "system_palette",
    "TRANSPARENT",
    "WHITE",
    "SILVER",
    "GRAY",
    "BLACK",
    "RED",
    "MAROON",
    "YELLOW",
    "OLIVE",
    "LIME",
    "GREEN",
    "CYAN",
    "TEAL",
    "BLUE",
    "NAVY",
    "MAGENTA",
    "PURPLE",
]
