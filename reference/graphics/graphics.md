# Graphics

Everything that reaches the screen, and the one bitmap that answers whether
things touched.

    bps.layers.add(island)      a world of cells, with a camera
    bps.layers.add(chun)        a thing that moves at its own coordinate
    bps.layers.add(hud)         glyphs on a fixed lattice
    bps.layers.add(walls)       clipped projective textures in screen space

## The shape

**A layer is one texture, one draw call, and one answerable collision
identity.** Those are three views of one fact — the GPU's, the compositor's
and the game's — and almost everything below is that premise seen from
somewhere.

    ┌─────────────────────────────────────────────────────────┐
    │  bps.layers          the draw order, back to front       │
    └─────────────────────────────────────────────────────────┘
         │              │               │
    ┌────┴────┐   ┌─────┴─────┐   ┌─────┴─────┐
    │ Console │   │  TileMap  │   │  Sprite   │      four pillars,
    │ glyphs  │   │  a world  │   │  a thing  │      each IS a Layer
    └────┬────┘   └─────┬─────┘   └─────┬─────┘
         │              │               │
         ├──────────────┼───────────────┤
         │              │               │
    ┌────┴──────┐  ┌────┴──────┐   ┌────┴──────────┐
    │   Atlas   │  │PaletteBank│   │ packed planes │
    │ the rects │  │ the colour│   │  1 bit a pixel│
    └───────────┘  └───────────┘   └───────────────┘

    The quad pillar is the fourth: a Layer and Atlas consumer outside
    the packed-plane branch.

**Four pillars, and the boundary between them is what the content IS**, not
how it is drawn. Content painted into a grid is tiles. Glyphs on a fixed
lattice are the console. Anything moving at its own coordinate is a sprite.
Final projective texture mappings and their screen-space clips in painter
order are quads. Each collection is a `Layer` in its own right, so there is no
container to reach through and nothing to keep in step with a parallel list.

**They are made of the same two things.** Art is an `Atlas` — one image cut
into rects — and colour is a `PaletteBank`, 256 tables the layer picks a row
of. A colour in BPS is an index, never a triple, which is why recolouring is a
palette job at every level and no pillar carries a tint.

**Collision is one bitmap per collidable layer, in screen space**, filled by
whichever pillar owns it and asked the same way whatever filled it. A
tilemap's terrain and a fighter's hurtbox answer one question with one AND.
The quad pillar is presentation only and can never join that question.

## The chapters

### The frame

| | |
|---|---|
| [layers.md](layers.md) | the draw order and the compositor: what `bps.layers` owns, the prepare/commit frame, blend, and where a layer's settle happens |
| [collision.md](collision.md) | one bit per pixel: the packed plane, the query surface, the compiled kernel, and how the three pillars differ in filling one |

### The pillars

| | |
|---|---|
| [console.md](console.md) | the glyph grid: a cell as one number, the variant table, the pen, and what recomposes |
| [tiles.md](tiles.md) | a world of cells over one sheet, with a camera: `cell` as the only road in, the retained window, and the coarse/fine split |
| [sprites.md](sprites.md) | independently moving things: one sprite as one layer and one collision identity, its many drawings of itself, the frame plan and the population curve |
| [quads.md](quads.md) | projective texture mappings and convex screen clips in painter order, with collision structurally absent |

### What the pillars are made of

| | |
|---|---|
| [palette.md](palette.md) | colour as an index: the 256-row bank, the register that picks a row, the modify path, and the pin that keeps drawing and collision agreeing |
| [atlas.md](atlas.md) | one image cut into rects, shared by all four pillars: the autogrid ruling, the rect table, the keying, and what is derived from a rect |

### Reading a file

| | |
|---|---|
| [tmx.md](tmx.md) | a Tiled map: the accepted profile, every refusal by name, and the global-id resolution |
| [sprite-package.md](sprite-package.md) | a `.sprite.json`: the accepted profile, the authoritative palettes, and what a package deliberately does not carry |

**A file format is always its own page.** It is read by someone holding a file
that will not load, who does not care how the pillar streams — and the
accepted profile is the part most likely to grow, so keeping it out stops a
pillar's chapter churning every time an attribute is accepted. Neither road is
privileged: a world comes from a file **or** from a game writing through
`cell`, and a sheet from a package **or** from a bare image read as a grid.

## What is shared, and what only looks shared

**Shared, and deliberately:** art, through `PixelBuffer` interning by path —
two maps over one tileset genuinely hold one texture. Colour, through a bank
handed to several holders. Solidity patterns, which live on the atlas so every
layer over one sheet gets one set. In each case `copy()` is the escape, and
the moment a holder stops sharing is a line in the game's own code.

**Not shared, and the reason is the same each time:** identity. Two things
told apart by a collision question are two layers, because the OR'd bits do
not say whose is whose. That is what makes "spend a layer to gain an
answerable identity" the engine's rule rather than a convention, and it is
what a sprite's `before`/`after` chains are the answer to — twenty-five
thousand bullets that were never separately askable.

## Where the pillars genuinely differ

| | console | tiles | sprites | quads |
|---|---|---|---|---|
| what a frame stages | a screen-sized indexed image, on the CPU | a retained vertex mirror, edited in place | one record per quad, rebuilt | mapping corners, clip polygons and Atlas keys |
| draw calls | one | one | one per material run | one |
| collision | composed from the cells | **retained** and slid with the camera | **composed** and refilled | impossible |
| art | one charset, 256 uniform cells | one sheet, uniform cells | one sheet, arbitrary rects | one Atlas, arbitrary rects |

The collision row is the sharpest of these. A tilemap scrolls a contiguous
grid in lockstep, so it keeps its plane and slides it; a sprite is at a
coordinate and every drawing moves independently, so there is nothing to slide
and the plane is rebuilt. That is the same split the two pillars make on the
draw side, and it is why one has apron arithmetic and the other has none.

## What is CPU-bound, and it is all of it

At 640x360 the rasteriser is doing nothing worth measuring — a quarter of a
million sprites compose within 1% of the same workload parked off-screen, and
the GPU never leaves its idle clocks. **The budget is 16.67ms of one thread,
shared with the game.** Every figure in these chapters is a CPU figure, and
every optimisation argued in them is about staging, uploading and array work
rather than about fill rate.
