"""An image cut into equal cells, addressed by index.

    atlas = Atlas(PixelBuffer("tiles/overworld.png"), 16, 16)
    len(atlas)              # how many cells fit
    atlas[42]               # cell 42, reading left to right then down
"""

from __future__ import annotations

from typing import Iterator, Optional

import numpy as np

from blitspersecond.common import Size

from .palette import Palette
from .pixel_buffer import PixelBuffer


class Atlas:
    """A `PixelBuffer` cut into equal `width` x `height` cells.

        atlas = Atlas(buffer, 8, 12)
        len(atlas)      # 256, if the image happens to hold that many
        atlas[0]        # top-left cell, as an (height, width) array
        atlas[-1]       # bottom-right

    Cells are numbered left to right and then down, so index `n` is at
    column `n % cols`, row `n // cols`. Nothing here knows what the cells
    are FOR: a charset's glyphs, a tilemap's tiles and a sprite sheet's
    frames are all this shape, and each engine brings its own rule about how
    many it needs and what an index means.

    **The image does not have to be square, or a power of two, or any
    particular grid.** It has to divide evenly by the cell size, and that is
    the only demand. The console previously assumed every charset atlas was
    16x16 glyphs, which was true of the two images that happened to exist
    and was never a rule -- so `len()` is the question an engine should ask
    ("does this hold the 256 I need?") rather than the shape.

    A read hands back a VIEW into the source buffer's pixels: editing a cell
    edits the image, which is what a tool wants and what an engine loading
    from a file should copy out of. Cells are strided rather than contiguous,
    because a cell's rows are `width` pixels out of a `cols * width` row.
    `cells` is the whole set stacked and contiguous, which costs a copy.

    **It carries the image's metadata too** -- `size`, `mode`, `indexed`,
    `palette` -- so an engine handed a sheet can ask the sheet rather than
    reaching through to `buffer`. A console guards `sheet.indexed`, which is
    a question about the sheet it was given and should read like one.

    Margins and inter-cell spacing (Tiled supports both) are NOT handled.
    Nothing in BPS ships an atlas that needs them yet, and guessing the
    parameters would be worse than the raise you get from an image that does
    not divide.
    """

    __slots__ = ("_buffer", "_cell", "_cols", "_rows", "_grid")

    def __init__(self, buffer: PixelBuffer, width: int, height: int) -> None:
        if not isinstance(buffer, PixelBuffer):
            raise TypeError(f"an atlas cuts up a PixelBuffer; got {type(buffer)}")
        width, height = int(width), int(height)
        if width <= 0 or height <= 0:
            raise ValueError(
                f"atlas cell size must be positive, got {width}x{height}"
            )
        image_width, image_height = buffer.size
        if image_width % width or image_height % height:
            raise ValueError(
                f"a {image_width}x{image_height} image does not divide into "
                f"{width}x{height} cells"
            )

        self._buffer = buffer
        self._cell = Size(width, height)
        self._cols = image_width // width
        self._rows = image_height // height
        # (row, col, y, x) over the source pixels, and a view throughout:
        # reshape splits each axis, transpose reorders the four. Built once
        # so indexing is arithmetic rather than reshaping per access.
        #
        # A channel axis rides along untouched. An indexed buffer is (h, w)
        # and an RGB/RGBA one is (h, w, c), and an atlas has to take both --
        # a charset is indexed, a tileset or a sprite sheet generally is not.
        pixels = buffer.pixels
        channels = pixels.shape[2:]
        self._grid = pixels.reshape(
            self._rows, height, self._cols, width, *channels
        ).transpose(0, 2, 1, 3, *range(4, 4 + len(channels)))

    @property
    def buffer(self) -> PixelBuffer:
        """The image being cut up. Live -- an atlas is a way of reading it,
        not a copy of it.

        Reach for this to DO something to the image: `edit()`, `copy()`, the
        `texture`. What the image IS -- `size`, `mode`, `indexed`, `palette`
        -- is repeated below, so a caller asking a question about the sheet
        asks the sheet."""
        return self._buffer

    @property
    def size(self) -> Size:
        """The whole image's `(width, height)` in pixels."""
        return self._buffer.size

    @property
    def mode(self) -> str:
        """The image's mode -- `"P"`, `"RGB"`, `"RGBA"`."""
        return self._buffer.mode

    @property
    def indexed(self) -> bool:
        """Whether the cells hold palette indices rather than colours.

        The question an engine asks before deciding it can use a sheet at
        all: a console's glyphs must be indexed (a glyph is a stencil of
        plane indices), and a tileset generally is not."""
        return self._buffer.indexed

    @property
    def palette(self) -> Optional[Palette]:
        """The table the cells index into, or None if they hold colours.

        Here rather than only on the buffer because a sheet of indices
        without its palette is unreadable -- they are one fact about the
        image, and separating them invites resolving cells against the wrong
        table."""
        return self._buffer.palette

    @property
    def cell(self) -> Size:
        """One cell's `(width, height)` in pixels."""
        return self._cell

    @property
    def cols(self) -> int:
        """Cells across."""
        return self._cols

    @property
    def rows(self) -> int:
        """Cells down."""
        return self._rows

    @property
    def cells(self) -> np.ndarray:
        """Every cell stacked as `(len(self), height, width)`, contiguous --
        plus a channel axis if the source image has one.

        A COPY, and unavoidably so: cells are strided in the source image, so
        stacking them in index order is a gather. Use it to fill a table once
        at load; use `atlas[n]` to look at one."""
        return self._grid.reshape(
            len(self), self._cell.height, self._cell.width, *self._grid.shape[4:]
        )

    def __len__(self) -> int:
        """How many cells the image holds -- `cols * rows`.

        This is the question to ask an atlas. A charset needs 256 of them and
        does not care whether they arrive as 16x16 or 32x8."""
        return self._cols * self._rows

    def __getitem__(self, index: int) -> np.ndarray:
        """One cell as an `(height, width)` view, numbered left to right then
        down. Negative indices count from the end."""
        count = len(self)
        position = int(index)
        if position < 0:
            position += count
        if not 0 <= position < count:
            raise IndexError(
                f"cell {index} outside atlas 0..{count - 1} "
                f"({self._cols}x{self._rows} cells)"
            )
        return self._grid[position // self._cols, position % self._cols]

    def __iter__(self) -> Iterator[np.ndarray]:
        for position in range(len(self)):
            yield self[position]

    def __repr__(self) -> str:
        return (
            f"<Atlas {self._cols}x{self._rows} cells of "
            f"{self._cell.width}x{self._cell.height}, {len(self)} total>"
        )
