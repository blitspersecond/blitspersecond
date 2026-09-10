# Sprites

A `Sprite` is one drawn thing that moves independently, over one sheet — and
it is a `Layer`, so the thing you pose is the thing you place in the draw
order.

    weiss = Sprite(SpriteSheet("weiss.sprite.json"))
    bps.layers.add(weiss)
    weiss.play("walk")                  # advances itself on the tick
    weiss.position = (160, 200)

One sprite is the small case of the pillar and not a different one. The same
sprite makes **many drawings of itself**, and they are what a trail, a tail, a
swarm and a bullet hell are:

    ghosts = weiss.before.extend(4)     # four more drawings, behind her
    smear  = weiss.after.append()       # one more, in front

    ghosts.positions[:] = history       # 25,000 of them write the same way

Everything else follows from one contract: **a `Sprite` is one identity, one
layer and one collision answer.** Two things that must be told apart by a
collision question are two sprites, therefore two layers. A mirror match is
two sprites binding one interned sheet — one texture, one set of tables, two
registers pointing at different rows of it.

That is why `before` and `after` are not a container of sprites. They are
drawings of *this* sprite: they share its art, its texture, its bind and its
collision plane, and they are individually positioned, posed, oriented,
shown, hidden and made solid. Twenty-five thousand of them are one draw call
and one answerable question.

The boundary against the other pillars is movement. Content painted into a
grid is [tiles](tiles.md); glyphs on a fixed lattice are the
[console](console.md); anything that moves at its own coordinate is here.

## Limits and costs

### Where a sprite is, and how it is turned

| | |
|---|---|
| position | whole pixels, always. A float is **floored** on the way in, so `x` reads back what was stored |
| the consequence | `sprite.x += 0.4` never moves the sprite — each write floors to the same integer. Keep the fraction in the game and assign the whole of it |
| where the art lands | `position + offset - origin` |
| `origin` | the sprite's own reference point, default `(0, 0)`. What it mirrors and turns about |
| `offset` | the *pose's*, carried by the asset. Chun-Li's 958 distinct per-frame offsets hang her art around an origin that stays between her feet |
| turning on the spot | `sprite.origin = sprite.centre`, read **once at setup** — `centre` is derived from the pose being shown, so reassigning it per tick makes an authored sheet wander |
| orientation | all eight axis-aligned turns, on any rect. A sprite has no cell to land in, so a quarter turn of a 24x32 pose is legal where the same turn in a tile grid is not — see [atlas.md](atlas.md) |
| the drawing surface | exactly the screen, with no working border. A sprite half off an edge writes what lands and drops what does not |

### What one identity may hold

| | |
|---|---|
| presentations | unbounded; capacity doubles, and slot ids survive the growth |
| slot 0 | the primary. It cannot be removed — `before` and `after` mean *earlier and later than this*, so there would be nothing left to order around. Park it with `visible = False` |
| a multipart pose | **the primary only.** Every instanced presentation is exactly one quad |
| an instanced presentation's art | the sheet's `primary_tileset` only |
| named collision planes | the primary's |
| the silhouette plane | every presentation that carries `collide` |
| palette rows | 0–255 on any tileset, whatever the package authored — the spare rows are where derived shades go |

The two restrictions in the middle of that table are what the rest of the
pillar is bought with. With them, four presentations and twenty-five thousand
are the same shape: one quad each, one texture, one bank, so a frame is a walk
and a gather rather than a dispatch.

### Memory

One sprite over the bullets sheet with 25,000 presentations — the fountain in
`examples/`:

| | |
|---|---|
| the palette bank | 256.0KB |
| staged quad records | 878.9KB |
| the index pattern | 585.9KB |
| slot state | 488.3KB |
| the draw order | 97.7KB |
| the collision plane, when collidable | 28.1KB |
| the sheet's art | 0.1KB |
| the rect table | 32 bytes |
| **total** | **2.3MB** |

**Slot state is 24 bytes a presentation** and that is the whole of what one
costs to *hold*: position, orientation, assembly, two bits, a palette row and
a liveness bit. Drawing one costs another 36 bytes staged and 24 of index
pattern, which is where the two largest rows above come from.

