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
    mutable internal state for in-place restamping; Location is the
    immutable value users see in signatures and can use as a dict key.

    OPEN: whether immutable is right HERE, specifically. It plainly is for
    `Size` and `Rect` -- an extent is a fact about a thing, and `size.width
    = 40` should not be a way to resize anything. A position is different:
    positions get NUDGED, and `thing.x = 10` is how a person says it.

    2026-08-28 is the evidence so far, and it went both ways. `Console.cursor`
    was collapsed onto this type -- it had reduced to exactly x, y and a
    bounds check -- and the collapse was reverted within the hour, because a
    NamedTuple cannot take `console.cursor.x = 10` and that assignment was
    the entire reason the cursor had a type at all. So `Cursor` stayed a
    mutable object with a back-reference to its console, and this type stayed
    immutable and unused.

    That is one data point, and one is not a verdict: a cursor also needs a
    bounds check on the way in, which no value type could carry. Kris: *"if
    we find we need to do this again the type may be wrong."* So the thing to
    watch for is a SECOND surface that wants a nudgeable position and ends up
    minting its own class rather than using this one -- sprite positions are
    the obvious candidate, and `Point` above is that shape already existing
    on the internal side. Two would say the immutability is the problem
    rather than the callers.
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
