"""A 256-glyph, byte-addressed character engine that is also a Layer.

This file is the class itself. The design record -- why it is one class and
not three -- and the map of the modules it is built from are in this
package's `__init__.py`.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Iterator, Optional, Tuple, Union

import numpy as np
from pyglet.gl import (
    GL_TEXTURE0,
    GL_TEXTURE1,
    GL_TRIANGLES,
    glActiveTexture,
    glBindTexture,
)

from blitspersecond.common import SCREEN, Size

from ..internal import Layer, PaletteTexture
from ..orientation import Orientation, applies_to
from ..palette import Palette, system_palette
from ..pixel_buffer import PixelBuffer
from ..atlas import Atlas
from . import shader

if TYPE_CHECKING:
    from blitspersecond.input.kbm import TextInput
from .charset import CHAR_COUNT, GLYPH_MASK, CharSet
from .internal import CellAccess, Cursor, GlyphAccess, PaintAccess
from .pen import Pen

#: One variant table slot per orientation, indexed BY THE ORIENTATION VALUE.
#:
#: Eight rather than four, and the index is the facing itself rather than a
#: shifted subset. Reference had four and packed `facing >> 1`, which works
#: only while TRANSPOSE is clear -- it hardcoded "a console cannot turn" into
#: the arithmetic. The rule is `applies_to`: a SQUARE cell may take all eight,
#: and 8x8 is a charset BPS ships. Indexing by the raw facing costs the
#: shift, serves both cases with no branch, and leaves the four turning slots
#: simply unfilled on a rectangular cell -- where the pen setter refuses them
#: anyway, so nothing ever gathers from them.
#:
#: The price is table memory: `_flat` materialises every slot, 256 glyphs
#: times the cell. 192KB at 8x12, half of it unused there; 128KB at 8x8 with
#: none wasted. Small enough to stay resident either way.
_VARIANTS = 8

#: A cell is ONE number: `facing << _FACING_SHIFT | glyph`.
#:
#: Two per-cell attributes, one plane. The shift is 8 because a console
#: addresses 256 glyphs (`charset.CHAR_COUNT`) and so a glyph id is a byte,
#: which makes the packed value *identical* to the index into `_flat`
#: -- so the render gather gathers, and does no arithmetic. It also means the
#: two halves cannot drift apart: `scroll` moves one buffer, `clear` fills one
#: buffer, and there is no second array to forget.
#:
#: The upper field is a VARIANT index; facing is merely all it currently
#: holds, in 3 of its 8 bits. That distinction is what the spare room is for:
#: a variant is a pre-computed remap of a glyph's plane indices, so ANY
#: per-cell attribute expressible as such a remap can widen this field and
#: stay free at render time. Inverse video is the obvious next one -- swap
#: plane 0 and plane 1 -- and would cost table memory and nothing else.
#:
#: Blink cannot join them: it varies with TIME, not with the cell, so there is
#: no static pattern to precompute. It belongs to the palette or to a
#: per-frame pass. That is the line -- a variant is a function of the cell.
_FACING_SHIFT = 8


class Console(Layer):
    """A grid of glyphs you can draw into, or type into -- and a Layer.

        console = Console()                       # ASCII8X12, 80x30
        bps.layers.add(console)
        console.write("ready")

        console = Console(CharSet.ASCII8X8)       # 80x45
        console = Console(40, 15)                 # by grid, cell worked back
        console = Console((16, 24))               # blank glyphs to fill
        console = Console(Atlas(buffer, 8, 8))    # your own font

    Two faces on one object, and a game uses whichever it came for. `cell`,
    `glyph` and `paint_buffer` are a byte-addressed glyph grid; `write`,
    `print` and `cursor` are a teletype over it. Neither taxes the other --
    see this package's `__init__` for why they are not two classes.

    Writes take ONE brush: the `pen`, which carries both the colour and the
    facing. Swap pens to change either; neither disturbs cells already on
    screen. Facing costs nothing at render time -- the mirrored patterns are
    derived once at construction and the facing folds into the glyph id, so a
    screen of mixed facings gathers exactly as an upright one does.

    COMPOSITING ON THE CPU IS THE POINT, NOT AN OVERSIGHT.

    A tile layer never touches pixel data: it batches quads and lets the GPU
    resample a shared atlas. This recomputes a whole screen image and
    re-uploads it when dirty. That looks like the slower design and is a
    deliberate trade, so that it does not get helpfully optimised away later.

    The reason is that a glyph is a STENCIL, not a picture. Its atlas holds
    plane indices, and the colour arrives at draw time from `paint_buffer` --
    an ordinary numpy array you can do arbitrary work on. That is the whole
    point of this surface: gradient and mask pens, coppers across the grid, a
    field computed per pixel and handed over whole. Every one of those is CPU
    nonsense done to a buffer, and it is nonsense this class exists to make
    cheap.

    Moving the resolve into a fragment shader would work -- sample the plane
    index, sample the paint buffer, select the slot -- and it is the wrong
    trade twice over. It swaps ~1.1ms of CPU recompose for re-uploading a
    900KB paint texture, which a program rewriting the field every frame pays
    anyway; and it puts the buffer somewhere numpy cannot reach, which costs
    the capability the CPU shape was bought for. The upload is not where the
    money goes regardless: 225KB is 4.6us and 13.8MB/s at 60Hz.

    If a console is ever genuinely too slow, the gather is what to look at --
    not the transfer, and not this.
    """

    def __init__(
        self,
        charset: Optional[Union[CharSet, Atlas, Tuple[int, int], int]] = None,
        rows: Optional[int] = None,
    ) -> None:
        char_width, char_height, sheet = self._resolve(charset, rows)

        self._char_width = char_width
        self._char_height = char_height
        self._cols, self._rows = self._screen_grid(char_width, char_height)

        # Every glyph pre-oriented, once: (variant, glyph, gh, gw). Orientation
        # then costs a wider gather rather than a branch -- `_refresh` folds
        # the variant into the glyph id and indexes a flat view of this, so the
        # composed shape and the work per cell are exactly what they were
        # before orientation existed. Zeroed rather than empty: on a
        # rectangular cell the four turning slots are never filled, and a
        # gather that somehow reached one should find blanks, not noise.
        self._variants = np.zeros(
            (_VARIANTS, CHAR_COUNT, char_height, char_width), dtype=np.uint8
        )
        # Variant 0 IS the upright table, as a view -- one store, so writing a
        # pattern through `glyph[id]` cannot leave the two disagreeing.
        self._glyphs = self._variants[0]
        if sheet is not None:
            # The first 256 cells, however the sheet happens to be laid out.
            # A sheet may hold more; the surplus has no glyph id to reach it.
            self._glyphs[:] = sheet.cells[:CHAR_COUNT]
        self._reflect()
        # A flat (variant * 256 + glyph) view of the same buffer, so the gather
        # is one index array and stays a view of the writes above.
        self._flat = self._variants.reshape(
            _VARIANTS * CHAR_COUNT, char_height, char_width
        )

        self._palette = system_palette()
        # A pen from the start, bound to this console's cells in the same
        # breath, so nothing downstream has to test for one. The SYSTEM pen:
        # Pen owns what that means, so this repeats no literal. White ink on a
        # transparent ground with the outline planes left alone -- which is why
        # a fresh console can be written on without choosing colours first,
        # and why it does not invent an outline colour on a font's behalf.
        self._pen = Pen(size=(char_width, char_height))
        # ONE plane, packed: see _FACING_SHIFT. Cell storage is x-major --
        # _grid[x, y] -- so every index in this file reads the way the API
        # does. Only the composed image stays row-major, because that is the
        # GPU's byte order rather than our choice. Left uninitialised because
        # clear() at the end of __init__ fills it.
        self._grid = np.empty((self._cols, self._rows), dtype=np.uint16)
        # Colour is screen-shaped -- one pen per PIXEL, not per cell. A plain
        # Pen paints a cell's slab with four constants and behaves exactly as
        # it always has; the room this leaves is for a pen that varies within
        # the cell, which a cell-shaped buffer could only hold the average of.
        self._colours = np.empty(
            (self._rows * char_height, self._cols * char_width, 4), dtype=np.uint8
        )
        self._cell = CellAccess(self)
        self._glyph = GlyphAccess(self)
        self._paint_buffer = PaintAccess(self)
        self._cursor = Cursor(self)
        # The line currently being echoed, where it started, and how wide it
        # was drawn last time -- the width so a backspace can blank the cells
        # it gave up rather than leaving the tail of the old text behind.
        self._echo: Optional["TextInput"] = None
        self._echo_at = (0, 0)
        self._echo_width = 0
        # Deferral state: how many `defer()` blocks are open, and the span of
        # CELL rows they have dirtied between them. `_dirty_top >
        # _dirty_bottom` means nothing is pending -- an empty span, not row 0.
        self._defer_depth = 0
        self._dirty_top = self._rows
        self._dirty_bottom = 0
        # The composed screen: one indexed buffer, uploaded when dirty and
        # drawn as a single quad. A canvas rather than a file, so it is not
        # shared with anything -- see PixelBuffer on why a size is never
        # interned.
        self._image = PixelBuffer(
            (self._cols * char_width, self._rows * char_height),
            "P",
            palette=self._palette,
        )
        # Built on first composite: a vertex list needs a current GL context
        # and construction must not.
        self._quad = None
        self.clear()
        # IS-A Layer: content and place are one object.
        super().__init__()

    # -- construction helpers ------------------------------------------------

    @staticmethod
    def _resolve(
        charset: Optional[Union[CharSet, Atlas, Tuple[int, int], int]],
        rows: Optional[int],
    ) -> tuple[int, int, Optional[Atlas]]:
        """Read the constructor's arguments into a cell size and the sheet to
        fill the glyph table from.

        Four roads in and one answer out, which is why they are gathered here
        rather than left in `__init__`: what follows them allocates buffers
        against a cell size and does not care how the size was arrived at.

            Console(CharSet.X)    a shipped font; the default road
            Console(atlas)        YOUR font -- any Atlas with 256 cells
            Console(cols, rows)   work the cell back from the grid, then take
                                  real letters IF a shipped set is that size
            Console((w, h))       a cell size, and no sheet -- 256 blank
                                  glyphs for a game that fills them itself

        A `None` sheet is not a failure. It is the blank-glyph road, and the
        grid road falls onto it silently on purpose: `Console(64, 45)` asks
        for 10x8 cells, which no shipped font is, and refusing would make a
        legal grid unreachable just because BPS ships two fonts."""
        # The one road that must validate early holds what it checked, so the
        # gate below does not run twice on it.
        validated: Optional[Atlas] = None
        if rows is not None:
            width, height = Console._cell_for_grid(charset, rows)
            found = CharSet.for_cell(width, height)
            sheet = found.atlas if found is not None else None
        elif isinstance(charset, Atlas):
            # Validated BEFORE the cell is read, because reading it is one of
            # the things a sheet can fail at: an atlas given its rects has no
            # cell, and asking would raise from inside `Atlas` rather than
            # against the console's own standard.
            Console._validate_sheet(charset)
            validated = charset
            width, height = charset.cell
            sheet = charset
        elif charset is None or isinstance(charset, CharSet):
            found = charset if charset is not None else CharSet.ASCII8X12
            width, height = found.cell
            sheet = found.atlas
        else:
            width, height = Console._validate_char_size(charset)
            sheet = None
        if sheet is not None and sheet is not validated:
            Console._validate_sheet(sheet)
        return width, height, sheet

    @staticmethod
    def _validate_sheet(sheet: Atlas) -> None:
        """What a console demands of a glyph sheet, wherever it came from.

        Here rather than on `CharSet` or on `Atlas` deliberately. `Atlas`
        knows only that it holds rects, which is true of a tileset and a
        sprite sheet too; the 256 is the CONSOLE's, because a cell packs its
        glyph id into a byte. Checking it here means a font you supply is held
        to exactly the standard a shipped one is, by the same code and with
        the same message."""
        if not sheet.gridded:
            raise ValueError(
                f"a glyph sheet must be an autogrid, and this atlas was given "
                f"its {len(sheet)} rects outright. A console measures its "
                f"screen in glyphs and packs a glyph id into a byte, so its "
                f"cells are one size by definition. Cut the font with "
                f"Atlas(buffer, w, h)."
            )
        if not sheet.indexed:
            raise ValueError(
                "a glyph sheet must be an indexed image -- a glyph is a "
                "stencil of plane indices, not a picture"
            )
        if len(sheet) < CHAR_COUNT:
            raise ValueError(
                f"a console addresses {CHAR_COUNT} glyphs; this sheet holds "
                f"{len(sheet)} ({sheet.cols}x{sheet.rows} cells of "
                f"{sheet.cell.width}x{sheet.cell.height})"
            )

    @staticmethod
    def _validate_char_size(value: object) -> tuple[int, int]:
        if not isinstance(value, tuple) or len(value) != 2:
            raise TypeError(
                "Console takes a CharSet, a (width, height) cell, or "
                "Console(cols, rows)"
            )
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
    def _cell_for_grid(cols: object, rows: int) -> tuple[int, int]:
        """The cell size a `cols` x `rows` grid needs, or a raise saying why
        that grid cannot exist."""
        if not isinstance(cols, int) or isinstance(cols, bool):
            raise TypeError(
                f"Console(cols, rows) takes two integers, got {type(cols)}"
            )
        if not isinstance(rows, int) or isinstance(rows, bool):
            raise TypeError(
                f"Console(cols, rows) takes two integers, got {type(rows)}"
            )
        if cols <= 0 or rows <= 0:
            raise ValueError(f"a console grid must be positive, got {cols}x{rows}")
        if SCREEN.width % cols or SCREEN.height % rows:
            raise ValueError(
                f"a {cols}x{rows} console does not tile the "
                f"{SCREEN.width}x{SCREEN.height} screen"
            )
        return SCREEN.width // cols, SCREEN.height // rows

    @staticmethod
    def _screen_grid(width: int, height: int) -> tuple[int, int]:
        if SCREEN.width % width or SCREEN.height % height:
            raise ValueError(
                f"character size {width}x{height} does not tile the "
                f"{SCREEN.width}x{SCREEN.height} screen"
            )
        return SCREEN.width // width, SCREEN.height // height

    # -- geometry ------------------------------------------------------------

    @property
    def cols(self) -> int:
        return self._cols

    @property
    def rows(self) -> int:
        return self._rows

    @property
    def size(self) -> Size:
        """The composed screen's size in pixels."""
        return self._image.size

    # -- palette and pen -----------------------------------------------------

    @property
    def palette(self) -> Palette:
        """The console's live index-to-colour table."""
        return self._palette

    @palette.setter
    def palette(self, value: Palette) -> None:
        if not isinstance(value, Palette):
            raise TypeError(f"expected Palette, got {type(value)}")
        self._palette = value
        self._image.palette = value

    @property
    def pen(self) -> Pen:
        """The brush this console writes with. Assigning replaces it -- that
        is how you swap a forwards pen for a backwards one. The console owns
        the brush and binds it to its own cell size."""
        return self._pen

    @pen.setter
    def pen(self, value: Pen) -> None:
        if not isinstance(value, Pen):
            raise TypeError(f"pen must be a Pen, got {type(value)}")
        facing = Orientation(value.facing)
        cell = (self._char_width, self._char_height)
        if not applies_to(facing, cell):
            raise ValueError(
                f"a {self._char_width}x{self._char_height} glyph cell cannot "
                f"take {facing!r}: a quarter turn would make it "
                f"{self._char_height}x{self._char_width}. A rectangular cell "
                f"faces NONE, FLIP_H, FLIP_V or ROT_180; a square one takes "
                f"all eight"
            )
        # Binds on first use and freezes; raises if this pen already carries a
        # different geometry.
        value._bind(self._char_width, self._char_height)
        self._pen = value

    def _facing_bits(self) -> int:
        """The current pen's facing, shifted into its half of a packed cell.

        The facing value IS the variant index -- see `_VARIANTS` -- so this is
        a shift and nothing else: no lookup, no branch, and no assumption
        about which orientations a cell allows."""
        return int(self._pen.facing) << _FACING_SHIFT

    # -- the three access faces ----------------------------------------------

    @property
    def cell(self) -> CellAccess:
        return self._cell

    @property
    def glyph(self) -> GlyphAccess:
        return self._glyph

    @property
    def paint_buffer(self) -> PaintAccess:
        """What this console's glyphs are painted with: four palette indices
        per pixel, addressed `[row, col, slot]` -- ARRAY order, not the cell
        grid's screen order. The input to compositing. See PaintAccess."""
        return self._paint_buffer

    @property
    def cursor(self) -> Cursor:
        """Where the console is writing, in CELLS -- origin top-left.

            console.cursor.x = 10                # move one axis
            console.cursor = (12, 4)             # move both
            x, y = console.cursor                # read it
            code = console.cell[console.cursor]  # index with it

        `write` and `print` advance it, `clear` homes it, and `console.char`
        reads and writes the cell it sits on."""
        return self._cursor

    @cursor.setter
    def cursor(self, value: Tuple[int, int]) -> None:
        """Move the cursor to `(x, y)`.

        A `Cursor` is accepted too, and means the same thing -- it unpacks as
        its coordinate pair, so `here.cursor = there.cursor` puts this cursor
        where that one is. That is NOT the 08-21 disguise case it resembles:
        the trap there was an assignment that looked like replacement and
        quietly did something else, and a cursor has no replacement semantics
        to be mistaken for. There is one cursor per console for its lifetime,
        and moving is the only thing this can mean."""
        x, y = value
        self._move_cursor(int(x), int(y))

    def _move_cursor(self, x: int, y: int) -> None:
        """The one place the cursor moves, and the one place it is bounded.

        Bounds live here rather than on `Cursor` because they are `cols` and
        `rows` -- this console's, never the position's. `Cursor.x` and
        `Cursor.y` come back through here for that reason."""
        if not (0 <= x < self._cols and 0 <= y < self._rows):
            raise ValueError(
                f"cursor {(x, y)} outside console "
                f"0,0-{self._cols - 1},{self._rows - 1}"
            )
        self._cursor._x, self._cursor._y = x, y

    @property
    def char(self) -> int:
        """PEEK and POKE the cell the cursor is on, without moving it.

            code = console.char             # peek
            console.char = ord("A")         # poke

        Kris: *"char was really just a way to peek and poke"*, and that is
        the whole specification -- character RAM is a byte array, the cursor
        is an address into it, and this reads or writes the byte there. An
        unsigned byte, 0..255; a poke adopts the current pen, as every cell
        write does.

        It sits on the console rather than on the cursor, where it used to
        be: an address should not also dereference itself. Here it is beside
        `write` and `print`, the console's other operations at wherever the
        cursor happens to be, and the difference is only that this one does
        not advance.

        The explicit road is `cell`, and it is the more capable one -- runs,
        slices, whole rows. This is the peek-and-poke spelling for a game
        that is already driving the cursor."""
        return int(self.cell[self._cursor.x, self._cursor.y])

    @char.setter
    def char(self, value: int) -> None:
        self.cell[self._cursor.x, self._cursor.y] = value

    # -- writing -------------------------------------------------------------

    def clear(self, pen: Optional[Pen] = None) -> None:
        """Blank the whole console -- every cell zeroed to glyph 0, painted
        with the current pen and facing the way the pen faces -- and home the
        cursor to (0, 0).

        Pass a `Pen` to paint every cleared cell with it and leave it as the
        current pen for subsequent writes.

        There is no partial-clear mode. Blanking part of the screen is writing
        cells, so it goes through the grid:

            console.cell[:, LYRIC_ROW] = 0     # blank one line

        which is one NumPy write rather than a run of stamped spaces, and
        leaves empty cells rather than glyph 32."""
        if pen is not None:
            self.pen = pen
        # Glyph 0 under the pen's facing is one packed value, so one fill.
        self._grid.fill(self._facing_bits())
        self._flood()
        self._refresh()
        self._move_cursor(0, 0)

    def write(
        self,
        text: str,
        *,
        at: Optional[Tuple[int, int]] = None,
    ) -> None:
        r"""Stamp `text` at the cursor in the current pen, advancing across the
        line, wrapping at the right edge and scrolling at the bottom. `\n`
        starts a new line. Pass `at=(x, y)` to move before writing.

        Each cell write adopts the current pen."""
        # A line that finished this frame is settled before anything else is
        # written, so a reply printed the moment `completed` turned true lands
        # on the row below the answer rather than appended to it.
        self._settle_echo()
        if at is not None:
            self.cursor = at
        for ch in text:
            if ch == "\n":
                self._newline()
                continue
            self.cell[self._cursor.x, self._cursor.y] = ord(ch)
            x = self._cursor.x + 1
            if x >= self._cols:
                self._newline()
            else:
                self._move_cursor(x, self._cursor.y)

    def print(
        self,
        value: object = "",
        *,
        at: Optional[Tuple[int, int]] = None,
    ) -> None:
        """Write an object's string form, then start a new line."""
        self.write(f"{value}\n", at=at)

    def echo(self, entry: "TextInput") -> "TextInput":
        """Show a line of typing as it happens, from the current cursor.

        Returns the line it was given, so the call reads as one phrase::

            name = console.echo(bps.keyboard.input().until(key.RETURN))

        Nothing blocks and nothing is polled by the caller. The line is fed by
        the engine's cut, and this layer redraws it each tick until it
        finishes. When it does, the cursor drops to the start of the next row
        so whatever is printed next begins on a clean line -- the terminator
        is what ended the line, not a character in it, so it is never shown.

        One at a time, matching the keyboard: echoing a new line stops
        redrawing the previous one, leaving whatever it had already drawn on
        screen as ordinary text.
        """
        self._settle_echo()
        self._echo = entry
        self._echo_at = (self._cursor.x, self._cursor.y)
        self._echo_width = 0
        self._paint_echo()
        return entry

    def _paint_echo(self) -> None:
        """Paint the line where it stands, clearing what it used to be.

        Blanks its own previous width rather than tracking edits: a backspace
        shortens the line, and the cells it gave up are still holding the
        characters that were there.
        """
        entry = self._echo
        if entry is None:
            return
        x, y = self._echo_at
        text = entry.value
        was, self._echo_width = self._echo_width, len(text)
        self._move_cursor(x, y)
        self._write_echo(text + " " * max(0, was - len(text)))
        # Leave the cursor where the next character will go, which is also
        # where a caret belongs if one is ever drawn.
        self._move_cursor(min(x + len(text), self._cols - 1), y)

    def _settle_echo(self) -> None:
        """Finish a line that has stopped listening, once.

        Called before any other write, and not only when the layer next
        advances. The game learns a line completed DURING its tick, and the
        obvious next thing it does is print a reply -- which has to land on
        the row below the answer, not appended to it. Advancing is too late:
        that happens after the tick has already printed.
        """
        entry = self._echo
        if entry is None or entry.listening:
            return
        # Cleared first, so the write below does not re-enter this.
        self._echo = None
        if entry.completed:
            self._paint_echo_final(entry)

    def _paint_echo_final(self, entry: "TextInput") -> None:
        x, y = self._echo_at
        text = entry.value
        was, self._echo_width = self._echo_width, len(text)
        self._move_cursor(x, y)
        self._write_echo(text + " " * max(0, was - len(text)))
        # The terminator ended the line, it is not a character in it. Drop to
        # the start of the next row so a reply begins on a clean one.
        self._move_cursor(x, y)
        self._newline()

    def _write_echo(self, text: str) -> None:
        """`write` without the settle guard, for the echo's own painting."""
        for ch in text:
            if ch == "\n":
                self._newline()
                continue
            self.cell[self._cursor.x, self._cursor.y] = ord(ch)
            x = self._cursor.x + 1
            if x >= self._cols:
                self._newline()
            else:
                self._move_cursor(x, self._cursor.y)

    def _advance(self) -> None:
        """One tick of playback: repaint the line being typed, if any.

        Called by `Layers._advance` after the game's tick and before the frame
        is composed, so a character settled this frame is on screen in the
        frame it was settled in.
        """
        entry = self._echo
        if entry is None:
            return
        if entry.listening:
            self._paint_echo()
        else:
            # The game never looked. Finish it anyway.
            self._settle_echo()

    def _newline(self) -> None:
        y = self._cursor.y + 1
        if y >= self._rows:
            self.scroll(0, -1)
            y = self._rows - 1
        self._move_cursor(0, y)

    def _write_run(
        self,
        x: Union[int, slice],
        y: Union[int, slice],
        value: Any,
    ) -> None:
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

        # Index with what the caller gave, not the widened slices: an int index
        # keeps its axis flat, so `cell[2:7, 3]` takes a length-5 run rather
        # than demanding a (5, 1) column.
        target = self._grid[x, y]
        codes = np.asarray(value)
        if codes.size and (codes.min() < 0 or codes.max() > GLYPH_MASK):
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

    def _cell_rows_touched(self, key: Any) -> tuple[int, int]:
        """The band of CELL rows a `paint_buffer[...]` write can have
        disturbed.

        Read off the leading index only, and conservatively: anything not
        obviously a contiguous row range means the whole screen. Getting this
        wrong in the narrow direction leaves stale pixels on screen, so it errs
        wide by construction -- a whole-plane recompose is 1.2ms, a wrong one
        is a rendering bug."""
        leading = key[0] if isinstance(key, tuple) else key
        height = self._colours.shape[0]
        top, bottom = 0, height
        if isinstance(leading, slice):
            start, stop, step = leading.indices(height)
            if step == 1 and start < stop:
                top, bottom = start, stop
        elif isinstance(leading, (int, np.integer)) and not isinstance(
            leading, bool
        ):
            top = int(leading) % height
            bottom = top + 1
        # Pixel rows to cell rows, rounding outwards: a write that clips a cell
        # still changes that cell's composed pixels.
        gh = self._char_height
        return top // gh, -(-bottom // gh)

    # -- deferral ------------------------------------------------------------

    @contextmanager
    def defer(self) -> Iterator["Console"]:
        """Suspend recomposing until the block ends, then do it once.

            with console.defer():
                console.cell[:, :] = codes.T
                console.paint_buffer[:] = field

        NOT called `batch`, deliberately: `Batch` is pyglet's word for a draw
        batch, and this engine already uses it that way. Both of those are
        nouns holding geometry; this is a verb that holds off.

        Every write recomposes what it touched, which is right for one write
        and wasteful for two: the pair above recomposes the whole screen
        twice, and the first result is discarded by the second. Measured on a
        full-screen demo that is ~1.26ms a frame of pure waste, against 3.74ms
        for the whole thing.

        Explicit rather than lazy on purpose. A console that deferred by
        itself would have to guess when you were finished, and the honest
        moment to guess is `composite` -- by which time a collision query may
        already have answered against a screen that had not been drawn yet.

        Nests, so a helper may defer without knowing whether its caller
        already did. Recomposes on the way out of an exception too: leaving
        the screen out of step with the buffers would turn a handled error
        into a rendering bug much further away.

        The dirty region is a SPAN of cell rows, not a set, so touching row 2
        and row 28 recomposes everything between them."""
        self._defer_depth += 1
        try:
            yield self
        finally:
            self._defer_depth -= 1
            if self._defer_depth == 0:
                self._sync()

    def _sync(self) -> None:
        """Recompose whatever a deferral dirtied, if any, and forget it.

        Every pillar has one of these: the step that makes what will be drawn
        agree with what was asked for. `TileMap._sync` expands its placements
        into vertex corners; this recomposes the cell rows a `defer` block left
        outstanding. Same name because it is the same guarantee, and the name
        states the guarantee rather than the mechanism -- there is nothing to
        "flush" on the tile pillar, but there is always something to sync."""
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

    # -- scrolling -----------------------------------------------------------

    def scroll(self, x: object, y: object = None) -> None:
        """Move the cell grid by `x, y` cells without wrapping.

            console.scroll(0, 1)          # down one line
            console.scroll((0, 1))        # the same, as a pair

        Both forms, because a coordinate pair is one idea and should not need
        a tuple built round it to be heard -- and because code holding an
        `(x, y)` it got from somewhere else should not have to unpack it.

        Positive x moves contents right and positive y moves them down. Moved
        cells keep their colours and their facing; newly exposed cells are
        glyph 0 in the current pen and the current orientation.

        DESTRUCTIVE, and relative -- the AMOS sense of the word, which is this
        pillar's lineage. HSCROLL and VSCROLL move the text itself by a
        character at a time and, in the manual's words, "blank lines are
        inserted to fill any gaps left by these scrolling operations". Same
        contract here: contents move, whatever falls off the edge is gone, and
        the gap comes back blank. Blank in the CURRENT pen, which is the one
        place we go further than AMOS did -- so a pen swapped since the last
        write shows in the new lines, not the old ones.
        See docs/references/amos/05-06-text.md.

        A tile layer's `scroll` is a DIFFERENT idea under the same name: an
        absolute camera position, a property, moving the VIEW and destroying
        nothing. Not an inconsistency -- a different lineage, and forced by a
        different extent. A tile layer has a world larger than the screen for
        a camera to move over; a console's grid IS the screen, so there is no
        camera to move and shifting the contents is the only thing the word
        can mean here."""
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
            # cannot do otherwise, because they are the same number. One buffer
            # moves, so there is no second one to keep in step.
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

    # -- the variant table ---------------------------------------------------

    def _reflect(self, byte: Optional[int] = None) -> None:
        """Derive the oriented variants from the upright ones -- all 256
        patterns, or one when a pattern is replaced.

        A slot's index IS its orientation, so this walks the orientations and
        applies each one's composition directly: TRANSPOSE first, then FLIP_H,
        then FLIP_V, which is Tiled's order and the one the enum documents. A
        glyph is `(gh, gw)`, so x is axis -1 and y is axis -2.

        On a rectangular cell the four transposing variants have no shape to
        live in and are skipped -- the pen setter refuses those facings, so
        nothing ever gathers from the blanks they leave."""
        cell = (self._char_width, self._char_height)
        source = self._variants[0] if byte is None else self._variants[0, byte]
        for value in range(1, _VARIANTS):
            facing = Orientation(value)
            if not applies_to(facing, cell):
                continue
            turned = source
            if facing & Orientation.TRANSPOSE:
                turned = turned.swapaxes(-2, -1)
            if facing & Orientation.FLIP_H:
                turned = turned[..., ::-1]
            if facing & Orientation.FLIP_V:
                turned = turned[..., ::-1, :]
            if byte is None:
                self._variants[value] = turned
            else:
                self._variants[value, byte] = turned

    def _set_pattern(self, byte: int, pattern: np.ndarray) -> None:
        """Replace one glyph's upright pattern and re-derive its variants, so
        a font edited at runtime stays consistent in every orientation."""
        self._variants[0, byte] = pattern
        self._reflect(byte)

    # -- painting: what the glyphs are drawn WITH -----------------------------

    def _flood(self) -> None:
        """Repaint every cell's colour with the current pen -- one copy of the
        pen per cell, which is what makes `clear()` under a fiddled pen come
        back as a screen of that pen rather than one stretched copy.

        The cell-shaped view is deliberate: `np.tile` would build the whole
        screen as a temporary first, and that is an allocation on the frame
        path (a console newline floods). Broadcasting into a reshaped view
        allocates nothing and measured ~7x faster than a constant broadcast."""
        paint = self._pen._bind(self._char_width, self._char_height)
        self._colours.reshape(
            self._rows, self._char_height, self._cols, self._char_width, 4
        )[:] = paint[None, :, None, :, :]

    def _paint(self, x: int, y: int, colour: np.ndarray) -> None:
        """Fill one cell's colour slab from the pen's `(gh, gw, 4)` paint -- a
        contiguous copy, which measured 2.4x faster than broadcasting four
        constants across the same slab."""
        top = y * self._char_height
        left = x * self._char_width
        self._colours[
            top : top + self._char_height, left : left + self._char_width
        ] = colour

    # -- compositing: glyph plus paint into the image -------------------------

    def _refresh(self) -> None:
        # The whole-screen recompose, and the one an open defer swallows --
        # `clear`, `scroll` and a replaced glyph pattern all arrive here, so
        # the check belongs in this one place rather than at each of them.
        if not self._touch(0, self._rows):
            return
        # Cells are (x, y, gh, gw); the image is row-major, so the transpose to
        # (y, gh, x, gw) is where the two orders meet -- once, here. Colour is
        # already screen-shaped, so it takes no part in the reshuffle. Facing
        # needs no unpacking: a packed cell IS the index into the variant
        # table, so this is a plain gather with no arithmetic.
        planes = (
            self._flat[self._grid]
            .transpose(1, 2, 0, 3)
            .reshape(self._colours.shape[:2])
        )
        self._image.pixels[:] = np.take_along_axis(
            self._colours, planes[..., None], axis=2
        )[..., 0]
        self._image.dirty = True

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

    def _stamp(self, x: int, y: int) -> None:
        glyph = self._flat[self._grid[x, y]]
        glyph_height, glyph_width = glyph.shape
        top = y * glyph_height
        left = x * glyph_width
        colours = self._colours[
            top : top + glyph_height, left : left + glyph_width
        ]
        self._image.pixels[
            top : top + glyph_height, left : left + glyph_width
        ] = np.take_along_axis(colours, glyph[..., None], axis=2)[..., 0]
        self._image.dirty = True

    # -- drawing -------------------------------------------------------------

    def _build_quad(self, program, texture) -> None:
        """One quad covering the console, built once.

        `picture.vert` takes `position`, `tex_coords`, `translate` and
        `colors`. The console never moves and never tints, so translate and
        colours are constant -- but they are per-vertex attributes the shader
        declares, so they are supplied rather than left unbound."""
        width, height = self._image.size
        self._quad = program.vertex_list_indexed(
            4,
            GL_TRIANGLES,
            indices=[0, 1, 2, 0, 2, 3],
            position=(
                "f",
                (0.0, 0.0, 0.0,
                 width, 0.0, 0.0,
                 width, height, 0.0,
                 0.0, height, 0.0),
            ),
            tex_coords=("f", texture.tex_coords),
            translate=("f", (0.0, 0.0, 0.0) * 4),
            colors=("f", (1.0, 1.0, 1.0, 1.0) * 4),
        )

    def _composite(self) -> None:
        """Draw the composed screen: one indexed quad, palette on unit 1."""
        # A safety net, not the mechanism: a defer left open across a frame is
        # a caller's bug, but drawing a stale screen because of it would be a
        # much harder bug to find than the missing dedent.
        self._sync()
        program = shader.program()
        # Uploads if the recompose above marked it dirty.
        texture = self._image.texture
        palette = PaletteTexture.get(self._palette)
        if self._quad is None:
            self._build_quad(program, texture)

        program.use()
        program["sprite_texture"] = 0
        program["palette_texture"] = 1
        glActiveTexture(GL_TEXTURE1)
        glBindTexture(palette.target, palette.id)
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(texture.target, texture.id)
        # Blend belongs to the compositor: it is on for the whole walk. See
        # `Display._compose`.
        assert self._quad is not None
        self._quad.draw(GL_TRIANGLES)
        program.stop()

    def __repr__(self) -> str:
        return f"<Console {self._cols}x{self._rows} cells [{self.tag}]>"
