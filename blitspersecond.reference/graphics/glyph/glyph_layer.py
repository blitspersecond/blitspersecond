"""A 256-glyph, byte-addressed character engine."""

from __future__ import annotations

from contextlib import contextmanager
from enum import Enum
from operator import index
from pathlib import Path
from typing import Iterator, Optional

import numpy as np
from pyglet.image import Texture

from blitspersecond.colors import Palette, system_palette
from blitspersecond.common import Size
from blitspersecond.display.layer import Layer
from blitspersecond.graphics.common import (
    CWORD,
    Collidable,
    CollisionQuery,
    Orientation,
    Pen,
    PixelBuffer,
)
from blitspersecond.graphics.tile import TileLayer
from blitspersecond.resources import ImageSpec, load_image_spec
from blitspersecond.system.config import Config


ATLAS_COLS = 16
ATLAS_ROWS = 16
CHAR_COUNT = ATLAS_COLS * ATLAS_ROWS

_CHARSET_DIR = Path(__file__).resolve().parents[2] / "assets" / "charset"

#: The orientations a glyph cell can take: the four that preserve a
#: rectangle, which is exactly the ones with TRANSPOSE clear. The turning
#: three are refused rather than special-cased on square charsets -- a rule
#: that holds for every charset is worth more than a capability that holds
#: for some of them, and 8x8 is the only square set BPS ships.
#:
#: These four are the Klein four-group: closed under composition, every
#: element its own inverse, and composition is plain XOR of the bits. The
#: full D4 is not abelian, which is the other reason not to let the turning
#: three in through a side door.
LEGAL_ORIENTATIONS = (
    Orientation.NONE,
    Orientation.FLIP_H,
    Orientation.FLIP_V,
    Orientation.ROT_180,
)

#: With TRANSPOSE clear a flag value is `2*H + 4*V`, so `>> 1` is `H + 2*V`
#: -- a dense 0..3 index into the variant table, no lookup needed.
_VARIANTS = len(LEGAL_ORIENTATIONS)

#: A cell is ONE number: `variant << _FACING_SHIFT | glyph`.
#:
#: Two per-cell attributes, one plane. The shift is 8 because there are 256
#: glyphs, which makes the packed value *identical* to the index into `_flat`
#: -- so the render gather gathers, and does no arithmetic. It also means the
#: two halves cannot drift apart: `scroll` moves one buffer, `clear` fills
#: one buffer, and there is no second array to forget.
#:
#: The upper field is a VARIANT index, not a facing. Facing is merely all it
#: currently holds, in 2 of its 8 bits. That distinction is what the spare
#: room is actually for: a variant is a pre-computed remap of a glyph's
#: plane indices, so ANY per-cell attribute expressible as such a remap can
#: widen this field and stay free at render time.
#:
#: Inverse video is the obvious next one -- swap plane 0 and plane 1 -- and
#: it would widen the field to 3 bits with no change to this packing or to
#: the gather. The price is table memory: `_flat` materialises every variant,
#: so each doubling costs `256 * gh * gw` bytes (24KB at 8x12). Cheap.
#:
#: Blink cannot join them: it varies with TIME, not with the cell, so there
#: is no static pattern to precompute. It belongs to the palette or to a
#: per-frame pass, not here. That is the line -- a variant is a function of
#: the cell alone.
_FACING_SHIFT = 8
_GLYPH_MASK = CHAR_COUNT - 1


class CharSet(Enum):
    """The character sets supplied with BPS."""

    ASCII8X8 = "ascii8x8.png"
    ASCII8X12 = "ascii8x12.png"

    @property
    def cell(self) -> tuple[int, int]:
        """This charset's glyph size in pixels, read from its atlas."""
        return _charset_cells()[self]

    @classmethod
    def for_cell(cls, width: int, height: int) -> Optional["CharSet"]:
        """The system charset whose glyphs are `width` x `height` pixels, or
        None if no shipped set is that size.

        Lets a caller who knows the cell size it wants -- because it worked
        back from a screen grid, as Console does -- land on real letters
        rather than the blank-glyph road."""
        for charset, cell in _charset_cells().items():
            if cell == (width, height):
                return charset
        return None


#: CharSet -> (width, height), read once from the atlases on first use. Not
#: hardcoded beside the filenames: the atlas is the authority on its own
#: glyph size, and a mismatch between the two would be silent.
_CELLS: dict[CharSet, tuple[int, int]] = {}


