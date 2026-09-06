# A pen: the instrument you write with. Four slots -- one per glyph plane --
# and a facing. NOT a palette and NOT a colour type: the slots hold indices
# INTO a palette, and the pen also carries which way the letters come out, so
# it does not belong in `colors` (it lived there until 2026-08-22; carrying
# `Orientation` made that placement an import cycle as well as a lie).
#
# It then sat in `graphics.common` until 2026-08-28, which was the same
# mistake one level along: "shared vocabulary the engines consume" was the
# address, and the console was the only engine that ever consumed it. Nothing
# in tile or sprite has ever mentioned a Pen. A pen writes GLYPHS -- four
# slots because a glyph has four planes -- so it is as console-specific as
# the glyph grid it stamps into, and it lives with it now.
#
# ---------------------------------------------------------------------------
# API DECISIONS (2026-08-22) -- spec, not a description of internals. Full
# narrative in docs/bps/design-log.md, "Pens you hold".
#
# 1. DECISION: a pen carries COLOUR AND FACING, and you swap pens rather than
#    setting a mode. There is no `layer.orientation`.
#    REQUIREMENT: modal write-state named with an attribute noun lies --
#    `layer.orientation` read as "the surface is flipped" when it meant "the
#    next stamp faces this way". A tool noun does not have that problem: a
#    pen is obviously in your hand, not on the page. Kris's model: "the same
#    as holding a pen in the other hand; I'd use a backwards_pen or a
#    forwards_pen and swap between them."
#
#    Why facing belongs on the INSTRUMENT and not the surface, physically:
#    this object is really a stencil -- the plastic sheet from school, or
#    Banksy's cardboard -- because it has EXTENT. A pen is a point that lays
#    a stroke; this is a cell-sized sheet you lay down and stamp through. And
#    flipping a sheet over to get the mirror is the natural operation on a
#    stencil, where rotating a pen is meaningless. So `facing` is not bolted
#    onto a colour object; it is intrinsic to a thing with a shape. The name
#    stays `Pen` deliberately -- Console is the normal path and a console
#    writes with a pen -- but the stencil is the mental model to reason with,
#    and `Stencil` is NOT available as a rename: see decision 2.
#    EVIDENCE: `brutal_deluxe.py` was ALREADY doing this before the API
#    allowed it properly -- `write(glyphs, text, row, pen)` assigns a whole
#    pen per line and swaps between gold and steel. The prior art invented
#    the idiom; this just stops fighting it. Reverses 08-19/20, which barred
#    facing from a pen on grounds that all assumed facing meant mirroring the
#    INK; a pen's facing is which way the LETTER comes out, which is neither
#    inert on a flat pen nor redundant on a per-pixel one.
#
# 2. DECISION: ONE pen class. Slots hold whatever that pen's slots hold -- an
#    index, or a plane you fiddled. `MagicPen` is deleted, not renamed.
#    REQUIREMENT: "magic pen is just misnamed if it's not working like pen"
#    -- it shared exactly one member with Pen (`paint`) and had none of `[i]`
#    or `__iter__`, so it was a paint-source wearing the word. Two classes for
#    one concept forced every holder to care which it had.
#    EVIDENCE: `Stencil` was proposed as the rename and rejected on evidence
#    -- the word is already taken and means the OPPOSITE thing. brutal_deluxe
#    calls the GLYPH the stencil ("the letters are a flat 2-bit stencil ...
#    the stencil never changes; only the colour under it does"), and the
#    render path agrees: the glyph selects which plane, the pen supplies the
#    value. Collapsing was then measured rather than argued: building the
#    full slab for every pen is FASTER (clear() 1.9x, one _paint 2.4x),
#    because the old tuple `paint` paid a tuple->array conversion and then a
#    strided broadcast where a slab is a memcpy. The cheap representation was
#    never cheap.
#
# 3. DECISION: slot access is POSITIONAL AND ONLY POSITIONAL. `pen[0]`
#    through `pen[3]`. There is no `foreground` and no `background`.
#    REQUIREMENT: 07-21 ruled slots 2 and 3 go by number because a font
#    decides what they draw and a name would prejudge it. On 08-22 that was
#    read as leaving 0 and 1 free to carry conventional names on top of the
#    index, and they were added on that basis.
#    REVERSED 2026-08-28. The 07-21 rule is not a rule about slots 2 and 3;
#    it is a rule about slots, and 0 and 1 are slots. Naming half of them
#    does not sidestep the objection, it INVITES it -- a reader who meets
#    `pen.foreground` and `pen[2]` in the same breath has to ask what 2 is
#    called, and the honest answer is that it has no name because a name
#    would decide what the font draws there. Four numbered slots never raise
#    the question. Kris: "otherwise we invite the question what is the name
#    for 2 and 3 and it prejudges content."
#    CONSEQUENCE: `Console` lost its forwarding shortcuts in the same pass,
#    so the road to a slot is `console.pen[1] = WHITE` -- one road, and it
#    reads as what it is. The 08-22 argument that named accessors are what
#    "lets one class serve both roads" survives, but it was never about the
#    names: slot READS returning what you last SET (see 4) is the mechanism,
#    and `pen[1]` does that identically.
#
# 4. DECISION: the slots are the declared colours; `paint` is the slab the
#    layer writes. Setting a slot updates both. Fiddling `paint` directly is
#    the advanced road, and from then on `paint` is the truth -- slot reads
#    describe only what you last set, not what is in the slab.
#    REQUIREMENT: two roads, and neither may tax the other. The normal road
#    sets slots and never sees an array; the advanced road (chrome ramps)
#    writes planes and never reads slots back.
#    EVIDENCE: real usage splits exactly that way. `clouds.py` mutates slots
#    in a hot loop and never touches `paint`; `brutal_deluxe.py` writes a
#    plane and never reads a slot. Making slot reads inspect the slab would
#    serve neither and would break `tuple(pen)`.
#
# 5. DECISION: a pen depends on a cell SIZE, not on a layer. `Pen(...,
#    size=(w, h))` binds immediately; a pen given no size BINDS ON FIRST USE
#    and freezes then. Either way the size is frozen, a mismatch is refused,
#    and a pen is REUSABLE across any number of layers whose cells match.
#    REQUIREMENT: a flat pen has no natural size, but a fiddled one must have
#    one and it must match the cells it paints. Naming a layer would overstate
#    the dependency -- nothing about a pen needs a surface, only dimensions --
#    and would imply a pen belongs to one layer when it does not.
#    EVIDENCE: an earlier cut took `Pen(layer)`, detecting the layer by
#    duck-typing a cell size off the first positional argument. It worked, but
#    it made two false suggestions: that a pen is owned by the layer it was
#    built against, and that you need a layer in hand to make one. Reuse was
#    always legal -- `_bind` only ever refused a DIFFERENT size -- so the
#    signature was hiding a capability the implementation already had.
#    Keyword `size=` also keeps the first positional slot unambiguously a
#    colour, which matters now that slots accept sequences as planes.
# ---------------------------------------------------------------------------

