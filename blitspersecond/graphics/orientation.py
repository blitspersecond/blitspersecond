"""The eight axis-aligned orientations, shared by everything that draws.

All of this exists because the trees in Green Hill Zone are backwards. Half
the scenery is the other half mirrored, and once seen it cannot be unseen --
which is the whole trick: draw the art once, place it twice, face it either
way. Two decisions here follow from that and would otherwise look arbitrary.

**An orientation belongs to the PLACEMENT, never to the source.** The art is
stored once and each cell says how to face it, so a pooled source-ref stays
placement-free and nothing is duplicated to be looked at backwards. That is
what makes mirroring free rather than a second copy of a tree.

**`SHAPE_PRESERVING` is the floor because it is what the hardware had.** A
tilemap entry carried one horizontal flip bit and one vertical flip bit, and
those four combinations are exactly the four that leave a source's shape
alone -- available on any source, whatever it is shaped like. The other four
come from Tiled's diagonal bit, arriving centuries later in engine terms, and
they are precisely the four with a caveat: see `applies_to`.

One vocabulary, because there is only one thing here: the dihedral group of
the square. A tile placement and a sprite's facing are the same eight
transforms, and they used to be spelled two different ways -- tiles ORed flag
bits, sprites carried a `flip_x` bool beside a 0-3 `rotation`. The two
spellings are a proven bijection (all eight, none duplicated), so the second
one was not a second capability, just a second dialect.

It also hid two of the three generators. Under the old sprite spelling a
vertical flip was `flip_x=True, rotation=2` and a transpose was
`flip_x=True, rotation=3` -- reachable, but only if you already knew, which is
the opposite of a vocabulary. Here all three are named.

Composition order is Tiled's, because Tiled is where the bits come from and
disagreeing with it would be a silent import bug: TRANSPOSE (the 'diagonal
flip', an x/y axis swap) applies FIRST, then FLIP_H, then FLIP_V. Callers just
OR bits together; the ordering is baked into the UV permutation tables that
consume this.

What this is NOT is rotation in the general sense. There is no arbitrary
angle and there is not meant to be: anything off the axes stops being a
permutation of four corners and starts needing real sampling.

## Which orientations are legal is a question about the SOURCE

Square sources take all eight; rectangular ones take the four that leave the
shape alone. `applies_to` is the whole rule and it is geometric -- it does not
know or care which engine is asking. A quarter turn of a rectangle has nowhere
to go: squeeze it back into the cell it came from and the pixels stop being
square, place it at its turned extent and it overflows its neighbours.

## What differs between engines is the PRICE, not the permission

**On the GPU an orientation is free, exactly.** A tile or a sprite orients by
pairing the quad's four static tex_coords with its four corners differently.
No extra geometry, no extra draw state, no memory. All eight cost what NONE
costs.

**On the CPU it is paid in table memory.** A glyph grid has no per-cell quad
to permute -- it composes its screen in numpy and gathers from a variant
table, so each orientation it offers must be MATERIALISED: 256 glyphs times
the cell, 24KB per variant at 8x12. A square grid entitled to all eight pays
for eight of those, and its packed cell field widens from 2 bits to 3.

Measured 2026-08-28, because the obvious guess is wrong: every variant is a
free non-contiguous view at creation, flips and transposes alike, and every
one costs the same 7-15us to make contiguous. The distinction is not that
turning copies and flipping does not -- both copy. It is that a turned
rectangle is a different shape, so it will not stack into the one densely
indexed array the gather depends on: four variants of a 12x8 cell stack into
`(4, 256, 12, 8)`, and `(256, 8, 12)` will not join them. A square cell has no
such problem, which is the same fact `applies_to` states from the other side.
"""

from enum import IntFlag
from typing import Tuple

import numpy as np


class Orientation(IntFlag):
    """How a drawn thing is turned: a property of the PLACEMENT, never of the
    source. The same tile appears flipped and unflipped within one map, and
    the same sprite art faces both ways, so a pooled source-ref stays
    placement-free and the orientation rides the thing being placed.
    """

    NONE = 0
    TRANSPOSE = 1  # Tiled's diagonal bit: swap x/y axes (applied first)
    FLIP_H = 2  # mirror across the vertical axis
    FLIP_V = 4  # mirror across the horizontal axis
    ROT_90 = TRANSPOSE | FLIP_H  # 90 degrees clockwise
    ROT_180 = FLIP_H | FLIP_V
    ROT_270 = TRANSPOSE | FLIP_V
    #: The OTHER diagonal mirror -- top-right to bottom-left, where TRANSPOSE
    #: is top-left to bottom-right. Verified against a 3x3 rather than
    #: reasoned: it is the only combination that lands on the anti-diagonal.
    #:
    #: Named because a shared vocabulary with a hole in it is not one. This
    #: was bare `Orientation(7)` -- a magic value appearing in a lookup table,
    #: unreachable by name, and the one transform an author could not ask for
    #: even though the engine drew it perfectly well. All eight of the
    #: dihedral group are now spelled: identity, three turns, two axis
    #: mirrors, two diagonal mirrors.
    ANTI_TRANSPOSE = TRANSPOSE | FLIP_H | FLIP_V


