# Collision

Every collidable layer owns a bit-packed screen-space mask of its solid
pixels, and a game asks pairwise questions of them:

    if player.collides_with(ladders).at(x, y, w, h):
        ...

Pixel-perfect, at 1.3µs a question. There are no entities, no shapes and no
physics: **the engine produces masks and answers overlap**, and what an
overlap means is the game's.

This is not tile code and it is not sprite code. It sits beside the pillars
because the engine's premise is that a layer is one texture, one draw call and
one answerable collision identity — so a tile layer, a sprite layer and a
console answer the identical question, and cross-pillar collision falls out
for free. An actor against terrain, against ladders, against painted triggers
and against another actor is one protocol throughout.

What lives here is the shared half: the bit layout, the participant, the
pairwise question, and the contract a pillar joins by implementing four
members. **The masks themselves belong to the pillars**, because how a plane
comes to be filled is nothing the other pillars can use. [layers.md](layers.md)
owns the `Layer` base and the frame those fills happen in; this page owns what
they fill and what reads it.

---

## Limits and costs

### The layout

| | |
|---|---|
| a plane | `(360, 20)` uint32 — one bit per pixel, 28.1KB, permanently cache-resident |
| bit address | pixel `x` at bit `x & 31` of word `x >> 5`, little bit-order, row 0 = top |
| the word | `uint32`, fixed. 640 is exactly 20 of them, so there is **no partial tail word** |
| planes per layer | one silhouette, plus whatever named planes the art authored |
| coordinates | screen pixels, `(0, 0)` top-left, half-open in both axes |

### Costs

Measured 2026-09-07 on a Ryzen 7 5700X3D, warm-up off the metric ring, on a
miss so no early-out flatters the figures.

| | |
|---|---|
| a 16x16 box question | **1.313µs** — 0.470µs of it in the kernel |
| the same, either side offset | 0.469µs / 0.474µs in the kernel against 0.481µs at zero — **offsets are free** |
| a whole-screen question | 8.65µs |
| resolving both masks | 0.159µs |
| the collidable gate | 0.066µs |
| console packing, all 30 cell rows, all five planes | 322µs, at the commit |
| console packing, one dirty cell row | 36µs |
| console plane read | 0.21µs — an array index |
| tilemap full repaint, collidable | 3.61ms |
| tilemap gutter column on a coarse step | 0.175ms p50 |
| sprite population, 25,000 members | 3.14ms, flattening to ~0.1µs a member |

**The Python is the larger half of a question, and it is not the mask
lookup.** Of 1.313µs, the kernel is 0.470 and resolving both planes is 0.159;
the rest is the two offset reads (0.222), the gate (0.066), and the call and
clip arithmetic. Resolving per question is what makes a query prepared at
setup still correct at frame 400, and it is nearly free.

### What refuses

The theme is that **nothing here answers `False`**. A `False` from a layer
that is out of collision is indistinguishable from "these two do not touch",
which is how a level ships with its hazards silently inert — working code
forever, and nothing to search for.

| | |
|---|---|
| a question involving a non-collidable layer | `ValueError`, at both doors |
| `collidable = True` on art that cannot derive a mask | `ValueError` naming why it is unbuildable, not merely off |
| `collidable` set to a non-bool | `ValueError` |
| `collider()` on a layer that is not collidable | `ValueError` |
| `collider(name)` with an unknown plane | `KeyError`, listing what the layer does author |
| `collision_plane` on a layer that implements none | `NotImplementedError` |
| a box that has walked off the screen | **not a refusal** — clipped, and answers `False` |

The two refusals for a non-collidable layer read differently on purpose,
because the fixes differ: art that can never collide wants authoring
differently, and a switch you turned off wants turning back on.

---

## Drawn and solid are one predicate

Palette index 0 is transparent, enforced by `Palette` at construction and at
write time. It is therefore both the alpha key and the collision key, and
since 2026-09-02 that is true of the whole pipeline rather than just the
table: `indexed.frag` **discards** index 0 rather than blending it away, so a
pixel is drawn or it is not.

**"Was it drawn" and "is it solid" are one predicate**, not two that agree. A
translucent pixel is the case that would have forced them apart — visible, but
how solid? — and there is no such pixel to ask about.

That is what makes the default plane trustworthy with no configuration: it is
the silhouette of everything solid in the layer, derived from the pixels
themselves rather than from a description of them that could drift.

