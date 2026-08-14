from typing import NamedTuple


class Size(NamedTuple):
    """A (width, height) extent in pixels.

    A real tuple: unpacks (`w, h = thing.size`), hashes, and compares equal
    to a plain `(w, h)`. Public signatures return Size so hints say which
    element is which; parameters stay liberal and accept any (int, int)
    tuple, so `(16, 16)` literals work without an import.
    """

    width: int
    height: int


class Location(NamedTuple):
    """An (x, y) position in pixels, origin top-left.

    The public counterpart to graphics.internal.Point: Point is the engine's
    mutable numpy-backed state for in-place restamping; Location is the
    immutable value users see in signatures and can use as a dict key.
    """

    x: int
    y: int


class Rect(NamedTuple):
    """An (x, y, width, height) rectangle in pixels, origin top-left.

    x/y may be negative (sprite-local frames put (0, 0) at the character's
    anchor); width/height are always positive. Same tuple contract as Size
    and Location: unpacks, hashes, compares equal to a plain 4-tuple.
    """

    x: int
    y: int
    width: int
    height: int
