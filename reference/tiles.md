# Tiles

A `TileMap` is a grid of cells indexing one sheet, with a camera onto a world
larger than the screen — and it is a `Layer`, so the thing you stamp into is
the thing you place in the draw order.

    island = TileMap(Atlas(PixelBuffer("island.png"), 16, 16), 256, 32)
    bps.layers.add(island)
    island.cell[3, 5] = island.tile[42]
    island.scroll = (x, 0)              # and it streams

Two faces onto it, and both hand back things the map owns:

    island.tile[42]     one cell of the SHEET  -> a Tile
    island.cell[x, y]   one cell of the WORLD  -> (Tile, Orientation)

Everything else follows from one contract: **one TileMap, one sheet, and the
sheet cannot be changed.** That is not a restriction being tolerated, it is
the assumption `TileBatch` is built around — one texture means one bind and
one `glDrawElements` for the whole layer at any quad count. Wanting two sheets
on screen means wanting two TileMaps, which is also how you get two collision
identities and two places in the draw order. Sharing the art between them is
free: `PixelBuffer` interns by path, so two maps over one tileset genuinely
hold one texture. **Identity is not art**, and it is identity a layer is the
unit of.

The boundary against the other pillars is the editor's: if content would be
painted into a Tiled tile layer, it belongs here. Anything independently
moving is a sprite.

## Limits and costs

### Geometry

| | |
|---|---|
| world size | any `cols` x `rows`, both at least 1. A one-column world is legitimate — it is the whole of a repeating sky |
| cell size | the sheet's, and rectangular cells are allowed. A world's cell size IS its sheet's cell size |
| what is retained | the viewport rounded OUTWARD to whole cells, plus one cell of border on every side |
| at 640x360, a 16px cell | the retained plane reaches 384 rows — twenty-four past the screen, not sixteen |
| an empty cell | `EMPTY_TILE`, which is `-1`. `cell` spells it `None` |
| coordinates | `cell[x, y]` is x-first, and a world is stored x-major, so nothing is transposed on any road |

### Memory

One layer over a real level, `Sonic_md_fg1` at an 8px cell, 480x34 cells:

| | |
|---|---|
| the palette bank | 256.0KB |
| the vertex mirror | 143.2KB |
| placements and their cull scratch | 77.5KB |
| the world | 79.7KB |
| the collision plane and its apron | 76.4KB |
| the sheet's rect table and pattern cache | 52.1KB |
| **total** | **685KB**, or 655KB with collision off |

**The bank is the largest single item and it is a fixed 256KB**, because a
`PaletteBank` is always 256 palettes of 256 colours — see
[palette.md](palette.md). A map mints its own unless one is assigned to it,
so layers that should recolour together want the same bank handed to each
rather than one apiece.

A placement is 13 bytes — where it sits, which cell of the sheet, how it is
turned. The two collision planes are 41KB each and are allocated
unconditionally, including for a sheet that can never collide: making that
case save the bytes would put an `Optional` back through every stamp, every
cull and both reads, to buy 82KB from the one map that cannot use them.

### Costs

Measured on a Ryzen 7 5700X3D with `util/tile_frame_cost.py`, p50 of 300
single-call samples, against the three Green Hill Zone layers.

| | |
|---|---|
| the camera did not move | 1.7µs — four divisions and a compare |
| it moved within a cell | 2.1µs, and no buffer is written at all |
| it crossed a cell boundary | 114–124µs, or 162–190µs collidable |
| the position upload | 29–38µs, one `glBufferSubData` for the whole live range |
| the draw | 85–100µs, one bind and one `glDrawElements` |
| a boundary-crossing frame, end to end | 236–300µs |
| a whole-window repaint | 157–317µs, or 1.6–4.2ms collidable |
| the first compose, cold | 0.6–0.7ms, or 1.8–7.0ms collidable |

**A fine scroll costs 0.4µs more than not moving at all.** The coarse/fine
split is not an optimisation that mostly works; between cell boundaries the
layer does arithmetic and nothing else.

**A drawn layer costs about 35µs a frame and that is FLAT in what it holds** —
one placement and a level's window of 1527 measure 0.9% apart, because the
draw is one bind and one `glDrawElements` at any quad count. Sixty-four drawn
tile layers are 2.40ms, 14.4% of a frame; the same layer with `enabled` off is
1.7µs. So counting placements is the wrong way to budget a tile layer, and
"wanting two sheets means wanting two TileMaps" is affordable rather than a
consolation.