**Named planes are the exception, and deliberately.** A hurtbox is authored
geometry and is never painted. The console makes the point sharpest: a glyph
plane whose pen slot holds palette index 0 draws nothing and collides all the
same, which is how invisible authoring marks fall out of a font. The predicate
is a statement about the *default silhouette*; a named plane is the art saying
something the pixels do not.

## One bit per pixel

The layout is exactly what `np.packbits(bitorder="little")` produces once the
result is viewed as `uint32` on a little-endian machine — which is every
platform BPS targets. So packing is a library call rather than a loop, and
unpacking for a debug viewer is its inverse rather than a second piece of
arithmetic that has to agree with the first.

**640 is why the word is a DWORD.** The screen is exactly 20 `uint32` words
wide, so there is no partial tail word and no unused high bits able to leak
junk into an `.any()` — a whole row is meaningful to its last bit. A source up
to 32px wide straddles at most two words, so landing one at an unaligned x is
two stores and not a loop. And a pattern shifted left by 31 still fits the
`uint64` staging arithmetic, which a `uint64` word size would not.

The size is a constant in one file rather than a literal spelled across three
pillars. An arch-dependent word is a knob nobody has needed.

**Patterns stage in `uint64` and stores hold `uint32`**, which is two moments
rather than an inconsistency. A row pattern is shifted left by up to 31 places
to land at an unaligned x, and the bits pushed past bit 31 are not overflow —
they are the carry into the next word. Staging wide is what makes that carry
recoverable. So `pack` hands back `uint64` for something about to move, and
`pack_store` hands back `uint32` for something that already sits where it
belongs; a screen-space plane is never shifted, and paying the wide type for
one would double the bytes to buy a carry with nowhere to go. The packing is
identical either way, which is why one is written as the other plus a cast.

`unpack` goes back to `(height, width)` bool. It allocates, and it is for
debug viewers and tests only — the packed form is the one the engine asks
questions of, and a bool plane is eight times the bytes to stream for the same
answer.

## From art to a plane

Solidity starts in source space as `PixelBuffer.mask`: a lazy, cached
`(h, w)` bool array, `pixels != 0`. It rebuilds only when the pixels change,
on its own dirty flag — the GPU texture and the mask are cleared by different
readers at different times, and one reader's refresh must not hide staleness
from the other. Invalidation is by identity and costs nothing: a repainted
buffer mints a *new* array, so `mask is not source` detects it in one compare.

Between that and a screen plane sits a per-source cache of packed patterns,
keyed by the orientation a placement gives a tile. That cache belongs to the
atlas rather than to collision — an orientation is a property of a placement,
and it takes a rect and a turn together to answer, which only the atlas holds.
**See atlas.md**; what matters here is that a pillar arrives holding packed
row patterns, ready to be stamped.

## Every pillar fills a plane, and no two do it the same way

| pillar | | how | cost shape |
|---|---|---|---|
| tilemap | **retained** | stamps a tile's bits as it places the quad; a coarse scroll slides whole words and rows, a fine one writes nothing | episodic |
| sprite | **rebuilt** | cleared and refilled, grouped by pose, one batched scatter a group | continuous |
| console | **packed** | packs the cell rows a write marked, all five planes together | proportional to what changed |

**They diverge because retention does.** Kris, 2026-09-05: *"tiles are
retained, sprites aren't which is why we have different optimisation paths."*
A tilemap's placements persist across frames, so a coarse camera step culls
what left and stamps the column that arrived, and every real cost is episodic
— a boundary crossing, a repaint at level load. A sprite population is rebuilt
every frame, because a sprite is at a coordinate rather than in a grid and no
scroll trick is available to it; its cost is continuous, watched, and scales
with the roster.

So the same per-stamp constant means different things on the two sides. For
sprites it is a per-frame bill, which is why that pillar batches; for tiles it
is an episodic one, which the compiled scatter mostly answers at N=1 without
batching at all. The mechanism for each is that pillar's chapter. What is
shared is the layout above and the question below.

**A tile's bits are stamped when its quad is**, which is the property that
makes the mask and the picture provably the same shape: one loop writes both,
so they cannot end up a pixel apart. Replacing a cell is the one path that
takes solidity away — a mask accumulates with OR, so the rect is zeroed first
and every placement still intersecting it is restamped.

