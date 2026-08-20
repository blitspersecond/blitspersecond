"""The eight axis-aligned orientations, shared by everything that draws.

One vocabulary, because there is only one thing here: the dihedral group of
the square. A tile placement and a sprite's facing are the same eight
transforms, and they used to be spelled two different ways -- tiles ORed
flag bits, sprites carried a `flip_x` bool beside a 0-3 `rotation`. The two
spellings are a proven bijection (all eight, none duplicated), so the second
one was not a second capability, just a second dialect.

It also hid two of the three generators. Under the old sprite spelling a
vertical flip was `flip_x=True, rotation=2` and a transpose was
`flip_x=True, rotation=3` -- reachable, but only if you already knew, which
is the opposite of a vocabulary. Here all three are named.

Composition order is Tiled's, because Tiled is where the bits come from and
disagreeing with it would be a silent import bug: TRANSPOSE (the 'diagonal
flip', an x/y axis swap) applies FIRST, then FLIP_H, then FLIP_V. Callers
just OR bits together; the ordering is baked into the UV permutation tables
that consume this.

Rendering cost is zero. An orientation is a different pairing of a quad's
four static tex_coords with its four corners -- no extra geometry, no extra
draw state. For rectangular sources, an engine swaps the placement's width
and height when TRANSPOSE is present, so the permutation turns the same
pixels rather than stretching them.
"""

from enum import IntFlag


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
    Orientation(7): (True, 1),
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