**Collision is the pillar's dominant cost wherever it is switched on**: 92% of
a repaint, and about a third of a coarse step. It is worth 2.4µs per placement
in a rebuild, against 0.2µs without.

**A whole-plane rebuild is the one large number, and three things cause it**:
the first compose, `origin` or `clear`, and `collidable` switched back on. It
is one frame of 3.6ms — 21.7% of the budget — so it drops a frame only if the
rest of that frame already exceeds 13.07ms. **A game moving the VIEW wants
`scroll`, not `origin`**: 2µs against 3600.

Level load is the largest figure here and most of a scene's is not stamping.
The three-layer scene's first compose is 15.11ms cold; the same maps over art
whose patterns are already packed are 1.7/4.3/3.7ms. Two maps sharing one
`Atlas` share its pattern cache and two that each cut their own from the same
picture do not, and the interning is weak — so **hold the atlas, not just the
art**, if several layers come off one sheet.

### What refuses

| | |
|---|---|
| a sheet that is not an `Atlas` | `TypeError`, naming `Atlas(PixelBuffer(path), w, h)` |
| an atlas given its rects outright | `ValueError` — a world cannot lie over rects with no common size; a sprite is the pillar for those |
| a world with no cells | `ValueError` |
| a `Tile` from another map | `ValueError`. The check is one `is` |
| a raw integer where a `Tile` belongs | `TypeError`, and `EMPTY_TILE` gets its own sentence — it is the array's word, not the accessor's |
| an id outside the sheet, on the array road | `IndexError`, vectorised over the whole write |
| a diagonal turn on a rectangular cell | `ValueError` — a quarter turn either overflows the neighbouring cells or squeezes the pixels out of square |
| a write past an edge, on any axis | `IndexError`, wrapped or not |
| a stamp outside the retained plane | `ValueError`, pointing at `scroll` rather than at stamping the world |
| `collidable` on an RGBA sheet | `ValueError` saying it is unbuildable, not switched off |

---

## A world, and `cell` is the only road in

A map makes its own world from the size you give it, all empty, and
`cell[x, y]` is how anything reaches it afterwards — reading, writing, one
cell or a whole block in one NumPy write.

    level.cell[3, 5]   = level.tile[7]                    # one cell
    level.cell[3, 5]   = (level.tile[7], Orientation.FLIP_H)
    level.cell[3, 5]   = None                             # empty it
    level.cell[3:9, 4] = wall                             # a run, one write
    level.cell[a:b, c] = level.cell[d:e, f]               # round-trips

**There is no public array, and one could not be made to work.** A NumPy write
cannot be observed, so a cell edited while it was on screen would keep the
quad and the mask bits it was stamped with, and correct itself only when the
camera wandered far enough to cull it and came back. Nor could a proxy catch
it: `map["tile"][x, y] = 7` is two operations and only the first is the map's.
So the array is private, `PLANE_DTYPE` is internal — a layout nobody outside
can name is one that can change — and `EMPTY_TILE` is the single word that
goes out, because a caller filling a buffer taken off `cell[:, :]` needs to
say "nothing here" and `np.zeros` says tile 0, which is a real tile.

**A block read is a copy, not a view**, and that is load-bearing rather than
cautious: a view would put the world back in public hands through the side
door, where `cell[0:10, 0:10]["tile"] = 5` reaches it with nothing noticing.
The copy is also the buffer to fill and write back, which is why this pillar
needs no `empty_map` — a fresh map hands back a correctly shaped, correctly
typed, all-`EMPTY_TILE` array.

It is also why slices exist here at all. A scalar read pays about 402ns of
NumPy scalar extraction, of which the returned domain object is some 5%; a
block read costs about 1µs fixed and then nothing, so copying 4096 cells costs
less than five scalar reads. **No per-cell API can be made competitive with a
block**, so the answer to "I have a lot of cells" is a block, not a faster
scalar.

**Reads wrap on a wrapped axis; writes never do.** Reading past the edge is
what a camera does when a world loops. Writing past it is a cell being named
and arithmetic being got instead, so it raises on every axis.

