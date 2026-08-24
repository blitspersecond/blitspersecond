from typing import Iterator, Optional, Tuple, Union

import numpy as np

from blitspersecond.common import Size
from blitspersecond.resources import PLANE_DTYPE

from blitspersecond.graphics.internal import Point, Vector
from blitspersecond.graphics.common import PixelBuffer
from .tile import Tile

TileIndex = Union[int, Tuple[int, int]]


class TileAtlas:
    """A grid reading laid over a PixelBuffer: here's an image, here's an
    index into it. Nothing else -- placement, scrolling, culling and
    collision are the consuming render engine's business (the TileEngine today;
    a sprite/BOB engine reading anim frames from a sheet is the same shape).

    HAS-A PixelBuffer (the buffer owns the pixel/texture lifecycle); the
    atlas owns only the grid: tile_size, cols x rows, and index -> Tile
    resolution. It carries no GL state of its own -- it is pure arithmetic
    over the buffer -- so one atlas can serve any number of consumers, all
    sampling the buffer's single texture.

    Two ways to read it. `atlas[i]` / `atlas[col, row]` is one Tile.
    `atlas.region(x, y, w, h)` is a rectangular block of tiles as a
    blittable plane -- the same array type TileMap hands out, so a brush cut
    from a tileset and a layer loaded from a TMX go through one blit path.
    There is deliberately no slice form: `[...]` means exactly one tile.

    Build a PixelBuffer first (directly, or via the image factory) and wrap
    it; an atlas does NOT accept an ImageSpec -- size/depth are the buffer's
    creation-time concern, tiling is a view concern.

    One tile's size is given as `w`, `h` in pixels and defaults to the full
    buffer (one tile). Larger sheets divide by floor: a 320x200 buffer at
    128x128 has 2 columns x 1 row = 2 tiles; the leftover strips are simply
    not addressable. It reads back as the `tile_size` property, a `(w, h)`
    pair -- arguments are separate ints, returns stay tuples.

    Flat indices are row-major from 0 (col = i % cols, row = i // cols),
    matching Tiled's tile IDs. The grid reads back as `cols` / `rows`, the
    same words Console, GlyphLayer and TileMap use for a cell count.

    Tile width and height are independent, so rectangular grids work.
    """

    def __init__(
        self,
        buffer: PixelBuffer,
        w: Optional[int] = None,
        h: Optional[int] = None,
    ) -> None:
        """`w` and `h` are one tile's size in PIXELS. Omit both to treat the
        whole buffer as a single tile."""
        if not isinstance(buffer, PixelBuffer):
            raise TypeError(f"expected PixelBuffer, got {type(buffer)}")
        if (w is None) != (h is None):
            raise TypeError(
                "pass both w and h (pixels per tile), or neither for one "
                "whole-buffer tile"
            )
        self._buffer = buffer
        self._tile_size: Size = Size(*buffer.size)
        if w is not None and h is not None:
            self.tile_size = (w, h)

    @property
    def buffer(self) -> PixelBuffer:
        return self._buffer

    @property
    def tile_size(self) -> Size:
        """One tile as a `(w, h)` pair. Defaults to the whole buffer."""
        return self._tile_size

    @tile_size.setter
    def tile_size(self, value: Tuple[int, int]) -> None:
        w, h = int(value[0]), int(value[1])
        if w <= 0 or h <= 0:
            raise ValueError(f"tile_size must be positive, got {(w, h)}")
        bw, bh = self._buffer.size
        if w > bw or h > bh:
            raise ValueError(f"tile_size {(w, h)} exceeds buffer size {(bw, bh)}")
        self._tile_size = Size(w, h)

    @property
    def cols(self) -> int:
        return self._buffer.size[0] // self._tile_size[0]

    @property
    def rows(self) -> int:
        return self._buffer.size[1] // self._tile_size[1]

    def _to_indices(self, index: TileIndex) -> Tuple[int, int]:
        """Resolve a flat (row-major, from 0) or (col, row) index to (col, row)."""
        cols, rows = self.cols, self.rows
        if isinstance(index, int):
            if not 0 <= index < cols * rows:
                raise IndexError(
                    f"tile index {index} out of range (0..{cols * rows - 1})"
                )
            return index % cols, index // cols
        col, row = index
        if not (0 <= col < cols and 0 <= row < rows):
            raise IndexError(f"tile ({col}, {row}) out of range ({cols}x{rows})")
        return int(col), int(row)

    def tile(self, index: TileIndex) -> Tile:
        col, row = self._to_indices(index)
        tw, th = self._tile_size
        # Pooled: Tile() interns through the global TileCache, so re-reading
        # the same tile never mints a new object. The cache adopts the
        # Point/Vector on a fresh tile and discards them on a hit.
        return Tile(Point(col * tw, row * th), Vector(tw, th), self._buffer)

    def region(self, x: int, y: int, w: int, h: int) -> np.ndarray:
        """A `w` x `h` block of tiles starting at column `x`, row `y`, as a
        blittable plane.

            brush = tileset.region(0, 0, 10, 5)
            layer.blit(brush, x, y)

        Returns the same structured grid `TileMap.tiles` is made of
        (PLANE_DTYPE, shape `(w, h)`, indexed `[x, y]`), so one blit stamps
        the whole block and a brush cut from a tileset travels the same path
        as a grid loaded from a TMX. `flags` is all zero -- an atlas rectangle
        carries no orientation -- and the array is a fresh writable copy, so
        callers may set per-cell flags before blitting.

        Arguments are in TILES, not pixels, and read x, y, w, h throughout --
        and so does the result. This used to hand back a `[row, col]` array
        while taking geometry arguments, which meant one call site had to hold
        both orders at once; the grid is the engine's to define, so it is
        x-major like every other grid here.

        Out of bounds is an IndexError rather than a silent clamp. A brush is
        asked for at a definite size; quietly returning a smaller one because
        it ran off the edge would be a bug the caller could not see.
        """
        x, y, w, h = int(x), int(y), int(w), int(h)
        if w <= 0 or h <= 0:
            raise ValueError(f"region size must be positive, got {(w, h)}")
        cols, rows = self.cols, self.rows
        if x < 0 or y < 0 or x + w > cols or y + h > rows:
            raise IndexError(
                f"region ({x}, {y}, {w}, {h}) does not fit an atlas of "
                f"{cols}x{rows} tiles"
            )
        grid = np.zeros((w, h), dtype=PLANE_DTYPE)
        # The VALUES stay flat row-major tile ids (that is Tiled's numbering
        # and _to_indices'); only the array's own axes are x-major.
        grid["tile"] = (
            np.arange(y, y + h, dtype=np.int32)[None, :] * cols
            + np.arange(x, x + w, dtype=np.int32)[:, None]
        )
        return grid

    def __len__(self) -> int:
        return self.cols * self.rows

    def __getitem__(self, index: TileIndex) -> Tile:
        """Read exactly one tile, by flat index or `(col, row)`.

        There is no slice form. It used to return a flat tuple of the same
        interned Tiles single indexing hands back, so it bought nothing over
        `atlas[i]` and made `[...]` mean two things. Walk every tile with
        iteration; take a rectangle with region().
        """
        if isinstance(index, slice):
            raise TypeError(
                "TileAtlas does not slice -- atlas[i] / atlas[col, row] for "
                "one tile, atlas.region(x, y, w, h) for a rectangular block, "
                "iteration for every tile"
            )
        return self.tile(index)

    def __iter__(self) -> Iterator[Tile]:
        for i in range(len(self)):
            yield self.tile(i)