from __future__ import annotations

from typing import Iterator, Optional, Union

import numpy as np

from blitspersecond.common import Size

from ..orientation import Orientation

#: The SYSTEM pen: transparent ground, white ink, and the two remaining
#: planes transparent. Pen owns what "system" means, the way
#: `colors.system_palette()` owns the stock 16 -- so a layer that wants a
#: sensible starting brush says `Pen(self)` and repeats no literal. This is
#: what lets a fresh layer be written on before anyone chooses a colour.
#:
#: NOT the identity mapping (0, 1, 2, 3), which was tried and is wrong: in
#: the system palette 2 and 3 are SILVER and GRAY, and the shipped charsets
#: use plane 2 for the font's outline -- so every default glyph would come
#: out silver-outlined and gray-shadowed. A default must not decide a font's
#: outline colour on its behalf. No slot has a name, for the same reason
#: (decision 3): a font decides what a plane draws, so a name would prejudge
#: it -- and for the same reason the default declines to paint 2 and 3 at all.
SYSTEM_SLOTS: tuple[int, int, int, int] = (0, 1, 0, 0)
_SLOTS = len(SYSTEM_SLOTS)

#: The four slots by number, for both of the things a slot index selects.
#:
#: A pen slot and a glyph collision plane are the SAME index seen from two
#: ends -- the pen says what a plane draws, `Console.collider` asks what it
#: collides as -- so they get one set of names rather than two:
#:
#:     pen[PLANE_2] = YELLOW              # what it draws
#:     console.collider(PLANE_2)          # what it collides as
#:
#: Numbered, and that is the point. Naming the INDEX prejudges nothing;
#: naming what it DRAWS would, which is the same reason `SYSTEM_SLOTS`
#: declines to paint 2 and 3 -- a font decides what a plane is for. All four
#: are offered, plane 0 included: it is conventionally the glyph's ground and
#: so usually the holes, but nothing stops a pen painting it a real colour,
#: and reserving it would assume a convention this module explicitly does not.
#:
#: Module constants rather than an `IntEnum`, because `Plane.PLANE_2` stutters
#: and these sit beside `SYSTEM_SLOTS`, already a plain tuple of the same four
#: indices. They are plain `int`s, so `pen[2]` and `pen[PLANE_2]` are one
#: thing and no API grows a second spelling it has to accept.
PLANE_0 = 0
PLANE_1 = 1
PLANE_2 = 2
PLANE_3 = 3

