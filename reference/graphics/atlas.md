# The Atlas

An `Atlas` is a set of rects over one image.

    glyphs = Atlas(PixelBuffer("font.png"), 8, 12)                 # autogrid
    cuts   = Atlas(buffer, {"blue": Rect(0, 0, 8, 8)})             # authored

    len(atlas)          how many rects
    atlas[key]          one rect's PIXELS, a view into the image
    atlas.rect(key)     where those pixels came from
    atlas.uvs[n]        the same rect as the sampler wants it
    atlas.solidity(key, orientation)    its solid pixels, packed and turned

**Giving a cell size is an autogrid** — a way of *generating* the rects, not a
second kind of atlas. Kris, 2026-09-01:

> *"we have 3 things now that are the same thing — the console glyphs, a
> tileset and now the tiles a sprite uses. each is a large image cut into
> rects that gets funneled through a drawing pipeline and eventually ends up
> as quads on a gpu"* … *"if we consider cells, rows etc on an Atlas as an
> autogrid — then it's still rects."*

So there is one storage and one lookup. A charset holds 256 identical rects;
Chun-Li's sheet holds 1741 in 982 distinct sizes. Same table, same answers.
`cell`, `cols` and `rows` are the autogrid's **parameters**, kept because the
consumer that supplied them usually needs them back — not a mode.

This is not a pillar. All three cut their art with one, which is why it has
its own chapter rather than living inside whichever reaches it hardest.

**There is no "sheet" type**, and a tileset is not something an atlas becomes.
A sheet is the *contract a pillar demands of an atlas*, and each states its
own where it constructs:

    a charset      = an atlas + "256 cells, indexed, uniform"
    a tileset      = an atlas + "a uniform grid" (+ indexed, if collidable)
    a sprite sheet = an atlas + palette banks + assemblies + clips

Three guards in three constructors. At three pillars they still do not repeat
each other, and none of them needed a type to hold it.

