"""The faces a console presents onto its own state.

    console.cell[x, y]              the glyph grid          -- CellAccess
    console.glyph[id]               the 256 patterns        -- GlyphAccess
    console.paint_buffer[r, c, s]   what glyphs draw WITH   -- PaintAccess
    console.cursor.x               where it is writing      -- Cursor

Internal because **you never name these types**, and never build one. They
exist so that `console.cell[3, 5] = ord("A")` can mean something different
from `console.glyph[65] = pattern`, and so that `console.cursor.x = 10` is a
sentence rather than `console.cursor_x = 10`. Each holds a back-reference to
the console that minted it and reaches straight into its state. That is the
test the package's exports follow: `CharSet`, `Console` and `Pen` are things
you build, so they are exported. Nothing here is.

The three buffer faces live apart from `Console` for the reason the split
usually is: each carries a substantial warning about its own indexing order,
and those warnings were the first three hundred lines of `console.py` -- read
before anyone reached the class the file is named after.

**Their real job is not indexing, it is telling the console what changed.**
A write goes to the buffer and then reports the band of cell rows it
disturbed, so the recompose can be the rows that moved rather than the
screen. That is why a paint-buffer READ hands back a read-only view: a write
through a captured view would reach the buffer without passing this way, and
the frame would silently not change.

The recompose band, and how `Cursor` ended up here after being taken apart
and put back together: `bps/reference/graphics/console.md`.
"""

from __future__ import annotations

from operator import index
from typing import TYPE_CHECKING, Any

import numpy as np

from blitspersecond.common import Size

from .charset import GLYPH_MASK

if TYPE_CHECKING:
    from .console import Console


class PaintAccess:
    """The paint buffer: four palette indices for every PIXEL on the console,
    addressed `paint_buffer[row, col, slot]`.

    The same substance as `pen.paint`, at a different extent -- a pen holds
    one cell's worth, this is the screen's worth. A glyph's pattern says which
    of the four each pixel takes, so this is what the glyphs are painted
    WITH: the input to compositing, not the result of it.

    NOT `pixels` and NOT `color_buffer`, both of which already mean the
    OUTPUT: `PixelBuffer.pixels` is the composed one-index-per-pixel image,
    and `color_buffer` is the FBO attachment. Naming the input after either
    would point the wrong way down the pipeline. It holds palette indices
    rather than colours besides, which `paint` says and `color` does not.

    THE AXES ARE NOT THE CELL GRID'S, and the names carry the warning:

        x, y        screen order      -- cell[x, y]
        row, col    array order       -- paint_buffer[row, col]

    The order is not negotiable -- see `bps/reference/graphics/console.md`, "Colour is
    not a cell attribute", for the measurement that settles it.

    This is the advanced road. Colour is NOT a per-cell attribute and never
    was; the pen stamps it cellwise because that is what text wants, but the
    buffer underneath is an image and can hold anything -- a gradient down a
    cell, a copper across the screen, a field that ignores the grid entirely.

        console.paint_buffer[:] = field            # whole screen, one memcpy
        console.paint_buffer[..., 1] = shade       # one slot, screen-shaped
        console.paint_buffer[36:48, 0:344] = band  # one row of cells' worth

    A read hands back a READ-ONLY view. It is a view because copying 900KB to
    look at it would be absurd, and read-only because a write through a
    captured view could not tell the console to recompose, and would show up
    as a frame that silently did not change."""

    __slots__ = ("_console",)

    def __init__(self, console: "Console") -> None:
        self._console = console

    def __getitem__(self, key: Any) -> np.ndarray:
        view = self._console._colors[key]
        if isinstance(view, np.ndarray):
            view = view.view()
            view.flags.writeable = False
        return view

    def __setitem__(self, key: Any, value: Any) -> None:
        self._console._colors[key] = value
        first, last = self._console._cell_rows_touched(key)
        if self._console._touch(first, last):
            self._console._refresh_rows(first, last)

    @property
    def shape(self) -> tuple[int, int, int]:
        """`(height, width, slots)` in pixels -- the shape a whole-plane write
        must match, and the shape to build a field with."""
        return self._console._colors.shape


class CellAccess:
    """The cell grid: one glyph and one facing per cell, addressed
    `cell[x, y]` -- X FIRST. Screen order, like every other surface here.

    This looks like a NumPy index and is not one, so it is worth being blunt
    about. The sharpest case:

        console.cell[:, 5]      every cell on ROW 5      (x varies)
        numpy_array[:, 5]       every cell in COLUMN 5   (row varies)

    Same expression, transposed meanings. Read `cell[a, b]` as "column a, row
    b" -- the order a person says a screen position in, and the order `Size`,
    `scroll` and every coordinate in the engine already use.

    Nothing is being transposed for your convenience: the buffer really is
    stored x-major, `_grid` is `(cols, rows)`, so this order is free and the
    array order would be the one costing a transpose."""

    __slots__ = ("_console",)

    def __init__(self, console: "Console") -> None:
        self._console = console

    def __getitem__(self, cell: tuple) -> int | np.ndarray:
        """One cell's glyph id, or a whole run of them. Indexed `[x, y]` --
        X FIRST, screen order, NOT NumPy's row-first.

            code = console.cell[3, 5]        # column 3, row 5
            row = console.cell[:, 5]         # every glyph id on ROW 5
            column = console.cell[3, :]      # every glyph id in COLUMN 3

        The glyph id ALONE, with the packed facing masked off. A read hands
        back what a write takes, so a value can make the round trip; if it
        carried the facing, callers would write it back and the cell would
        then hold a facing the pen never chose. Facing is the pen's, at the
        moment of writing -- there is exactly one place it comes from."""
        x, y = cell
        if isinstance(x, int) and isinstance(y, int):
            return int(self._console._grid[x, y] & GLYPH_MASK)
        # The mask already copies, so the buffer stays private either way.
        return (self._console._grid[x, y] & GLYPH_MASK).astype(np.uint8)

    def __setitem__(self, cell: tuple, value: object) -> None:
        """Stamp one cell, or a whole run of them in one go. Indexed `[x, y]`
        -- X FIRST, screen order, NOT NumPy's row-first.

            console.cell[3, 5] = ord("A")          # column 3, row 5
            console.cell[:, 5] = 0                 # blank ROW 5
            console.cell[2:7, 5] = np.frombuffer(b"HELLO", np.uint8)

        The same rule a pen slot follows: a scalar fills, an array lands. A
        run is one NumPy write per buffer rather than one full cell write per
        column, which is the difference between blanking a line for free and
        paying ~12us a cell for it.

        Every cell written adopts the current pen and its facing, exactly as a
        single write does -- a bulk write is the same operation, wider."""
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

            self._console._grid[x, y] = self._console._facing_bits() | byte
            self._console._paint(x, y, self._console._pen.paint)
            # The one-cell fast path, unless a defer is open -- then this cell
            # rides along with everything else in its row band.
            if self._console._touch(y, y + 1):
                self._console._stamp(x, y)
            return
        self._console._write_run(x, y, value)


