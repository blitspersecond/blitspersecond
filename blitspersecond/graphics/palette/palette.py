"""One palette: a row of a bank, and the modify path over it.

Bound, not a value: this holds a bank and a row number rather than a slice, so
it stays valid for the life of the bank and a write reaches the table. The
operator vocabulary and its costs: `bps/reference/palette.md`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from . import operators
from ..color import ColorQuad
from .internal import _CHANNELS, _byte, _quad
from .system import SIZE

if TYPE_CHECKING:  # pragma: no cover
    from .bank import PaletteBank

class Palette:
    """One row of a bank, addressed by colour index.

        pb[0][1]                  the colour at index 1
        pb[0][1] = ColorQuad(...) write it

    **Bound, not detached.** This holds its bank and its row number, so a
    write reaches the table and flags it dirty. It is not storage and it is
    not a copy -- there is nothing in here to get out of step with the bank,
    because there is nothing in here at all.
    """

    __slots__ = ("_bank", "_row")

    def __init__(self, bank: "PaletteBank", row: int) -> None:
        self._bank = bank
        self._row = row

    def __len__(self) -> int:
        return SIZE

    def __getitem__(self, index: int) -> ColorQuad:
        """The colour at `index`, as a quad BOUND to this slot -- so
        `pb[0][1].red = 0` reaches the table rather than a copy of it."""
        return ColorQuad._bound(self, self._at(index))

    def _at(self, index) -> int:
        """One rule for every road into this palette."""
        if isinstance(index, bool) or not isinstance(index, (int, np.integer)):
            raise TypeError(
                f"a palette is addressed by colour index 0..{SIZE - 1}; got "
                f"{index!r}"
            )
        index = int(index)
        if not -SIZE <= index < SIZE:
            raise IndexError(f"colour {index} out of range -- a palette holds {SIZE}")
        # Normalised, so the pin below sees index 0 however it was spelled --
        # `pb[0][-SIZE]` is index 0 and must not slip past as "not zero".
        return index + SIZE if index < 0 else index

    def _slot(self, index: int) -> np.ndarray:
        return self._bank._table[self._row, self._at(index)]

    def _write(self, index: int, channel: int, value: int) -> None:
        index = self._at(index)
        written = _byte(value, _CHANNELS[channel])
        # The pin again: `pb[0][0].alpha = 200` is refused the same way a
        # whole-colour write is, silently, because there is one rule and it
        # does not depend on which road reached it.
        if index == 0 and channel == 3:
            written = 0
        self._bank._table[self._row, index, channel] = written
        self._bank._dirty = True

    def __setitem__(self, index: int, value) -> None:
        """Write one colour.

        **Index 0's alpha is pinned to 0, whatever you pass.** It is the
        engine-wide hole: the mask is `pixels != 0` and the shader discards
        what the table calls transparent, so an opaque index 0 would fill in
        every hole in the art at once.
        """
        index = self._at(index)
        colour = _quad(value)
        if index == 0:
            colour = colour.copy()
            colour[3] = 0
        self._bank._table[self._row, index] = colour
        self._bank._dirty = True

    # -- the modify path ---------------------------------------------------
    #
    # Every operator changes THIS row and hands it back, so they chain. A
    # palette is a row of a bank rather than a value, so there is nowhere for
    # a derived table to go except a row somebody chose -- put the source in
    # the destination first (`bank[40] = bank[0]`), then modify it.
    #
    # The arithmetic is in `operators`; what is here is validation, the write
    # back, and the dirty flag.

    def _apply(self, rgb: np.ndarray) -> "Palette":
        """Write RGB back and flag the bank. Alpha is untouched, so the hole
        at index 0 survives every operator without any of them knowing."""
        self._bank._table[self._row, :, :3] = rgb
        self._bank._dirty = True
        return self

    def _rgb(self) -> np.ndarray:
        return self._bank._table[self._row, :, :3]

    def blend(self, other: "Palette", strength: float = 1.0) -> "Palette":
        """Move every colour `strength` of the way toward `other`'s, entry by
        entry. `strength` is 0 (unchanged) to 1 (`other` exactly)."""
        if not isinstance(other, Palette):
            raise TypeError(
                f"blend toward another palette -- `bank[row]`; got "
                f"{type(other).__name__}"
            )
        return self._apply(
            operators.blend(self._rgb(), other._rgb(), _amount(strength, "strength"))
        )

    def duotone(self, colour, mix: float = 1.0) -> "Palette":
        """Turn every colour `mix` of the way into a duotone of `colour`.

            bank[40] = bank[0]
            bank[40].duotone(HAIR, 0.6).darken(0.5)     # one ghost of a trail

        Keeps each entry's luminance. See `operators.duotone` for why this is
        not `blend` toward a flat table.
        """
        return self._apply(
            operators.duotone(self._rgb(), _rgb_triple(colour), _amount(mix, "mix"))
        )

    def darken(self, amount: float = 1.0) -> "Palette":
        """Scale every channel by `1 - amount`; `1` is black."""
        return self._apply(operators.darken(self._rgb(), _amount(amount, "amount")))

    def cycle(self, start: int = 0, stop: int = SIZE, by: int = 1) -> "Palette":
        """Rotate the colours in `[start, stop)` by `by` places, wrapping.

            bank[0].cycle(WATER, WATER + 8)      # the river moves
        """
        first, last = self._at(start), int(stop)
        if not first < last <= SIZE:
            raise ValueError(
                f"a cycle runs [start, stop) inside 0..{SIZE}; got "
                f"[{start}, {stop})"
            )
        if isinstance(by, bool) or not isinstance(by, (int, np.integer)):
            raise TypeError(f"cycle by a whole number of places; got {by!r}")
        return self._apply(operators.cycle(self._rgb(), first, last, int(by)))

    def negate(self) -> "Palette":
        """Invert every channel. Its own undo."""
        return self._apply(operators.negate(self._rgb()))

    def multiply(self, other: "Palette") -> "Palette":
        """Channel-wise product with `other` -- the darkening blend mode."""
        if not isinstance(other, Palette):
            raise TypeError(
                f"multiply by another palette -- `bank[row]`; got "
                f"{type(other).__name__}"
            )
        return self._apply(operators.multiply(self._rgb(), other._rgb()))

    def __repr__(self) -> str:
        return f"<Palette row {self._row} of {self._bank!r}>"


def _amount(value, what: str) -> float:
    """A 0..1 strength. Outside that is refused rather than clamped: an
    operator asked for 2.0 was asked for something, and quietly doing 1.0
    would answer a question nobody put."""
    if isinstance(value, bool) or not isinstance(value, (int, float, np.floating)):
        raise TypeError(f"{what} is a number 0..1; got {value!r}")
    if not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"{what} is 0..1; got {value}")
    return float(value)


def _rgb_triple(value) -> np.ndarray:
    """A colour to operate TOWARD: `(r, g, b)`, or a `ColorQuad`."""
    if isinstance(value, ColorQuad):
        return value.channels[:3]
    if not isinstance(value, (list, tuple, np.ndarray)) or len(value) not in (3, 4):
        raise TypeError(
            f"a colour is (r, g, b) or a ColorQuad; got {value!r}"
        )
    return np.array(
        [_byte(channel, name) for channel, name in zip(value, _CHANNELS)][:3],
        dtype=np.uint8,
    )
