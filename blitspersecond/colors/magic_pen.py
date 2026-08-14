# A magic pen: a pen whose four palette indices vary ACROSS a cell instead of
# being constant over it. Blind -- it holds an array shaped exactly like one
# glyph and paints it verbatim. It does not know where on screen the cell lands,
# which glyph is in it, or what the pen next door is doing. Everything
# interesting -- a chrome ramp, a copper bar, a rolling glint -- is the CALLER
# slicing a gradient up and handing each cell the slab that belongs to it.
#
# This is what the screen-shaped colour buffer in GlyphLayer exists for. A
# cell-shaped one could only hold the average of a pen that varies inside a
# cell, which is the difference between liquid metal and stairs.

from __future__ import annotations

import numpy as np


class MagicPen:
    """One cell's worth of per-pixel pens: a `(height, width, 4)` array holding
    the same four planes a `Pen` has, but one set per pixel.

    Interchangeable with `Pen` wherever a glyph layer paints -- both answer
    `paint`, and the layer writes whatever comes back into the cell's colour
    slab. A `Pen`'s four constants broadcast flat across it; a `MagicPen`'s
    array lands as authored.
    """

    __slots__ = ("_pixels",)

    def __init__(self, pixels: np.ndarray) -> None:
        if not isinstance(pixels, np.ndarray):
            raise TypeError(f"magic pen pixels must be an ndarray, got {type(pixels)}")
        if pixels.ndim != 3 or pixels.shape[2] != 4:
            raise ValueError(
                f"magic pen pixels must have shape (height, width, 4), "
                f"got {pixels.shape}"
            )
        if not np.issubdtype(pixels.dtype, np.integer):
            raise TypeError("magic pen pixels must be palette indices")
        if pixels.size and (np.any(pixels < 0) or np.any(pixels > 0xFF)):
            raise ValueError("magic pen palette indices must be in the range 0..255")
        self._pixels = np.ascontiguousarray(pixels, dtype=np.uint8)

    @property
    def pixels(self) -> np.ndarray:
        """The live index array. Mutating it changes what the pen paints NEXT --
        cells already written keep what they were stamped with, exactly the way
        a `Pen`'s slots do."""
        return self._pixels

    @property
    def size(self) -> tuple[int, int]:
        """The cell size this pen fits, as `(width, height)` -- screen order,
        like every other size in the library."""
        height, width = self._pixels.shape[:2]
        return width, height

    @property
    def paint(self) -> np.ndarray:
        """What the layer writes into a cell's colour slab."""
        return self._pixels

    def __eq__(self, other: object) -> bool:
        if isinstance(other, MagicPen):
            return np.array_equal(self._pixels, other._pixels)
        return NotImplemented

    def __repr__(self) -> str:
        width, height = self.size
        return f"MagicPen({width}x{height})"
