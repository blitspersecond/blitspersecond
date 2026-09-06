"""The sprite pillar's screen-space collision planes.

One packed plane per sprite layer, composed from the presentations that carry
a `collide` bit. The layout and the question are `graphics.collision`'s; what
is here is how a population of poses becomes one bitmap.

**Composed, not retained.** A tilemap scrolls a contiguous grid in lockstep,
so it keeps its plane and slides it; a sprite is at a coordinate and every
presentation may move independently, so there is nothing to slide and the
plane is rebuilt. That is the same split the two pillars make on the draw
side, and it is why this file has no apron arithmetic, no drift and no
`translate`.

## Three levels of participation, and they are not the same question

    can it collide at all      the sheet is indexed, so always -- the two
                               loaders refuse anything else, which is why
                               `Sprite._collision_refusal` returns None
    is the layer in play       `Layer.collidable`, the fast-track switch
    does THIS quad             `collide`, per slot

The last one is why a ghost trail can be the loudest thing on screen and
contribute nothing, and why a cloaked sprite hits without being drawn.
**`visible` is never consulted here.** An invisible slot still occupies space.

## Named planes are filled rectangles, and that is all they are

An assembly authors `attack`, `body_hurt`, `push` and so on as **AABBs** in
its local frame. They get one plane each, and composing one is filling its
rects in -- axis-aligned, so there is no geometry here at all, just a solid
pattern of the right size stamped through the same `local_rect` the art goes
through.

**Nothing downstream knows they were rectangles.** A query is a bitwise
intersection over packed planes, so a box plane and a pixel silhouette answer
the same way and cost the same; the boxes are never consulted as boxes, never
used to cull, and never turned back into geometry. That is what lets
`attack` against a tilemap's terrain be the same question as `attack` against
another sprite's `body_hurt`.

**They belong to the primary alone.** A named plane is authored per-assembly
geometry that must land in a SPECIFIC target, so N presentations would mean
N x K rect stamps into fragmented planes; the silhouette is one composition
into one bitmap, which is what makes a population affordable. Kris,
2026-09-01: *"the special case collisions - hurt boxes etc will have to be a
property only of index 0; the mask based ones can continue to ride the normal
path."*

## One stamp shape

Reference had two -- word-slice strokes for a rich sprite's handful of parts,
a many-position scatter for a dense population -- and its own docstring gives
the reason: *"Routing those through the many-placement scatter path costs more
setup than collision work."* That was true of a NumPy scatter and is not true
of a compiled one. Measured on one 16x16 placement: 8.52us for the slice
strokes against 0.42us for `stamp_many` at N=1, so the scatter is cheaper
before there is anything to batch. The split was an artefact of the tool.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional, Tuple

import numpy as np

from blitspersecond.common import SCREEN, Location

from ..collision import CWORD, CWORD_BITS, pack, stamp_many, unpack, words_for
from .frame import local_rect
from .presentation import PRIMARY

if TYPE_CHECKING:
    from .sprite import Sprite


class SpriteCollision:
    """One sprite layer's packed planes, rebuilt from its presentations.

    **Exactly the screen, with no working border.** The tile pillar keeps one
    because a bordered plane is how a scroll slides content in without
    restamping; a sprite layer never scrolls, so nothing is waiting outside to
    arrive.

    It carried one anyway until 2026-09-05, on the argument that a sprite half
    off the left edge would otherwise need clipping per placement. Measured,
    that argument was false: `stamp_many` guards every row and word access
    already, so a stamp starting outside the plane drops what does not fit and
    writes what does, and the clipping a border would avoid is two comparisons
    inside a loop that runs regardless. Removing it changed nothing across 534
    swept positions with a 200x200 box, and took Chun-Li's seven planes from
    870.7KB to 196.9KB and a compose from 47.9us to 33.8us.

    Reference DID need one, which is how it got here: its stamps were NumPy
    slice assignments, and a slice cannot take a negative or oversized index.
    The border was defending a constraint the compiled kernel does not have,
    and it was ported without it.

    So screen `(0, 0)` is plane `(0, 0)`, and this pillar has no offset at all
    -- which is what `Layer.collision_offset` already said a sprite should be.
    """

    __slots__ = ("_sprite", "_planes", "_solid")

    def __init__(self, sprite: "Sprite") -> None:
        shape = (SCREEN.height, words_for(SCREEN.width))
        self._sprite = sprite
        # The silhouette under `None`, and one plane per name the sheet
        # declares. Allocated together because a sheet's vocabulary is fixed
        # and known: a sheet that authors none -- the R-Type case, and the
        # bullets -- gets exactly one plane and pays for exactly one.
        self._planes: Dict[Optional[str], np.ndarray] = {
            None: np.zeros(shape, dtype=CWORD)
        }
        for name in sprite.sheet.planes:
            self._planes[name] = np.zeros(shape, dtype=CWORD)
        #: Packed solid rectangles by `(width, height)`. A named plane's
        #: content is nothing but these, and a pose reuses its own box sizes
        #: frame after frame, so the cache is small and hot.
        self._solid: Dict[Tuple[int, int], np.ndarray] = {}

    # -- composing --------------------------------------------------------

    def compose(self) -> None:
        """Rebuild the silhouette from every presentation that collides.

        Cleared and refilled rather than updated, because there is no cheaper
        truth available: presentations move independently, so a plane that
        tried to track them would need to know where each one used to be.
        Clearing 40KB is one `fill`.
        """
        for plane in self._planes.values():
            plane.fill(0)
        self._stamp_primary()
        self._stamp_named()
        self._stamp_chain(self._sprite._before)
        self._stamp_chain(self._sprite._after)

    def _stamp_primary(self) -> None:
        """The primary, walked part by part.

        Multipart poses have no row in the draw's `Tables` -- *"a row here is
        one quad by construction"* -- so they are walked here exactly as the
        draw walks them, through the same `local_rect`. `collide[PRIMARY]` is
        one bit for the whole pose: a part carries no flag of its own, on the
        same grounds the per-part palette override was removed, that two parts
        of one tileset wanting different treatment is a sprite of its own.
        """
        sprite = self._sprite
        slots = sprite._slots
        if not slots.collide[PRIMARY]:
            return
        index = int(slots.assembly[PRIMARY])
        if index < 0:
            return
        turn = int(slots.orientation[PRIMARY]) & 7
        px, py = (int(v) for v in slots.position[PRIMARY])
        origin = sprite.origin
        sheet = sprite.sheet
        for part in sheet.assemblies[sprite._assembly_names[index]].parts:
            atlas = sheet.tilesets[part.tileset].atlas
            x, y, _w, _h = local_rect(
                turn, part.offset, atlas.rect(part.tile), origin
            )
            stamp_many(
                self._planes[None],
                atlas.solidity(part.tile, turn),
                np.array([px + x], dtype=np.int64),
                np.array([py + y], dtype=np.int64),
            )

    def _stamp_named(self) -> None:
        """Fill the primary pose's authored boxes into their own planes.

        Each rect goes through the SAME `local_rect` as the art, so a mirrored
        Chun-Li's `attack` box mirrors with her arm rather than staying where
        the artist drew it. `local_rect` swaps width and height under the four
        transposing turns, which is exactly right for an axis-aligned box: a
        quarter-turned rectangle is a rectangle, and the dimensions it returns
        are the turned ones.

        A pose with no boxes leaves its planes cleared, which is the honest
        answer -- `sprite-19472-hit-001` authors `body_hurt`, `throwable` and
        `push` but no `attack`, so asking about `attack` on that frame must
        say no rather than say the last frame's.
        """
        sprite = self._sprite
        slots = sprite._slots
        if not slots.collide[PRIMARY]:
            return
        index = int(slots.assembly[PRIMARY])
        if index < 0:
            return
        assembly = sprite.sheet.assemblies[sprite._assembly_names[index]]
        if not assembly.collision:
            return
        turn = int(slots.orientation[PRIMARY]) & 7
        px, py = (int(v) for v in slots.position[PRIMARY])
        origin = sprite.origin
        for name, rects in assembly.collision.items():
            plane = self._planes[name]
            for rect in rects:
                x, y, width, height = local_rect(
                    turn, Location(rect.x, rect.y), rect, origin
                )
                stamp_many(
                    plane,
                    self._solid_pattern(width, height),
                    np.array([px + x], dtype=np.int64),
                    np.array([py + y], dtype=np.int64),
                )

    def _solid_pattern(self, width: int, height: int) -> np.ndarray:
        """A packed all-ones rectangle, cached by size.

        The whole of what a named plane is made of. Cached rather than built
        per stamp because a pose's box sizes repeat every frame it is held,
        and because several poses of one move share them.
        """
        key = (int(width), int(height))
        pattern = self._solid.get(key)
        if pattern is None:
            pattern = pack(np.ones((key[1], key[0]), dtype=bool))
            self._solid[key] = pattern
        return pattern

    def _stamp_chain(self, order: np.ndarray) -> None:
        """A chain's collidable slots, grouped by pose and scattered.

        The mirror of `_drawable`, and deliberately not a call to it: the draw
        filters on `visible` and this filters on `collide`, so the two gathers
        differ. What they SHARE is the table -- `Tables.rect` holds
        `(assembly, orientation)` local rects computed through the one
        `local_rect`, so art and planes cannot drift apart even though each
        gathers its own rows.

        Grouping by `assembly * 8 + orientation` is what turns this into the
        shape a scatter wants: one cached pattern, many positions. A uniform
        population of one pose is a single group and a single call.
        """
        if not order.size:
            return
        sprite = self._sprite
        slots = sprite._slots
        ids = slots.assembly[order]
        keep = slots.collide[order] & (ids >= 0)
        # Single-part only, exactly as the draw requires: a blank pose stamps
        # nothing and a multipart one belongs to the primary.
        keep[keep] &= sprite._assembly_quads[ids[keep]] == 1
        live = order[keep]
        if not live.size:
            return

        tables = sprite._plan._tables_for(sprite)
        assemblies = slots.assembly[live]
        turns = (slots.orientation[live] & 7).astype(np.intp)
        tables.ensure(assemblies, sprite._assembly_names)

        local = tables.rect[assemblies, turns]
        xs = (slots.position[live, 0] + local[:, 0]).astype(np.int64)
        ys = (slots.position[live, 1] + local[:, 1]).astype(np.int64)
        keys = assemblies.astype(np.int64) * 8 + turns

        plane = self._planes[None]
        height, words = plane.shape
        width = words * CWORD_BITS
        sheet = sprite.sheet
        names = sprite._assembly_names
        for key in np.unique(keys):
            selected = keys == key
            index, turn = int(key) >> 3, int(key) & 7
            part = sheet.assemblies[names[index]].parts[0]
            atlas = sheet.tilesets[part.tileset].atlas
            pattern = atlas.solidity(part.tile, turn)
            gx, gy = xs[selected], ys[selected]

            # Drop what cannot reach the plane before the kernel walks its
            # rows. A WORK bound, not a correctness one -- the kernel guards
            # every access -- but a bullet a screen away would otherwise cost
            # its full height in skipped iterations.
            #
            # Tested here rather than before the grouping because the pattern
            # is known here, so the bound is the placement's actual extent
            # instead of the largest the sheet could produce.
            rows, columns = pattern.shape
            near = (
                (gx + columns * CWORD_BITS > 0)
                & (gx < width)
                & (gy + rows > 0)
                & (gy < height)
            )
            if not near.all():
                gx, gy = gx[near], gy[near]
                if not gx.size:
                    continue
            stamp_many(plane, pattern, gx, gy)

    # -- reading ----------------------------------------------------------

    def plane(self, name: Optional[str] = None) -> np.ndarray:
        """One stored plane, `(360, 20)` CWORD -- exactly the screen.

        `None` is the silhouette; a name is one the sheet declares.
        `Layer.collider` has already refused an unknown one, so this is not a
        second place that validates the vocabulary.

        Screen `(0, 0)` is plane `(0, 0)`: there is no border and no offset,
        so a query reads this directly. And unlike the tile pillar's, these
        buffers are never swapped -- there is no `translate` -- so they are
        stable for the layer's life.
        """
        return self._planes[name]

    def unpacked(self, name: Optional[str] = None) -> np.ndarray:
        """One plane as the screen, `(360, 640)` bool. Allocates; viewers and
        tests. No windowing, because the plane IS the screen."""
        return unpack(self._planes[name], SCREEN.width)

    def __repr__(self) -> str:
        named = len(self._planes) - 1
        return f"<SpriteCollision silhouette+{named} named, screen-sized>"