**The scalar road checks ownership and the array road checks range**, and the
asymmetry is deliberate. A `Tile` knows which map minted it, so one from
another map is refused — it is an index into art this map has never seen. A
raw array carries no `Tile`, so all that can be checked is that its ids exist
on this sheet. The difference is which failure is worth moving. An id out of
range is a certain `IndexError` later, inside a refill, whenever the camera
next brings that cell into view, with nothing in the traceback naming the line
that wrote it; the check does not add a failure, it moves one back to its
cause. A valid id from the wrong sheet is only ever a wrong picture, on the
frame it happens, in front of whoever just typed it. Kris, 2026-09-05:
*"seeing poo all over the screen is fine; you can visually see you did
something wrong — it's better than the engine exploding because it's trying to
draw a non existent tile later."*

**Orientation round-trips.** A read hands back `(Tile, Orientation)` and a
write takes it, so `cell[a, b] = cell[c, d]` preserves a flip with no special
form. A console's `cell` deliberately drops the facing, and both rules are
right: a console's facing comes from the pen at the moment of writing, while a
tile's orientation is a property of the cell, sitting beside the tile id.
Different owners, different round-trip rules.

**A world is not a game's world.** This one is a rendering and collision
format — an id and three orientation bits, shaped for a gather into quads and
a stamp into a mask. A game's own map is likely to be something else: hex
axial coordinates, a graph to run A* over, cells holding entities. The
expected shape is that a game keeps its own and bridges into this through
`cell`, rather than that the two are one array.

## The map holds a window

A world is any size. What the map holds on the GPU is the viewport plus one
cell of border, and no more.

So **you never stamp a level.** Set `origin` to say where the world sits, set
`scroll` to say where you are looking, and cells appear as they come into
view. Writing a cell that is currently on screen changes what is drawn on the
next frame; writing one that is not changes the world alone, and the refill
picks it up when the camera arrives.

The border is what makes scrolling cheap: the row about to come on screen is
already stamped, waiting just outside. Its extent is `bounded_plane`, the
viewport rounded outward to whole cells plus one cell on every side, and it
lives in one place because two things read it and must not each work it out —
the map refuses a stamp outside it, and `CollisionMask` sizes its apron to
hold it. Those two agreeing is not optional: an apron short of the plane drops
border content **silently**.

**Nothing can be put in the plane that the world does not contain.** A tile
layer draws its world and nothing else. For a decoration at an arbitrary
pixel, a sprite with one part is the road, and it is not culled.

This is Commander Keen's arrangement rather than AMOS's `SCROLL`: pan the view
and redraw the newly exposed edge, instead of blitter-copying the screen and
blanking the gap. The deciding fact is that there IS a world behind the screen
here, so a destructive copy would be strictly more work for the same picture.
`Console.scroll` is the other lineage and means the other thing, for the same
reason in reverse — a console's grid IS the screen, with nothing behind it to
look at.

## Stamping a cell

A placement is where it sits, which cell of the sheet, and how it is turned.
It carries no source rectangle and no size, and neither is an omission: the
map has one sheet that cannot change, so a source rectangle is arithmetic from
the index; and a placement's displayed size is always the sheet's cell,
because a rectangular source may not be turned diagonally and turning a square
one leaves its shape alone. Storing either would be storing something that can
disagree with what it was derived from.

**Orientation costs nothing per frame.** Flipping and turning is an indexed
reshape of four UV rows, applied once when the quad is placed, so the
per-frame path never sees a flag. That is the mechanical reason `orientation`
states its law the way it does: an orientation belongs to the placement, never
to the source. The eight-entry permutation table is computed from the
coordinate transforms rather than hand-derived, because the compose order is
exactly the Tiled trap — transpose first, then the mirrors, and getting it
backwards is invisible on a square symmetric tile and wrong on everything
else.

Corner order matches pyglet's Sprite, so a `TextureRegion`'s twelve
tex_coords floats pair up verbatim — but a region is not what is read. A row
of `Atlas.uvs` answers the same question for 0.03µs against 1.66µs, and
building a `TextureRegion` per placement would put a Python object behind
every quad in the one pillar built to have none.

**A write to a visible cell restamps in proportion to what it changed**, and
it does so by rectangle rather than by search. Every placement is a grid cell,
so placements are cell-aligned and disjoint: the rect a cell occupies contains
that cell's placement and no other. So the region's placements are dropped by
rectangle, the mask rect is cleared, and the region is built again from the
world — through the same call a refill makes, so a written cell and a
scrolled-to cell are stamped by one road. An emptied cell needs no case of its
own, because an empty cell is simply not replaced.

