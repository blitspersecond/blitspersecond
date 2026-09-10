"""Colour: the palette bank, and the colours in it.

    from blitspersecond.graphics import PaletteBank, ColorQuad, RED

**A colour in BPS is an index, not a triple.** Art stores index 5; what index
5 looks like is a palette's business, which is why recolouring is a palette
job and never a pixel one.

`PaletteBank` is 256 palettes of 256 colours as one contiguous block --
`bank[row]` is one palette, `bank[row][index]` one colour, and `active` is the
register that says which row is drawn. Sharing follows the `PixelBuffer` rule:
a bank handed to several holders is one table, and `copy()` is how a holder
stops sharing.

Index 0 is the engine-wide hole and its alpha is pinned to 0 on every write
road. Every other index honours exactly what you wrote.

**`bps/reference/graphics/palette.md` is the chapter**: the register, the modify path, the
upload, the shader's alpha floor, every limit and every refusal by name.
"""

from .bank import PaletteBank
from .palette import Palette
from .system import (
    ROWS,
    SIZE,
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
    "PaletteBank",
    "Palette",
    "ROWS",
    "SIZE",
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
