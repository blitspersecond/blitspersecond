"""Pens: the instrument you write with, and where a gradient comes from.

Run from the repository root:

    venv/bin/python examples/pen.py

A pen carries four palette indices -- one per glyph plane -- and a facing. You
do not set a mode to change either; you pick up a different pen. This shows
the three things a pen can be:

    flat        four palette indices, the everyday case
    facing      the same colours, mirrored -- a pen you hold the other way
    gradient    one slot holding a plane instead of an index

The gradient is the one that needs explaining, because BPS deliberately does
NOT build ramps for you. A pen paints palette INDICES, so a colour gradient is
a run of consecutive palette entries holding the interpolated colours -- and
budgeting palette RAM is an art-direction decision, not something the engine
should decide on your behalf. `load_ramp` below is the whole of what that
takes: interpolate the stops, burn them into a bank you chose, hand back the
indices. Copy it into your own game; it is nine lines.

Close the window to quit.
"""

import numpy as np

from blitspersecond import BlitsPerSecond
from blitspersecond.colors import WHITE, system_palette
from blitspersecond.console import Console
from blitspersecond.graphics.common import Orientation, Pen, PixelBuffer
from blitspersecond.graphics.glyph import CharSet
from blitspersecond.resources import ImageSpec

SCREEN_W, SCREEN_H = 640, 360

# Where our ramp lives in palette RAM. Picked by hand, the ordinary way you
# would budget an Amiga's colours -- the system palette uses the low indices,
# so anything above them is ours.
RAMP_BANK = 200

# Cyclic stops: the last is the first, so a scrolling ramp has no seam.
COPPER = [
    (0x20, 0x10, 0x00),
    (0xC0, 0x80, 0x20),
    (0xFF, 0xF0, 0xC0),
    (0xC0, 0x80, 0x20),
    (0x20, 0x10, 0x00),
]


def load_ramp(palette, base, stops, steps):
    """Burn interpolated `stops` into `palette[base:base + steps]` and hand
    back those indices, ready to drop into a pen slot.

    This is the part the engine leaves to you -- see the module docstring.
    """
    stops = np.asarray(stops, dtype=float)
    where = np.linspace(0, len(stops) - 1, steps)
    low = np.floor(where).astype(int)
    high = np.minimum(low + 1, len(stops) - 1)
    blend = (where - low)[:, None]
    for offset, (red, green, blue) in enumerate(
        (stops[low] * (1.0 - blend) + stops[high] * blend).astype(np.uint8)
    ):
        palette[base + offset] = (int(red), int(green), int(blue), 255)
    return np.arange(base, base + steps, dtype=np.uint8)


def backdrop() -> np.ndarray:
    """A plain vertical wash, so that transparency has something to reveal."""
    amount = (np.arange(SCREEN_H, dtype=np.float32) / (SCREEN_H - 1))[:, None, None]
    top = np.array((24, 28, 64), dtype=np.float32)
    bottom = np.array((118, 62, 88), dtype=np.float32)
    wash = top * (1.0 - amount) + bottom * amount
    return np.broadcast_to(wash, (SCREEN_H, SCREEN_W, 3)).astype(np.uint8)


def main() -> None:
    bps = BlitsPerSecond()

    # Something behind the console. Slot 0 of every pen below is left on the
    # transparent index, so this is what shows through -- including through
    # the half a mask removes, which is the only way a mask demonstrates
    # anything at all.
    sky = bps.layers.tile(
        PixelBuffer(ImageSpec(size=(SCREEN_W, SCREEN_H), mode="RGB", data=backdrop())),
        w=SCREEN_W,
        h=SCREEN_H,
    )
    sky.blit(0, 0, 0)

    console = bps.layers.add(Console(CharSet.ASCII8X12))
    layer = console.layer

    palette = system_palette()
    # One ramp entry per scanline of a cell: that is what makes a length-N
    # sequence read as a gradient running DOWN the cell. A pen is exactly one
    # cell, so its `size` is where the height comes from.
    cell_width, cell_height = layer.pen.size
    ramp = load_ramp(palette, RAMP_BANK, COPPER, cell_height)
    console.palette = palette

    # 1. A flat pen -- four indices. The everyday case.
    plain = Pen(0, WHITE)      # slot 0 transparent: the backdrop shows
    console.pen = plain
    console.write("A pen carries colour and facing.", at=(2, 2))

    # 2. The same pen held the other way round. Facing lives ON the pen, so
    #    swapping pens is how you mirror -- there is no layer-wide mode to
    #    set and then remember to unset.
    backwards = Pen(0, WHITE, facing=Orientation.FLIP_H)
    console.pen = backwards
    console.write("R", at=(2, 4))
    console.pen = plain
    console.write("ussian: swap pens, do not set a mode.", at=(3, 4))

    # 3. A gradient. The ramp is a run of palette indices, so it goes into a
    #    slot exactly like a single index would -- `pen[1]` is slot 1 whether
    #    it holds one colour or one per scanline.
    chrome = Pen(0, WHITE, size=layer.glyph.size)
    chrome[1] = ramp
    console.pen = chrome
    console.write("CHROME -- one colour per scanline", at=(2, 6))

    # 4. A mask is the same mechanism: a plane of indices, with 0 (the
    #    transparent entry) wherever you want nothing drawn. The bottom half
    #    of every letter is cut away and the backdrop shows through it --
    #    which is why this demo needs a backdrop to be a demo at all.
    top_half = np.zeros((cell_height, cell_width), dtype=np.uint8)
    top_half[: cell_height // 2, :] = WHITE
    half = Pen(0, 0, size=layer.glyph.size)
    half[1] = top_half
    console.pen = half
    console.write("HALF -- the mask cuts the bottom away", at=(2, 8))

    console.pen = plain
    console.write("Close the window to quit.", at=(2, 11))

    bps.run()


if __name__ == "__main__":
    main()