class GlyphAccess:
    """Indexed reads and writes over a console's 256 patterns."""

    __slots__ = ("_console",)

    def __init__(self, console: "Console") -> None:
        self._console = console

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
        screen.

        Do NOT build a pattern from this. A pattern is a NumPy array and
        therefore `(height, width)`, while this is `(width, height)` like
        every other size in the engine, so `np.zeros(console.glyph.size)` is
        transposed -- and on the square 8x8 charset that mistake still works,
        breaking only on 8x12. Take the shape from an existing pattern
        instead, which cannot be got wrong:

            pattern = np.zeros_like(console.glyph[0])
        """
        return Size(self._console._char_width, self._console._char_height)

    def __getitem__(self, glyph_id: int) -> np.ndarray:
        """A COPY of one glyph's plane-index pattern.

        A copy, so that editing it cannot corrupt the pre-flipped variant
        table behind the console's back. The consequence is that a
        read-modify-write must put the pattern back explicitly:

            pattern = console.glyph[code]
            pattern[0, 0] = 1
            console.glyph[code] = pattern       # <- required
        """
        return self._console._glyphs[self._index(glyph_id)].copy()

    def __setitem__(self, glyph_id: int, pattern: np.ndarray) -> None:
        byte = self._index(glyph_id)
        if not isinstance(pattern, np.ndarray):
            raise TypeError(
                f"glyph pattern must be an ndarray, got {type(pattern)}"
            )

        expected = self._console._glyphs.shape[1:]
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
            raise ValueError(
                "glyph pattern plane indices must be in the range 0..3"
            )

        self._console._set_pattern(byte, pattern)
        if np.any((self._console._grid & GLYPH_MASK) == byte):
            self._console._refresh()



class Cursor:
    """WHERE the console is writing, in CELLS. An address, and nothing else.

        console.cursor.x = 10           # move one axis
        console.cursor = (12, 4)        # move both
        x, y = console.cursor           # read it
        code = console.cell[console.cursor]

    Screen order, like every other surface in the engine: x across, y down,
    origin top-left. `write` and `print` advance it; `clear` homes it.

    **This class exists to make `console.cursor.x = 10` work, and for no
    other reason.** Kris: *"it was only ever about making Console().cursor.x
    feel usable and readable"*, and *"I think people want to do x = 10."*
    The alternative was `console.cursor_x` -- flat properties with a prefix
    doing the grouping, which is the same job `CellAccess` does for
    `console.cell[x, y]`, and why this sits with the access faces rather than
    in a module of its own. It is not a thing you hold; it is a shape a
    console presents.

    **It was tried as a `common.Location` and that is why the assignment is
    the whole specification.** A `NamedTuple` gives `console.cursor.x`,
    unpacking, hashing and equality for free, and would have deleted this
    class outright -- but it is immutable, so `console.cursor.x = 10` raises
    and single-axis moves become
    `console.cursor = (10, console.cursor.y)`. A read-only `.x` is `[0]` with
    a nicer name, and earns nothing a bare tuple would not. Mutability is the
    only thing here that is load-bearing, so this stays an object.

    **It does not carry `char`.** Kris: *"that doesn't even belong to the
    cursor at all"* -- an address should not dereference itself. Peeking and
    poking the cell it points at is `console.char`, beside `write` and
    `print`, the console's other operations at wherever the cursor is.

    Bounds live on the console, not here: `cols` and `rows` were never the
    position's to know, so every move goes through `Console._move_cursor`."""

    __slots__ = ("_console", "_x", "_y")

    def __init__(self, console: "Console") -> None:
        self._console = console
        self._x = 0
        self._y = 0

    @property
    def x(self) -> int:
        """Column, 0 at the left edge. Assigning moves the console's cursor,
        bounds-checked."""
        return self._x

    @x.setter
    def x(self, value: int) -> None:
        self._console._move_cursor(int(value), self._y)

    @property
    def y(self) -> int:
        """Row, 0 at the top edge. Assigning moves the console's cursor,
        bounds-checked."""
        return self._y

    @y.setter
    def y(self, value: int) -> None:
        self._console._move_cursor(self._x, int(value))

    def __iter__(self):
        return iter((self._x, self._y))

    def __repr__(self) -> str:
        return f"Cursor(x={self._x}, y={self._y})"