**Participation is three separate questions**, and collapsing them is the bug.
A sheet is palettised, so a layer *can* collide; `collidable` switches the
layer; and on a sprite, a per-presentation `collide` bit decides each member.
`visible` is never consulted at any of those levels — an invisible
presentation still occupies space.

## The offset, and why a fine scroll moves nothing

`collision_offset` says where screen `(0, 0)` sits inside a layer's planes.

**A sprite and a console report `(0, 0)` and mean it**: their planes *are* the
screen. A sprite is at a coordinate and a console is screen space, so neither
has anywhere else to be. Only a tilemap does — it has a camera over a world
larger than the screen, so its planes are stored with a border, and it reports
that border plus whatever sub-word remainder its camera has not moved.

**One number carries both** displacements, because they are the same kind of
thing: a constant offset between where a bit is stored and where it appears.
Splitting them would mean two things to keep in step.

It exists so a mask never has to move for fine scrolling. Reading a plane
through an offset is free — 0.469µs against 0.481µs at zero, inside noise —
where shifting the plane instead would cost tens of microseconds per layer per
frame. So a camera that moved less than a cell writes **nothing at all**:
content appearing to move is the same thing as the screen looking somewhere
else. The identical number reaches the GPU as one vertex attribute from one
statement, so the CPU and the GPU look at the same offset into the same
content and the mask cannot end up a pixel off the picture.

## Asking

**`collider()` mints a handle**, and it is what a question is asked *of*:

    chunli.collides_with(ladder)
    chunli.collides_with(ladder.collider())          # the same question
    chunli.collider("attack").collides_with(ladder)
    chunli.collider("attack").collides_with(ryu.collider("body_hurt"))

The first is sugar for the second — `collides_with` on a bare layer means its
default plane — so a game that never names a plane never meets `Collider`.
That is the heritage path and not a fallback: pixel-perfect with no
configuration at all.

**Selecting a plane is a property of the participant, not an argument to the
question.** So `collides_with` takes one argument however rich either side is,
and there is nothing to widen when a pillar grows named planes. The sugar is
spelled in one place, so both seats accept both spellings and neither has its
own idea of what a participant is.

**The word `collider` means a participant here**, not a layer that only
collides. A shadow collider — the knee-high wall, the ladders a map author
paints for the engine rather than the player — is a *layer*; `layer.collider()`
is a *handle* onto one plane of one. Both senses stay, so the distinction is
worth keeping in view.

**A handle resolves its mask on every read and caches nothing.** Hold one for
the life of a scene: the planes underneath are rewritten as the world moves,
so a handle that remembered an array would answer about a frame that has gone.
What it remembers is which layer and which plane, which are the two things
that do not change.

    query = player.collides_with(ladders)   # prepared, computes nothing
    query.at(x, y, w, h)                    # -> bool, screen-space box
    query.at()                              # -> bool, whole screen

**The answer is a bool and stays a bool.** Detail is expressed by asking a
narrower question — a smaller window, a named plane on either side — never by
a richer answer. What an overlap means is a game's decision, and an engine
returning a contact point or a penetration depth would be an engine with
opinions about physics it does not have. At 1.3µs a box, a fighting-game
roster of a hundred boxes costs 130µs a frame, so the way to learn more is to
ask again.

**Screen space is a semantic, not an implementation detail.** "Collides" means
*visually coincident on screen*, so two layers scrolling at different parallax
rates collide where they **appear** to overlap — which is what a player sees
and therefore what a player will expect.

**A box that has walked off the screen is clipped, not refused.** A thing
off-screen has not stopped existing, it has stopped being able to touch
anything, so it answers `False`. The kernel reads out-of-range as empty, so
clipping is not what makes that safe — it is what stops a preposterous box
being slow: without it, `at(-1_000_000, 0, 2_000_000, 10)` walks 31,250 empty
chunks to reach the same answer.

**Draw state is not consulted, on purpose.** A disabled layer keeps answering.
What is drawn is a view decision; what is solid is a world one, and conflating
them would make every fade, blink and cutscene a gameplay event. To take a
layer's contents out of play, clear their collide bits rather than hiding the
layer.

### Joining the protocol

A pillar implements four members and inherits everything above:

