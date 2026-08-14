from typing import Iterator, Optional, Tuple, Union

from pyglet.image import TextureRegion

from blitspersecond.common import Size

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
    atlas owns only the grid: tile_size, columns x rows, and index -> Tile /
    TextureRegion resolution. It carries no GL state of its own -- region()
    resolves against the buffer's texture, everything else is arithmetic --
    so one atlas can serve any number of consumers, all sampling the
    buffer's single texture.

    Build a PixelBuffer first (directly, or via the image factory) and wrap
    it; an atlas does NOT accept an ImageSpec -- size/depth are the buffer's
    creation-time concern, tiling is a view concern.

    tile_size defaults to the full buffer size (one tile). Larger sheets
    divide by floor: a 320x200 buffer at 128x128 has 2 columns x 1 row = 2
    tiles; the leftover strips are simply not addressable.

    Flat indices are row-major from 0 (col = i % columns, row = i // columns),
    matching Tiled's tile IDs.

    Width and height are independent, matching Tiled's rectangular grid
    vocabulary; TileEngine preserves that shape.
    """

    def __init__(
        self,
        buffer: PixelBuffer,
        tile_size: Optional[Tuple[int, int]] = None,
    ) -> None:
        if not isinstance(buffer, PixelBuffer):
            raise TypeError(f"expected PixelBuffer, got {type(buffer)}")
        self._buffer = buffer
        self._tile_size: Size = Size(*buffer.size)
        if tile_size is not None:
            self.tile_size = tile_size

    @property
    def buffer(self) -> PixelBuffer:
        return self._buffer

    @property
    def tile_size(self) -> Size:
        """(width, height) of one tile. Defaults to the full buffer size (one tile)."""
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
    def columns(self) -> int:
        return self._buffer.size[0] // self._tile_size[0]

    @property
    def rows(self) -> int:
        return self._buffer.size[1] // self._tile_size[1]

    def _to_indices(self, index: TileIndex) -> Tuple[int, int]:
        """Resolve a flat (row-major, from 0) or (col, row) index to (col, row)."""
        cols, rows = self.columns, self.rows
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

    def region(self, index: TileIndex) -> TextureRegion:
        """Resolve a tile to a GPU sub-rect (needs a current GL context)."""
        t = self.tile(index)
        return self._buffer.texture.get_region(
            t.source.x, t.source.y, t.size.x, t.size.y
        )

    def __len__(self) -> int:
        return self.columns * self.rows

    def __getitem__(self, index: TileIndex) -> Tile:
        return self.tile(index)

    def __iter__(self) -> Iterator[Tile]:
        for i in range(len(self)):
            yield self.tile(i)
