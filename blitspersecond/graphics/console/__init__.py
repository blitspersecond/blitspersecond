"""The console: a grid of glyphs you can draw into, or type into.

    from blitspersecond.graphics.console import CharSet, Console, Pen

**`Console` IS a `Layer`.** One class. Build one, add it to the draw order,
reorder it, remove it -- exactly like any other layer, with no wrapper in
between:

    console = Console(CharSet.ASCII8X12)
    bps.layers.add(console)
    console.after(background)
    console.write("ready")

It answers to two audiences and they are the same object. Reach for
`console.cell[...]`, `console.glyph[...]` and `console.paint_buffer` and it
is a byte-addressed glyph grid. Reach for `write`, `print`, `cursor` and
`echo` and it is a teletype. A game that only wants the grid simply never
calls the teletype methods.

## The modules

    console.py    Console: the class, and everything only it touches --
                  construction, the variant table, writing, scrolling,
                  deferral, the recompose and the draw
    charset.py    CharSet -- the fonts BPS ships, each declaring its own
                  cell size -- and the one demand a console makes of one:
                  at least 256 glyphs, because a glyph id is a byte
    pen.py        Pen: the instrument, four slots and a facing
    internal.py   the four access faces behind `cell`, `glyph`,
                  `paint_buffer` and `cursor` -- shapes you reach through a
                  console and never build
    shader.py     the one program this layer draws with

## The design record is not here

`bps/reference/graphics/console.md` is the authority: how the packed cell and the
variant table work, the pen's decisions and the two reversals that shaped
them, the recompose band, and the generality that was removed and why. This
package documents itself only as far as someone editing these files needs.

Neighbouring pages: `bps/reference/graphics/layers.md` for the draw order and where
blend belongs, `bps/reference/graphics/collision.md` for the masks, and
`bps/reference/input/text.md` for what feeds `echo`.
"""

from .charset import CharSet
from .console import Console
from .pen import PLANE_0, PLANE_1, PLANE_2, PLANE_3, Pen, SYSTEM_SLOTS

#: What you BUILD. The four access faces -- `CellAccess`, `GlyphAccess`,
#: `PaintAccess`, `Cursor` -- are reached through a console and never
#: constructed, so they stay in `internal.py` and out of here.
__all__ = [
    "CharSet",
    "Console",
    "PLANE_0",
    "PLANE_1",
    "PLANE_2",
    "PLANE_3",
    "Pen",
    "SYSTEM_SLOTS",
]
