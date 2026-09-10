# Conventions

The rules that hold across every subsystem. They are here rather than in a
chapter because no pillar owns them: the same argument order governs an
`Atlas`, a `CollisionQuery` and a `PixelBuffer`, and a chapter that stated it
would be stating it for everyone else too.

## Arguments split; returns are values

**A position or a size passed INTO a call is separate ints, in `x, y, w, h`
order.** Not a tuple, not a pair of pairs, and not keyword geometry.

    Atlas(buffer, width, height)
    PixelBuffer.upload(x, y, width, height)
    CollisionQuery.at(x, y, w, h)

**What comes BACK is a value type** — `Location`, `Size` or `Rect`, each a
`NamedTuple`. They unpack, hash and compare equal to the plain tuple, so a
caller who wants the tuple already has it; what the type adds is `.x`/`.y`
and a name in the signature.

    def size(self) -> Size
    def origin(self) -> Location
    def rect(self, key) -> Rect

The asymmetry is deliberate and it is not enforcement. Setters that take a
value type take `Union[Tuple[...], Location]`, so a bare tuple is accepted
wherever one is wanted. The value types are what the engine **hands back**,
never what it demands.

## Two exceptions, both structural

**Property setters cannot follow the convention**, because the language has
no other form: `obj.prop = a, b` is a tuple assignment whatever you want it
to be.

    console.cursor = (x, y)
    tilemap.scroll = (x, y)
    tilemap.origin = (x, y)
    sprite.position = (x, y)

Each is paired with a tuple-returning getter, which is consistent with
returns keeping their tuples. **The convention's boundary is "argument
lists", not "everywhere".**

**Optional positions stay tuples.** `Console.write(text, at=(x, y))` takes a
position that may be omitted, defaulting to the cursor. Splitting it needs
`x=None, y=None` plus a both-or-neither check at runtime, and it makes
`write(text, x=3)` a meaningless call that the type system still accepts.
**Required positions split; optional ones do not.**

## Unsettled: accepting both forms

`Console.scroll` takes either — `scroll(0, 1)` and `scroll((0, 1))` both
work, on the reasoning that a coordinate pair is one idea and code already
holding an `(x, y)` should not have to unpack it to be heard.

It is the only place in the engine that does this. One site is a local
choice rather than a convention, so **it is recorded here rather than
generalised**: someone adding a second should decide whether the pattern is
the rule, not inherit it by copying.

## The axis seam

**Geometry is `(x, y)`. A numpy plane is `[row, col]`.** They do not convert
into each other and neither is wrong, so the seam is documented rather than
erased:

    atlas.rect(key)         -> Rect(x, y, w, h)
    atlas[key]              -> an (h, w) view of the pixels
    PixelBuffer.mask        -> an (h, w) bool array

So a call takes `x, y, w, h` and the array it produces is shaped `(h, w)`.
Both follow from what the object is — a rectangle is geometry, an array is
rows of columns — and an object that straddles both says so at each end
rather than picking one.

## The engine's vocabulary, and where it stops

**BPS names things the BPS way, and a parser speaks its format's language.**
The TMX reader keeps Tiled's own attribute names — `tilewidth`,
`tileheight` — because they are the file's, and renaming them would make the
parser lie about what it is reading. The engine's own API says `w` and `h`.

**A foreign symbol is not ours to respell or rename**: `glClearColor`,
`GL_COLOR_ATTACHMENT0`, pyglet's arguments. A GLSL uniform is a special case
— it IS ours, but Python reads it back **by string**, so renaming one means
editing the shader and the lookup together. Get it wrong and the frame goes
black at runtime rather than failing at import.

## Spelling

**The API is American. Prose is not policed.** Kris, 2026-09-10: *"prose I
dont care; api and classes american"*.

So `color`, never `colour` — **every identifier, not only the public ones.**
Class names, methods, parameters, attributes, module names, private state and
local variables alike. Kris, 2026-09-10: *"locals can be fixed as well, code
stays consistent"*. A file that spells it one way in the signature and the
other three lines down is what the rule exists to stop, and that is a reading
problem rather than an API one — which is why it reaches inside.

**Prose is deliberately unconstrained, which is not the same as unsettled.**
Docstrings and these chapters are written in British English — around 300
instances of `colour` across the tree — and **none of it is work owed**. Only
an identifier can be wrong.

**A docstring naming an identifier is not prose, and follows the code.** Kris,
2026-09-10: *"docstrings should reflect methods"*. The two roles sit on one
line often enough that the rule has to be stated:

    """Every colour `mix` of the way into a duotone of `color`."""

The first is the concept and stays British; the backticked one is the
parameter and takes its spelling from the signature. The same applies to a
name a docstring *rejects* — `internal.py` argues its buffer is `paint` and
not `color`, and the name it turns down is spelled the way it would have been
written.
