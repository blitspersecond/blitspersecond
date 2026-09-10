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

    **It is a tuple. What it adds is `.x`/`.y` and a name in a signature**,
    and that is the whole value: `if sprite.position.x <= 10:` says what it
    means, and the IDE offers the fields as you type it, where
    `sprite.position[0]` offers nothing and reads as less. Immutability,
    unpacking, hashing and equality with a plain 2-tuple all come from
    `tuple` and are not what this type is for. Nor is it enforcement --
    setters take `Union[Tuple[...], Location]`, so a bare tuple is accepted
    wherever one is wanted. This is what the engine hands back, never what it
    demands.

    **Immutable is settled**, and it was never the interesting question --
    a tuple is immutable too. A position gets nudged and `thing.x = 10` is how
    a person says it, but that assignment belongs to the OWNER and never
    reaches through the value. An owner exposes its own `x`, `y` and
    `position` properties over whatever it actually stores -- a sprite keeps
    its position in a slot row and mints a `Location` on read -- so this type
    is a value at the boundary and never has to take a write.

    `Cursor` is the apparent counterexample and is not one: it is a mutable
    object because `console.cursor.x = 10` reaches one level deeper, through a
    value the caller does not own, and the bounds check has to live on the way
    in. Both cases are about who owns the setter, not about immutability.
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
