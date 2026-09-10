"""The fonts BPS ships, and the one thing a console demands of one.

`CharSet` is public -- it is what you hand `Console(...)`. The rest is the
console's constraint on a font, which is a COUNT and not a shape: a cell packs
its glyph id into a byte, so a console addresses 256 glyphs, so a charset
atlas must hold at least that many cells. A charset therefore DECLARES its own
cell size, because an image of a grid does not carry its grid.

Why that constraint is a count rather than the 16x16 shape this module used to
assert: `bps/reference/graphics/console.md`, "Not a 16x16 charset".
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from blitspersecond.common import PACKAGE_ROOT, Size

from ..atlas import Atlas
from ..pixel_buffer import PixelBuffer

#: How many glyphs a console addresses, and the only demand it makes of a
#: charset atlas. 256 because a cell packs `facing << 8 | glyph` into one
#: number, so the glyph id is a byte -- see `console._FACING_SHIFT`. A font
#: may ship MORE cells than this; the surplus is simply unreachable.
CHAR_COUNT = 256
GLYPH_MASK = CHAR_COUNT - 1

_CHARSET_DIR = PACKAGE_ROOT / "assets" / "charset"


class CharSet(Enum):
    """The character sets supplied with BPS.

    Each declares its own cell size, because the atlas cannot: an image is a
    field of pixels and the grid over it is the author's decision. The name
    says the same thing for a human, and the two are checked against each
    other by nothing -- if they ever disagree, the declaration wins and the
    font comes out scrambled, which is loud enough."""

    #: This charset's glyph size in pixels. Set by `__new__` from the member
    #: declaration; annotated here so it is a known attribute rather than one
    #: conjured at construction.
    cell: Size

    ASCII8X8 = ("ascii8x8.png", 8, 8)
    ASCII8X12 = ("ascii8x12.png", 8, 12)

    def __new__(cls, filename: str, width: int, height: int) -> "CharSet":
        # The VALUE is the filename, so `repr` reads as one and the cell size
        # lands as an attribute beside it rather than inside a tuple nobody
        # wanted to see.
        charset = object.__new__(cls)
        charset._value_ = filename
        charset.cell = Size(width, height)
        return charset

    @property
    def atlas(self) -> Atlas:
        """This charset's glyphs, cut into cells.

        Built on every access rather than held, which is the difference
        between a menu and a pantry: an enum member is evaluated when the
        module is imported, so a member that WAS an atlas would load every
        shipped font whether or not a game named one. `PixelBuffer` interns
        by path, so asking twice while anything still holds the image is
        free; the cost when nothing does is one PIL load, measured at 0.3ms.

        Unvalidated beyond dividing evenly -- `Console` checks that a sheet
        is indexed and holds enough glyphs, because those are the CONSOLE's
        demands and apply just as much to a font you supply yourself."""
        return Atlas(
            PixelBuffer(str(_CHARSET_DIR / self.value)),
            self.cell.width,
            self.cell.height,
        )

    @classmethod
    def for_cell(cls, width: int, height: int) -> Optional["CharSet"]:
        """The system charset whose glyphs are `width` x `height` pixels, or
        None if no shipped set is that size.

        Lets a caller who knows the cell size it wants -- because it worked
        back from a screen grid -- land on real letters rather than the
        blank-glyph road."""
        for charset in cls:
            if charset.cell == (width, height):
                return charset
        return None
