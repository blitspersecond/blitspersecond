# A pen: the instrument you write with. Four slots -- one per glyph plane --
# and a facing. NOT a palette and NOT a colour type: the slots hold indices
# INTO a palette, and the pen also carries which way the letters come out.
#
# It lives here, with the glyph grid it stamps into, because a pen writes
# GLYPHS -- four slots because a glyph has four planes -- and nothing in tile
# or sprite has ever mentioned one. Two earlier addresses (`colors`, then
# `graphics.common`) are recorded in `bps/reference/console.md`.
#
# The decisions this file embodies -- why facing is on the instrument, why
# there is one pen class and not two, why slots go by number, the two roads
# through `paint`, why a pen binds to a SIZE, and why the system pen is
# (0, 1, 0, 0) -- are in `bps/reference/console.md`, "The pen". Do not
# re-derive them here; several are reversals and the net position is only
# stated there.

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
    each one draws is the font's decision, so the pen does not name them.

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
