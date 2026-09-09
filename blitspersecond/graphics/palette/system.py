"""The fixed facts: how big a palette is, how many a bank holds, and the
colours every bank comes up holding.

Data, not behaviour: the constants are what a GAME reads (`pen[1] = RED`) and
the types are what the engine drives.
"""

import numpy as np

SIZE = 256

#: Palettes in every bank, always. Fixed rather than sized: a bank is one
#: 256x256 GPU table, a row number is a byte like a colour index is, and
#: nothing has to ask how many there are before addressing one.
ROWS = 256

# The system palette: the 16 basic colours every machine since CGA has agreed
# on -- the HTML 4.01 / VGA set, ratified 1999, known to every kid who ever
# typed a hex code. The engine's default colour map, and not just the
# console's: a colour is a palette index, so these name sprite and tile
# colours too.
#
# Index 0 is the transparent hole, so the 16 colours live at 1..16 and BLACK
# is a real index, not the hole.
#
# Spelled out (not looped) so an editor resolves each name and a kid can read
# the whole map at a glance.
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

# The RGB behind each index, in index order (index 0's colour is ignored --
# the hole is pinned transparent whatever its RGB). This is the single source
# the default row is built from; the constants above are its row numbers.
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


def _default_row() -> np.ndarray:
    """What every palette starts as: the system palette, then black.

    There is no `system_palette()` to call -- a bank comes up holding this, so
    a console or a blank canvas writes in real, named colours from the moment
    it exists. Why the tail is black and opaque rather than transparent:
    `bps/reference/palette.md`.
    """
    row = np.zeros((SIZE, 4), dtype=np.uint8)
    # Opaque FIRST, for the whole row: the tail above 16 is black-opaque, not
    # black-transparent. A transparent entry there would be discarded by the
    # shader while still reading as solid to the mask, which is the one
    # disagreement the index 0 pin exists to prevent.
    row[:, 3] = 255
    for index, value in enumerate(_RGB):
        row[index, :3] = (
            (value >> 16) & 0xFF,
            (value >> 8) & 0xFF,
            value & 0xFF,
        )
    row[TRANSPARENT, 3] = 0
    return row


#: Built once at import; every bank is 256 copies of it.
_DEFAULT = _default_row()

def _blank() -> np.ndarray:
    """A fresh bank: every row the system palette, hole at index 0.

    Tiled from one prebuilt row rather than filled per bank -- 256 rows of a
    256x4 block is one `np.tile`, and the row itself is built once at import.
    """
    return np.tile(_DEFAULT, (ROWS, 1, 1))
