"""The console: a grid of glyphs you can draw into, or type into.

    from blitspersecond.graphics.console import Console, Pen

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
`echo` and it is a teletype. **A game that only wants the grid simply never
calls the teletype methods**, and pays nothing for their existence.

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

Split out of a single 1200-line module on 2026-08-28. The test applied was
not size: it was that the buffer access faces each carry a substantial
warning about their own indexing order, and those warnings were the first
three hundred lines of the file -- read before anyone reached the class it is
named after.

**The split found something, and it took three passes to land.** `Cursor`
went into a module of its own, and its constructor type check made it import
`Console` while `Console` was building a `Cursor` -- a genuine import cycle,
and a new one. Reference's cursor addressed a `GlyphLayer` with `Console` a
separate controller above it, so that graph was one-way; fusing the two
classes put the cursor's owner and the cursor's subject in the same object.

Routing around the cycle was the wrong instinct. What it was pointing at was
how little of `Cursor` was load-bearing, and four passes took it apart:

- *"you'd never want to create a Cursor by itself; it's inherent to a
  Console -- and really just a way to make the interface more comfortable."*
  So the constructor check was guarding a road nobody walks. Deleted, and
  the cycle with it, along with `Console.cursor` accepting a replacement --
  there is no second cursor to swap in.
- *"it was only ever about making Console().cursor.x feel usable and
  readable."* So it is grouping, not modelling -- the same job `CellAccess`
  does for `console.cell[x, y]`. It moved in beside the other faces and out
  of the package's exports, which now carry exactly what you BUILD.
- *"char was really just a way to peek and poke"*, and *"that doesn't even
  belong to the cursor at all."* An address should not dereference itself.
  `char` is `Console.char` now, beside `write` and `print` -- the console's
  operations at wherever the cursor happens to be.
- What was left was `x`, `y` and a bounds check, which is `common.Location`
  plus a rule that was always the console's. So it was collapsed onto that
  type and the class deleted outright.

**And that last step was reverted within the hour, which is the useful part
of the record.** Kris: *"I think people want to do x = 10."* A `NamedTuple`
is immutable, so `console.cursor.x = 10` raises and single-axis moves become
`console.cursor = (10, console.cursor.y)`. The class exists to make that
assignment a sentence; a read-only `.x` is `[0]` with a nicer name and earns
nothing a bare tuple would not. **Mutability turned out to be the only
load-bearing thing in it**, which was not obvious until everything else had
been taken away.

So `Cursor` is an object again -- but the four passes still paid. It carries
`x`, `y` and an unpack, and nothing else: no constructor check, no `console`
property, no `char`, not exported, and bounds on the console where `cols` and
`rows` live. Whether `Location` should have been able to take the collapse is
open, and on the board.

Worth keeping in view when the tile pillar lands: fusing a controller into
its layer collapses a DAG, and the next type with a back-reference to its
surface will meet the same edge.

## Why this reverses a recorded decision

This tree previously had THREE things -- `Console`, a `GlyphLayer`, and a
`Controller` base for "an object that holds a layer without being one". The
old `display/controller.py` records the split as settled, and records that
fusing them was tried on 2026-07-21 and reverted the following day.

**That demotion no longer stands, and the reasoning it rested on was the
wrong shape.** It held that teletype semantics must stay optional, because a
game wanting text and a game wanting the raw grid are two audiences with one
surface each. The first half is right and is preserved here. The second half
does not follow: optional means *not forced to call it*, which one class
already gives you. Nothing about having a `write()` method obliges anyone to
write. The split was defending a requirement that fusing never threatened.

## What the contortion was actually for

`Controller` existed to make ONE LINE read nicely:

    console = bps.layers.add(Console(CharSet.ASCII8X12))

Everything else followed from wanting that, and each step exists only to prop
up the step before it. To construct-and-add inline, `add` has to return what
you passed -- so it was generic in its argument rather than returning the
layer. To pass a `Console` to something called `add(Layer)`, `add` has to
unwrap -- so `Console` needs a public `.layer`. To have a `.layer` without
being one, `Console` needs a base class saying what that means -- so
`Controller`. And a thing holding a layer cannot be reordered, so `Controller`
has to proxy `before`, `after`, `first`, `last` and `swap_with` back down to
it. A hundred and fourteen lines of type, plus a generic signature on `add`,
to save a line break.

It was not free, either. `Console` proxied `cols`, `rows`, `palette`, `pen`
and `clear` through to its layer, so five members existed twice and could
disagree, and it shared seven of its fifteen public members with
`GlyphLayer`. That overlap is what made the trap: `layers.add(Console(...))`
under a `-> Layer` signature silently handed back a GlyphLayer that answered
to `.pen` and `.cols` while `.write` and `.print` raised, and a test
asserting only `.attached` kept passing while testing nothing it claimed.
It needed two types to be possible at all, which is why the record lives
here rather than with `Layers`: nothing about a draw order caused it, and
with one type there is nothing left there to warn about.

The line is two lines now, and nothing else is:

    console = Console(CharSet.ASCII8X12)
    bps.layers.add(console)

`add` returns nothing, takes a `Layer`, and refuses anything else. There is
no `.layer` to unwrap, no proxying, and no way to be holding the wrong
object, because there is only one object.

`GlyphEngine` is gone with them. It was a twelve-line alias for `GlyphLayer`,
left over from when engines attached themselves through a global lookup.

## How it reaches the screen

It composes its whole screen into ONE `PixelBuffer` of palette indices and
draws that as a single quad: `picture.vert` + `indexed.frag`, the pixels on
texture unit 0 and the palette's 256x1 row on unit 1. See `shader.py`.

**It does not go through the tile engine, and reference did.** There it built
an unattached `TileLayer` over its screen buffer and blitted one whole-image
tile into it -- which meant taking `default.vert`, carrying per-vertex `scale`
and `rotation` attributes and building three matrices, for a quad stamped once
at (0, 0) that never scales or rotates. `TileAtlas`, `TileCache` and
`TileBatch` came along too, all of them machinery for placing MANY tiles from
one atlas, to place exactly one. The picture vertex stage already exists for
"a whole buffer drawn wherever it sits", which is the console's requirement
stated exactly; sprites use the same pair.

## Status

**Landed:** `Console` itself -- reference's `glyph_layer.py`, `console.py`
and `cursor.py` merged into the one class in `console.py` -- plus `Pen`,
`shader.py`, and everything they lean on: `PixelBuffer`, `Palette`,
`PaletteTexture`, `Orientation`, `Layer`.

**Outstanding:** `CollisionQuery`/`CWORD`/`Collidable`, which the mask and
`collides_with` want; they backport with the tile engine, since that is what
supplies something worth colliding with.

`echo()` landed 2026-08-31 on the input stream. The line it draws is a
`TextInput` reducer owning its own buffer -- a console line may be open for
minutes, and the engine's history is half a second sized for reconstructing a
quarter-circle. The console keeps its own text and the engine keeps none on
its behalf. See `docs/bps/INPUT_STREAM.md`.

**Not needed, despite reference wanting them:** the tile engine (above), and
`Config` -- both call sites only read the screen size to check the cell tiles
it exactly, and 640x360 is fixed rather than configured.
"""

from .charset import CharSet
from .console import Console
from .pen import Pen, SYSTEM_SLOTS

#: What you BUILD. The four access faces -- `CellAccess`, `GlyphAccess`,
#: `PaintAccess`, `Cursor` -- are reached through a console and never
#: constructed, so they stay in `internal.py` and out of here.
__all__ = [
    "CharSet",
    "Console",
    "Pen",
    "SYSTEM_SLOTS",
]