| | |
|---|---|
| `_collision_refusal()` | why this layer may never be collidable, or `None` if it may |
| `planes` | the named planes the art authored; empty is the common case |
| `collision_plane(name)` | one packed screen-space plane |
| `collision_offset` | where screen `(0, 0)` sits inside them — `(0, 0)` unless the pillar has a camera |

The bare base supplies none of them, so an ordinary layer refuses the question
rather than answering `False` about a mask it never had.

**Plane keys are opaque, including their type.** A sprite sheet authors
strings — `attack`, `body_hurt`; a console authors the four glyph plane
numbers, because a font has no names to give. A key is anything hashable and
the engine reserves no spelling of one. Damage, push, throws and reactions
stay game-side: an engine with opinions about them would be a fighting-game
engine rather than one you could write a platformer in.

## When a question is answered

**Every plane is settled at the commit**, on every attached layer, through
`Layer._settle_collision` — so a query answers about the frame that was
painted rather than one composed to answer it. A collision is a fact about a
frame that really existed.

That makes a question **order-independent inside a tick**: move a sprite and
then ask, or ask and then move, and the answer is the same. The alternative —
resolving live — sounds more helpful and is not, because it makes the answer
depend on where in the tick it was asked and describes a frame nobody has
seen.

It also puts the cost on the right side of the accounting line. Building a
plane lazily on the first question gives the same answer and **bills it to the
game**, which is exactly what `prepare` is kept outside `gameloop_ms` to
avoid. A tick that asks spends nothing building.

