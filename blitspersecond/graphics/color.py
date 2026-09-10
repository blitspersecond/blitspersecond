"""One colour, as four channels.

Outside the palette package because it depends on nothing there: a buffer or
an atlas can name a colour without importing tables. Documented with them
anyway, at `bps/reference/graphics/palette.md`, because that is where a reader looks.
""" 

from __future__ import annotations

import numpy as np

class ColorQuad:
    """A colour as four 8-bit channels, RGBA.

        red = ColorQuad(255, 0, 0, 255)     free-standing: a value you hold
        pb[0][1].red = 0                    bound: a handle on one slot

    **Two roles, told apart by where it came from.** One you construct is a
    value and writing a channel changes your value. One handed back by a
    palette is BOUND -- it holds no channels of its own, reads through to the
    table and writes through to it, flagging the bank dirty. So it cannot go
    stale, and `pb[0][1].red = 0` means what it reads like.
    """

    __slots__ = ("_channels", "_palette", "_index")

    def __init__(self, red: int, green: int, blue: int, alpha: int = 255) -> None:
        """Three channels is the normal spelling; alpha defaults to opaque.

        Alpha is not usually information the caller has -- art is drawn or it
        is not -- so `ColorQuad(255, 0, 0)` means what it looks like. Saying
        it is for the cases that genuinely want it.
        """
        self._channels = np.array([red, green, blue, alpha], dtype=np.uint8)
        self._palette = None
        self._index = -1

    @classmethod
    def _bound(cls, palette: "Palette", index: int) -> "ColorQuad":
        quad = cls.__new__(cls)
        quad._channels = None
        quad._palette = palette
        quad._index = index
        return quad

    @property
    def channels(self) -> np.ndarray:
        """The four channels as uint8 -- a copy, never the table."""
        if self._palette is None:
            return self._channels.copy()
        return self._palette._slot(self._index).copy()

    def _get(self, channel: int) -> int:
        if self._palette is None:
            return int(self._channels[channel])
        return int(self._palette._slot(self._index)[channel])

    def _set(self, channel: int, value: int) -> None:
        if self._palette is None:
            self._channels[channel] = value
            return
        self._palette._write(self._index, channel, value)

    @property
    def red(self) -> int:
        return self._get(0)

    @red.setter
    def red(self, value: int) -> None:
        self._set(0, value)

    @property
    def green(self) -> int:
        return self._get(1)

    @green.setter
    def green(self, value: int) -> None:
        self._set(1, value)

    @property
    def blue(self) -> int:
        return self._get(2)

    @blue.setter
    def blue(self, value: int) -> None:
        self._set(2, value)

    @property
    def alpha(self) -> int:
        return self._get(3)

    @alpha.setter
    def alpha(self, value: int) -> None:
        self._set(3, value)

    # -- tuple-like --------------------------------------------------------
    #
    # A colour reads as `(r, g, b, a)` everywhere else in this engine, so it
    # unpacks, slices and compares like one.

    def _values(self) -> tuple:
        return (self.red, self.green, self.blue, self.alpha)

    def __len__(self) -> int:
        return 4

    def __getitem__(self, key):
        """`quad[0]`, `quad[:3]`, `quad[-1]` -- ints and slices, as a tuple."""
        return self._values()[key]

    def __iter__(self):
        """`r, g, b, a = quad`, and `tuple(quad)`."""
        return iter(self._values())

    def __eq__(self, other) -> bool:
        """Equal to another quad, or to the tuple it reads as. Bound or free
        makes no difference: what is compared is the colour, not where it is
        kept."""
        if isinstance(other, ColorQuad):
            return self._values() == other._values()
        if isinstance(other, (tuple, list)) and len(other) in (3, 4):
            values = tuple(other) + (255,) * (4 - len(other))
            return self._values() == values
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._values())

    def __repr__(self) -> str:
        where = "" if self._palette is None else " bound"
        return (
            f"<ColorQuad{where} {self.red} {self.green} {self.blue} " f"{self.alpha}>"
        )
