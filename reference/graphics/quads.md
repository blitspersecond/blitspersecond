# Quads

An ordered list of clipped, projectively mapped textures in screen space.

    from blitspersecond.common import Rect
    from blitspersecond.graphics import Atlas, PixelBuffer
    from blitspersecond.graphics.quads import Quads

    floor = Quads(Atlas(PixelBuffer("flats.png"), rects))
    bps.layers.add(floor)

    floor.clear()
    floor.add(
        texture="FLOOR4_8",
        corners=((170, 210), (470, 210), (760, 440), (-120, 440)),
        clip=Rect(0, 210, 640, 150),
    )

`Quads` is the quad pillar's layer: the collection is what sits in the draw
order. Its content is a list of final presentation commands. It has no world,
camera, animation, visibility pass or file format. The caller owns those
decisions and submits the resulting screen-space mapping and coverage.

## A mapping quad and a clip polygon

The four `corners` are not merely the edges of two textured triangles. They
define a projective coordinate plane:

    p1 -- p2       (0, 0) -- (1, 0)
     |      |   =     |          |
    p4 -- p3       (0, 1) -- (1, 1)

One repetition of the selected texture maps to that quadrilateral. The
mapping continues beyond all four sides and repeats in both directions. A
rectangle is the ordinary case; a trapezoid gives depth-like convergence;
any convex four-point perspective distortion is valid.

**Four points define a plane here, and that is what makes the primitive
sound.** In 3D they cannot be trusted to: a plane needs three coplanar points,
a fourth is not necessarily coplanar with them, and that is why the triangle is
the primitive everywhere else and quads are avoided. In 2D the objection cannot
arise — every quad is coplanar by definition. A line already determines the
plane; a triangle is a superset of a line, and a quad is a superset of two
triangles. The primitive that is unsafe in a 3D pipeline is the natural one in
screen space, so a command takes four corners rather than a rect and a
transform, and needs no further defence for it.

**The other reason is the art pipeline.** Geometry says a quad is safe here;
the content says it is what the art already is. Everything this engine draws
from is rectangular — an Atlas cuts rects, a `source` crop is a rect, a texture
is a rect — so one quad carries one source rectangle to one destination and
there is nothing to cut up or reassemble. A triangle primitive would push that
division onto every caller, in a shape no tool authors art in, purely to satisfy
a constraint that 2D does not have.

**Winding order carries no meaning, so it is spent on orientation instead.**
There is no front or back in 2D and the engine enables no backface culling
anywhere, which leaves the direction a perimeter runs genuinely free. Both the
mapping corners and clip points may therefore run clockwise or
counterclockwise; what each perimeter must be is convex, non-crossing and free
of collapsed edges. The texture's top-left, top-right, bottom-right and
bottom-left follow `p1` through `p4`, so choosing their order is the whole of a
mirror or a turn and no orientation argument exists on a command.

The other pillars reach that same freedom from the opposite end. A tile or
sprite flips by an indexed reshape of its four UV rows — `Orientation`, applied
once when the quad is placed ([tiles.md](tiles.md)). It is the same
corner-to-texel correspondence being permuted; a cell's position is pinned by
its grid so tiles can only permute the texture side, while a quad's caller is
writing positions already. One freedom, two doors, and no shared machinery
between them.

Coverage is a separate question. With no `clip`, the four mapping corners are
also the coverage polygon, so a basic call still draws one finite quad.
`clip=` may instead be a `Rect` or a sequence of at least three screen points
around a convex perimeter. The GPU fills that polygon and samples the
projective plane there. Thus an infinite floor is finite data:

    clip=Rect(0, 0, 640, 360)