The settle is not gated on `enabled`, which is what lets a collider layer
nobody sees keep a plane worth asking about — it scrolls with everything else,
so its geometry has to keep moving while nothing draws it. The mechanism is
[layers.md](layers.md#the-settle-half); what matters here is the guarantee it
buys.

## The kernel

`collide_any` is **the one place in the engine that shifts collision bits**.
Everything else moves whole words and whole rows. Keeping the sub-word
arithmetic to one file is the same rule the draw side follows: two statements
of one transform drift, and they drift invisibly.

It walks the box a word at a time. For each 32-pixel chunk it funnel-shifts
both planes through their own offsets — 32 bits straddling two words, each
half guarded independently so a chunk hanging off either end contributes only
the part that is really there — trims the partial last word so bits past the
box cannot answer for it, and returns on the first hit. If one side's word is
empty it skips reading the other.

### Why this is compiled

**Compiling is the exception in this engine, not the habit.** NumPy is the
tool, and most wins come from giving it a better data shape. This is the only
place in `graphics` that compiles, so the case is written out rather than
assumed. Re-measured 2026-09-05, 16x16 box against `(360, 20)` planes, a miss:

| | |
|---|---|
| plain windowed AND, numpy | 3.02µs |
| the same with per-side offsets, numpy | 5.64µs (+2.62) |
| shifting the whole 29KB mask, numpy | 5.78µs |
| this kernel | 0.48µs |
| this kernel, both sides offset | 0.48µs (+0.00) |

**The speed on its own would not justify it.** Composition already collapses
the bulk case — 25,000 bullets are one plane and one question — so a game asks
tens of box queries a frame, not hundreds. At ten, NumPy costs 0.35% of a
frame against the kernel's 0.03%. That is not a reason to compile anything.

**The reason is the second row against the third.** In NumPy, offsetting per
query costs 2.62µs and normalising the whole mask costs 5.78µs, so normalising
wins after 2.2 questions — and normalising means moving the bits every time
the camera moves, which is the exact property the offset exists to avoid. *The
tool would have chosen the design.* Compiled, an offset costs nothing
measurable and the mask never moves, so the problem chooses it instead. Those
rows are the tile pillar's worst case and not the common one: a sprite against
a sprite offsets neither side.

NumPy also cannot stop early — it computes the whole window and then asks
`.any()`, where this returns on the first set word.

**`stamp_many`'s case is different and stronger: NumPy has no vectorised
accumulating scatter at all.** The data-oriented shape gets most of the way
with no compiler — grouping placements by pose so one cached pattern serves
many positions takes a stamp from 8.51µs to 0.260µs, 32.7x — but it lands on
`np.bitwise_or.at`, which is 95% of what remains. It cannot be avoided:
`flat[idx] |= vals` is *buffered*, so three sprites landing in one word give
`0b100` where the answer is `0b111`. Overlapping sprites are not an edge case;
composing overlaps into one plane is what a silhouette **is**. So `.at` is
compulsory, and `.at` is NumPy's unbuffered path — a scalar read-modify-write
loop through the ufunc protocol at 7.3ns an element against 0.6ns here. Both
are scalar loops by then; NumPy has already fallen off its own fast path.

Neither kernel releases the GIL. Releasing it is worth having where another
thread is waiting — audio has one, synthesising off the frame thread. This
runs inside the game's own tick, on the one thread the 16.67ms budget belongs
to, and there is nothing to let in. The flag would be a claim the code could
not keep.

### Bounds are the kernel's own business

Numba does not bounds-check by default, so an index handed to it wrong reads
whatever is next in memory and returns a **plausible** answer. The first draft
did exactly that and passed 4,000 random cases against a reference before the
error was found — it had been reading zeros. Every row and word access is
therefore guarded in the kernel, out-of-range reads as empty, and the fuzz
runs a second time with `boundscheck=True` so a future edit cannot quietly
reintroduce one.

## What it is not

### Not entities, shapes or physics

The engine produces masks and answers overlap. It does not know what a plane
*means*: `attack`, `body_hurt` and `throwable` are opaque keys the art
supplies. Damage, push, throws and reactions are game-side decisions, and an
engine with opinions about them would be a fighting-game engine rather than
one you could write a platformer in.

### Not a central bitplane stack

An engine-global packed stack — one `uint32` word per pixel, one bit per layer
— is the shape that makes point queries a single load and the footprint one
buffer. Measured costs would be trivial either way, so those wins buy nothing,
while the stack costs bit allocation and reclaim registry state and carries a
real footgun: repurposing a layer changes its meaning, and a shared buffer
holds stale stamps under a live bit.

Layer-local masks reset with their layer, hold no global state, and keep the
hot path cheaper — plain stores into your own words, rather than
read-modify-write on words shared with 31 other layers. Only the masks
actually in use occupy cache, which is the arrangement a floor-hardware cache
budget wants.

### Not a restamp

There is no general rebuild. A tileset is immutable at runtime, so the case a
restamp existed for — repainting a sheet under a live map — is forbidden
rather than rare. Destruction that replaces a cell restamps that cell;
destruction that cuts a hole in the ground is the picture layer's job, not a
tilemap's. The one rebuild left runs when `collidable` is switched on over a
map that was already drawing, and it reads one bordered screenful.

It does not run when a map repaints, and a map's first compose is a repaint.
A tile's bits go in with its quad, so painting a whole window leaves the plane
already complete — the rebuild is for the case where the placements were
already there and only the switch moved. Running it after a repaint is the
same walk twice, and the second walk is dearer than the first: over the same
1527 placements, 6.6ms rebuilding against 3.7ms stamping them with their
quads, because reading the placement SoA row by row costs more than the
refill's road to the same bits.

### Not an allocator

Nothing in the steady frame path allocates. Mask shifts are `out=` ufunc
strokes and in-place operations on permanently allocated buffers, and coarse
culling uses permanent scratch. Measured on the shift, the allocating
`np.roll` formulation is 5.4x slower than the three-stroke in-place version —
the cost is allocation and cold pages, not the copy. Fresh allocations are how
working sets fall out of cache, and the budget is sized for floor hardware
rather than a 96MB-L3 dev box where nothing ever spills.

## Still open

- **Batching the tile stamp.** The remaining ~2.2µs a stamp is Python call
  overhead into the kernel plus a per-placement pattern lookup, and only
  batching removes it. Grouping by `(tile, orientation)` reaches the existing
  scatter with no new kernel — measured on real Sonic layers, a full repaint
  groups down 5-8x and a repeating sky 79x. Not urgent: the case that is
  *watched*, a gutter column during continuous scrolling, is 1% of a frame,
  and the expensive case fires at level load where the whole screen changes
  and there is no frame of reference to see a stall against.
- **Shadow collider layers still build GL state on `blit()`.** A mask-only
  path that skips the shader and the batch is wanted for colliders
  constructed before a window exists.
- **No arbitrary unstamping.** Departed content is removed at coarse camera
  boundaries and a `blit` clears the one rect it replaces; there is no way to
  remove a shape that is not a placement's own cell.
- **Rectangle-authored planes are not settled.** A plane's runtime
  representation is implementation, chosen behind the boolean, and an
  AABB-settled plane meeting a pixel plane in the cheapest windowed test is
  designed and unbuilt.
