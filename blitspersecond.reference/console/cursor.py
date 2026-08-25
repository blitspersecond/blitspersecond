from __future__ import annotations

from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from blitspersecond.graphics.glyph import GlyphLayer

    from .console import Console


class Cursor:
    """The console's currently addressed cell.

    `x` and `y` move the cursor -- screen order, same as every other surface
    in the engine (x across, y down, origin top-left). `char` inspects or
    edits the addressed cell without moving it. A write adopts the glyph
    layer's current pen.
    """

    __slots__ = ("_glyphs", "_x", "_y")

    def __init__(self, glyphs: Union["GlyphLayer", "Console"]) -> None:
        """Takes the surface to address: a GlyphLayer, or any controller over
        one -- `Cursor(console)` and `Cursor(console.layer)` are the same
        cursor. Reaching through `.layer` is the same hook `layers.add` and
        `layers.remove` use, so a caller never has to unwrap by hand."""
        from blitspersecond.graphics.glyph import GlyphLayer

        layer = getattr(glyphs, "layer", glyphs)
        if not isinstance(layer, GlyphLayer):
            raise TypeError(
                f"a cursor addresses a GlyphLayer, or a controller over one "
                f"(Cursor(console)); got {type(glyphs)}"
            )
        self._glyphs = layer
        self._x = 0
        self._y = 0

    @property
    def layer(self) -> "GlyphLayer":
        """The glyph layer this cursor addresses. Read-only -- a cursor is
        bound to one surface for life; point a different one at a different
        layer rather than re-aiming this one."""
        return self._glyphs

    @property
    def x(self) -> int:
        """Column, 0 at the left edge."""
        return self._x

    @x.setter
    def x(self, value: int) -> None:
        self._move(int(value), self._y)

    @property
    def y(self) -> int:
        """Row, 0 at the top edge."""
        return self._y

    @y.setter
    def y(self, value: int) -> None:
        self._move(self._x, int(value))

    @property
    def char(self) -> int:
        """The addressed cell's unsigned byte character code."""
        return self._glyphs.cell[self._x, self._y]

    @char.setter
    def char(self, value: int) -> None:
        self._glyphs.cell[self._x, self._y] = value

    def _move(self, x: int, y: int) -> None:
        if not (0 <= x < self._glyphs.cols and 0 <= y < self._glyphs.rows):
            raise ValueError(
                f"cursor {(x, y)} outside console "
                f"0,0-{self._glyphs.cols - 1},{self._glyphs.rows - 1}"
            )
        self._x, self._y = x, y

    def __iter__(self):
        return iter((self._x, self._y))

    def __repr__(self) -> str:
        return f"Cursor(x={self._x}, y={self._y})"