Only collision needs the clear. A quad is opaque geometry already sitting
there, so re-pointing it replaces what it drew; a mask accumulates with OR, so
the old bits have nowhere else to go.

A block write passes its **bounding box**, not its exact selection. Restamping
a cell that did not change is the same quad over the same bits: being generous
costs a stamp, being narrow loses one.

A rectangle of the world is stamped as a block rather than a loop —
everything done per cell is uniform across the plane, so it is a gather and
four broadcasts. The bounds test is vectorised with it, and it raises rather
than dropping quietly, because a rejection there means the visible window and
the retained plane have come apart, and that is worth a traceback.

## The mask fills as the quads do

`collision.md` owns the packed layout, the query and the kernel. What is the
tile pillar's is how one gets filled.

**Push, not pull.** The mask is written when content changes and never
rebuilt to answer a question. That is the right way round because the two
directions have wildly different frequencies: content changes when the camera
crosses a cell boundary, and questions are asked several times a frame. The
expensive direction and the frequent direction are different directions.

**A tile's bits are stamped when its quad is**, which is the property that
makes the mask and the picture provably the same shape. So the mask is filled
by the three occasions a tile is drawn at all: the first frame's whole window,
a write to a visible cell, and the gutter a scroll exposes.

The plane is stored larger than it is exported. What comes out is exactly the
640x360 interior; the apron holds real map content waiting to arrive, so a
stamp just off-screen lands in it and destructive scrolling walks it into the
queryable area without anything being restamped. Its size is the retained
plane, plus up to 31 pixels of camera the store has not moved for, plus one
word for a pattern landing unaligned at the outermost position. Sizing it from
the cell instead is a silent bug rather than a loud one: the plane overshoots
the screen by more than a cell at most cell sizes, so stamps past a
cell-sized apron are declined, the border arrives empty, and every query about
it answers False.

A coarse step slides the stored plane in whole words and whole rows, into a
permanent scratch that is then swapped in — two buffers, because a move has no
carry to stage, and nothing is allocated per frame.

A fine step moves no bits at all. `_fine` is one number, and both the vertex
attribute and the query's offset read it, so the picture and the mask cannot
end up a pixel apart. It is latched to the frame that was painted rather than
read live off `scroll`, because a collision is a fact about that frame: read
live, a game that moved the camera and then asked would get a different answer
from one that asked first, and neither would be about a frame that ever
existed.

A hidden layer still streams and still settles its plane. A collider nobody
sees — ladders painted for the engine rather than the player — scrolls with
everything else, so what is drawn is a view decision and what happens in time
is not.

**A rebuild from the placements exists for one case**: `collidable` switched
back on over a map that was already drawing. It is not how a plane is normally
filled, and it deliberately does not run after a repaint, because a repaint
has already stamped the bits with the quads. Over the same 1527 placements the
rebuild costs 6.6ms against 3.7ms for stamping them as they are placed —
reading the placement array row by row is dearer than the road that writes
both together.

## One bind, one draw call

The GPU side is a NumPy structured array and a batch that turns it into quads.
Positions are expanded from it in one vectorised pass and sent as a single
`glBufferSubData` for the whole live range — not one per placement. Texture
rectangles are already on the GPU; they were written when the quad was placed
and stay put until a cull reorders them.

**pyglet's `Sprite` and `Batch` are not used, and the reason is generality
this layer cannot spend.** They perform well for what they promise, and what
they promise costs a Python `EventDispatcher` per quad, a `SpriteGroup` per
sprite hashed for coalescing, a per-sprite vertex-list allocation, six
attribute streams per quad of which most never change, and property →
`set_region` → element-wise ctypes writes on every move. A tilemap needs none
of it: quads are axis-aligned and never rotated or scaled, tint is one
whole-batch register, the pool is fixed, and positions already live in a SoA.

So the render state collapses to a position VBO rewritten in one call when the
bounded local map changes, a tex_coords VBO written at placement time and
flushed once, a fixed index pattern rebuilt only when capacity grows, and
`colors`, `translate`, `scale` and `rotation` fed as **generic** vertex
attributes — set per draw, no buffers. A disabled attribute array reads the
current generic value, which is what lets the shared vertex stage be used
untouched.