#: What one slot may hold: a palette index, or a whole plane once someone has
#: taken the per-pixel road (decision 4 -- a slot read describes what you last
#: SET, so setting a ramp means reading one back).
#:
#: Both halves are load-bearing and the annotation on SYSTEM_SLOTS above is
#: too. Without it the tuple infers as `tuple[Literal[0], Literal[1], ...]`,
#: so `list(SYSTEM_SLOTS)` is a `list[Literal[0, 1]]` -- a list that will
#: accept only the two values it happened to be built from, and rejects every
#: slot assignment in this file including `pen[1] = YELLOW`.
Slot = Union[int, np.ndarray]


def _is_plane(value: object) -> bool:
    """Whether a slot assignment is a plane rather than a palette index.

    An ndarray always is; so is any other sequence, so a list ramp reads the
    same as an array one. A bare int never is, and numpy scalars are ints."""
    if isinstance(value, np.ndarray):
        return value.ndim > 0
    return isinstance(value, (list, tuple))


class Pen:
    """The instrument you write with: four palette indices -- one per glyph
    plane -- and a facing.

        pen = Pen(BLACK, WHITE)                  # flat, sizes itself on use
        pen[1] = YELLOW                          # the glyph's ink plane
        pen[2] = BLACK                           # the font's outline plane

    Slots go by NUMBER, all four of them. A glyph has four planes and what
    each one draws is the font's decision, so the pen does not name them --
    see decision 3 in this file's header.

        backwards = Pen(BLACK, WHITE, facing=Orientation.FLIP_H)
        layer.pen = backwards                    # swap, do not set a mode

    For per-pixel ink -- a chrome ramp, a colour-clash stunt -- give the pen a
    size so it has a real array, then fiddle it:

        chrome = Pen(BLACK, WHITE, size=layer.glyph.size)
        chrome[1] = ramp                         # slot 1 is the glyph's ink

    A pen depends on a cell SIZE, not on a layer, which is why `size` takes a
    `(width, height)` and not a surface. A pen is reusable across any number
    of layers whose cells are that size; only a mismatch is refused.

    A `ramp` is a run of consecutive palette INDICES holding the interpolated
    colours -- BPS does not build one for you, because budgeting palette RAM
    is an art decision. `examples/pen.py` carries the nine lines that do it.

    A pen's `size` binds on first use and is frozen after; painting with it
    on a layer of a different cell size raises."""

    __slots__ = ("_slots", "_facing", "_cell", "_slab")

    def __init__(
        self,
        *args: object,
        size: Optional[tuple[int, int]] = None,
        facing: Orientation = Orientation.NONE,
    ) -> None:
        if len(args) > _SLOTS:
            raise ValueError(f"a pen has {_SLOTS} slots; got {len(args)} values")

        slots: list[Slot] = list(SYSTEM_SLOTS)
        for index, value in enumerate(args):
            slots[index] = int(value)  # pyright: ignore[reportArgumentType]
        self._slots: list[Slot] = slots
        self._facing = Orientation(facing)
        self._cell: Optional[Size] = None
        self._slab: Optional[np.ndarray] = None

        if size is not None:
            width, height = size
            self._bind(int(width), int(height))

    # -- binding ------------------------------------------------------------

    def _bind(self, width: int, height: int) -> np.ndarray:
        """Adopt a cell size, or confirm the one already adopted.

        Called by the layer at every paint site; a no-op once bound to the
        same geometry, which is why it can sit on the write path."""
        cell = self._cell
        if cell is not None:
            if cell != (width, height):
                raise ValueError(
                    f"pen is bound to {cell[0]}x{cell[1]} cells and cannot "
                    f"paint {width}x{height} ones -- a pen's size freezes on "
                    f"first use; build another with Pen(..., size=(w, h))"
                )
            assert self._slab is not None
            return self._slab

        slab = np.empty((height, width, _SLOTS), dtype=np.uint8)
        slab[:] = self._slots
        self._cell = Size(width, height)
        self._slab = slab
        return slab

    @property
    def bound(self) -> bool:
        """Whether this pen has adopted a cell size yet."""
        return self._cell is not None

    @property
    def size(self) -> Optional[Size]:
        """This pen's extent in pixels, or None while it is still sizeless.

        A pen is exactly one cell -- it does not have cells, it IS one -- so
        this is `size`, the same word every other surface in the engine uses,
        not the layer's cell vocabulary."""
        return self._cell

    @property
    def paint(self) -> np.ndarray:
        """The `(height, width, 4)` slab the layer writes into a cell.

        Writing to this directly is the advanced road: from then on the slab
        is the truth and slot reads describe only what you last set."""
        if self._slab is None:
            raise RuntimeError(
                "pen is not bound to a cell size yet, so it has no paint to "
                "fiddle -- give it one (Pen(..., size=layer.glyph.size)) or "
                "write with it once first"
            )
        return self._slab

    # -- facing -------------------------------------------------------------

    @property
    def facing(self) -> Orientation:
        """Which way the letters this pen writes come out.

        Not the ink's mirroring -- a per-pixel pen mirrors its own `paint`
        array for that.

        Which orientations are legal is the SURFACE's rule and never the
        pen's, and it is geometric: `graphics.orientation.applies_to` allows
        all eight on a square cell and only the four shape-preserving ones on
        a rectangular one. So a pen facing ROT_90 is fine on an 8x8 charset
        and refused on an 8x12 one, by the cell rather than by the pen."""
        return self._facing

    @facing.setter
    def facing(self, value: Orientation) -> None:
        self._facing = Orientation(value)

    # -- slots --------------------------------------------------------------

    def __getitem__(self, index: int) -> Slot:
        return self._slots[index]

    def __setitem__(self, index: int, value: object) -> None:
        """Set one slot -- to a palette index, or to a whole plane.

            pen[1] = WHITE                       # flat
            pen[1] = ramp                        # a gradient down the cell
            pen[2] = np.where(mask, BLACK, 0)    # a mask, same mechanism

        A 1-D sequence as tall as the cell is read as a VERTICAL gradient,
        one value per scanline, broadcast across the width -- the copper-bar
        reading, and the one rule that holds for every cell size rather than
        only square ones. Anything (height, width) lands verbatim."""
        if _is_plane(value):
            self._set_plane(index, value)
            return
        colour = int(value)  # pyright: ignore[reportArgumentType]
        self._slots[index] = colour
        if self._slab is not None:
            self._slab[:, :, index] = colour

    def _set_plane(self, index: int, values: object) -> None:
        if self._slab is None or self._cell is None:
            raise RuntimeError(
                "a flat pen has no cell to shape a plane against -- give it a "
                "size (Pen(..., size=layer.glyph.size)) or take the one the "
                "layer is already holding (layer.pen[1] = ramp)"
            )
        width, height = self._cell
        plane = np.asarray(values)
        if plane.ndim == 1 and plane.shape[0] == height:
            # A scanline ramp: one value per row, across the full width.
            plane = plane[:, None]
        if plane.size and (plane.min() < 0 or plane.max() > 0xFF):
            raise ValueError("pen plane values must be palette indices, 0..255")
        try:
            self._slab[:, :, index] = plane
        except ValueError as error:
            raise ValueError(
                f"a plane for a {width}x{height} pen must be ({height}, "
                f"{width}), or a length-{height} ramp down the cell; got "
                f"{plane.shape}"
            ) from error
        # The slot now holds a plane, which is what a slot read should say.
        self._slots[index] = self._slab[:, :, index].copy()

    def __len__(self) -> int:
        return _SLOTS

    def __iter__(self) -> Iterator[Slot]:
        return iter(self._slots)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Pen):
            if self._facing is not other._facing:
                return False
            return len(self._slots) == len(other._slots) and all(
                np.array_equal(mine, theirs)
                for mine, theirs in zip(self._slots, other._slots)
            )
        return NotImplemented

    def __repr__(self) -> str:
        # Slots positionally, in the order the constructor takes them.
        slots = ", ".join(
            f"<{value.shape[0]}x{value.shape[1]} plane>"
            if isinstance(value, np.ndarray)
            else str(value)
            for value in self._slots
        )
        size = f", {self._cell[0]}x{self._cell[1]}" if self._cell else ""
        facing = f", facing={self._facing.name}" if self._facing else ""
        return f"Pen({slots}{facing}{size})"