def _charset_cells() -> dict[CharSet, tuple[int, int]]:
    if not _CELLS:
        for charset in CharSet:
            spec = load_image_spec(str(_CHARSET_DIR / charset.value))
            width, height = spec.size
            _CELLS[charset] = (width // ATLAS_COLS, height // ATLAS_ROWS)
    return _CELLS


class _PaintAccess:
    """The paint buffer: four palette indices for every PIXEL on the layer,
    addressed `paint_buffer[row, col, slot]`.

    The same substance as `pen.paint`, at a different extent -- a pen holds
    one cell's worth, this is the screen's worth. A glyph's pattern says
    which of the four each pixel takes, so this is what the glyphs are
    painted WITH: the input to compositing, not the result of it.

    NOT `pixels` and NOT `color_buffer`, both of which already mean the
    OUTPUT: `PixelBuffer.pixels` is the composed one-index-per-pixel image,
    and `color_buffer` is the FBO attachment (and pyglet's
    `get_color_buffer()`). Naming the input after either would point the
    wrong way down the pipeline. It holds palette indices rather than
    colours, besides, which `paint` says and `colour` does not.

    THE AXES ARE NOT THE CELL GRID'S. `cell[x, y]` is screen order because
    that grid is the engine's to define; this is `[row, col]` because it is
    the screen's, and the screen is row-major all the way down to the GPU.
    Storing it the other way round would not fix that -- it would only move
    the transpose into the recompose and make it unconditional. Measured, a
    transposed whole-plane write costs 1.2ms against 21us native, so the
    order is not negotiable and the NAMES carry the warning instead:

        x, y        screen order      -- cell[x, y]
        row, col    array order       -- paint_buffer[row, col]

    This is the advanced road. Colour is NOT a per-cell attribute and never
    was; the pen stamps it cellwise because that is what text wants, but the
    buffer underneath is an image and can hold anything -- a gradient down a
    cell, a copper across the screen, a field that ignores the grid entirely.
    Clouds looks blocky because it chooses to, not because it must.

        layer.paint_buffer[:] = field           # whole screen, one memcpy
        layer.paint_buffer[..., 1] = shade      # one slot, screen-shaped
        layer.paint_buffer[36:48, 0:344] = band # one row of cells' worth

    A read hands back a READ-ONLY view. It is a view because copying 900KB to
    look at it would be absurd, and read-only because a write through a
    captured view could not tell the layer to recompose, and would show up as
    a frame that silently did not change."""

    __slots__ = ("_engine",)

    def __init__(self, engine: GlyphLayer) -> None:
        self._engine = engine

    def __getitem__(self, key: object) -> np.ndarray:
        view = self._engine._colours[key]  # pyright: ignore[reportArgumentType]
        if isinstance(view, np.ndarray):
            view = view.view()
            view.flags.writeable = False
        return view

    def __setitem__(self, key: object, value: object) -> None:
        self._engine._colours[key] = value  # pyright: ignore[reportArgumentType]
        first, last = self._engine._cell_rows_touched(key)
        if self._engine._touch(first, last):
            self._engine._refresh_rows(first, last)

    @property
    def shape(self) -> tuple[int, int, int]:
        """`(height, width, slots)` in pixels -- the shape a whole-plane write
        must match, and the shape to build a field with."""
        return self._engine._colours.shape


class _CellAccess:
    """The cell grid: one glyph and one facing per cell, addressed
    `cell[x, y]` -- X FIRST. Screen order, like every other surface here.

    This looks like a NumPy index and is not one, so it is worth being blunt
    about. The sharpest case:

        layer.cell[:, 5]        every cell on ROW 5      (x varies)
        numpy_array[:, 5]       every cell in COLUMN 5   (row varies)

    Same expression, transposed meanings. Read `cell[a, b]` as "column a,
    row b" -- the order a person says a screen position in, and the order
    `Size`, `blit`, `scroll` and every coordinate in the engine already use.

    Nothing is being transposed for your convenience: the buffer really is
    stored x-major, `_grid` is `(cols, rows)`, so this order is free and the
    array order would be the one costing a transpose.

    The colour side goes the other way, and its names say so:

        cell[x, y]                  x, y      screen order  -- the grid
        paint_buffer[row, col]      row, col  array order   -- the image

    That is not an inconsistency but the rule: the grid is the engine's to
    define, the image belongs to the screen, and the screen is row-major
    down to the GPU. See _PaintAccess for the measurement that settles it."""

    __slots__ = ("_engine",)

    def __init__(self, engine: GlyphLayer) -> None:
        self._engine = engine

    def __getitem__(self, cell: tuple) -> int | np.ndarray:
        """One cell's glyph id, or a whole run of them. Indexed `[x, y]` --
        X FIRST, screen order, NOT NumPy's row-first.

            code = layer.cell[3, 5]         # column 3, row 5
            row = layer.cell[:, 5]          # every glyph id on ROW 5
            column = layer.cell[3, :]       # every glyph id in COLUMN 3

        The glyph id ALONE, with the packed facing masked off. A read hands
        back what a write takes, so a value can make the round trip; if it
        carried the facing, callers would write it back and the cell would
        then hold a facing the pen never chose. Facing is the pen's, at the
        moment of writing -- there is exactly one place it comes from."""
        x, y = cell
        if isinstance(x, int) and isinstance(y, int):
            return int(self._engine._grid[x, y] & _GLYPH_MASK)
        # The mask already copies, so the buffer stays private either way.
        return (self._engine._grid[x, y] & _GLYPH_MASK).astype(np.uint8)

    def __setitem__(self, cell: tuple, value: object) -> None:
        """Stamp one cell, or a whole run of them in one go. Indexed
        `[x, y]` -- X FIRST, screen order, NOT NumPy's row-first.

            layer.cell[3, 5] = ord("A")             # column 3, row 5
            layer.cell[:, 5] = 0                    # blank ROW 5
            layer.cell[3, :] = 0                    # blank COLUMN 3
            layer.cell[2:7, 5] = np.frombuffer(b"HELLO", np.uint8)

        The same rule a pen slot follows: a scalar fills, an array lands. A
        run is one NumPy write per buffer rather than one full cell write per
        column, which is the difference between blanking a line for free and
        paying ~12us a cell for it.

        Every cell written adopts the current pen and its facing, exactly as
        a single write does -- a bulk write is the same operation, wider."""
        x, y = cell
        if isinstance(x, int) and isinstance(y, int):
            try:
                byte = index(value)  # pyright: ignore[reportArgumentType]
            except TypeError as error:
                raise TypeError(
                    f"glyph index must be a byte, got {value!r}"
                ) from error
            if not 0 <= byte <= 0xFF:
                raise ValueError(f"glyph index must be a byte, got {byte}")

            self._engine._grid[x, y] = self._engine._facing_bits() | byte
            self._engine._paint(x, y, self._engine._pen.paint)
            # The one-cell fast path, unless a defer is open -- then this cell
            # rides along with everything else in its row band.
            if self._engine._touch(y, y + 1):
                self._engine._stamp(x, y)
            return
        self._engine._write_run(x, y, value)


class _GlyphAccess:
    """Indexed reads and writes over a GlyphLayer's 256 patterns."""

    __slots__ = ("_engine",)

    def __init__(self, engine: GlyphLayer) -> None:
        self._engine = engine

    @staticmethod
    def _index(value: object) -> int:
        try:
            # The TypeError catch below is the validation; there's no way to
            # narrow `object` to SupportsIndex before making the attempt.
            byte = index(value)  # pyright: ignore[reportArgumentType]
        except TypeError as error:
            raise TypeError(f"glyph id must be a byte, got {value!r}") from error
        if not 0 <= byte <= 0xFF:
            raise ValueError(f"glyph id must be a byte, got {byte}")
        return byte

    @property
    def size(self) -> Size:
        """One glyph's size in pixels, `(width, height)` -- screen order, the
        same question every other surface answers with `size`.

        This is also a cell's size: a cell is exactly one glyph's worth of
        screen. It lives here, beside `glyph[n]`, so that glyph work never
        has to change namespace mid-thought.

        Do NOT build a pattern from this. A pattern is a NumPy array and
        therefore `(height, width)`, while this is `(width, height)` like
        every other size in the engine, so `np.zeros(layer.glyph.size)` is
        transposed -- and on the square 8x8 charset that mistake still works,
        breaking only on 8x12. Take the shape from an existing pattern
        instead, which cannot be got wrong:

            pattern = np.zeros_like(layer.glyph[0])
        """
        return Size(self._engine._char_width, self._engine._char_height)

    def __getitem__(self, glyph_id: int) -> np.ndarray:
        """A COPY of one glyph's plane-index pattern.

        A copy, so that editing it cannot corrupt the pre-flipped variant
        table behind the layer's back. The consequence is that a read-modify-
        write must put the pattern back explicitly:

            pattern = layer.glyph[code]
            pattern[0, 0] = 1
            layer.glyph[code] = pattern       # <- required
        """
        return self._engine._glyphs[self._index(glyph_id)].copy()

    def __setitem__(self, glyph_id: int, pattern: np.ndarray) -> None:
        byte = self._index(glyph_id)
        if not isinstance(pattern, np.ndarray):
            raise TypeError(f"glyph pattern must be an ndarray, got {type(pattern)}")

        expected = self._engine._glyphs.shape[1:]
        if pattern.shape != expected:
            raise ValueError(
                f"glyph pattern must have shape {expected}, got {pattern.shape}"
            )
        if not (
            np.issubdtype(pattern.dtype, np.integer)
            or np.issubdtype(pattern.dtype, np.bool_)
        ):
            raise TypeError("glyph pattern must contain integer plane indices")
        if pattern.size and (np.any(pattern < 0) or np.any(pattern > 3)):
            raise ValueError("glyph pattern plane indices must be in the range 0..3")

        self._engine._set_pattern(byte, pattern)
        if np.any((self._engine._grid & _GLYPH_MASK) == byte):
            self._engine._refresh()


class GlyphLayer(Layer):
    """A byte-addressed set of 256 fixed-size glyphs -- and a Layer: the
    glyph grid is the thing in the draw order.

    Produced by the GlyphEngine factory (the mainline); constructing
    GlyphLayer directly builds the same object *unattached*.

    Pass a :class:`CharSet` to start from one of BPS's system character sets,
    or ``(width, height)`` to start with 256 blank glyphs of that size.

    Character patterns are readable and replaceable through ``glyph[id]``;
    pattern values select one of the four slots in each cell's pen.

    Writes take ONE brush: the ``pen``, which carries both the colour and the
    facing. Swap pens to change either; neither disturbs cells already on
    screen. Facing is limited to the four
    orientations that preserve a rectangle, and costs nothing at render time
    -- the mirrored patterns are derived once at construction and the facing
    folds into the glyph id, so a screen of mixed facings gathers exactly as
    an upright one does.

    COMPOSITING ON THE CPU IS THE POINT, NOT AN OVERSIGHT.

    A tile layer never touches pixel data: it batches quads and lets the GPU
    resample a shared atlas. This layer instead recomputes a whole screen
    image and re-uploads it when dirty. That looks like the slower design
    and is a deliberate trade, so that it does not get helpfully optimised
    away later.

    The reason is that a glyph is a STENCIL, not a picture. Its atlas holds
    plane indices, and the colour arrives at draw time from `paint_buffer`
    -- which is an ordinary numpy array you can do arbitrary work on. That
    is the whole point of this surface: gradient and mask pens, coppers
    across the grid, a field computed per pixel and handed over whole. Every
    one of those is CPU nonsense done to a buffer, and it is nonsense this
    layer exists to make cheap.

    Moving the resolve into a fragment shader would work -- sample the plane
    index, sample the paint buffer, select the slot -- and it is the wrong
    trade twice over. It swaps ~1.1ms of CPU recompose for re-uploading a
    900KB paint texture, which a program like the clouds demo rewrites every
    frame anyway; and it puts the buffer somewhere numpy cannot reach, which
    costs the capability the CPU shape was bought for. The upload is not
    where the money goes regardless: 225KB is 4.6us and 13.8MB/s at 60Hz.

    If a glyph layer is ever genuinely too slow, the gather is what to look
    at -- not the transfer, and not this.
    """

    def __init__(self, charset: CharSet | tuple[int, int]) -> None:
        if isinstance(charset, CharSet):
            atlas = load_image_spec(str(_CHARSET_DIR / charset.value))
            atlas_width, atlas_height = atlas.size
            if atlas_width % ATLAS_COLS or atlas_height % ATLAS_ROWS:
                raise ValueError(
                    f"{charset.name} must be a {ATLAS_COLS}x{ATLAS_ROWS} "
                    f"glyph atlas, got {atlas_width}x{atlas_height} pixels"
                )
            char_width = atlas_width // ATLAS_COLS
            char_height = atlas_height // ATLAS_ROWS
        else:
            char_width, char_height = self._validate_char_size(charset)
            atlas = ImageSpec(
                size=(
                    ATLAS_COLS * char_width,
                    ATLAS_ROWS * char_height,
                ),
                mode="P",
            )

        self._cols, self._rows = self._screen_grid(char_width, char_height)

        if atlas.mode != "P":
            raise ValueError("a character set atlas must be an indexed image")

        self._atlas = atlas
        # Every glyph pre-flipped, once: (variant, glyph, gh, gw). Orientation
        # then costs a wider gather rather than a branch -- `_refresh` folds
        # the variant into the glyph id and indexes a flat view of this, so
        # the composed shape and the work per cell are exactly what they were
        # before orientation existed (measured at 1.00x). 96KB for the
        # shipped 8x12 set, small enough to stay resident.
        self._variants = np.empty(
            (_VARIANTS, CHAR_COUNT, char_height, char_width), dtype=np.uint8
        )
        # Variant 0 IS the upright table, as a view -- one store, so writing a
        # pattern through `glyph[id]` cannot leave the two disagreeing.
        self._glyphs = self._variants[0]
        self._glyphs[:] = (
            np.asarray(atlas.data, dtype=np.uint8)
            .reshape(
                ATLAS_ROWS,
                char_height,
                ATLAS_COLS,
                char_width,
            )
            .transpose(0, 2, 1, 3)
            .reshape(CHAR_COUNT, char_height, char_width)
        )
        self._reflect()
        # A flat (variant * 256 + glyph) view of the same buffer, so the
        # gather is one index array and stays a view of the writes above.
        self._flat = self._variants.reshape(
            _VARIANTS * CHAR_COUNT, char_height, char_width
        )
        self._char_width = char_width
        self._char_height = char_height
        self._palette = system_palette()
        # A pen from the start, bound to this layer's cells in the same
        # breath, so nothing downstream has to test for one. The SYSTEM pen:
        # Pen owns what that means (slot n -> index n), so the layer repeats
        # no literal. White ink on a transparent ground with the outline
        # planes left alone -- which is why a fresh layer can be written on
        # without choosing colours first, and why it does not invent an
        # outline colour on a font's behalf.
        self._pen = Pen(size=(char_width, char_height))
        # ONE plane, packed: see _FACING_SHIFT. Cell storage is x-major --
        # _grid[x, y] -- so every index in this file reads the way the API
        # does. Only _image.pixels stays row-major, because that is the GPU's
        # byte order rather than our choice. Left uninitialised because
        # clear() at the end of __init__ fills it.
        self._grid = np.empty((self._cols, self._rows), dtype=np.uint16)
        # Colour is screen-shaped -- one pen per PIXEL, not per cell. A plain
        # Pen paints a cell's slab with four constants and behaves exactly as
        # it always has; the room this leaves is for a pen that varies within
        # the cell, which a cell-shaped buffer could only hold the average of.
        self._colours = np.empty(
            (self._rows * char_height, self._cols * char_width, 4), dtype=np.uint8
        )
        self._cell = _CellAccess(self)
        self._glyph = _GlyphAccess(self)
        self._paint_buffer = _PaintAccess(self)
        # Deferral state: how many `defer()` blocks are open, and the span of
        # CELL rows they have dirtied between them. `_dirty_top >
        # _dirty_bottom` means nothing is pending -- an empty span, not row
        # zero.
        self._defer_depth = 0
        self._dirty_top = self._rows
        self._dirty_bottom = 0
        self._image = PixelBuffer(
            ImageSpec(
                size=(self._cols * char_width, self._rows * char_height),
                mode="P",
                palette=self._palette,
            )
        )
        # The renderer: the whole composed screen image as ONE tile on an
        # unattached TileLayer -- the same draw path every other surface
        # takes. The stamp itself is deferred to first composite: blit()
        # needs a current GL context, construction must not.
        self._renderer = TileLayer(
            self._image,
            self._image.width,
            self._image.height,
        )
        self._stamped = False
        self._collision_mask = np.zeros(
            (self._image.height, self._image.width // 32), dtype=CWORD
        )
        self._collision_source: Optional[np.ndarray] = None
        self.clear()
        # IS-A Layer: content and place are one object. See TileLayer.
        super().__init__()

    @staticmethod
    def _validate_char_size(value: object) -> tuple[int, int]:
        if not isinstance(value, tuple) or len(value) != 2:
            raise TypeError("charset must be a CharSet or a (width, height) tuple")

        width, height = value
        if (
            not isinstance(width, int)
            or isinstance(width, bool)
            or not isinstance(height, int)
            or isinstance(height, bool)
        ):
            raise TypeError("character width and height must be integers")
        if width <= 0 or height <= 0:
            raise ValueError(
                f"character width and height must be positive, got {width}x{height}"
            )
        return width, height

    @staticmethod
    def _screen_grid(width: int, height: int) -> tuple[int, int]:
        screen = Config().display
        if screen.width % width or screen.height % height:
            raise ValueError(
                f"character size {width}x{height} does not tile the "
                f"{screen.width}x{screen.height} screen"
            )
        return screen.width // width, screen.height // height

    @property
    def cols(self) -> int:
        return self._cols

    @property
    def rows(self) -> int:
        return self._rows

    @property
    def palette(self) -> Palette:
        return self._palette

    @palette.setter
    def palette(self, value: Palette) -> None:
        if not isinstance(value, Palette):
            raise TypeError(f"expected Palette, got {type(value)}")
        self._palette = value
        self._image.palette = value

    @property
    def pen(self) -> Pen:
        """The brush this layer writes with. Assigning replaces it -- that is
        how you swap a forwards pen for a backwards one. The layer owns the
        brush and binds it to its own cell size; `Console.pen` proxies this."""
        return self._pen

    @pen.setter
    def pen(self, value: Pen) -> None:
        if not isinstance(value, Pen):
            raise TypeError(f"pen must be a Pen, got {type(value)}")
        facing = Orientation(value.facing)
        if facing not in LEGAL_ORIENTATIONS:
            raise ValueError(
                f"a {self._char_width}x{self._char_height} glyph cell cannot "
                f"take {facing!r}: a quarter turn would make it "
                f"{self._char_height}x{self._char_width}. Glyph pens face "
                f"NONE, FLIP_H, FLIP_V or ROT_180"
            )
        # Binds on first use and freezes; raises if this pen already carries a
        # different geometry.
        value._bind(self._char_width, self._char_height)
        self._pen = value

    def _facing_bits(self) -> int:
        """The current pen's facing, shifted into its half of a packed cell.

        TRANSPOSE is clear on every legal glyph facing, so the flag value is
        `2*H + 4*V` and `>> 1` is the dense variant index -- no lookup, no
        branch. See LEGAL_ORIENTATIONS and _FACING_SHIFT."""
        return (self._pen.facing >> 1) << _FACING_SHIFT

    @property
    def cell(self) -> _CellAccess:
        return self._cell

    @property
    def glyph(self) -> _GlyphAccess:
        return self._glyph

    @property
    def paint_buffer(self) -> _PaintAccess:
        """What this layer's glyphs are painted with: four palette indices
        per pixel, addressed `[row, col, slot]` -- ARRAY order, not the cell
        grid's screen order. The input to compositing; `texture` is the
        output. See _PaintAccess."""
        return self._paint_buffer

    def clear(self) -> None:
        """Set every cell to glyph 0, painted with the current pen and facing
        the way the pen faces.

        For part of the screen there is no separate mode: write the cells.
        `layer.cell[:, y] = 0` blanks a row, `layer.cell[a:b, y] = 0` blanks a
        run -- the same operation, narrower."""
        # Glyph 0 under the pen's facing is one packed value, so one fill.
        self._grid.fill(self._facing_bits())
        self._flood()
        self._refresh()

    def _write_run(self, x: object, y: object, value: object) -> None:
        """Stamp a slice of cells: chars, facing and colour, then recompose
        only the rows that changed."""
        xs = x if isinstance(x, slice) else slice(x, x + 1)
        ys = y if isinstance(y, slice) else slice(y, y + 1)
        x0, x1, xstep = xs.indices(self._cols)
        y0, y1, ystep = ys.indices(self._rows)
        if xstep != 1 or ystep != 1:
            raise ValueError("cell runs are contiguous -- no step in the slice")
        if x0 >= x1 or y0 >= y1:
            return

        # Index with what the caller gave, not the widened slices: an int
        # index keeps its axis flat, so `cell[2:7, 3]` takes a length-5 run
        # rather than demanding a (5, 1) column.
        target = self._grid[x, y]
        codes = np.asarray(value)
        if codes.size and (codes.min() < 0 or codes.max() > _GLYPH_MASK):
            raise ValueError("glyph ids must be bytes, 0..255")
        try:
            # Glyph and facing land together, because they are one number. The
            # uint16 keeps the OR from wrapping when `codes` arrives as uint8.
            self._grid[x, y] = codes | np.uint16(self._facing_bits())
        except ValueError as error:
            raise ValueError(
                f"a run of {target.shape} cells takes that shape or a single "
                f"value; got {codes.shape}"
            ) from error

        # Colour is screen-shaped, so the run is a band of scanlines crossed
        # with a band of columns; one broadcast fills it, pen per cell.
        paint = self._pen._bind(self._char_width, self._char_height)
        top, bottom = y0 * self._char_height, y1 * self._char_height
        left, right = x0 * self._char_width, x1 * self._char_width
        self._colours[top:bottom, left:right].reshape(
            y1 - y0, self._char_height, x1 - x0, self._char_width, 4
        )[:] = paint[None, :, None, :, :]
        if self._touch(y0, y1):
            self._refresh_rows(y0, y1)

    def _cell_rows_touched(self, key: object) -> tuple[int, int]:
        """The band of CELL rows a `pixels[...]` write can have disturbed.

        Read off the leading index only, and conservatively: anything not
        obviously a contiguous row range means the whole screen. Getting this
        wrong in the narrow direction leaves stale pixels on screen, so it
        errs wide by construction -- a whole-plane recompose is 1.2ms, a
        wrong one is a rendering bug."""
        leading = key[0] if isinstance(key, tuple) else key
        height = self._colours.shape[0]
        top, bottom = 0, height
        if isinstance(leading, slice):
            start, stop, step = leading.indices(height)
            if step == 1 and start < stop:
                top, bottom = start, stop
        elif isinstance(leading, (int, np.integer)) and not isinstance(leading, bool):
            top = int(leading) % height
            bottom = top + 1
        # Pixel rows to cell rows, rounding outwards: a write that clips a
        # cell still changes that cell's composed pixels.
        gh = self._char_height
        return top // gh, -(-bottom // gh)

    @contextmanager
    def defer(self) -> Iterator["GlyphLayer"]:
        """Suspend recomposing until the block ends, then do it once.

            with layer.defer():
                layer.cell[:, :] = codes.T
                layer.pixels[:] = field

        NOT called `batch`, deliberately: `Batch` is pyglet's word for a
        draw batch, and this engine already uses it that way in `TileBatch`.
        Both of those are nouns holding geometry; this is a verb that holds
        off. Reusing the word on a graphics layer would read as "give me a
        draw batch".

        Every write into a glyph layer recomposes what it touched, which is
        right for one write and wasteful for two: the pair above recomposes
        the whole screen twice, and the first result is discarded by the
        second. Measured on the clouds demo that is ~1.26ms a frame of pure
        waste, against 3.74ms for the whole thing.

        This is the advanced road's answer, and it is explicit rather than
        lazy on purpose. A layer that deferred by itself would have to guess
        when you were finished, and the honest moment to guess is `composite`
        -- by which time `collision_mask` may already have answered a
        question against a screen that had not been drawn yet.

        Nests, so a helper may defer without knowing whether its caller
        already did. Recomposes on the way out of an exception too: leaving
        the screen out of step with the buffers would turn a handled error
        into a rendering bug much further away.

        The dirty region is a SPAN of cell rows, not a set, so touching row 2
        and row 28 recomposes everything between them. That suits a demo
        handing over whole frames, which is what this is for."""
        self._defer_depth += 1
        try:
            yield self
        finally:
            self._defer_depth -= 1
            if self._defer_depth == 0:
                self._flush()

    def _flush(self) -> None:
        """Recompose whatever a deferral dirtied, if any, and forget it."""
        first, last = self._dirty_top, self._dirty_bottom
        self._dirty_top, self._dirty_bottom = self._rows, 0
        if first < last:
            self._refresh_rows(first, last)

    def _touch(self, first: int, last: int) -> bool:
        """Note that cell rows `first`..`last` need recomposing.

        Returns True if the caller should recompose them itself, False if a
        deferral has taken responsibility. Every path that would redraw goes
        through here, so there is one place that knows the rule."""
        if self._defer_depth == 0:
            return True
        self._dirty_top = min(self._dirty_top, first)
        self._dirty_bottom = max(self._dirty_bottom, last)
        return False

    def _refresh_rows(self, first: int, last: int) -> None:
        """Recompose only rows `first` up to `last` into the image.

        `_refresh` does the whole screen; clearing one line has no business
        paying for 30 of them."""
        top = first * self._char_height
        bottom = last * self._char_height
        planes = (
            self._flat[self._grid[:, first:last]]
            .transpose(1, 2, 0, 3)
            .reshape(bottom - top, self._cols * self._char_width)
        )
        self._image.pixels[top:bottom] = np.take_along_axis(
            self._colours[top:bottom], planes[..., None], axis=2
        )[..., 0]
        self._image.dirty = True

    def scroll(self, x: object, y: object = None) -> None:
        """Move the cell grid by `x, y` cells without wrapping.

            layer.scroll(0, 1)          # down one line
            layer.scroll((0, 1))        # the same, as a pair

        Both forms, because a coordinate pair is one idea and should not
        need a tuple built round it to be heard -- and because code holding
        an `(x, y)` it got from somewhere else should not have to unpack it.

        Positive x moves contents right and positive y moves them down.
        Moved cells keep their colours and their facing; newly exposed cells
        are glyph 0 in the current pen and the current orientation.

        DESTRUCTIVE, and relative -- the AMOS sense of the word, which is
        this pillar's lineage. HSCROLL and VSCROLL move the text itself by a
        character at a time and, in the manual's words, "blank lines are
        inserted to fill any gaps left by these scrolling operations". Same
        contract here: contents move, whatever falls off the edge is gone,
        and the gap comes back blank. Blank in the CURRENT pen, which is the
        one place we go further than AMOS did -- so a pen swapped since the
        last write shows in the new lines, not the old ones.
        See docs/references/amos/05-06-text.md.

        AMOS also scoped a scroll to the current line, or to the region
        above or below the cursor. This moves the whole grid. Those are
        cursor-relative operations and the cursor lives in Console, not
        here -- if they are wanted, that is where they belong.

        `TileLayer.scroll` is a DIFFERENT idea under the same name: an
        absolute camera position, a property, moving the VIEW and destroying
        nothing. Not an inconsistency -- a different lineage, and forced by
        a different extent. A tile layer has a world larger than the screen
        for a camera to move over; a glyph layer's grid IS the screen, so
        there is no camera to move and shifting the contents is the only
        thing the word can mean here. See TileLayer.scroll.
        """
        if y is None:
            if not isinstance(x, tuple) or len(x) != 2:
                raise TypeError("scroll takes (x, y) or x, y")
            x, y = x
        if (
            not isinstance(x, int)
            or isinstance(x, bool)
            or not isinstance(y, int)
            or isinstance(y, bool)
        ):
            raise TypeError("scroll x and y must be integers")
        if x == 0 and y == 0:
            return

        src_x0 = max(-x, 0)
        src_x1 = self._cols - max(x, 0)
        src_y0 = max(-y, 0)
        src_y1 = self._rows - max(y, 0)

        gw = self._char_width
        gh = self._char_height

        if src_x0 < src_x1 and src_y0 < src_y1:
            # Facing travels with the character it belongs to -- and now it
            # cannot do otherwise, because they are the same number. One
            # buffer moves, so there is no second one to keep in step.
            cells = self._grid[src_x0:src_x1, src_y0:src_y1].copy()
            # Colour moves in pixels, because that is how it is stored -- the
            # slab of a moved cell travels intact, whatever is inside it.
            colours = self._colours[
                src_y0 * gh : src_y1 * gh, src_x0 * gw : src_x1 * gw
            ].copy()
        else:
            cells = None
            colours = None

        self._grid.fill(self._facing_bits())
        self._flood()

        if cells is not None and colours is not None:
            dst_x0 = max(x, 0)
            dst_y0 = max(y, 0)
            dst_x1 = dst_x0 + cells.shape[0]
            dst_y1 = dst_y0 + cells.shape[1]
            self._grid[dst_x0:dst_x1, dst_y0:dst_y1] = cells
            self._colours[
                dst_y0 * gh : dst_y1 * gh, dst_x0 * gw : dst_x1 * gw
            ] = colours

        self._refresh()

    def _reflect(self, byte: Optional[int] = None) -> None:
        """Derive the mirrored variants from the upright ones -- all 256
        patterns, or one when a pattern is replaced. Slot order is the dense
        `H + 2*V` from LEGAL_ORIENTATIONS: 1 mirrors x, 2 mirrors y, 3 both.
        A glyph is (gh, gw), so x is axis -1 and y is axis -2."""
        source = self._variants[0] if byte is None else self._variants[0, byte]
        target = self._variants if byte is None else self._variants[:, byte]
        target[1] = source[..., ::-1]
        target[2] = source[..., ::-1, :]
        target[3] = source[..., ::-1, ::-1]

    def _set_pattern(self, byte: int, pattern: np.ndarray) -> None:
        """Replace one glyph's upright pattern and re-derive its mirrors, so
        a font edited at runtime stays consistent in every orientation."""
        self._variants[0, byte] = pattern
        self._reflect(byte)

    def _flood(self) -> None:
        """Repaint every cell's colour with the current pen -- one copy of the
        pen per cell, which is what makes `clear()` under a fiddled pen come
        back as a screen of that pen rather than one stretched copy.

        The cell-shaped view is deliberate: `np.tile` would build the whole
        screen as a temporary first, and that is an allocation on the frame
        path (`scroll` floods on every console newline). Broadcasting into a
        reshaped view allocates nothing and measured ~7x faster than the old
        constant broadcast."""
        paint = self._pen._bind(self._char_width, self._char_height)
        self._colours.reshape(
            self._rows, self._char_height, self._cols, self._char_width, 4
        )[:] = paint[None, :, None, :, :]

    def _paint(self, x: int, y: int, colour: np.ndarray) -> None:
        """Fill one cell's colour slab from the pen's `(gh, gw, 4)` paint --
        a contiguous copy, which measured 2.4x faster than broadcasting four
        constants across the same slab."""
        top = y * self._char_height
        left = x * self._char_width
        self._colours[
            top : top + self._char_height, left : left + self._char_width
        ] = colour

    def _refresh(self) -> None:
        # The whole-screen recompose, and the one an open defer swallows --
        # `clear`, `scroll` and a replaced glyph pattern all arrive here, so
        # the check belongs in this one place rather than at each of them.
        if not self._touch(0, self._rows):
            return
        # Cells are (x, y, gh, gw); the image is row-major, so the transpose
        # to (y, gh, x, gw) is where the two orders meet -- once, here. Colour
        # is already screen-shaped, so it takes no part in the reshuffle.
        # Facing needs no unpacking: a packed cell IS the index into the
        # variant table, so this is a plain gather with no arithmetic and no
        # temporary index array.
        planes = (
            self._flat[self._grid]
            .transpose(1, 2, 0, 3)
            .reshape(self._colours.shape[:2])
        )
        self._image.pixels[:] = np.take_along_axis(
            self._colours, planes[..., None], axis=2
        )[..., 0]
        self._image.dirty = True

    def _stamp(self, x: int, y: int) -> None:
        glyph = self._flat[self._grid[x, y]]
        glyph_height, glyph_width = glyph.shape
        top = y * glyph_height
        left = x * glyph_width
        colours = self._colours[top : top + glyph_height, left : left + glyph_width]
        self._image.pixels[
            top : top + glyph_height, left : left + glyph_width
        ] = np.take_along_axis(colours, glyph[..., None], axis=2)[..., 0]
        self._image.dirty = True

    @property
    def collidable(self) -> bool:
        return True

    @property
    def collision_mask(self) -> np.ndarray:
        # Collision is answered off the COMPOSED pixels, so a deferral that is
        # still open has to be made real first. Without this, asking a layer
        # what it collides with mid-defer answers about the screen as it was
        # before the writes -- a wrong answer, not a late one.
        self._flush()
        source = self._image.mask
        if source is not self._collision_source:
            packed = np.packbits(source, axis=1, bitorder="little")
            self._collision_mask[:] = np.ascontiguousarray(packed).view(CWORD).reshape(
                self._collision_mask.shape
            )
            self._collision_source = source
        return self._collision_mask

    def collides_with(self, other: Collidable) -> CollisionQuery:
        if not other.collidable:
            raise ValueError(f"collides_with() target {other!r} is not collidable")
        return CollisionQuery(self, other)

    @property
    def texture(self) -> Texture:
        return self._image.texture

    @property
    def size(self) -> Size:
        return self._image.size

    @property
    def indexed(self) -> bool:
        return self._image.indexed

    def prepare(self) -> None:
        self._renderer.prepare()

    def _composite(self) -> None:
        # A safety net, not the mechanism: a defer left open across a frame
        # is a caller's bug, but drawing a stale screen because of it would
        # be a much harder bug to find than the missing dedent.
        self._flush()
        if not self._stamped:
            self._renderer.blit(0, 0, 0)
            self._stamped = True
        self._renderer._composite()