**Two programs, because a tileset either is indexed or it is not.** The
indexed one is the mainline: the sheet holds palette indices on unit 0 and the
map's palette bank on unit 1. That table is 256x256, and the fragment stage
reads the row `palette_row` names — which is the bank's `active` register, so
moving it recolours the whole layer at the next draw and rewrites no table.
The bank owns its texture and uploads it at the head of the composite, where
it cannot land mid-pass and disturb the unit the sheet is bound to.

**RGBA is supported on principle**: if the hardware can do it, a game should
be able to reach it, and refusing in order to look more retro would be
affectation rather than design. One constraint rides with it and it is
mechanical rather than policy — an RGBA tilemap has no collision at all.
Solidity is derived from index 0 meaning transparent, and an RGBA sheet stores
colours rather than indices, so there is no index 0 and nothing to derive a
mask from. It is
unbuildable, not switched off, which is why a map asked for both refuses at
construction rather than quietly carrying an always-empty plane. That trade
costs nothing anyone wanted: RGBA is what a game reaches for on fog, cloud
translucency and high-colour backdrops, which is scenery, and nothing asks
scenery a collision question.

Touching the sheet's texture re-uploads it only if the pixels were edited, and
the id is stable, so nothing downstream rebuilds — which is what makes a
writable sheet cost nothing on the frames it did not change.

The compositor has already bound the framebuffer and set a 1:1 ortho, so this
sets no projection and no viewport. The present pass owns the flip that makes
a quad placed at y=0 appear at the top of the screen. Both programs are
built once per process and lazily, because compiling needs a current GL
context and importing must not: a map can be constructed before there is a
window and only drawn after there is one.

## Then the camera moves

Setting `scroll` stores two floats and stamps nothing. The next frame works
out what that means, once, from wherever the camera ended up — so a game that
nudges the camera twice in a tick pays for one reconciliation rather than two.
Fractional values are welcome, since a game accumulating `speed * dt` produces
them, but the view lands on a whole pixel, because a pixel engine that sampled
between them would not be one.

The reconciliation splits the camera in two, and the halves are different
jobs. The **coarse** part has moved the world under the placements, so they
are translated, the ones that left the retained plane are culled, and whatever
cells that exposed are stamped. The **fine** part reaches the GPU as one
generic vertex attribute and moves nothing — negated, because the view moving
right moves the content left.

That is why panning between cell boundaries costs the same whether the layer
holds ten quads or ten thousand.

A cull compacts the survivors into a permanent scratch and swaps the two, so
it allocates nothing however often the camera crosses a boundary, and the
batch performs the identical `take` over the texture rectangles — which is
what keeps a placement and its quad at the same index. The position upload is
marked stale unconditionally afterwards, because even a cull that dropped
nothing moved every surviving corner.

The refill then stamps only what arrived: the entering columns in full, then
the entering rows over only the columns that were already there, so the corner
where the two meet is not stamped twice. A doubled stamp is invisible but not
free — it is a placement, a quad, and a slot the next cull has to consider.

**`origin` and `scroll` are different properties and mean different things.**
The camera is where you are looking; the origin is where the world IS. A map
smaller than the screen is placed with `origin` rather than by parking the
camera at a negative scroll, which would say the game is looking somewhere
outside its own world. Changing it drops every placement and repaints, because
every stamp was positioned relative to the old value — rare by nature, since a
world is put somewhere once and then looked at.

Parallax is the game's, not the engine's: it is more layers, scrolled at
different rates.

### Worlds with no edges

`wrap` says whether the world repeats when the camera runs off it, per axis.

    sky.wrap   = (True, False)      # a strip that goes on forever
    level.wrap = (True, False)      # and a level that loops

Off by default on both axes, because a world that silently repeats when you
scroll past its end is a surprise, and running out of world is a thing a game
may legitimately want to detect.

**Per axis, because the useful cases need them different.** Civilization's map
wraps east to west and not north to south: the globe joins up along one axis
and the poles are a genuine edge. A side scroller is the same shape — the
level loops while the sky wrapping round into the ground is not a level but a
bug you can see. One flag for both would make both unreachable.

