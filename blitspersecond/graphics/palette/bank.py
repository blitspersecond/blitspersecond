"""A bank: 256 palettes as one block, and the register that picks one.

Why 256 fixed, why one texture rather than one per palette, what the upload
costs, and why sprites carry their own register instead of using `active`:
`bps/reference/graphics/palette.md`.
"""

from __future__ import annotations

from ctypes import POINTER
from typing import Optional

import numpy as np
from pyglet.gl import (
    GL_NEAREST,
    GL_RGBA,
    GL_UNSIGNED_BYTE,
    GLubyte,
    glBindTexture,
    glTexSubImage2D,
)
from pyglet.image import Texture

from .internal import _palette
from .palette import Palette
from .system import ROWS, SIZE, _blank

class PaletteBank:
    """Several palettes as one array: `(rows, 256, 4)` uint8, RGBA.

    One array rather than a list of objects because the GPU stores a bank as
    one 256x256 texture -- contiguous here means the upload is one
    `tobytes()`
    with nothing gathered first.

    Every bank is 256 palettes. There is no size, nothing to grow and
    nothing to shrink: the table is one fixed 256x256 block, so a row number
    is always valid, `len` is a constant, and no caller has to ask how many
    there are before addressing one.

    **Nothing here hands out the array.** `bank[row]` is a `Palette` bound to
    this bank, and every road to a colour goes through it, so a write is seen
    and the table is flagged. A view or a copy would both read as "the
    palette" and behave differently from it.
    """

    def __init__(self) -> None:
        self._table = _blank()
        #: Which row is being drawn through. A register, not a property of
        #: the colours: moving it recolours everything reading this bank at
        #: the next draw, and changes no table, so it does not dirty.
        self._active = 0
        # True on creation: a bank that has never been uploaded needs one, and
        # nothing here can tell "never uploaded" from "uploaded and unchanged".
        # The projection clears this; whether it has a texture yet is its
        # business, not the bank's.
        self._dirty = True
        self._texture: Optional[Texture] = None

    def __len__(self) -> int:
        return ROWS

    def __getitem__(self, row: int) -> Palette:
        """`bank[row]` -- one palette. Row only: a colour is named through the
        palette (`bank[0][1]`), not by a second index here."""
        self._check(row)
        return Palette(self, row)

    def _check(self, row) -> None:
        """One rule for both roads: a bank is addressed by row, and only by
        row. `bank[0, 1]` would read as "row 0, colour 1" and work, which is a
        second spelling for something `bank[0][1]` already says."""
        if isinstance(row, bool) or not isinstance(row, (int, np.integer)):
            raise TypeError(
                f"a bank is addressed by row -- name the colour through the "
                f"palette, `bank[row][index]`, not {row!r}"
            )
        if not -ROWS <= int(row) < ROWS:
            raise IndexError(f"palette {row} out of range -- a bank holds {ROWS}")

    def __setitem__(self, row: int, value) -> None:
        """Replace a whole palette: 256 colours, in one write."""
        self._check(row)
        # `bank[40] = bank[0]` -- the first move of every derive-into-a-spare-
        # row: put what you are deriving FROM in the row you are deriving
        # INTO, then modify it. Straight out of the source table, so it is a
        # 1KB copy and no Python.
        colours = (
            value._bank._table[value._row]
            if isinstance(value, Palette)
            else _palette(value)
        )
        if colours[0, 3]:
            colours = colours.copy()
            colours[0, 3] = 0
        self._table[row] = colours
        self._dirty = True

    @property
    def active(self) -> int:
        """The row being drawn through, 0..ROWS-1. Zero by default.

        A register, not a property of the colours: moving it changes no table
        and does not dirty the bank.

        **Everything holding this bank shares it.** A console and a tilemap
        draw through it directly. A sprite does not -- two sprites over one
        interned sheet must point at different rows -- so it reads this ONCE,
        as the row a fresh register starts on, and carries its own from
        there. That is what a package's `default_palette` becomes: this,
        rather than a second field beside the bank saying the same number.
        `bps/reference/graphics/palette.md`.
        """
        return self._active

    @active.setter
    def active(self, row: int) -> None:
        self._check(row)
        self._active = int(row) % ROWS

    @property
    def current(self) -> Palette:
        """The active palette -- `bank[bank.active]`, without saying it
        twice."""
        return self[self._active]

    @property
    def texels(self) -> np.ndarray:
        """The whole bank as one block: `(ROWS, SIZE, 4)` uint8, RGBA.

        The bulk road: the projection that uploads this, and anything doing
        arithmetic over a whole table. C-contiguous, row-major from row 0, so
        `bank[i]` is texture row i. Its identity is stable for the bank's life,
        because nothing reallocates.

        **Writes here are not seen, so set `dirty` when you are done.** Numpy
        writes cannot be observed, exactly as with `PixelBuffer.pixels`.

        **And the index 0 pin is yours to keep.** An opaque index 0 fills in
        every hole in the art.
        """
        return self._table

    @property
    def texture(self) -> Texture:
        """This bank on the GPU: one 256x256 RGBA texture, unit 1's table.

        Lazily created and dirty-gated, exactly like `PixelBuffer.texture`: a
        bank that is never drawn never touches GL, so one can be built before
        there is a window.

        **Read this at the head of a layer's composite.** An upload lands on
        whatever texture unit is active, so one arriving mid-pass disturbs the
        unit the art is bound to. Why a texture and not a uniform, and what
        the upload costs: `bps/reference/graphics/palette.md`.
        """
        if self._texture is None:
            self._texture = Texture.create(
                SIZE, ROWS, min_filter=GL_NEAREST, mag_filter=GL_NEAREST
            )
            self._dirty = True  # a new texture holds nothing yet
        if self._dirty:
            # Raw rather than `blit_into`, which is this same call wrapped in
            # an `ImageData` and a `tobytes()`: 36us against 59us for the
            # whole bank, because the conversion is pure overhead over an
            # array that is already exactly what GL wants.
            #
            # No `glPixelStorei` here. The table is C-contiguous and the whole
            # texture is written, so the default unpack state is right -- and
            # that state is GLOBAL, which is why `PixelBuffer._upload_region`
            # resets it after every partial upload rather than leaving it set.
            #
            # Row-major from row 0 up, which is where `texelFetch` with y=0
            # reads: `bank[i]` lands on row i and the shader's register is the
            # row number unchanged.
            glBindTexture(self._texture.target, self._texture.id)
            glTexSubImage2D(
                self._texture.target,
                0,
                0,
                0,
                SIZE,
                ROWS,
                GL_RGBA,
                GL_UNSIGNED_BYTE,
                self._table.ctypes.data_as(POINTER(GLubyte)),
            )
            self._dirty = False
        return self._texture

    def copy(self) -> "PaletteBank":
        """An independent bank with the same colours, and its own texture.

        The escape from a bank that arrived shared: a sheet is interned, so
        two sprites over one sheet draw through one bank and a write by either
        reaches the other. This is a call rather than something the engine
        does for you, so the moment it happens is in the code. `bps/reference/graphics/palette.md`.
        """
        twin = PaletteBank.__new__(PaletteBank)
        twin._table = self._table.copy()
        twin._active = self._active
        twin._dirty = True
        twin._texture = None  # its own, made when something draws it
        return twin

    @property
    def dirty(self) -> bool:
        """True if this table has changed since it was last uploaded. Set by
        every write; cleared by the projection that does the upload."""
        return self._dirty

    @dirty.setter
    def dirty(self, value: bool) -> None:
        self._dirty = bool(value)