**An atlas is agnostic about shape.** It holds rects and does not care what
they are shaped like. Which turns a rect may take is a question about the slot
it has to land in, and belongs to whoever owns that slot — see
[orientation](#orientation-is-not-decided-here).

## Limits and costs

### What refuses

| | |
|---|---|
| a source that is not a `PixelBuffer` | `TypeError` |
| both a cell size and a mapping | `TypeError` — rects given outright need no grid to generate them |
| neither | `TypeError`, naming both roads |
| a non-positive cell | `ValueError` |
| an image that does not divide into the cell | `ValueError`, naming both sizes |
| an empty mapping | `ValueError` — an atlas needs at least one rect |
| an authored rect with non-positive size, a negative origin, or running outside the image | `ValueError`, naming the rect |
| `cell`, `cols`, `rows` or `cells` on an authored atlas | `TypeError` |
| an unknown name | `KeyError`, listing up to eight of what it does hold |
| an index outside the table | `IndexError`, naming the grid |

**`cell` and its siblings refuse on an authored atlas, and the reason is not
that it is the wrong kind of atlas.** The rects are the same rects either way.
It is that `cell`, `cols`, `rows` and `cells` describe a *generator this atlas
did not use*, so there is no honest number to return. `gridded` is the
question a consumer that needs them asks once, at construction, so it can
refuse with its own reason rather than tripping over `cell` somewhere later.

### Costs

| | |
|---|---|
| the rect table | int32, four numbers a rect |
| a 262,144-cut atlas's table | **4MB**, against 21MB as `Rect` objects |
| filling it by autogrid | **14µs**, against 132ms for a Python loop |
| `uvs`, per rect | **0.03µs** from the table, against 1.66µs through `texture.get_region()` |
| priming Chun-Li's 1741 upright patterns | **14.7ms and 3.3MB** |
| one turned variant, on first use | ~8µs |
| `atlas[key]` | a view, free |
| `cells` | a gather and a copy |

The table is int32 rather than a list of `Rect` because the rects are bulk
data: the autogrid fills it with four vectorised writes, and `Rect` is what
comes back **out**, one at a time.

## Two ways to cut one image

An autogrid needs the image to divide exactly, and generates the table
arithmetically. Authored rects are validated one by one against the image, and
each failure names the rect its author gave it.

Both produce the same table, and everything downstream reads it the same way.
That is the whole of what the unification bought: one draw path serves an
autogrid and an authored package alike.

## The table, and the key

Integer keys number an autogrid's cells left to right then down, and take
negative indices. Authored rects answer to their author's strings, in the
order the author gave them — a package is a file and its order is a decision,
so it is preserved rather than sorted.

**One place resolves a key**, so the four things that come out cannot come to
disagree about which rect a key means. They answer different questions about
the same rect and are only useful while they agree.

That resolution is a **cache key, not an address**: a named atlas resolves only
by name, so anything caching per rect asks with the key the caller gave rather
than with the row it landed on.

`len()` is how many rects, and it means one thing whichever way the table was
filled — which is why it is not an autogrid parameter. It is the question to
ask an atlas: a charset needs 256 and does not care whether they arrive as
16x16 or 32x8.

## What comes out of a rect

Four things, and which you want depends on where you compose.

**`atlas[key]` is the pixels**, as an `(h, w)` view into the source image —
editing it edits the image. A console wants this: it gathers glyph pixels into
a screen it paints itself, in NumPy, and never sends a rect to the hardware.

**`atlas.rect(key)` is the place** — `Rect(x, y, w, h)`, origin top-left. A
tilemap and a sprite want this: neither touches pixel data on the render path,
they hand a rectangle to the sampler and let the hardware do the reading.

It is a method returning four integers rather than a type, and deliberately.
A handle carrying its own rect and size is an object per cut, which needs a
pool to afford the allocation, and the whole apparatus serves four numbers.
The rule that settled it was to wait for a second caller before building one;
the sprite pillar was that caller, and one table answered for both.

**`atlas.uvs` is every rect as the sampler wants it** — `(n, 4, 3)` float32,
corners in this engine's order (TL, TR, BR, BL), which is what the batches'
permutation tables turn to orient a quad. Built once for the whole sheet
because it cannot change: a rect table is fixed at construction and a sheet is
one immutable texture, so a rect's twelve floats are constant for the atlas's
life. It is derived from the texture's **own** `tex_coords` rather than from
`0..1`, so a driver that pads to a power of two is followed rather than
assumed. Lazy, because it needs a live GL context and an atlas can be built
without one.

**`atlas.cells` is every rect's pixels stacked**, and it is an autogrid
parameter in disguise: rects given outright have no common shape to stack
into. A copy, unavoidably — rects are strided in the source, so stacking them
in key order is a gather. Fill a table with it once at load; use `atlas[key]`
to look at one.

## Solidity: one rect, turned, packed

    atlas.solidity(42, Orientation.FLIP_H)

`(height, words)` uint64, one bit per pixel along x, ready to be shifted into
place and OR'd into a screen-space mask. Height and width swap under the four
transposing orientations, so a turned rectangular rect packs the pixels it
will actually occupy. This is what `collision.md` sends its reader here for.

**It has to be per rect, because a buffer does not hold rotations.** An
orientation belongs to the placement rather than to the source, so the pixels
alone cannot answer — it takes a rect and a turn together, and the atlas is
the only thing holding both.

**Living here is what lets layers share it.** Parallax and a mirror match are
several maps over one atlas, and they get one pattern set with no registry to
keep and nothing to reclaim when a layer dies. Two atlases cut separately from
one interned buffer keep separate caches, which is why the ordinary way to
build parallax is to hand both maps the same sheet.

Each rect's eight variants fill lazily — a world uses one or two, and a sheet
of 1500 rects has no business packing 12,000 patterns for the handful a level
shows. The **upright** patterns are primed in one pass on the first call.

That priming is not to save one rect's 8µs, which is nothing against a frame.
It is to avoid the **cluster**: a scene bringing two hundred new poses into
view in one frame packs them all in that frame, and 200 × 8µs is 1.7ms of
spike in an engine whose whole presentation pillar exists to keep frames on
time. Paying it once at setup, where a hitch is invisible, is the trade.

Transpose first, then the mirrors — the order the flag bits document, and the
order a sprite's local rect applies. Getting it backwards is invisible on a
square symmetric rect and wrong on everything else.

**There is no staleness to detect, and this is the wrong place to police it.**
A sheet is immutable, so these patterns are computed once and stay correct for
as long as the atlas lives. An atlas holds a *reference* to a buffer it neither
made nor owns, interned by path and shared with every other atlas and layer
over the same art; it cannot promise the pixels will hold still. Watching for
a repainted buffer and rebuilding would make a broken case broken differently
— new stamps correct, already-stamped screen bits stale — and half updated is
worse than either end of it.

Indexed surfaces only: solidity is palette index 0 meaning transparent.

### Not one `packbits` over the whole sheet

This is the shape to reach for otherwise — pack the entire image once and make
each rect's upright pattern a **view** into it — and it fails twice.

A rect's pixel 0 sits at bit `x & 31` of that array and its boundary words
carry its neighbours' pixels, so only a rect both word-aligned and a whole
number of words wide can be a view: **3 of Chun-Li's 1741**. Normalising the
rest back to bit 0 costs **18.6ms against 9.9ms** packing them individually,
because `packbits` is a tuned primitive and a hand-rolled shift-merge is three
NumPy calls per rect.

The memory angle is closed too rather than unexplored. The whole-sheet form is
1.47MB where the bool mask is 11.76MB, and the saving is not worth having: the
mask is read at prime time and when a turned variant is first built, never in
a frame, so its size is a residency question and not a streaming one.

## What an atlas does not answer

**Every question about the image is asked of the buffer**, which is public and
one word away: `size`, `mode`, `embedded_palette`, `edit()`, `copy()`, the
texture — the last of those being the colours the file carried, which is data
rather than a table the buffer owns. An
atlas answers about the **cutting** — the rects, the keys, and what is derived
from a rect. Proxying the image's own properties would be a second surface
saying what one already says.

The distinction consumers make is two-valued: **indexed with a palette, or
direct**. Nothing in the engine branches on RGB against RGBA. A tilemap picks
its shader on it; the console and the sprite pillar refuse direct art outright,
a glyph being a stencil of plane indices and a sprite tileset being read
through a palette bank. That question is `buffer.mode == "P"`, and a refusal
raised on it names the mode it found — so the gate and the message speak one
word.

### Orientation is not decided here

Which of the eight orientations a rect may take is a question about the slot
it must land in, not about the rect. A grid cell can hold a swapped extent
only by squeezing it, which stops the pixels being square, or by overflowing
its neighbours — so a grid refuses a quarter turn unless its cells are square.
A sprite has no slot, so it turns arbitrary rects and places them at their
true extent.

The atlas is agnostic. It holds the rect; whoever owns the slot owns the rule.

## What it is not

**Not a texture, and not an owner.** An atlas is a way of *reading* a buffer,
and holds it live rather than copying it.

**Not a handle factory.** A `Tile` names the map that minted it, not the sheet,
precisely because art is shared: one sheet backs many maps, so a handle naming
the sheet could not answer a question about identity.

**Not where colour lives.** Every pillar holds its own `PaletteBank` — a
console's, a tilemap's and a sprite's alike — and an atlas holds none. A
`PixelBuffer` reports the colours its file carried and owns no table either,
because two tilemaps over one tileset image are two layers that must be able
to light it differently. A glyph sheet settles it from the other end: its
indices mean "plane 0, plane 1" rather than colours, so an embedded table
there is not merely unused, it would be wrong to use.

That is the answer to a question this chapter carried as open until
2026-09-09 — whether palette banks generalise below the sprite pillar. They
do, and the shape they generalise into puts colour on the LAYER rather than
on the art or the cutting of it. N tables for one indexed image is the
hardware model, a day/night tilemap and a console colour scheme are the
second and third callers, and both now hold the same bank type a sprite does.