A wrapped axis costs nothing to have on. The world is not copied, tiled or
grown: the visible window simply stops being clamped, and cells are read
modulo the world's size. So a one-column strip is a complete repeating sky,
and covering a 640px screen with it is the same work as covering it with
anything else. The same canonical cells can appear more than once on screen,
and a world narrower than the viewport appears many times — which is the
normal case here rather than the exotic one.

The alternative a caller reaches for otherwise is `np.tile`, and it is worse
three ways: it allocates N copies of the art's indices, it only extends as far
as it was tiled, and it bakes the seam in at build time so the world can never
be edited as one thing again.

Content that leaves the plane is gone, and reversing the camera restamps it
from the world rather than recovering it.

## Two roads in, and neither is privileged

A world comes from a game writing through `cell`, or out of a Tiled file.

    island = TiledFile("island.tmx").tilemap("blue_island")
    bps.layers.add(island)

Tiled is the editor and TMX the file it writes, and a TMX file is a factory:
one map per layer, minted with its own camera and its own writable world,
while the file itself stays a cached, immutable asset. **BPS is not a second
Tiled.** It accepts the subset that maps exactly onto `TileMap` and refuses
the rest by name, at load, where the file is still the thing you can fix.

Nothing here is a capability the hand-built road lacks; it is one way of
filling in the same cells. The three orientation bits are the same three
`Orientation` spells in the same composition order, deliberately, so reading
them is a mask and a shift rather than a translation. And nothing in it
touches GL — it reads XML, decodes indices and cuts an `Atlas`, all of which
work without a window.

A world arriving from a file is validated once, at that door. Everything
checked is cheap and vectorised, and every failure is one a later refill would
otherwise report as a crash or a wrong picture, far from the line that caused
it.

The format itself — the accepted profile, the encodings, the global-id
resolution and every refusal by name — is [tmx.md](tmx.md). It is a file
format rather than part of this pillar, and it is documented as one.

## The sheet

A tilemap draws from an `Atlas` — see [atlas.md](atlas.md), which owns the
rect table, the keying, and the packed oriented-pattern cache that collision
reads. What this pillar requires of one is short:

- **It must be an autogrid.** A world's cell size IS its sheet's cell size —
  the camera, the bounds and every placement are measured in it — so a world
  cannot be laid over rects that have no common size.
- **Solidity comes from palette index 0**, which is what makes an indexed
  sheet a precondition for collision rather than a preference.
- **A sheet is immutable at runtime**, so its packed patterns are computed
  once and stay correct for as long as the atlas lives.

Destruction is a cell being replaced, not a sheet being cut. Dirt becomes
rubble becomes nothing — Dig Dug, Bomberman, and every breakable door there
has ever been. A tile sheet cannot host a crater even privately, because its
tiles are shared by every cell that names them, so digging into the dirt tile
digs the same hole into every dirt tile on screen. Carving an arbitrary shape
out of one image is what `PixelBuffer.copy()` and a picture layer are for.

## What it is not

**Not a sprite pool.** Placements cannot be moved, hidden or re-dressed
individually. They are anonymous by construction, which is what destructive
culling requires, and re-dressing one is writing the cell it came from.

**Not a bound resource.** There is no `bind()`, no `unbind()`, and — more to
the point — no "unbound" state for any method here to know about. The sheet is
a constructor argument, assigned once.

**Not a public array.** The world is reached through `cell` and nothing else,
for the reason that section gives: a raw array cannot be observed, and a proxy
could not catch a two-step write either.

**Not a general rebuild.** A tileset is immutable at runtime, so repainting a
sheet under a live map — the case a wholesale restamp would exist for — is
forbidden rather than rare.

## Still open

**The tint and fade register.** Colour SCHEME is settled — `map.palette` is a
`PaletteBank` and `palette.active` picks the row, which is how a layer changes
scheme. What is still unreached is the other register: the batch takes a
whole-layer `colors` multiplier and both fragment stages multiply by it, so a
tint or a fade is plumbed to the GPU with nothing on `TileMap` passing one.
That is a multiply over whatever the palette resolved to, so it does not
duplicate the bank — a flash-to-white and a colour scheme are different
mechanisms. Where it should live is on the board as *Decide where the colour
registers live*.

**Whether `Layers.add` returns its argument or the layer** is contradicted
across three texts and is not this chapter's decision to take. `layers.md`
records it the same way.
