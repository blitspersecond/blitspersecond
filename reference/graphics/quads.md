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

Coverage is a separate question. With no `clip`, the four mapping corners are
also the coverage polygon, so a basic call still draws one finite quad.
`clip=` may instead be a `Rect` or a sequence of at least three screen points
around a convex perimeter. The GPU fills that polygon and samples the
projective plane there. Thus an infinite floor is finite data:

    clip=Rect(0, 0, 640, 360)

No infinite coordinate is submitted. The viewport rectangle is simply the
part of the infinite mapping that is allowed to draw. A smaller floor or wall
opening can use its actual projected convex outline. Coordinates may be
fractional or outside BPS's fixed `0..640 x 0..360` screen; normal viewport
clipping removes what cannot appear.

The clip should stay on the intended side of the mapping's projective
horizon. A fragment exactly on that horizon has no finite texture coordinate
and is discarded.

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
bad commands. Use `with commands.edit():` to mark a retained, still-valid block
dirty once around later writes.

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

Both the mapping corners and clip points may run clockwise or
counterclockwise, but each perimeter must be convex, non-crossing and have no
collapsed edge. The texture's top-left, top-right, bottom-right and
bottom-left follow `p1` through `p4`; choosing their order is enough to mirror
or rotate a mapping.

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
