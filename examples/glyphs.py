"""Custom glyphs: a blank layer, direct cell writes, and text over the top.

Run from the repository root:

    venv/bin/python examples/glyphs.py

The advanced road. `Console((8, 8))` gives 256 EMPTY glyphs at that cell size
-- no font, nothing to say -- and you define what they draw. That is how the
clouds demo turns a text grid into a chunky bitmap, and it is what this shows,
in three parts:

    1. define      panel.glyph[n] = pattern
    2. stamp       panel.cell[x, y] = n, and whole runs at once
    3. write       a second Console, with a font, over the top

A Console with no charset is a bare glyph grid; one with a charset can also
type. Same class either way -- the teletype half simply goes unused here.

Three things vary per cell: the GLYPH, its FACING, and its COLOUR. Only the
glyph is written directly -- facing and colour ride in from whichever pen is
held at the time. That is fine when a run shares a pen and awkward when it
does not, and both cases are below and labelled.

Close the window to quit.
"""

import numpy as np

from blitspersecond import BlitsPerSecond
from blitspersecond.graphics import (
    CYAN,
    GRAY,
    WHITE,
    YELLOW,
    Orientation,
)
from blitspersecond.graphics.console import CharSet, Console, Pen

# Glyph ids we are about to invent. Byte values, nothing more -- on a blank
# layer there is no character set to disagree with.
BLANK, CORNER, ACROSS, DOWN = 0, 1, 2, 3
BAR = 8  # BAR + n is a column filled n eighths from the bottom

PANEL_X, PANEL_Y, PANEL_W, PANEL_H = 8, 6, 34, 18
# How tall each bar stands, as a fraction of the panel interior.
BARS = (0.35, 0.85, 0.55, 1.0, 0.7, 0.2, 0.9, 0.45, 0.95, 0.6, 0.3, 0.75)
BAR_COLORS = (CYAN, YELLOW, WHITE, GRAY)


def define_glyphs(layer: Console) -> None:
    """Invent the four shapes the panel is drawn from, plus eight bar fills.

    A pattern is a NumPy array of PLANE indices (0..3), one per pixel, saying
    which of the pen's four slots that pixel draws -- not a colour. Shape and
    dtype come from an existing pattern, which is the only way to get them
    right: `glyph.size` is (width, height) while the array is (height, width).
    """
    width, height = layer.glyph.size

    across = np.zeros_like(layer.glyph[0])
    across[height // 2, :] = 1
    layer.glyph[ACROSS] = across

    down = np.zeros_like(layer.glyph[0])
    down[:, width // 2] = 1
    layer.glyph[DOWN] = down

    # ONE corner, drawn as top-left. The other three are this glyph facing a
    # different way -- which is what makes facing a per-cell attribute worth
    # having, rather than four separate glyphs.
    corner = np.zeros_like(layer.glyph[0])
    corner[height // 2, width // 2 :] = 1
    corner[height // 2 :, width // 2] = 1
    layer.glyph[CORNER] = corner

    for eighth in range(1, 9):
        bar = np.zeros_like(layer.glyph[0])
        bar[height - (height * eighth) // 8 :, :] = 1
        layer.glyph[BAR + eighth] = bar


def draw_panel(layer: Console, pens) -> None:
    """A framed box, drawn from one corner glyph facing four ways.

    The edges are RUNS -- one write each, whatever their length -- because a
    whole edge shares a pen. The corners are four separate writes, because
    each needs a different facing and a pen carries exactly one.
    """
    x0, y0 = PANEL_X, PANEL_Y
    x1, y1 = x0 + PANEL_W - 1, y0 + PANEL_H - 1

    layer.pen = pens[Orientation.NONE]
    layer.cell[x0 + 1 : x1, y0] = ACROSS
    layer.cell[x0 + 1 : x1, y1] = ACROSS
    layer.cell[x0, y0 + 1 : y1] = DOWN
    layer.cell[x1, y0 + 1 : y1] = DOWN

    # One glyph, four facings, four pens. Held as four prepared pens rather
    # than rebuilt per corner -- a pen is reusable on any layer whose cells
    # match, so these are made once at startup.
    for (x, y), facing in (
        ((x0, y0), Orientation.NONE),
        ((x1, y0), Orientation.FLIP_H),
        ((x0, y1), Orientation.FLIP_V),
        ((x1, y1), Orientation.ROT_180),
    ):
        layer.pen = pens[facing]
        layer.cell[x, y] = CORNER


def draw_bars(layer: Console) -> None:
    """A bar per column, each in its own colour.

    THE AWKWARD ONE. A column is a run and could be a single write, but each
    bar wants a different colour, and colour rides in from the pen -- so the
    pen is swapped per bar and the run written per bar. That is fine for
    twelve bars. `clouds.py` is the same shape at 3600 cells with 19 colours
    a frame, and it is why that demo reaches past this API entirely.
    """
    base = PANEL_Y + PANEL_H - 2  # bottom row inside the frame
    interior = PANEL_H - 3  # rows a bar may occupy
    # Its OWN pen, not one borrowed from the panel's set: this one is mutated
    # per bar, and a pen is a live object -- recolouring a shared one would
    # quietly restyle whatever else was holding it.
    pen = Pen(0, WHITE, size=layer.glyph.size)
    for index, fraction in enumerate(BARS):
        x = PANEL_X + 2 + index * 2
        eighths = max(1, round(fraction * interior * 8))
        whole, remainder = divmod(eighths, 8)

        pen[1] = BAR_COLORS[index % len(BAR_COLORS)]
        layer.pen = pen

        # The solid part is a RUN: one write however tall the bar is.
        if whole:
            layer.cell[x, base - whole + 1 : base + 1] = BAR + 8
        # ...and the tip is a single partial cell on top of it.
        if remainder:
            layer.cell[x, base - whole] = BAR + remainder


def main() -> None:
    bps = BlitsPerSecond()

    # A BLANK console: 256 empty glyphs at 8x8, no character set. Everything
    # it can ever draw is something we define below. Building it and placing
    # it are two steps -- `add` puts a layer in the draw order and returns
    # nothing, so the thing you keep is the thing you built.
    layer = Console((8, 8))
    layer.tag = "panel"
    bps.layers.add(layer)
    # layer.after(other_layer)
    define_glyphs(layer)

    # Four pens, one per facing, made once. Sized to the layer so they are
    # ready to use; a pen is frozen to that size and reusable ever after.
    pens = {
        facing: Pen(0, WHITE, size=layer.glyph.size, facing=facing)
        for facing in (
            Orientation.NONE,
            Orientation.FLIP_H,
            Orientation.FLIP_V,
            Orientation.ROT_180,
        )
    }

    draw_panel(layer, pens)
    draw_bars(layer)

    # Text goes on its own layer, because this one has no font -- a console
    # needs a character set and ours is 256 shapes we invented. Two layers,
    # one grid each, which is exactly how karaoke composes clouds and lyrics.
    console = Console(CharSet.ASCII8X12)
    bps.layers.add(console)
    console.pen = Pen(0, WHITE)
    console.write("CUSTOM GLYPHS", at=(8, 2))
    console.pen = Pen(0, GRAY)
    console.write("one corner glyph, four facings", at=(8, 3))
    console.write("bars: a pen swap per colour", at=(8, 4))

    bps.start()


if __name__ == "__main__":
    main()