**The art is 128 bytes and its colour table is 256KB.** A `PaletteBank` is
always 256 palettes of 256 colours — see [palette.md](palette.md) — so on a
sheet this small the bank is two thousand times the pixels. That is not a
sprite fact; it is the same fixed cost a tilemap and a console pay, and it
stops being remarkable at the scale of real art.

The collision plane is a fixed 28.1KB because it is exactly the screen at one
bit a pixel, and it does not grow with the population: twenty-five thousand
presentations compose into the same bitmap four do.

### Loading a sheet

| | |
|---|---|
| a package, cold | **0.82ms** for fumiko — 32 poses, 8 clips, a 768x32 image |
| the same, art already interned | 0.63ms |
| a two-pose package, cold | 0.34ms |
| of which, parsing the JSON | 136µs |
| a bare image as a grid | 225µs |
| naming a sheet already loaded | **5.4µs** |
| `copy()` | 13.5µs |
| a fresh `PaletteBank` | 16.6µs |

A sheet is interned by path, so naming the same file twice hands back the same
object and the second cost is the 5.4µs above. The table is **weak**: when the
last holder lets go the sheet is collected and the next construction re-reads
the file. `copy()` is the escape from sharing and is cheap because it copies
the banks and shares everything else — see [the sheet](#the-sheet-it-binds).

Solidity patterns are the one load cost that is not here, because they belong
to the atlas and are shared by every layer over one sheet: priming Chun-Li's
1741 upright patterns is 14.7ms and 3.3MB, once, in [atlas.md](atlas.md).

### What a population costs to draw

Every presentation alive, visible and drawn on every frame; the bullets sheet,
640x360, compose measured across every layer's draw and excluding the present
wait. `util/sprite_sweep.py`.

| presentations | compose | p99 | µs per 1,000 | of a 60fps budget |
|---|---|---|---|---|
| 10,000 | 0.95ms | 1.19ms | 95.1 | 6.8% |
| 25,000 | 1.87ms | 2.20ms | 75.0 | 12.9% |
| 50,000 | 3.46ms | 4.09ms | 69.2 | 23.5% |
| 100,000 | 6.37ms | 7.30ms | 63.7 | 43.2% |
| 150,000 | 9.28ms | 10.41ms | 61.9 | 62.6% |
| 200,000 | 12.04ms | 13.35ms | 60.2 | 81.3% |
| 250,000 | 15.09ms | 16.68ms | 60.4 | 101.8% |

**60fps holds to 150,000 presentations. 100,000 fits inside half a budget.**

**There is no knee.** Cost per presentation *falls* with population — 95µs per
thousand at ten thousand, 60 at a quarter of a million — as the fixed
per-frame cost amortises. So there is no cliff to stay under and no threshold
to discover: how many presentations the pillar affords is a decision about how
much of a frame it may have, not a fact about the hardware.

The budget column is the one to read. At 200,000 the draw still fits but the
frame does not reliably: pace falls to 97.3% with 81% of the budget spent on
compose alone, and whatever the game is doing has to come out of the rest.

**The GPU is not what is being measured.** Run parked off-screen — identical
staging, identical upload, nothing rasterised — compose is within 1% at every
population. At 640x360 the rasteriser is doing nothing worth measuring, and
the pillar is CPU-bound at every size in that table.

### What refuses

| | |
|---|---|
| a `Sprite` built on anything but a `SpriteSheet` | `TypeError`, naming both loaders |
| a multipart pose on an instanced presentation | `ValueError`, naming the assembly and its part count |
| an instanced presentation posed off the primary tileset | `ValueError`, naming both tilesets |
| `centre` with no pose presented | `ValueError` — there is no art to find the middle of |
| removing the primary | `ValueError`, pointing at `visible = False` |
| a slot id that is not live | `IndexError`, saying how many are |
| an array view over non-contiguous slots | `ValueError` — the view would be a copy and every write silently lost |
| `red`, `green`, `blue` | `AttributeError`: recolouring is a palette job, and the message names the road |
| `alpha` | `AttributeError`: a pixel is drawn or it is not |
| a palette row outside 0–255 | `ValueError`, naming the tileset |
| a palette register on a tileset the sheet does not draw | `KeyError`, listing the ones it does |
| a palette register on an instanced presentation, for a non-primary tileset | `KeyError`, saying which tileset it does draw |
| an unknown clip | `KeyError`, listing the clips the sheet carries |
| a frame outside a clip | `IndexError`, giving the clip's length |
| an unknown assembly name | `KeyError`, listing what the sheet holds |
| index addressing on a package sheet | `TypeError` — a package's assemblies have authored names |
| a cell outside a grid sheet | `IndexError`, naming the grid |
| a cell size passed with a package path | `TypeError` |
| a width with no height | `TypeError` |

## The sheet it binds

    weiss = SpriteSheet("weiss.sprite.json")    # a package
    crate = SpriteSheet("crates.png", 16, 16)   # a bare image, as a grid

    sheet.tilesets     name -> the image it draws from, cut and coloured
    sheet.assemblies   name -> one timeless pose
    sheet.clips        name -> one carried animation, unplayed
    sheet.planes       every collision plane name any assembly declares
    sheet.grid         (cols, rows) for a grid sheet, else None

A sheet is a **record and nothing else**: three maps plus the collision
vocabulary they declare between them. Construction is GL-free and creates no
playback state — no clock, no current frame, no position — so a sheet can be
built before there is a window and shared by every sprite that binds it.

**A package is to a sprite sheet what a TMX is to a tileset**: where the
structure comes from, not where the pixels come from. It is one road in and
not the privileged one — a game generating its own sheet never touches the
loader — and the format has its own chapter in
[sprite-package.md](sprite-package.md).

The bare-image road needs no package at all. It reads an indexed image as a
uniform grid and synthesises the records: one tileset, one single-part
assembly per cell, named by row-major index, no clips, no named planes. That
is the road that makes a sprite usable before authoring tools exist.

**Only a grid sheet may be addressed by index.** `sheet.assembly(3)` and
`sheet.assembly((2, 1))` are grid vocabulary; a package's assemblies have
authored names, and asking one for cell 3 raises rather than resolving to a
coincidence.

### A tileset is a cutting and a colouring

    atlas: Atlas          where the rects are
    palette: PaletteBank  what the indices mean

Two fields, and both belong to other chapters — the cutting to
[atlas.md](atlas.md), the bank to [palette.md](palette.md). What is the
sprite pillar's is that they travel **together**, keyed by tileset name: the
palette register is `sprite.palette_index["character"]`, so a caller holding
the name wants both, and keying two dicts the same way is the shape that
drifts apart.

A tileset carries no `default_palette` field. The row a fresh register starts
on is `palette.active`, which the bank already is; a package's
`default_palette` is loaded into it and read back off it, and `copy()` carries
it across without this record doing anything.

**A sprite sheet is indexed by construction.** Both loaders refuse a direct
image and point it at the tile pillar, which is what makes the whole pillar
simpler downstream: one shader program with no branch, and a sprite that
*can* always collide, so participation is never a question about the art.

### There is no "sheet" type beyond this, and no spec

`SpriteSheet` is the runtime object and the validated one. Splitting it into
a validated spec plus a runtime copy is the shape to reach for when a global
resource manager holds one shared spec that per-sheet mutation must not
contaminate. There is no resource manager here: **interning by path is the
sharing and `copy()` is the escape**, so the second type has nothing left to
do.

Naming one file twice hands back the same object. The table is weak, so a
sheet lives while something holds it and is collected when the last holder
lets go — a cache rather than a leak, at the honest cost that dropping a sheet
and rebuilding it re-reads the file.

`copy()` mints new banks and shares the atlases, the assemblies and the clips.
That is the same law pixels follow one level down: colour is what a holder
usually wants its own of, and everything else is genuinely one thing.

### The primary tileset

Slot 0 may draw from any tileset the sheet has, and a multipart pose spans
several. **Every other presentation is pinned to one**, named
`primary_tileset` in the package and defaulting to the first tileset in the
file. That pin is what makes the instanced block a single texture under a
single bank — one bind, no material transitions, and a stray assembly cannot
break the batch.

The name is deliberately not `primary`, which throughout this pillar means
slot 0. This is a fact about the art.

## Presentations

    ghosts = weiss.before.extend(4)     # four drawings, behind
    smear  = weiss.after.append()       # one drawing, in front

    q q q q Q q q q q
    |_____| ^ |_____|
     before |  after
          slot 0

**That is one sequence, not three.** Every position in it is an ordinary quad
drawn in an ordinary order; one of them is also the sprite. `before` and
`after` are not containers — they are what "earlier than slot 0" and "later
than slot 0" are called, and they exist only because the order is read
relative to that one position.

It is *stored* pre-split because slot 0 is exactly where a bulk gather has to
break anyway: it is the one position that may expand to several quads and
cross to another tileset. A single order array would have to find that
position and split around it every frame. Two arrays are that split, kept
rather than rediscovered — an encoding, not a second idea.

### A slot id says which; a chain says where it draws

These are deliberately different numbers.

    weiss[slot]        an ADDRESS -- which presentation
    weiss.before[0]    a POSITION -- where in the chain it draws

A slot id is an index into every state array and is **stable for the
presentation's life**. Reordering never touches it and never touches the
state arrays. Moving a drawing forward in the order is a write to one int32
array; the thing being moved does not know it happened.

Making one number carry both is the shape to reach for — signed, ascending,
zero in the middle — and it costs more than it saves. Moving a presentation
in the draw order would mean moving its state; the sign would have to mean
"behind" as well as "identity"; and every question about growing or
reordering becomes a question about renumbering. Split, both are easy.

### The order is an array, not a linked list

Each chain is an `int32` array of slot ids in draw order, and `append`,
`prepend`, `insert_before`, `insert_after` and `remove` are ordinary array
operations.

A linked list optimises for frequent mutation and rare traversal, and this is
the exact opposite: **every frame walks the whole order and almost nothing
ever reorders.** An array is a gather — `position[order]` — with nothing to
chase and no separate materialisation step. The `O(n)` memmove an insert costs
is paid only when the draw order actually changes, which for a trail is at
setup and for a swarm is never.

The one shape to avoid follows from it. **Appending in a loop is quadratic**,
because each call concatenates the order array — 2,000 of them is about 30ms.
`extend(n)` is one allocation and one concatenate, and it is the door a swarm
uses.

### Liveness is not linkage

| | |
|---|---|
| `visible`, `collide` | per-frame: spawn, reap, park. Never relinks |
| `append`, `remove` | authoring the order. Setup, or a rare change |

**A dead bullet is a parked slot, not a removed one.** Reaping with `remove()`
is an `O(n)` memmove per corpse and destroys the thing the arrays exist for.
Reaping is `visible[dead] = False`, and respawning is
`np.flatnonzero(~visible)` — the free list is the liveness bit read backwards.

The two mechanisms stay distinct because the cheap one has to read like the
obvious one. Reaching for the wrong one costs a great deal with nothing
announcing it.

### `visible` and `collide` are orthogonal, and neither gates the other

Four states, all reachable:

| | drawn | not drawn |
|---|---|---|
| **solid** | an ordinary sprite | a cloak, an invisible wall, a mine |
| **not solid** | a ghost trail, a spent effect | a parked slot |

Making `visible` a master switch over `collide` is cheaper to implement and
always the wrong model, because the argument for it — *invisible things
obviously aren't there* — is an argument about the common case rather than
about the mechanism. The compositor already had the right model:
`Layer.enabled` has only ever meant *draw this*, and a disabled layer keeps
colliding.

The decoupling pays twice. The bits dirty **disjoint** things — `visible`
invalidates staging, `collide` invalidates the planes — so a blinking sprite
stops restamping a plane it cannot have changed. That is not a separate
optimisation; it is the same claim about independence, stated as cost. When
two flags genuinely are orthogonal, their invalidation is too.

### What the primary has that the others do not

| the primary only | every presentation |
|---|---|
| the clip cursor | position, orientation, assembly |
| multipart assemblies | `visible`, `collide` |
| named collision planes | the silhouette plane |
| a palette register per tileset | one palette register |

**The primary carries what is per-identity and authored; every presentation
carries what is per-presentation and uniform in shape.** The multipart
restriction is the one that pays for the rest: with it, every instanced
presentation is exactly one quad, so four of them and twenty-five thousand
are the same shape and there is one representation rather than two.

### Reaching a population

    with swarm.edit():
        swarm.positions[:, 1] += fall
        swarm.visible[dead] = False

`Presentations` hands out **views** into the state arrays, not copies, so a
write reaches the sprite. Writing through a view does not pass a setter, so
nothing tells the sprite it moved — `edit()` is what does, once around the
batch rather than once per write.

A batch's slots have to be **contiguous** for a view to exist at all. Slots
allocated in one `extend` are contiguous by construction; a batch assembled
from ids that came back from earlier removals is not, and asking it for an
array raises rather than handing back a copy every write would be lost
through.

Seeding is from the primary, which is the template: a new presentation
arrives with the sprite's position, orientation, pose and palette register,
because it is another drawing of *this sprite*. Only `visible` and `collide`
are not inherited — **a fresh presentation arrives parked**, since allocating
must not put art on screen.

## Colour is a row, not a multiplier

    chun.palette_index["character"] = 3     # a costume swap
    chun.palette_index["effects"]           # 25, and left alone

A colour in BPS is an index, and what an index looks like is a palette's
business — see [palette.md](palette.md). What this pillar adds is **the
register**: which row of a tileset's bank a drawing is read through.

**One register per tileset on the primary**, because a multipart pose spans
several and a player's costume pick must set the character's without moving
the effects'. **One register on an instanced presentation**, for the primary
tileset, because that is the only art it draws — asking one for another
tileset's register raises and says which one it has.

This is the hardware OBJ model made literal: a palette field per sprite entry
rather than per tile. It is what makes a mirror match cost one sheet — red
Chun-Li and blue Chun-Li are two sprites, two layers, two registers, one set
of shared tables and one texture upload. The register is a number at the GPU
too: a tileset's bank is one 256x256 texture, so writing this picks a row the
shader reads rather than binding a second table.

Defaults come from each tileset's bank register, so a sprite arrives wearing
what the package said and a presentation arrives wearing what the sprite is.

### There is no tint register anywhere in this pillar

`red`, `green`, `blue` and `alpha` all refuse, and each refusal names the road
out.

**Recolouring is a palette job and never a pixel one.** A multiplier can only
darken what is already there; a row can say what colour it produces. Every
effect a multiplier could serve — a dimmer trail, a hit flash, a costume — is
a row, and carrying both would be two ways to spell one intent with the weaker
one easier to reach.

**Alpha refuses for the reason the engine paints the way it does.** A sprite
pixel is drawn or it is not, with nothing in between: the pass paints back to
front, colour information is last-writer-wins, and indexed art carries exactly
that — index 0 transparent, everything else opaque, and the shader discards
rather than blends. Making a whole layer sheer is `layer.blend = True`, which
is compositing and belongs to the compositor.

A whole-**pass** tint register does survive on `SpriteBatch`, multiplying
whatever the palette produced over every quad in a pass. Nothing drives it. It
is kept because a post-process over a whole layer is the shape that would want
one, and that is a per-pass decision rather than a per-drawing one — so it
cannot cut a run, and it costs one call a pass rather than one a run.

### How a trail fades

    bank = sheet.tilesets["character"].palette
    for step, ghost in enumerate(ghosts, 1):
        row = SHADE_ROW + step - 1
        bank[row] = bank[costume]
        bank[row].duotone(signature, step / len(ghosts)).darken(...)
        ghost.palette_index["character"] = row

Generated once at load, into the bank's spare rows, and each ghost's register
is written once at setup. **A fading trail then costs nothing per frame** —
what changes per tick is where the ghosts are, not what colour they are, and
the shade is pinned to a position in the chain rather than to a ghost.

Pulling each row toward the character's signature colour rather than only
darkening it is what makes a trail read as a silhouette of *her* rather than a
dim copy: `duotone` keeps every entry's luminance and gives it one hue, so the
art's light and shade survive while its variety collapses. Lerping toward the
colour instead drags the dark pixels *up* toward a bright signature, and the
shadow comes out brighter than the sprite.

That writes into the **sheet's** bank, which is interned — a sister sprite two
layers up sees those rows too. That is deliberate and it is the same law
pixels follow: `bank.copy()` is how a holder that wants its own colours stops
sharing.

## Animation

    chun.play("walk")
    chun.play("attack", loop=False)

That is the whole of animation from a game's side. A clip advances itself on
the fixed tick from there, and nothing needs calling per frame to keep it
running.

### A clip is data; an animation is a cursor

A `Clip` is on the **sheet**: a named sequence of `(assembly,
duration_ticks)`, authored and immutable. An `Animation` is a **cursor** into
one — which frame, how long it has been held, whether it has finished.
Calling both "animation" is how a reader stops being sure which one advances.

**Ticks are the only unit.** `duration_ticks` is an integer count of fixed
simulation ticks; milliseconds never enter. A clip advances on the fixed-step
loop or not at all, and the milliseconds an authoring tool exports are a
preview concern that stops at the loader.

### `play()` is the only verb, and it is four things at once

Start, cancel, interrupt and reissue. There is no `stop()`, no `restart()` and
no `resume()`, because each would be `play()` with a different name and a game
would then have to decide which it meant.

    if chun.playing != "walk":
        chun.play("walk")

That guard is the idiom, and it is the shape a game already has: `play` is
always conditional, and the per-tick advance is not something a game elects
to do.

**Hitstop is reissue.** Setting a frame restarts that frame's hold, so pinning
a sprite is `play(chun.playing, frame=chun.animation.frame)` — the same verb,
not an omission.

**It applies its first frame immediately**, so a `play()` issued in this
tick's game callback shows this tick rather than letting one frame of the
previous pose leak through.

`loop` defaults to what the sprite was last told, and to looping. A one-shot
holds its last frame and reports `done`.

### Where it runs, and how to opt out

Animation is the sprite pillar's work in the frame's BEGIN slot — see
[layers.md](layers.md) — so a clip has already advanced and the pose is
already set by the time the game's tick callback runs.

**Writing `sprite.assembly` by hand drops the clip.** Two things writing one
pose every tick is a fight, and the explicit write is the one that means it.
Posing by hand is the ordinary way to drive a presentation: a trail ghost
showing a snapshot of the past is exactly that, and only the primary has a
cursor at all.

Constructing an `Animation` directly is a real road and not a leftover. It
gives a sequencer nothing advances for you — call `update()` and read
`assembly` off it — which is what a game driving several cursors over one
sheet wants: a paper-doll, a UI flourish, a replay scrubbing backwards, none
of which want a layer per cursor.

## The frame plan

No GL here. The plan turns "where everything is" into one float array and a
list of draw runs; the draw step issues them. Keeping the projection separate
is what makes it testable without a window, and it is where the pillar's cost
actually lives.

    walk the before chain   -- one quad each
    emit the primary        -- one quad per part
    walk the after chain    -- one quad each

In that order, always, with no sort. The chains **are** the draw order, so
there is nothing to decide here about sequence — only where one run of quads
ends and the next begins.

The whole plan is rebuilt when something moved, and not otherwise: a still
scene stages nothing and uploads nothing.

### One quad is nine floats

    x, y, w, h, u0, v0, du, dv, turn

**36 bytes a quad, staged once.** There are no vertex attribute arrays at all:
`quads.vert` reads this record at `gl_VertexID / 4` and rebuilds the four
corners itself, so the corner expansion happens where a vertex stage was
running anyway rather than being manufactured on the CPU and uploaded. The
same quad as four expanded vertices is 96 bytes.

`turn` is a number in the record rather than eight pre-permuted corner sets,
because turning a quad permutes **which corner reads which `(u, v)`** without
moving the box. That is a vertex-stage job, and making the CPU do it means
building a table whose eight-fold axis exists only to undo itself.

### The one orientation formula

    drawn at = position + local_rect(orientation, offset, source, origin)

`local_rect` is shared by draw staging and collision stamping **so the two
cannot drift**. It is one function and stays one function: transpose swaps the
axes, then the two mirrors reflect about the sprite's origin, matching the
order the flag bits are documented in. Getting it backwards is invisible on a
square symmetric source and wrong on everything else.

Width and height swap under the four transposing orientations, which is the
whole reason a sprite may take all eight where a tile placement may not: a
sprite has no cell to return to and no neighbours to overflow.

The per-assembly tables it fills are computed **once per orientation and
reused for every presentation**, so a 25,000-bullet population of two kinds
fills sixteen rows and gathers the rest. The tables are rebuilt when the
origin moves, because the origin is inside the formula — that is per assembly
rather than per presentation, so it stays cheap at any population.

### Runs

A **run** is a maximal stretch of adjacent quads sharing a material:
`(buffer, palette bank, palette row)`. Coalescing is **strictly
order-preserving** — never reordering across a material boundary — because
that is exactly where transparency stops commuting.

Boundaries are found by a vectorised diff rather than by walking, which is the
difference between a population of 25,000 being a few array operations and
being 25,000 inspections.

**Only the palette row can cut a run inside a chain.** Instanced presentations
are pinned to the sheet's `primary_tileset`, so the buffer and the bank are
constant across a whole chain by construction. A uniform population therefore
finds **zero** boundaries and emits **one run**.

The material has three fields and not four: there is no colour in it, so
nothing about how a drawing *looks* can cut a run except which table it
reads.

**A run is adjacency, not distinct values.** The count is how many times the
row changes walking the chain, not how many rows are in play — so a six-ghost
trail holding six shades cuts six runs, and a swarm ageing through those same
six rows in arbitrary slot order cuts wherever two neighbours disagree.
Keeping a population's rows in chain order is what keeps it in few runs, and
for a trail that falls out for free: distance along the chain already is age.

The one genuine interruption is a **multipart primary crossing to another
texture** — Chun-Li's effects over her character — which is one bind in the
middle of a frame and is what the primary is allowed to be expensive for.

## The draw

    bind the record texture      once per pass
    for each run:
        bind the bank            if it changed -- and rebind the atlas after
        bind the atlas           if it changed
        set the palette row      if it changed
        glDrawElements           count * 6 indices, offset into a static pattern

Where a tilemap spends its single bind on a uniform grid, this spends binds on
ordered composition. That difference is the pillar, not an implementation
detail.

The index pattern is static: quad *i* is `[0,1,2, 0,2,3] + 4i`, uploaded once
per growth. A run draws by **offset** into it, which is what lets many runs
share one buffer — and it is what makes `gl_VertexID` the quad-corner number
rather than a draw position, so four vertices run per quad instead of six.

### One program, not two

The tilemap carries an indexed/direct pair because a tile sheet may be RGBA —
fog, cloud, a high-colour backdrop, none of which anything asks collision
questions about. **A sprite sheet is indexed by construction**, so there is no
branch here and nothing to choose: unit 0 carries palette indices, unit 1 the
tileset's whole bank as a 256x256 table, and the row says which line of it to
read.

That is not a limitation accepted quietly. Palette indices are what make the
pillar's colour model work at all — a register per tileset, a recolourable
character with pinned effects, a mirror match costing one sheet. An RGBA
sprite would have none of it.

### Order of binds inside a run

**Touch the texture before binding either unit.** A texture's first use may
upload, and an upload lands in whatever unit is currently active, so letting
one run between the unit assignments puts it in the wrong one.

A **palette upload may itself disturb unit 0**, so a run that changes bank
invalidates the remembered atlas and rebinds it. Changing the row within a
bank does not: the bank is one texture and the row is a uniform, so a costume
swap between two runs of one tileset costs one call and nothing else.

What is still expensive is a change of **buffer**, which is why the plan works
to keep instanced presentations on one tileset.

### A buffer texture, not a shader storage buffer

The quad records are read through a **buffer texture**, not a shader storage
buffer. An SSBO does the same job and measured about 1% ahead with the ranges
overlapping — but it needs GL 4.3, which is above the rest of this engine and
above macOS at 4.1. *"I don't think the complexity of maintaining two paths
warrants the marginal performance gain"*.

## Collision

The plane layout and the query vocabulary belong to
[collision.md](collision.md). What is this pillar's is how a population of
poses becomes one bitmap.

**Composed, not retained.** A tilemap scrolls a contiguous grid in lockstep,
so it keeps its plane and slides it. A sprite is at a coordinate and every
presentation may move independently, so there is nothing to slide: the plane
is cleared and refilled whenever something moved. That is the same split the
two pillars make on the draw side, and it is why there is no apron arithmetic
here, no drift and no translate.

It is rebuilt in the render phase alongside the frame, so the tick that asks
about it spends nothing.

**The planes are exactly the screen, with no working border.** The compiled
stamp guards every access, so a sprite half off an edge writes what lands and
drops what does not.

### Three levels of participation, and they are different questions

| | |
|---|---|
| can it collide at all | always — both loaders refuse a non-indexed sheet, so a sprite always can |
| is the layer in play | `Layer.collidable`, the fast-track switch |
| does **this** drawing | `collide`, per presentation |

The last one is why a ghost trail can be the loudest thing on screen and
contribute nothing, and why a cloaked sprite hits without being drawn.
`visible` is never consulted: an invisible presentation still occupies space.

`collide` is all-or-nothing across a multipart pose. A sprite's parts are one
thing at one place; half of it being solid is two sprites.

### Named planes are filled rectangles, and that is all they are

An assembly authors `attack`, `body_hurt`, `push` and the rest as **AABBs** in
its local frame. They get one plane each, and composing one is filling its
rects in — axis-aligned, so there is no geometry here at all, just a solid
pattern of the right size stamped through the same `local_rect` the art goes
through.

**Nothing downstream knows they were rectangles.** A query is a bitwise
intersection over packed planes, so a box plane and a pixel silhouette answer
the same way and cost the same; the boxes are never consulted as boxes, never
used to cull, and never turned back into geometry. That is what lets `attack`
against a tilemap's terrain be the same question as `attack` against another
sprite's `body_hurt`.

**They belong to the primary alone.** A named plane is authored per-assembly
geometry that must land in a specific target, so N presentations would mean
N × K rect stamps into fragmented planes. The silhouette is one composition
into one bitmap, and that is what makes a population affordable.

`sheet.planes` is every plane name any assembly declares, precalculated — the
vocabulary a query selector validates against, so a typo'd plane name is an
error rather than a hitbox that never hits.

### One stamp shape

There is one stamp path, not two: a scatter over many placements, used at
every count including one.

The shape to reach for otherwise is a split — word-slice strokes for a rich
sprite's handful of parts, a many-position scatter for a dense population —
and it is an artefact of the tool rather than of the problem. Measured on one
16x16 placement: **8.52µs** for the slice strokes against **0.42µs** for the
scatter at N=1. The scatter is cheaper *before* there is anything to batch,
because it is compiled and the strokes are NumPy.

## What it is not

Three shapes a reader arriving from another engine would reasonably build,
and the reason each does not fit.

**Not a layer holding many sprites.** The obvious split is a sprite *layer*
that owns a pool of sprite *instances*, and it costs the thing the pillar is
for: an instance is then a drawn thing without a collision identity, so the
layer has to answer collision questions on behalf of things it cannot tell
apart, and every question about identity becomes a question about which level
of the split owns it. **A `Sprite` is a `Layer`** — one identity, one plane,
one answer — and the many-drawings case is served by presentations, which are
explicitly *not* identities. Wanting two collision answers is wanting two
sprites, which is free: the sheet is interned.

That ruling settles a second thing for nothing. A dress-up doll — a wardrobe
in one sheet, garments composed and recoloured independently — needs the
garments to restack, and a sprite has no mutable per-part draw order. Under
the fusion a garment is a **layer**, so it restacks through the vocabulary the
compositor already has and carries its own palette register because registers
are per sprite.

**Not a per-part palette override.** A part naming its own table is the shape
to reach for when a character recolours while her effects stay pinned — and
that is the per-*tileset* register's job, needing no override at all. Parts
from different tilesets already draw under different registers; two parts of
*one* tileset wanting different tables is a sprite of its own, which is a
layer of its own. Measured on a real sheet, nothing uses it: 0 of Chun-Li's
3,237 parts.

**Not a depth number.** A `z()` bucket assigned at construction is one more
ordering vocabulary beside the one the compositor has, and it cannot express
what a trail needs anyway — a bucket with no setter means N drawings cannot
restack. Depth between sprites is stack position in `bps.layers`; depth within
one sprite is position in a chain. Both are mutable, and there is one of each.

## Still open

**Whether a whole-layer tint generalises.** The register survives at pass
scope on `SpriteBatch` with nothing driving it, and no pillar exposes a tint
surface: not the console, not the tilemap, not a sprite. The question the
board carries is whether a post-process over a layer wants one, and it has
**zero** consumers to reason from — which is a harder question than the one it
was opened as, when one pillar had the feature and the answer was whether it
generalised.

**Where `Layer.opacity` lands.** A whole-layer fade is compositing state on
any pillar, but unlike blend it has to reach a shader rather than being GL
state the compositor can set — so `Layer` would own the value and each pillar
would apply it. Stated as direction and not built.

**A proper benchmark.** The population curve above is a fountain of bullets
with collision off, which prices staging and upload and nothing else. What a
real game does — a few dozen animated characters, multipart poses, named
planes, queries — is not the same workload and has no figure.
