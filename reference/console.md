# The Console

The console is a 256-glyph, byte-addressed character engine that is also a
layer. This page is how it works, why it is shaped this way, and what it
deliberately does not do. The [guide's console page](../guide/console.md)
teaches the surface; this one assumes you have read it.

It is one class answering to two audiences. Reach for `cell`, `glyph` and
`paint_buffer` and it is a grid of bytes you index. Reach for `write`,
`print`, `cursor` and `echo` and it is a teletype. A game that only wants the
grid never calls the teletype methods and pays nothing for their existence —
which is the whole argument for there being one class, and is worked through
in [What it is not](#what-it-is-not).

Two systems this page leans on have their own pages: [layers](layers.md) for
the draw order and the `Layer` contract, and [collision](collision.md) for the
mask representation and the query surface.

---

## Limits and costs

### Geometry

| | |
|---|---|
| screen | 640x360, fixed — not configurable |
| shipped grids | **80x30** on 8x12 cells, **80x45** on 8x8 cells |
| any other grid | legal if the cell divides 640x360 exactly, and you supply a font of that cell size |
| glyphs addressable | 256, because a glyph id is a byte. A sheet may hold more; the surplus has no id to reach it |
| shipped glyphs | 94 inked, ids 33..126 — printable ASCII; id 32 is a blank space |
| free ids | 162 — `0..31` and `127..255` are blank on both shipped fonts |
| character mapping | `ord` / `chr`, with no layer in between |
| collidable | off by default, so a console that never collides costs nothing |

**Supplying your own charset.** The console's demand on a font is a **count,
not a shape**: at least 256 cells, cut to one cell size. A 16x16 sheet is not
required — 32x8 holds the same 256 glyphs. The font must declare its cell
size, because an image of a grid does not carry its grid: 128x192 is 256
glyphs of 8x12, or 384 of 8x8, or one enormous one, and only the author knows
which.

A charset's atlas is built on every access rather than held — the difference
between a menu and a pantry. An enum member is evaluated at import, so a
member that *was* an atlas would load every shipped font whether or not a game
named one. `PixelBuffer` interns by path, so asking twice while anything still
holds the image is free.

### Memory

| | |
|---|---|
| variant table | 192KB at 8x12 (half of it unused), 128KB at 8x8 |
| paint buffer | 900KB — four palette indices for every screen pixel |
| composed image | 225KB — one index per screen pixel |
| cell grid | 4KB at 80x30 — one `uint16` per cell |
| collision planes | 28KB each, bit-packed; 112KB for all four. Held only while asked for |

### Costs

| operation | cost |
|---|---|
| whole-screen recompose, 80x30 | 1172µs |
| two full-screen writes without `defer()` | ~1.26ms/frame wasted, of 3.74ms |
| whole-plane paint write, native axes | 21µs |
| whole-plane paint write, transposed | 1.2ms |
| collision silhouette derive | 20µs |
| collision named plane derive | 217µs |
| collision cached plane read | 0.2µs |
| charset load, when nothing already holds the image | 0.3ms |

A partial write recomposes only the band of cell rows it touched, so the
1172µs figure is the ceiling rather than the price of a write.

### What refuses

Everything below raises at the point of the mistake rather than later.

| | |
|---|---|
| a grid, or a cell size, that does not tile 640x360 | `ValueError` |
| a glyph sheet that is not an indexed image | `ValueError` — a glyph is a stencil of plane indices, not a picture |
| a glyph sheet holding fewer than 256 cells | `ValueError` |
| a glyph sheet built from authored rects rather than a cell size | `ValueError` — a console's cells are one size by definition |
| a quarter-turn pen on a rectangular cell | `ValueError` — a rectangular cell faces `NONE`, `FLIP_H`, `FLIP_V` or `ROT_180`; a square one takes all eight |
| a pen already bound to a different cell size | `ValueError` — a pen's size freezes on first use |
| a pen slot outside 0..255, or a fifth slot | `ValueError` |
| a cursor moved outside the grid | `ValueError` |
| a strided cell slice | `ValueError` — runs are contiguous |
| a glyph id outside 0..255 | `ValueError` |
| a character above U+00FF — `console.write("€")` | `ValueError` — `ord` is the bridge and 8364 is not a byte. U+0080..U+00FF do *not* raise: they land on ids 128..255, which are not ASCII and are blank on the shipped fonts |
| a write through a captured `paint_buffer` view | the view is read-only |

---

## A cell is one number

A console cell is a single 16-bit value:

    facing << 8 | glyph

One plane, one array, two attributes packed into it. The shift is 8 because a
console addresses 256 glyphs — a glyph id is a byte, which is what makes this
a *byte-addressed* character engine and what `CHAR_COUNT` in `charset.py`
exists to state.

**The shipped fonts hold the usable subset of ASCII, and `ord` is the
conversion.** Both carry 94 inked glyphs at ids 33..126 with no gaps —
printable ASCII — plus id 32, space, which is blank because a space is. So
writing a character and writing its code are the same operation.

    console.write("A")
    console.cell[0, 0] = ord("A")     # the same thing — 65 either way
    chr(console.char)                 # "A" back again

There is no translation layer here to know about. `write` maps each character
with `ord` and stores the result, because a glyph id is a byte and ASCII
already numbers its characters inside one — no font table, no encoding step,
nothing to configure. It is also the sharpest evidence that the two audiences are one
object: the teletype road and the grid road put the same number in the same
array.

**The other 162 ids are blank and free.** Both shipped sets leave 0..31 and
127..255 empty, so those have no character meaning and are where a game's own
glyphs go, reached by number rather than by `ord`. Id 0 blank is also what
makes `clear()` mean an empty screen rather than a screenful of something.

**A glyph id is a byte; ASCII is seven bits.** Ids 0..127 cover it. **Ids
128..255 are not ASCII** and carry no character meaning at all — they are the
font's, the same as 0..31.

That is a position, not an omission. ASCII stops at 127, and the range above
it was codepage territory: a different set of characters on each system, and
frequently swapped out for another at runtime. There is no single meaning
there to honour, so the console does not invent one — and swapping the meaning
of the upper half is exactly what handing it a different `CharSet` does.

`ord` will still map onto that range, because Unicode's first 256 codepoints
are Latin-1 — itself just one of the claimants. So `console.write("é")` puts
233 in the cell and does not complain. That is an accident of codepoint
numbering rather than a mapping the console offers. On both shipped fonts 233
is blank, so the character draws nothing and advances the cursor.

Above U+00FF it raises rather than substituting: `console.write("€")` is a
`ValueError`, because 8364 is not a byte. So this is not a text widget — it
addresses glyphs, and `ord` is the only bridge from characters to them.

Reading through `cell` masks the facing off, so the round-trip holds whatever
the pen is doing: `console.cell[x, y]` is the glyph id, never the packed value.

Three more things follow, and they are the reason for the packing rather than
consequences of it.

**The packed value is the render index.** The variant table is laid out as
`variant * 256 + glyph`, so a cell's stored number is *identical* to its index
into that table. The compose step gathers with the grid directly and does no
arithmetic at all — no unpack, no shift, no second lookup.

**The two halves cannot drift apart.** `scroll` moves one buffer. `clear`
fills one buffer. There is no parallel facing array to forget, so no code path
can move a character and leave its orientation behind.

**The upper field is a variant index, and facing is merely what it holds
today** — 3 bits of 8. That distinction is what the spare room is for. A
variant is a pre-computed remap of a glyph's plane indices, so *any* per-cell
attribute expressible as such a remap can widen this field and cost nothing at
render time. Inverse video is the obvious next one: swap plane 0 and plane 1,
pay some table memory, and the compose step does not change.

The line that bounds it: **a variant is a function of the cell.** Blink is
not — it varies with time, so there is no static pattern to precompute. Blink
belongs to the palette or to a per-frame pass, and no amount of spare bits
will make it a variant.

## The variant table

Every glyph is pre-oriented once, at construction, into a table shaped
`(variant, glyph, height, width)`. Orientation then costs a wider gather
rather than a branch: the compose step folds the variant into the glyph id and
indexes a flat view, so the work per cell is exactly what it was before
orientation existed.

**Eight slots, indexed by the facing itself.** The obvious cut is four slots
with the facing packed as `facing >> 1`, and it works only while TRANSPOSE is
clear — which is "a console cannot turn" hardcoded into arithmetic. The rule is `applies_to`: a *square* cell may take
all eight orientations, and 8x8 is a charset BPS ships. Indexing by the raw
facing costs the shift, serves both cases with no branch, and leaves the four
turning slots simply unfilled on a rectangular cell — where the pen setter
refuses them anyway, so nothing ever gathers from them.

The price is table memory, and it is paid up front: **192KB at 8x12** with
half of it unused, **128KB at 8x8** with none wasted. Small enough to stay
resident either way, which is the only reason materialising every slot is
affordable.

Variant 0 *is* the upright table, held as a view rather than a copy. One
store, so a pattern written through `console.glyph[id]` cannot leave the two
disagreeing.

## Colour is not a cell attribute

The paint buffer holds four palette indices for every **pixel** on the
console, addressed `paint_buffer[row, col, slot]`. Not per cell — per pixel.

It is the same substance as a pen's own slab at a different extent: a pen
holds one cell's worth, this is the screen's worth. A glyph's pattern says
which of the four each pixel takes, so this is what the glyphs are painted
*with* — the input to compositing, not the result of it. That is why it is
`paint` and not `pixels` or `color_buffer`, both of which already name the
output: `PixelBuffer.pixels` is the composed one-index-per-pixel image and
`color_buffer` is the FBO attachment. Naming the input after either would
point the wrong way down the pipeline.

**Colour was never a per-cell attribute.** The pen stamps it cellwise because
that is what text wants, but the buffer underneath is an image and can hold
anything: a gradient down a cell, a copper bar across the screen, a field that
ignores the grid entirely.

    console.paint_buffer[:] = field            # whole screen, one memcpy
    console.paint_buffer[..., 1] = shade       # one slot, screen-shaped
    console.paint_buffer[36:48, 0:344] = band  # one row of cells' worth

A cell-shaped colour buffer could hold only the average of a pen that varies
within its cell, and that capability is the one being protected.

**The axes are not the cell grid's, and the names carry the warning.**

    x, y        screen order      cell[x, y]
    row, col    array order       paint_buffer[row, col]

`cell[x, y]` is screen order because that grid is the engine's to define.
`paint_buffer[row, col]` is array order because it is the screen's, and the
screen is row-major all the way down to the GPU. Storing it the other way
round does not fix the mismatch — it moves the transpose into the recompose
and makes it unconditional. Measured, a transposed whole-plane write costs
**1.2ms against 21µs** native. The order is not negotiable, so the naming is
what has to do the work.

## The pen

A pen is the instrument: four slots and a facing. The slots hold indices
*into* a palette, not colours, and the pen carries which way its characters
come out.

**Facing belongs on the instrument, not the surface.** The shape this replaced
was `layer.orientation`, and the tell is that attribute nouns describe the
thing they hang off. `sprite.orientation` describes something you can see —
read it and it tells you how that sprite is drawn now. `layer.orientation`
described the *future*: setting it changed nothing on screen, and
already-stamped cells kept their own facing. A tool noun does not have that
problem, because you know without being told that the pen is in your hand and
not on the page. Swapping pens is not a mode; it is a choice of tool.

**The mental model is a stencil**, and that is what makes facing intrinsic
rather than bolted on. A pen is a point that lays a stroke; this object has
*extent* — a cell-sized sheet you lay down and stamp through, like the plastic
ones from school. Flipping a sheet over to get the mirror is the natural
operation on a stencil, where rotating a pen is meaningless.

The name stays `Pen` because the console is the normal path and a console
writes with a pen. **`Stencil` is not available as a rename**, and this is
evidence rather than taste: the word is already spent on the other end of the
mechanism. The glyph is the stencil — a flat 2-bit pattern carrying no colour
of its own, which never changes while the colour under it does — and the
render path agrees, the glyph selecting which plane while the pen supplies the
value.

**One pen class, for flat ink and per-pixel ink alike.** A separate
per-pixel type is the obvious split and it is the wrong one: it shares exactly
one member with `Pen` and none of `[i]` or `__iter__`, so it is a paint-source
wearing the word, and two classes for one concept force every holder to care
which it has. The cheaper-looking half is not even cheaper: building the full slab for every pen is
**faster** — `clear()` 1.9x, one `_paint` 2.4x — because the tuple form paid a
tuple-to-array conversion and then a strided broadcast where a slab is a
memcpy. The cheap representation was never cheap.

**Slots go by number, all four.** There is no `foreground` and no
`background`. A font decides what each plane draws, so a name would prejudge
it — and naming half of them does not sidestep that objection, it stages it. A
reader who meets `pen.foreground` and `pen[2]` in the same paragraph has to
ask what 2 is called, and the honest answer, that it has no name because
naming it would decide what the font draws there, only makes sense if 0 and 1
have no name either. Four numbered slots never raise the question.
`foreground` is a label rather than a test, and it fails on the shipped
charsets, where plane 2 is the font's outline and plane 1 is not obviously
more "fore" than it is.

**Two roads, and neither taxes the other.** The slots are the declared
colours; `paint` is the slab the layer writes. Setting a slot updates both.
Fiddling `paint` directly is the advanced road, and from then on `paint` is
the truth — slot reads describe only what you last set, not what is in the
slab. Real usage splits exactly that way: one demo mutates slots in a hot loop
and never touches `paint`, another writes a plane and never reads a slot back.
Making slot reads inspect the slab would serve neither, and would break
`tuple(pen)`.

Because a slot holds whatever you put in it, the question of how you set a
gradient or a mask without reaching into internals does not arise:

    pen[1] = WHITE                       # flat
    pen[1] = ramp                        # gradient down the cell
    pen[2] = np.where(mask, BLACK, 0)    # a mask is the same mechanism

A 1-D sequence as tall as the cell reads as one value per scanline — the
copper-bar reading, and the one rule that holds for every cell size rather
than only square ones.

**Swapping is the idiom; mutating a held pen stays legal**, and that is a hard
constraint rather than a courtesy. A demo that mutates one pen through an
80x45 loop every frame is prior art the surface has to keep working, and
requiring a fresh `Pen` per cell would be a per-frame allocation in the frame
path — which this engine forbids outright.

**A pen depends on a cell size, not on a layer.** `Pen(..., size=(w, h))`
binds immediately; a pen given no size binds on first use and freezes then.
Either way the size is frozen, a mismatch is refused, and one pen is reusable
across any number of layers whose cells match. `Pen(layer)` is the obvious
signature and it misleads twice: it implies a pen is owned by the layer it was
built against, and that you need a layer in hand to make one. Neither is true,
because a bind only ever refuses a *different* size. Keyword `size=` also keeps the first positional slot
unambiguously a colour, which matters now that slots accept sequences.

**The system pen is `(0, 1, 0, 0)`, and the identity mapping was a bug.**
`Pen` owns what "system" means, so a fresh console repeats no literal.
`(0, 1, 2, 3)` is the tempting identity and it is wrong: palette 2 and 3 are
SILVER and GRAY, and the shipped charsets use plane 2 for the font's outline,
so every default glyph would arrive silver-outlined and gray-shadowed — the
engine deciding a font's outline colour on its behalf, which is the very thing
leaving slots 2 and 3 unnamed exists to avoid. Declining to paint them carries
that ruling into the default, and the gain is real: a fresh console shows text
with zero colour setup, where an all-zeros pen drew nothing and left you to
discover why.

**The engine does not build ramps.** A pen paints palette *indices*, so a
colour gradient is a run of consecutive palette entries holding the
interpolated colours, and budgeting palette RAM is an art decision. The helper
is nine lines a game owns — interpolate the stops, burn them into a bank you
chose, hand back the indices — and its supported home is `examples/pen.py`.

## What changed, and what recomposes

Every write reports the band of **cell rows** it disturbed, and the recompose
is those rows rather than the screen.

That is the real job of the access faces behind `cell`, `glyph` and
`paint_buffer`. They exist to make `console.cell[3, 5] = ord("A")` mean
something different from `console.glyph[65] = pattern`, but indexing is the
smaller half; telling the console what changed is the larger one. Pixel rows
round outwards to cell rows, because a write that clips a cell still changes
that cell's composed pixels.

**This is why a paint-buffer read hands back a read-only view.** It is a view
because copying 900KB to look at it would be absurd. It is read-only because a
write through a captured view would reach the buffer without passing through
the reporting path, and the frame would silently not change — a far harder bug
to find than a refused write.

`_touch` is the one funnel every write reaches: a cell, a run of text, a paint
band, a replaced glyph pattern, a scroll, a clear. Anything that needs doing
per-write is done there once rather than at each site, which is also what
makes collision invalidation a single line.

**Deferral is explicit, and named for what it does.** Every write recomposes
what it touched, which is right for one write and wasteful for two:

    with console.defer():
        console.cell[:, :] = codes.T
        console.paint_buffer[:] = field

Without the block that pair recomposes the whole screen twice and the first
result is discarded by the second. Measured on a full-screen demo, that is
**~1.26ms a frame of pure waste against 3.74ms for the whole thing**.

It is `defer` and not `batch` because `Batch` is pyglet's word for a draw
batch and this engine already uses it that way — both of those are nouns
holding geometry, and this is a verb that holds off.

It is explicit rather than lazy on purpose. A console that deferred by itself
would have to guess when you had finished, and the honest moment to guess is
`composite` — by which time a collision query may already have answered
against a screen that had not been drawn yet.

It nests, so a helper may defer without knowing whether its caller already
did, and it recomposes on the way out of an exception too: leaving the screen
out of step with the buffers would turn a handled error into a rendering bug
much further away. The dirty region is a **span** of cell rows and not a set,
so touching row 2 and row 28 recomposes everything between them.

There is a net at composite time for a defer left open across a frame. It is a
net and not the mechanism — an unclosed block is a caller's bug, but drawing a
stale screen because of it would be much harder to find than the missing
dedent.

For scale: a whole-screen recompose on an 80x30 console measures **1172µs**.

## One quad to the screen

The console composes its whole screen into a single `PixelBuffer` of palette
indices and draws that as one quad: `picture.vert` + `indexed.frag`, the
pixels on texture unit 0 and the palette's table on unit 1. A console has one
palette, so that table is a single 256x1 row and the `palette_row` the
fragment stage reads is 0.

The composed buffer is a canvas rather than a file, so it is interned with
nothing and shared with nothing.

The program is built on first use, because compiling needs a current GL
context and importing must not — a console can be constructed before there is
a window, and cannot be drawn before there is one.

**There is no shared shader cache.** A cache keyed by the two source paths,
so that everything wanting the same compiled pair gets one program, is the
standard answer and the wrong one here. The engine has **five programs in
total** — the framebuffer
post-pass, two for tiles, one for sprites, this one — and the set is fixed
when the engine is written, not discovered at runtime. There is no material
system and no intention of one. A keyed cache is the right answer when you
cannot know what will be asked for; when the answer is a five-item list
decided in advance, it is a lookup standing between a reader and a fact they
could have read.

What it costs is locality. A program is coupled to the draw path that issues
it — the vertex attributes it expects, the units it samples, what must be set
per draw rather than once — and none of that could be written beside the
program while the program lives in a table keyed by two strings. Compiling one
extra copy of `picture`+`indexed`, because sprites use it too, is a few
milliseconds once per process and one GL object.

Blend is the compositor's and is **off** by default — it is read per layer
from `Layer.blend`, and indexed art is drawn or discarded rather than blended.
See [layers](layers.md).

## Typing into it

`console.echo(entry)` draws a line of typing at the cursor as it happens. It
does not block and nothing is polled by the caller: the line is fed by the
engine's per-frame cut of the input stream, and the console redraws it each
tick until it finishes.

**The console keeps its own text, and the engine keeps none on its behalf.**
The line handed to `echo` is a reducer owning its own buffer. The engine's own
input history is half a second — sized for reconstructing a quarter-circle on
a stick — and a console line may be open for minutes. One mechanism, two
lifetimes, and the console's is not stored in the engine's window.

The last drawn width is held so that a backspace can blank the cells it gave
up. Without it the tail of the old text survives underneath the shorter new
one.

A line that finished this frame is settled before anything else is written.
That ordering is what lets a reply printed the moment `completed` turns true
land on the row below the answer rather than appended to it.

The terminator ended the line and is not a character in it, so it is never
echoed, and the cursor drops to the start of the next row when the line
finishes.

One line at a time, matching the keyboard: echoing a new line stops redrawing
the previous one and leaves whatever it had already drawn on screen as
ordinary text.

## Colliding with text

The console offers four collision planes and a silhouette, and it is the
pillar that **pulls**. A tilemap stamps because a tile has to be painted
somewhere; a sprite composes because everything moved. A console already holds
both halves of every plane it could offer — the glyph gather and the composed
image — so it writes nothing and derives on the read.

Nothing is added to the recompose. Reading and writing text are the main uses
and colliding with text is the oddball, so the representation favours the main
case: `_refresh` is untouched, the partial band stays, and **a console nobody
asks retains no mask memory at all.** Invalidation rides `_touch`, which is
already the one funnel every write reaches, so it is one line rather than one
per write path.

**The vocabulary is the pen's own slot indices**, so a pen slot and a
collision plane are one number seen from two ends:

    pen[PLANE_2] = YELLOW              # what it draws
    console.collider(PLANE_2)          # what it collides as

Two verbs, and they are not the same one. `collider(name)` is the public one
and lives on `Layer`, which a console is; `collision_plane(name)` and `planes`
are the provider hooks each pillar implements. The tilemap and the sprite
implement the same pair — see [collision](collision.md).

All four planes are offered, plane 0 included. It is conventionally the
glyph's ground and so usually the holes, but a pen may paint it a real colour,
and reserving it would assume a convention `Pen` explicitly declines to
assume. A plane whose pen slot is transparent draws nothing and collides all
the same, which is how a level painted in glyphs yields three collision planes
out of one layer.

**The silhouette is the composed pixels, not the glyph plane indices.** A pen
slot may legitimately hold index 0, and the paint buffer resolves through
colour rather than through the glyph, so only the pixels know what was
actually drawn. Drawn and solid stay one predicate.

The silhouette rides the composed image's mask and is nearly free; a named
plane pays the glyph gather; a repeat read is a dict hit. See
[Limits and costs](#costs) for the figures.

A console asked for several named planes in one frame pays the gather once per
plane. Sharing it between them would trade away the property that a console
nobody asks retains nothing, which is the more valuable one.

## What it is not

Three shapes a reader might reasonably expect. They are settled, and recorded
so they are not re-proposed rather than because they are open.

**One class, not a console over a glyph layer.** Separating teletype behaviour
from a glyph buffer looks like a clean seam, and the seam costs more than it
saves. Two types that share seven of fifteen public members can be confused:
under a `-> Layer` signature, `layers.add(Console(...))` hands back the layer,
which answers to `.pen` and `.cols` while `.write` and `.print` raise, and a
test asserting only `.attached` passes while testing nothing it claims. The
wrapper also needs a public `.layer`, a base class to say what holding one
means, and five ordering verbs proxied back down — 114 lines to save a line
break. So `add` takes a `Layer`, returns nothing, and refuses anything else;
the two-line form is the only form:

    console = Console(CharSet.ASCII8X12)
    bps.layers.add(console)

Teletype semantics do stay optional, which was the real requirement. Optional
means not forced to call it, and one class gives that already.

**The picture stage, not the tile engine.** A console is a grid of cells drawn
from one sheet, so the tile engine looks like the natural route. Going that way
means `default.vert`, per-vertex scale and rotation, three matrices, and an
atlas, a cache and a batch — machinery for placing *many* tiles from one sheet
— to place one quad at (0, 0) that never scales or rotates. The picture vertex
stage is "a whole buffer drawn wherever it sits", which is the requirement
stated exactly.

**A mutable cursor, not an immutable address.** A cursor is `x`, `y` and an
unpack, which is `common.Location` plus a bounds rule — except that
`Location` is a `NamedTuple`, so `console.cursor.x = 10` would raise and a
single-axis move would become `console.cursor = (10, console.cursor.y)`.
Mutability is the whole of what the class is for; a read-only `.x` is `[0]`
with a nicer name. So `Cursor` carries `x`, `y` and an unpack and nothing
else, is neither constructible nor exported, and keeps its bounds on the
console where `cols` and `rows` live. There is no `cursor.char`, because **an
address should not dereference itself** — peek and poke are `Console.char`,
beside `write` and `print`.

## Still open

- **Whether a mutable `Location` should exist**, so the cursor need not be a
  console type at all. What the cursor needs is an assignable `x`; nothing
  about that is the console's.
- **Whether `Layers.add` returns the layer it was given.** It currently
  returns `None` and refuses anything that is not a `Layer`, which is what
  makes the wrong-object trap above impossible. A board card argues it should
  hand the layer back so a caller can get a handle inline, with the named
  two-line form staying the documented one. Both positions are written down
  and they are not the same rule. Belongs to [layers](layers.md) once
  settled.
- **Where the colour registers live**, and whether a grown palette bank
  belongs where it currently sits.
- **Whether the console layer must hold a strong reference to its
  `PixelBuffer`**, now that buffers intern weakly.
