# The console: teletype semantics over a glyph layer -- cursor, write(), wrap,
# newline, scrolling, and input. It is a controller, not a layer; the
# GlyphLayer it controls is the thing in the draw order.

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional, Tuple, TYPE_CHECKING

from blitspersecond.colors import Palette, Pen
from blitspersecond.graphics.glyph import GlyphLayer

from .cursor import Cursor

if TYPE_CHECKING:
    from blitspersecond.input.kbm.input import Input


class Console:
    """A text console: clear / write / print with a per-cell colour pen.
    Spectrum / Amiga flavour, not a VT220.

    Console controls an existing GlyphLayer, which the GlyphEngine factory
    has already placed on screen:

        console = Console(GlyphEngine(CharSet.ASCII8X12))
        console.glyphs.after(background)

    The console adds no rendering state of its own."""

    def __init__(
        self,
        glyphs: GlyphLayer,
    ) -> None:
        if not isinstance(glyphs, GlyphLayer):
            raise TypeError(f"expected GlyphLayer, got {type(glyphs)}")
        self._glyphs = glyphs
        self._cursor = Cursor(self._glyphs)

    # -- the composed pieces ------------------------------------------------

    @property
    def glyphs(self) -> GlyphLayer:
        """The glyph layer underneath the console."""
        return self._glyphs

    # -- geometry -----------------------------------------------------------

    @property
    def cols(self) -> int:
        return self._glyphs.cols

    @property
    def rows(self) -> int:
        return self._glyphs.rows

    @property
    def cursor(self) -> Cursor:
        """The live cursor: its position and the addressed cell's contents."""
        return self._cursor

    @cursor.setter
    def cursor(self, pos: Tuple[int, int]) -> None:
        x, y = pos
        self._cursor._move(int(x), int(y))

    # -- palette and pen (delegated to the glyph layer) ----------------------

    @property
    def palette(self) -> Palette:
        """The console's live index-to-colour table."""
        return self._glyphs.palette

    @palette.setter
    def palette(self, value: Palette) -> None:
        self._glyphs.palette = value

    @property
    def pen(self) -> Pen:
        """The console's pen -- the four palette indices written into each cell.
        Mutable: `console.pen.foreground = RED`, `console.pen[2] = LIME`. Assign
        a whole `Pen` to replace it."""
        return self._glyphs.pen

    @pen.setter
    def pen(self, value: Pen) -> None:
        self._set_pen(value)

    @property
    def foreground(self) -> int:
        """Shortcut for `pen.foreground` (slot 1)."""
        return self._glyphs.pen.foreground

    @foreground.setter
    def foreground(self, value: int) -> None:
        self._glyphs.pen.foreground = value

    @property
    def background(self) -> int:
        """Shortcut for `pen.background` (slot 0)."""
        return self._glyphs.pen.background

    @background.setter
    def background(self, value: int) -> None:
        self._glyphs.pen.background = value

    # -- writing --------------------------------------------------------------

    def clear(self, pen: Optional[Pen] = None) -> None:
        """Blank the whole console -- every cell zeroed to char 0 -- and home
        the cursor to (0, 0) -- top-left. Pass a `Pen` to paint every cleared
        cell with it
        and leave it as the current pen for subsequent writes."""
        if pen is not None:
            self._set_pen(pen)
        self._glyphs.clear()
        self._cursor._move(0, 0)

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
        if at is not None:
            self.cursor = at
        for ch in text:
            if ch == "\n":
                self._newline()
                continue
            self._glyphs.cell[self._cursor.x, self._cursor.y] = ord(ch)
            x = self._cursor.x + 1
            if x >= self.cols:
                self._newline()
            else:
                self._cursor._move(x, self._cursor.y)

    def print(
        self,
        value: object = "",
        *,
        at: Optional[Tuple[int, int]] = None,
    ) -> None:
        """Write an object's string form, then start a new line."""
        self.write(f"{value}\n", at=at)

    def echo(self, entry: "Input") -> "Input":
        """Echo a keyboard-owned Input from the current cursor."""
        from blitspersecond.input.kbm.input import Input
        from .echo import _ConsoleEcho

        if not isinstance(entry, Input):
            raise TypeError(f"expected keyboard Input, got {type(entry)}")
        _ConsoleEcho(self, entry)
        return entry

    def _newline(self) -> None:
        y = self._cursor.y + 1
        if y >= self.rows:
            self._scroll()
            y = self.rows - 1
        self._cursor._move(0, y)

    def _scroll(self) -> None:
        """Shift every row up one and blank the bottom row in the current pen."""
        self._glyphs.scroll((0, -1))

    def _set_pen(self, value: Pen) -> None:
        if not isinstance(value, Pen):
            raise TypeError(f"pen must be a Pen, got {type(value)}")
        for slot, colour in enumerate(value):
            self._glyphs.pen[slot] = colour

    @contextmanager
    def _using_pen(self, value: Pen) -> Iterator[None]:
        previous = tuple(self._glyphs.pen)
        self._set_pen(value)
        try:
            yield
        finally:
            for slot, colour in enumerate(previous):
                self._glyphs.pen[slot] = colour
