# The system palette: the 16 basic colours every machine since CGA has agreed
# on -- the HTML 4.01 / VGA set, ratified 1999, known to every kid who ever
# typed a hex code. The engine's default colour map, and not just the console's:
# a colour is a palette index, so these name sprite and tile colours too.
#
# Index 0 is the transparent hole (the engine-wide empty index; a Palette pins
# it transparent whatever its RGB), so the 16 colours live at 1..16 and BLACK is
# a real index, not the hole. The names below ARE those indices -- write
# `console.foreground = RED` and mean it.

from __future__ import annotations

from .palette import Palette

# Palette index constants. Spelled out (not looped) so an editor resolves each
# name and a kid can read the whole map at a glance.
TRANSPARENT = 0
WHITE = 1
SILVER = 2
GRAY = 3
BLACK = 4
RED = 5
MAROON = 6
YELLOW = 7
OLIVE = 8
LIME = 9
GREEN = 10
CYAN = 11
TEAL = 12
BLUE = 13
NAVY = 14
MAGENTA = 15
PURPLE = 16

# The RGB behind each index, in index order (index 0's colour is ignored -- the
# Palette pins it transparent). This is the single source the palette is built
# from; the constants above are its row numbers.
_RGB = (
    0x000000,  # 0  transparent hole
    0xFFFFFF,  # 1  white
    0xC0C0C0,  # 2  silver
    0x808080,  # 3  gray
    0x000000,  # 4  black
    0xFF0000,  # 5  red
    0x800000,  # 6  maroon
    0xFFFF00,  # 7  yellow
    0x808000,  # 8  olive
    0x00FF00,  # 9  lime
    0x008000,  # 10 green
    0x00FFFF,  # 11 cyan
    0x008080,  # 12 teal
    0x0000FF,  # 13 blue
    0x000080,  # 14 navy
    0xFF00FF,  # 15 magenta
    0x800080,  # 16 purple
)


def system_palette() -> Palette:
    """The stock 16-colour palette (index 0 transparent, 1..16 the basic
    colours). The `palette=None` default for GlyphEngine and Console builds
    this, so a bare console writes in real, named colours."""
    raw: list[int] = []
    for rgb in _RGB:
        raw += [(rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF]
    return Palette(raw)


__all__ = [
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