def swaps_axes(flags: Orientation) -> bool:
    """Whether this orientation turns a WxH source into an HxW one.

    True for exactly the four containing TRANSPOSE -- the quarter turns and
    the two diagonal mirrors. The other four (NONE, FLIP_H, FLIP_V, ROT_180)
    leave the shape alone, which is why ROT_180 is safe where ROT_90 is not:
    upside-down is a mirror pair, sideways is an axis swap.

    This is the load-bearing distinction in the whole module -- axis-
    preserving against axis-swapping -- and `applies_to` below is the policy
    built on it. Nothing else should branch on it directly.
    """
    return bool(flags & Orientation.TRANSPOSE)


#: The four orientations that leave ANY source's shape alone -- backwards,
#: upside down, and both, but never sideways. Always available, whatever the
#: source is shaped like.
#:
#: Named for the property rather than for permission ("legal" invites the
#: question "legal for what?", and on a SQUARE source all eight are legal). It
#: is also not merely a filtered list: with TRANSPOSE clear a flag value is
#: `2*H+4*V`, so `>> 1` yields a dense 0..3 index, which is what lets a glyph
#: grid pack a cell's facing beside its glyph in one number and gather
#: variants without arithmetic.
SHAPE_PRESERVING = (
    Orientation.NONE,
    Orientation.FLIP_H,
    Orientation.FLIP_V,
    Orientation.ROT_180,
)


def applies_to(flags: Orientation, size: Tuple[int, int]) -> bool:
    """Whether `flags` may be applied to a source of `size`.

    **The whole policy, in one place:**

        square source       all eight
        rectangular source  SHAPE_PRESERVING only

    A quarter turn of a rectangle is a different shape, and there is nowhere
    for it to go. Placed in the cell it came from it must be squeezed, which
    stops the pixels being square -- the one thing this engine will not do.
    Placed at its turned extent it overflows into its neighbours. A glyph
    grid cannot even hold it: its variant table is one densely indexed array
    and a differently shaped entry will not stack.

    Squareness decides, never which engine is asking. Reference had it the
    other way round and the cost is visible: the glyph layer refused all four
    turning orientations unconditionally, so an 8x8 console --
    `CharSet.ASCII8X8`, where a quarter turn is free and correct -- was told
    "a quarter turn would make it 8x8".

    An earlier version of this returned the turned EXTENT instead of a
    verdict, so that a tile engine could place at the swapped size while a
    grid refused. That was a policy with an exception carved into it for one
    caller, and the exception is void: rectangles do not turn ANYWHERE, so no
    caller ever legally receives a swapped size and the extent was machinery
    for a case that cannot arise.
    """
    return not swaps_axes(flags) or int(size[0]) == int(size[1])


#: The sprite spelling, decomposed. Index by `Orientation` and get back the
#: `(flip_x, quarter_turns)` pair it is equal to -- the bijection written
#: down, so `flip_x` and `rotation` can stay as conveniences over the flags
#: without either becoming a second source of truth.
_AS_FLIP_ROT = {
    Orientation.NONE: (False, 0),
    Orientation.ROT_90: (False, 1),
    Orientation.ROT_180: (False, 2),
    Orientation.ROT_270: (False, 3),
    Orientation.FLIP_H: (True, 0),
    Orientation.ANTI_TRANSPOSE: (True, 1),
    Orientation.FLIP_V: (True, 2),
    Orientation.TRANSPOSE: (True, 3),
}

#: The inverse: `(flip_x, quarter_turns)` -> Orientation.
_FROM_FLIP_ROT = {pair: flags for flags, pair in _AS_FLIP_ROT.items()}


def as_flip_rotation(flags: Orientation) -> tuple[bool, int]:
    """Decompose an orientation into `(flip_x, quarter_turns_clockwise)`.

    Every orientation is exactly one such pair -- mirror first, then turn --
    which is what lets a sprite offer `flip_x` and `rotation` as views onto
    one stored value instead of as separate state."""
    return _AS_FLIP_ROT[Orientation(flags & 7)]


def from_flip_rotation(flip_x: bool, rotation: int) -> Orientation:
    """Compose `(flip_x, quarter_turns_clockwise)` back into an orientation."""
    return _FROM_FLIP_ROT[(bool(flip_x), int(rotation) & 3)]


#: The same decomposition as arrays, for the dense path: index by an
#: orientation value (0-7) and get the mirror flag or the turn count. A pool
#: stores orientations and needs both to pick a cached variant, per frame, for
#: thousands of instances -- so the lookup is a gather, not a dict.
FLIP_OF = np.array(
    [_AS_FLIP_ROT[Orientation(i)][0] for i in range(8)], dtype=np.intp
)
ROTATION_OF = np.array(
    [_AS_FLIP_ROT[Orientation(i)][1] for i in range(8)], dtype=np.intp
)