No infinite coordinate is submitted. The viewport rectangle is simply the
part of the infinite mapping that is allowed to draw. **Screen space is what
makes that affordable**: every command is a plane without bounds, but the
output never is, so clipping one to `0,0 – 640,360` throws away only what could
not have been seen. A smaller floor or wall opening can use its actual
projected convex outline instead. Coordinates may lie
outside BPS's fixed `0..640 x 0..360` screen; normal viewport clipping removes
what cannot appear. They may also be fractional, which is a ruling rather than
a tolerance — see [The whole-pixel rule stops at the corner](#the-whole-pixel-rule-stops-at-the-corner).

The clip should stay on the intended side of the mapping's projective
horizon. A fragment exactly on that horizon has no finite texture coordinate
and is discarded.

**The pillar is named for the default, not the general case.** What it draws
is a plane clipped by a convex polygon — which is what the shaders are called,
`plane.vert` and its two fragment stages, and what makes the infinite floor
above possible at all. But the clip defaults to the mapping quad, so the
overwhelmingly common call is four corners drawn where they sit: a quad.
`Planes` would make every reader learn the general case before drawing the
simple one. `Quads` names what a call gives you by default, and `clip=` is how
you ask for the rest of the plane.

## One list, one Atlas, one draw

The Atlas is fixed when the layer is built. Each `texture` names one of its
rects, whether the Atlas was autogridded or given named rects outright. All
live commands therefore share one GPU texture. A commit expands their clip
polygons into ordered triangle fans, uploads the compact position, mapping and
texture-region streams, and issues one indexed draw.

By default the whole selected texture is one tile of the plane.
`source=(x, y, width, height)` chooses integer pixels within that texture as
the tile instead. Its origin is the selected texture's top-left, not the Atlas
image's, and it must fit wholly inside that texture. Repetition wraps inside
the chosen region and cannot leak into its Atlas neighbours.

`add()` records one command and returns no handle. Geometric validation is not
a scalar toll at this door: immediately before submission, one compiled pass
validates every live mapping, source and clip, removes malformed, collapsed,
crossing, concave, non-finite and wholly offscreen commands, then compacts the
survivors without changing painter order. `clear()` drops the list but retains
its allocated storage and Atlas. This is a command list, not a retained world:
a changing view is expressed by clearing it and submitting the next view in
order.

## The bulk door

Like a Sprite swarm, a producer with many commands can allocate one contiguous
block and write directly through NumPy views:

    import numpy as np

    counts = np.array([4, 3, 0], dtype=np.uint32)
    commands = floor.extend(3, clip_counts=counts)
    commands.texture_ids[:] = floor.texture_id("FLOOR4_8")
    commands.corners[:] = mapping_quads
    commands.clip_points[:] = packed_clip_points
    commands.source_rects[0] = (8, 8, 32, 32)  # optional pixel crop

**This door is deliberately sharp, and `add()` is the other one.** `add()`
checks its arguments as you call it and costs a Python call per command.
`extend()` exists for the case where that call is itself the cost: it hands a
producer the layer's own storage and stays out of the way. Three things follow,
and all three are the bargain rather than gaps in it — a row you do not write is
invalid rather than defaulted, nothing is validated until the batch pass
immediately before upload, and what you are handed is live storage that can be
moved out from under a reference you kept. Each is stated below rather than
guarded against.

`texture_id()` validates an Atlas key once, so broadcasting one texture across
thousands of commands does not repeat a name lookup. `clip_counts` has one
entry per command: zero uses its four mapping corners as coverage, while a
positive count reserves that many consecutive points in `clip_points`.
`source_rects` is an optional `(x, y, width, height)` pixel rectangle per
command, local to its selected texture; leaving a row untouched selects the
whole texture. Unwritten command slots begin invalid and disappear in the same
submission-time cull.

The returned `QuadCommands` properties are real views into the layer, not
copies. The block is invalidated by `clear()` or by a commit that has to compact
bad commands, and reaching through it after either raises. Use
`with commands.edit():` to mark a retained, still-valid block dirty once around
later writes.

**Read the property each time; do not keep the array it hands back.** A later
`add()` or `extend()` that outgrows an arena replaces it, and the properties
re-fetch, so a write through one is correct whenever it runs. An ndarray
captured before that growth still refers to the arena that was let go:

    held = commands.corners               # captured, then an arena grew
    held[:] = mapping_quads               # lands in storage nothing reads
    commands.corners[:] = mapping_quads   # lands in the layer

The two writes read identically and only the second one draws. Nothing raises,
because the block really is still live — its range is still right and `clear()`
has not run; it is the array underneath that moved. The command keeps what it
was born with, which after `extend()` is the invalid geometry the batch cull
then removes, so the symptom is quads that do not appear rather than an error.

Making that raise would mean either invalidating the block on every growth —
which would also reject the correct pattern above, since the property was never
wrong — or checking array identity on every property access, on the door whose
reason for existing is that it does not check things per command. So it is
documented instead. A producer that stages a frame's geometry in its own arrays
and writes it through one `extend()` never meets the question, and that is also
the faster shape.

Internally, commands and custom clip points occupy separate growing NumPy
arenas. A command keeps an integer offset and point count into the packed clip
arena; a zero count means its mapping corners are also its clip. `clear()`
only rewinds both live cursors. Each arena retains its capacity and doubles
independently when its next append will not fit.

Two cached Numba kernels own the ragged scalar work. The first validates and
compacts commands and their variable clips in place; the second expands the
surviving spans into retained position, owner and triangle-index arrays. NumPy
still broadcasts inverse homographies and vertex attributes. `Quads`
construction loads both cached signatures before presentation, so compilation
or cache loading cannot become a first-frame hitch.

## Limits and costs

### Costs

Measured 2026-09-14 on a Ryzen 7 5700X3D by `util/quad_frame_cost.py`, warm-up
discarded rather than averaged in. The workload is a synthetic population of
valid, on-screen commands — nothing is culled, because a dead command is
cheaper and would price the cull instead of the commit. Two arms: plain
four-corner commands, and commands each carrying a five-point convex clip.
**Every figure is per command and flat from 1,000 to 50,000**, which is the
property worth having; the totals scale off it.

| | unclipped | 5-point clip |
|---|---|---|
| `add()`, one command | **1.82µs** | **5.22µs** |
| `extend()`, per command in a block | **0.010µs** | **0.012µs** |
| validate, cull and compact | 0.044µs | 0.094µs |
| inverse homography | 0.046µs | 0.046µs |
| corners to vertices and fans | 0.103µs | 0.172µs |
| **the whole commit** | **0.224µs** | **0.377µs** |
| the draw | one bind and one `glDrawElements`, at any command count |  |

**The bulk door is worth 180x, and 430x once clips are involved.** At 10,000
commands that is 18.8ms of `add()` against 0.116ms of `extend()`: one door
spends a 60Hz frame on ingestion before anything is drawn, and the other does
not appear in the budget. This is the measurement the door exists for — the
per-command cost of each is flat, so the gap is the Python call itself and not
something that amortises away.

A clip roughly doubles the commit rather than multiplying it: the ragged kernel
and the wider validation are 1.6x to 2.1x their fast-path equivalents, and the
homography does not care, because it is solved per command from four corners
whatever the coverage turns out to be.

**These are commit figures, not a frame budget.** They say what a command list
costs to turn into geometry, and deliberately not what the caller spent
deciding on it: a renderer that traverses a BSP, projects, and clips against
leaves does that work in its own domain first. In `testsuite/e1m1.py` the
pillar is around 1% of the frame it appears in, which is why that demo proves
the pillar works and cannot price it.

**Not quoted here: the buffer upload.** It is real and it is in the commit
figures above, but isolating it needs one population per process — a GL buffer
grows and never shrinks, so a sweep in one process carries driver state from
the small runs into the large ones and reverses the ordering. The instrument
says so in its own docstring.

## The GPU owns the projection across the polygon

At submission, BPS solves the inverse homography from screen position to the
mapping quad. Each coverage vertex receives the resulting homogeneous
coordinate `(u*q, v*q, q)`. Those three values interpolate linearly through
every triangle in the clip fan; the fragment shader divides by `q`, wraps
`(u, v)`, and maps it into the selected Atlas region.

That makes every triangle sample one continuous projective plane. There is no
affine diagonal seam to hide by subdividing a long wall, and an n-point clip
does not change texture alignment. This is still a flat 2D command: there is
no model matrix, camera, depth coordinate or scene graph behind it.
Nearest-neighbour minification and magnification remain the engine-wide rule.

## The whole-pixel rule stops at the corner

The other pillars all land on whole pixels. A sprite's position is **floored**
on the way in and reads back what was stored; a tilemap's camera lands the view
on a whole pixel however fractional the scroll that produced it. A quad's
corners are float32 and are never floored.

They cannot be. Corners here are produced programmatically — projected,
interpolated, swung by a camera this pillar knows nothing about — and they do
not naturally arrive on integer boundaries. Rounding them would quantise the
projection rather than the picture: a wall would shear a pixel one way and back
as the view turned, and a tiled floor would swim under a camera that was moving
smoothly.

The GPU renders them back onto whole pixels anyway. Rasterisation covers whole
pixels, sampling stays nearest-neighbour, and nothing downstream ever sees the
fraction. So what reaches the panel still obeys the engine's rule; only the
coordinate submitted to get there does not.

**What makes that safe is the collision plane this pillar does not have.**
Elsewhere the rule is not really about the picture — it is about a second
reader. A mask stamped from the same geometry has to agree with the pixels
drawn from it, and a fractional coordinate is exactly how the two end up a
pixel apart. Quads has no mask and can never be given one, so the rasteriser is
the only consumer of these numbers and there is nothing for the fraction to put
out of step.

## Painter order is the order given

Commands are never sorted. Their triangle fans enter the index buffer in
insertion order, and BPS uses no depth buffer for this pass, so a later command
paints over an earlier one. A BSP renderer can traverse, project and clip in
its own domain and use the resulting command order directly. Layers remain a
coarser composition tool: walls, floor, actors and HUD may be separate layers
when their whole groups need a stable front-to-back relationship.

## Collision is impossible

Quads is presentation only. It allocates no packed plane, accepts no
`collidable` constructor argument, and setting `collidable = True` raises.
This is stronger than collision being off by default: no gameplay geometry is
recoverable from this pillar.

World collision belongs to the system that prepared the commands. A DOOM
renderer, for example, keeps its BSP and world shapes authoritative while
Quads draws their projected result. Rolling or scaling the composed screen
does not rotate a collision plane, either; gameplay continues in the
uncomposed coordinate space that owns it.

## Colour

An RGBA Atlas samples directly. An indexed Atlas uses the layer's
`PaletteBank`, just like a tilemap: row zero starts with the image's embedded
palette and `quads.palette.active` selects the row for the whole command list.
The palette belongs to the layer, not the shared image.

**Which of the two you chose decides whether the layer needs `blend`.** An
indexed fragment discards when its palette entry is transparent, so an indexed
quad layer composes correctly with blending off — the engine's default, and
the same reason a sprite layer sits over a tilemap without it. A direct
fragment has nothing to discard on: it passes the sheet's authored alpha
through untouched, so an RGBA sheet whose art has holes draws those holes
opaque until the layer sets `blend = True`. That is a `Layer` property rather
than a quad one; [layers.md](layers.md) owns what it costs in the walk.

## Culls and refusals

| | |
|---|---|
| construction without an `Atlas` | `TypeError` |
| a texture key absent from the Atlas | `KeyError`, before the list changes |
| negative or non-integer `extend()` count | `ValueError` or `TypeError` |
| mismatched, negative or non-integer bulk clip counts | `ValueError` |
| impossible or malformed source rectangle | command culled before upload |
| anything but four numeric mapping points | command culled before upload |
| fewer than three numeric custom clip points | command culled before upload |
| NaN, infinite, crossing, concave or collapsed geometry | command culled before upload |
| coverage wholly beyond one viewport edge | command culled before upload |
| `collidable = True` or `collider()` | `ValueError`: Quads is presentation only |
