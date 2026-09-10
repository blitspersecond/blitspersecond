"""A 256-glyph, byte-addressed character engine that is also a Layer.

This file is the class itself. The design record -- why it is one class and
not three -- and the map of the modules it is built from are in this
package's `__init__.py`.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import (
    TYPE_CHECKING,
    Any,
    FrozenSet,
    Hashable,
    Iterator,
    Optional,
    Tuple,
    Union,
)

import numpy as np
from pyglet.gl import (
    GL_TEXTURE0,
    GL_TEXTURE1,
    GL_TRIANGLES,
    glActiveTexture,
    glBindTexture,
)

from blitspersecond.common import SCREEN, Size

from ..collision import CWORD, pack_store, words_for
from ..internal import Layer
from ..orientation import Orientation, applies_to
from ..palette import PaletteBank
from ..pixel_buffer import PixelBuffer
from ..atlas import Atlas
from . import shader

if TYPE_CHECKING:
    from blitspersecond.input.kbm import TextInput
from .charset import CHAR_COUNT, GLYPH_MASK, CharSet
from .internal import CellAccess, Cursor, GlyphAccess, PaintAccess
from .pen import PLANE_0, PLANE_1, PLANE_2, PLANE_3, Pen

#: One variant table slot per orientation, indexed BY THE ORIENTATION VALUE.
#:
#: Eight rather than four, and the index is the facing itself rather than a
#: shifted subset, so a SQUARE cell may take all eight (`applies_to`) with no
#: branch. On a rectangular cell the four turning slots stay unfilled and the
#: pen setter refuses them, so nothing ever gathers from them.
#:
#: Why eight and what the table costs: `bps/reference/graphics/console.md`, "The
#: variant table".
_VARIANTS = 8

#: A cell is ONE number: `facing << _FACING_SHIFT | glyph`.
#:
#: Two per-cell attributes, one plane. The shift is 8 because a console
#: addresses 256 glyphs (`charset.CHAR_COUNT`) and so a glyph id is a byte,
#: which makes the packed value *identical* to the index into `_flat` -- so
#: the render gather gathers, and does no arithmetic. It also means the two
#: halves cannot drift apart: `scroll` moves one buffer, `clear` fills one
#: buffer, and there is no second array to forget.
#:
#: The upper field is a VARIANT index and facing is only what it holds today.
#: What may widen it, and what may not: `bps/reference/graphics/console.md`, "A cell is
#: one number".
_FACING_SHIFT = 8

#: The collision vocabulary: the four glyph planes, by number. Built from the
#: pen's constants rather than written out, so the two ends of one index --
#: what a plane draws, what it collides as -- cannot drift apart.
_PLANES: FrozenSet[Hashable] = frozenset(
    (PLANE_0, PLANE_1, PLANE_2, PLANE_3)
)

#: Where the silhouette lives in the plane store. The four glyph planes index
#: themselves -- `PLANE_0` IS 0 -- so only the unnamed one needs a slot, and
#: it takes the row past them rather than displacing the numbering.
_SILHOUETTE_SLOT = 4


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

        # A bank, not one table. Every bank comes up holding the system
        # palette in every row, so a fresh console writes in real, named
        # colours with nothing to build first -- and the spare rows are a
        # colour scheme away: write them, then move the register.
        self._palette = PaletteBank()
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
        self._colors = np.empty(
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
        )
        # Built on first composite: a vertex list needs a current GL context
        # and construction must not.
        self._quad = None
        # The five collision planes -- four glyph planes and the silhouette --
        # allocated once and never rebound. 29KB each at 640x360, so 141KB for
        # a console that collides and the same for one that does not, which is
        # not worth an `is None` on the read path. What `collidable` buys is
        # the FILLING, and that is the half with a cost worth switching off.
        #
        # Kept rather than derived on demand, which is the change of 2026-09-07
        # and the one thing this pillar gave up: an answer must be about the
        # frame that was PAINTED, and a plane pulled mid-tick is about neither
        # that frame nor the next one. See `bps/reference/graphics/collision.md`,
        # "When a question is answered".
        self._collision: np.ndarray = np.zeros(
            (
                len(_PLANES) + 1,
                self._rows * char_height,
                words_for(self._cols * char_width),
            ),
            dtype=CWORD,
        )
        # The cell rows whose bits no longer describe the image, repacked once
        # at the commit. Starts FULL so the first commit fills through the same
        # branch every later one uses -- no-prior is not a separate case, it is
        # the initial dirty state. Set before `clear()`, which composes and
        # therefore reaches `_touch`.
        self._collision_top: int = 0
        self._collision_bottom: int = self._rows
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
        if sheet.buffer.mode != "P":
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
    def palette(self) -> PaletteBank:
        """The console's colour tables, live.

        A whole bank. Writing into it recolours what is already on screen at
        the next compose -- no recompose, because the pixels are indices and
        only the table changed. Hand the same bank to another layer and they
        share it, deliberately; `copy()` is how one of them stops.
        """
        return self._palette

    @palette.setter
    def palette(self, value: PaletteBank) -> None:
        if not isinstance(value, PaletteBank):
            raise TypeError(f"expected PaletteBank, got {type(value)}")
        self._palette = value

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

    def prepare(self) -> None:
        """The `Layer` BEGIN slot. This pillar's work is the typed line."""
        self._repaint_input()

    def _repaint_input(self) -> None:
        """Bring the drawn input line up to date: repaint it while it is
        being typed, or paint its finished state once it is not.

        Runs before the game's tick, so a line the game reads this tick is
        already on screen as the game sees it.

        `_settle_echo` is NOT this method's to own -- every write path calls
        it first, because a game that learns a line completed during its tick
        prints the reply before the layer is next prepared, and that reply
        must land on the row below the answer.
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
        self._colors[top:bottom, left:right].reshape(
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
        height = self._colors.shape[0]
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

        Every write recomposes what it touched, which is right for one write
        and wasteful for two: without the block the pair above recomposes the
        whole screen twice and the first result is discarded by the second.

        Nests, so a helper may defer without knowing whether its caller
        already did. Recomposes on the way out of an exception too: leaving
        the screen out of step with the buffers would turn a handled error
        into a rendering bug much further away.

        The dirty region is a SPAN of cell rows, not a set, so touching row 2
        and row 28 recomposes everything between them.

        Why it is explicit rather than lazy, why it is not called `batch`, and
        what the waste measures: `bps/reference/graphics/console.md`, "What changed,
        and what recomposes"."""
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

    def _settle_collision(self) -> None:
        """Recompose what a deferral left, then repack it. The `Layer` hook.

        The recompose comes with it rather than staying behind `enabled`,
        because the silhouette is the composed PIXELS -- a hidden console that
        packed its planes off an image it had not recomposed would answer
        about the text before the last one.
        """
        self._sync()
        self._refresh_collision()

    def _refresh_collision(self) -> None:
        """Repack the dirty cell rows into the planes. Called by the commit.

        **After the frame is painted, and only there.** The planes answer
        about the frame that was drawn, so this runs where the drawing runs
        and a question never reaches it -- see `collision_plane`.

        One gather for the whole band, five packs off it. The gather is what a
        named plane costs (0.217ms against 0.020ms for the silhouette, which
        rides pixels the recompose already wrote), and asking for it once for
        all four planes is only possible because they are derived together at
        one moment. On demand they could not share it without retaining the
        ~900KB index array across the tick, which is the trade this pillar was
        right to refuse.

        It IS a second gather on a frame where `_refresh_rows` already gathered
        the same band. Sharing that would mean holding it across the tick for
        the same reason, and the band is bounded by what actually changed: two
        rows of thirty is two thirtieths of a gather.

        Row-aligned throughout, so there is no edge mask anywhere. A cell row
        is a whole number of pixel rows and 640 pixels is 20 words with no
        tail, so a band is a contiguous slice of every plane.
        """
        first, last = self._collision_top, self._collision_bottom
        self._collision_top, self._collision_bottom = self._rows, 0
        if not self._collidable or first >= last:
            return
        top, bottom = first * self._char_height, last * self._char_height
        # The silhouette is the composed PIXELS -- `!= 0` -- and deliberately
        # not `planes != 0`; `collision_plane` says why the two disagree on
        # purpose.
        self._collision[_SILHOUETTE_SLOT, top:bottom] = pack_store(
            self._image.pixels[top:bottom] != 0
        )
        planes = self._plane_indices(first, last)
        for slot in range(len(_PLANES)):
            self._collision[slot, top:bottom] = pack_store(planes == slot)

    def _touch(self, first: int, last: int) -> bool:
        """Note that cell rows `first`..`last` need recomposing.

        Returns True if the caller should recompose them itself, False if a
        deferral has taken responsibility. Every path that would redraw goes
        through here, so there is one place that knows the rule."""
        # Whatever changed, the planes no longer describe it. This is the one
        # funnel every write reaches -- a cell, a run of text, a paint band, a
        # replaced glyph pattern, a scroll, a clear -- so the masks are
        # invalidated once here rather than at each of them, and the deferral
        # branch below is beside the point: content changed either way.
        #
        # **One funnel matters more here than it looks.** There are THREE
        # painters of `_image.pixels` -- `_refresh`, `_refresh_rows` and
        # `_stamp`, the one-cell fast path -- and repacking at each would be
        # three chances for a hitbox to drift off what was drawn. `_stamp`
        # also writes a SUB-WORD span, so it would need a masked
        # read-modify-write in the path whose whole purpose is to be cheap.
        # Noting the band here and repacking it once at the commit costs that
        # path two integer comparisons.
        self._collision_top = min(self._collision_top, first)
        self._collision_bottom = max(self._collision_bottom, last)
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
            colors = self._colors[
                src_y0 * gh : src_y1 * gh, src_x0 * gw : src_x1 * gw
            ].copy()
        else:
            cells = None
            colors = None

        self._grid.fill(self._facing_bits())
        self._flood()

        if cells is not None and colors is not None:
            dst_x0 = max(x, 0)
            dst_y0 = max(y, 0)
            dst_x1 = dst_x0 + cells.shape[0]
            dst_y1 = dst_y0 + cells.shape[1]
            self._grid[dst_x0:dst_x1, dst_y0:dst_y1] = cells
            self._colors[
                dst_y0 * gh : dst_y1 * gh, dst_x0 * gw : dst_x1 * gw
            ] = colors

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
        self._colors.reshape(
            self._rows, self._char_height, self._cols, self._char_width, 4
        )[:] = paint[None, :, None, :, :]

    def _paint(self, x: int, y: int, color: np.ndarray) -> None:
        """Fill one cell's colour slab from the pen's `(gh, gw, 4)` paint -- a
        contiguous copy, which measured 2.4x faster than broadcasting four
        constants across the same slab."""
        top = y * self._char_height
        left = x * self._char_width
        self._colors[
            top : top + self._char_height, left : left + self._char_width
        ] = color

    # -- compositing: glyph plus paint into the image -------------------------

    def _refresh(self) -> None:
        # The whole-screen recompose, and the one an open defer swallows --
        # `clear`, `scroll` and a replaced glyph pattern all arrive here, so
        # the check belongs in this one place rather than at each of them.
        if not self._touch(0, self._rows):
            return
        planes = self._plane_indices(0, self._rows)
        self._image.pixels[:] = np.take_along_axis(
            self._colors, planes[..., None], axis=2
        )[..., 0]
        self._image.dirty = True

    def _plane_indices(self, first: int, last: int) -> np.ndarray:
        """Cell rows `first`..`last` as screen-shaped glyph plane indices, 0..3.

        Cells are (x, y, gh, gw); the image is row-major, so the transpose to
        (y, gh, x, gw) is where the two orders meet -- once, here. Colour is
        already screen-shaped, so it takes no part in the reshuffle. Facing
        needs no unpacking: a packed cell IS the index into the variant table,
        so this is a plain gather with no arithmetic.

        **Both readers of the console's glyphs come through here**: the
        recompose, which turns these into colours through the pen, and the
        collision derivation, which turns them into bits. That is the reason
        it is a method rather than three copies of one expression -- the two
        must agree about which plane a pixel belongs to or a hitbox drifts off
        the thing it was drawn on.
        """
        return (
            self._flat[self._grid[:, first:last]]
            .transpose(1, 2, 0, 3)
            .reshape(
                (last - first) * self._char_height,
                self._cols * self._char_width,
            )
        )

    def _refresh_rows(self, first: int, last: int) -> None:
        """Recompose only rows `first` up to `last` into the image.

        `_refresh` does the whole screen; clearing one line has no business
        paying for 30 of them."""
        top = first * self._char_height
        bottom = last * self._char_height
        planes = self._plane_indices(first, last)
        self._image.pixels[top:bottom] = np.take_along_axis(
            self._colors[top:bottom], planes[..., None], axis=2
        )[..., 0]
        self._image.dirty = True

    def _stamp(self, x: int, y: int) -> None:
        glyph = self._flat[self._grid[x, y]]
        glyph_height, glyph_width = glyph.shape
        top = y * glyph_height
        left = x * glyph_width
        colors = self._colors[
            top : top + glyph_height, left : left + glyph_width
        ]
        self._image.pixels[
            top : top + glyph_height, left : left + glyph_width
        ] = np.take_along_axis(colors, glyph[..., None], axis=2)[..., 0]
        self._image.dirty = True

    # -- drawing -------------------------------------------------------------

    # -- collision: packed at the commit, read for nothing --------------------

    def _collision_refusal(self) -> Optional[str]:
        """Never refused -- a console's image is indexed, always.

        The gate asks whether the art CAN derive a mask at all. `_image` is
        mode "P" from construction with no way to make it anything else, so
        unlike a tilemap -- whose tileset may be RGBA -- there is nothing to
        check. Eligible is not the same as on: `Layer` starts every layer at
        `collidable = False` and this pillar keeps that default, which costs a
        console that never collides exactly nothing.
        """
        return None

    @Layer.collidable.setter  # type: ignore[misc]
    def collidable(self, value: bool) -> None:
        Layer.collidable.fset(self, value)  # type: ignore[misc]
        if value:
            # Nothing was packed while the switch was off, so the whole screen
            # is dirty and the next commit fills it. Until then the planes read
            # empty, which is the honest answer: this layer was not being
            # masked when the last frame was painted.
            self._collision_top, self._collision_bottom = 0, self._rows
        else:
            # Zeroed rather than freed. The buffers are 141KB and permanent --
            # `collidable` buys the packing, not the bytes -- so the only thing
            # switching off has to do is make sure a read finds nothing rather
            # than history.
            self._collision[:] = 0

    @property
    def planes(self) -> FrozenSet[Hashable]:
        """All four glyph planes, by number: `PLANE_0` .. `PLANE_3`.

        The pen's slot indices, because a pen slot and a collision plane are
        one index seen from two ends -- the pen says what a plane draws, this
        says what it collides as.

        Plane 0 is offered with the rest. It is conventionally the glyph's
        ground and so usually the holes, but a pen may paint it a real colour,
        and reserving it here would assume a convention `Pen` explicitly
        declines to assume.

        Numbers where a sprite has `attack` and `body_hurt`, and that is the
        vocabulary being opaque rather than this pillar being odd: plane names
        come from the art, and a font has none to give.
        """
        return _PLANES

    def collision_plane(self, name: Optional[Hashable] = None) -> np.ndarray:
        """One packed screen-space plane: the silhouette, or one glyph plane.

        **A read derives nothing.** It hands back the plane the last commit
        packed, which describes the frame that was painted -- so a `defer`
        block open right now, or a line typed a moment ago, is not in it and
        should not be. `_sync` is deliberately NOT called here any more: this
        answers about a frame already on screen, and composing one to answer
        about would both invent a frame and bill the game for it.

        Retained rather than pulled, since 2026-09-07. The pillar used to
        derive on read and hold nothing, on the ground that Kris, 2026-09-04:
        *"collisions on the console are the oddball path; reading writing are
        the main uses -- so favour internal representations that prefer the
        main case."* That still decides the shape here -- `_refresh` gains
        nothing and `_refresh_rows` keeps its partial band -- but the pull
        itself had to go, because what it pulled was the live image rather
        than the painted one. `_refresh_collision` repacks only the cell rows
        `_touch` marked, so the main case still pays for what it changed.

        **The two derivations cost very differently**, measured on an 80x30
        console at 640x360:

            silhouette      0.020ms     rides the pixels, already there
            named plane     0.217ms     pays the glyph gather itself
            (`_refresh`     1.172ms     for scale -- a whole recompose)

        The silhouette is nearly free because the recompose already built the
        pixels it reads. A named plane is not: it needs `_plane_indices`, and
        that gather IS most of a recompose. Deriving all five together is what
        lets one gather serve four planes; on demand they could not share it
        without retaining the ~900KB index array across a tick.

        The silhouette is the composed pixels -- `!= 0` -- and deliberately
        NOT `_plane_indices(...) != 0`. A pen slot may legitimately hold
        palette index 0, and `paint_buffer` resolves through colour rather
        than through the glyph, so only the composed pixels know what was
        actually drawn. Drawn and solid stay one predicate.

        A named plane asks the other question and may disagree with it: plane
        2 with the pen leaving slot 2 transparent is drawn as nothing and
        collides as something, which is how a level painted in glyphs gets
        three collision planes out of one layer. That breaches nothing -- the
        one-predicate law is about the default silhouette, and a named plane
        has always been authored geometry independent of what is drawn, which
        is what a sprite's `attack` box is.

        No offset and no apron. `collision_offset` stays the base's `(0, 0)`
        because these planes ARE the screen; a tilemap needs both only because
        it has a camera over a world larger than one.
        """
        # `collider()` has already refused an unknown name, so this is either
        # the silhouette or one of the four numbers that index themselves.
        slot = _SILHOUETTE_SLOT if name is None else int(name)  # type: ignore[arg-type]
        return self._collision[slot]

    def _build_quad(self, program, texture) -> None:
        """One quad covering the console, built once.

        `picture.vert` takes `position`, `tex_coords`, `translate`, `colors`
        and `palette_row`. The console never moves, never tints and has one
        palette, so translate, colours and the row are all constant -- but
        they are per-vertex attributes the shader declares, so they are
        supplied rather than left unbound. The row is 0 because a console's
        table is a bank of one; the register exists for the pillars that
        carry more."""
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
            palette_row=("f", (0.0,) * 4),
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
        # The upload, at the head of the composite and before any quad: once
        # per frame per changed bank, and never mid-pass where it could
        # disturb the unit the art is bound to.
        palette = self._palette.texture
        if self._quad is None:
            self._build_quad(program, texture)
        self._quad.palette_row[:] = (float(self._palette.active),) * 4

        program.use()
        program["sprite_texture"] = 0
        program["palette_texture"] = 1
        glActiveTexture(GL_TEXTURE1)
        glBindTexture(palette.target, palette.id)
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(texture.target, texture.id)
        # Blend is the compositor's, read per layer from `Layer.blend` and
        # OFF by default -- indexed art is drawn or discarded, never blended.
        # See `Display._compose`.
        assert self._quad is not None
        self._quad.draw(GL_TRIANGLES)
        program.stop()

    def __repr__(self) -> str:
        return f"<Console {self._cols}x{self._rows} cells [{self.tag}]>"
