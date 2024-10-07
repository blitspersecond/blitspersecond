from .image import Image
from .palette import Palette
from .tile import Tile
from typing import Tuple


class TileSet:
    _image: Image
    _palette: Palette
    _index = 0
    _tile_cache: dict

    def __init__(self, image: Image, tilesize: Tuple[int, int] = (0, 0)) -> None:
        self._palette = image.palette
        self._image = image
        self._size = image.size
        self._tile_cache = {}
        self._palette_version = -1
        if tilesize != (0, 0):
            self._tilesize = tilesize
        else:
            self._tilesize = self._size

    @property
    def size(self) -> Tuple[int, int]:
        return self._size

    @property
    def tilesize(self) -> Tuple[int, int]:
        return self._tilesize

    @tilesize.setter
    def tilesize(self, tilesize: Tuple[int, int]) -> None:
        if not (
            isinstance(tilesize, tuple)
            and len(tilesize) == 2
            and all(isinstance(s, int) and s > 0 for s in tilesize)
        ):
            raise ValueError("Tile size must be a tuple of two positive integers.")
        elif tilesize[0] > self._image.size[0] or tilesize[1] > self._image.size[1]:
            raise ValueError("Tile size is too large for the image dimensions.")
        self._index = 0
        self._tilesize = tilesize
        self._tile_cache.clear()

    @property
    def palette(self) -> Palette:
        return self._palette.copy()

    def _reset_tiles(self):
        """Reset all tiles and clear the unique tile list when the palette changes."""
        self.clear()
        for tile in self._unique_tiles:
            tile.invalidate()
        self._unique_tiles = []
        # Update the stored palette version to the latest one
        self._palette_version = self._palette.get_version()

    def _tile(self, index: int) -> Tile:
        # Calculate the number of tiles per row based on the tile width
        tiles_per_row = self._image.size[0] // self._tilesize[0]

        # Determine row and column for the tile
        row = index // tiles_per_row
        col = index % tiles_per_row

        # Calculate the (x, y) coordinates for the top-left corner of the tile
        x = col * self._tilesize[0]
        y = row * self._tilesize[1]

        if x >= self._image.size[0] or y >= self._image.size[1]:
            raise IndexError("Tile index out of bounds.")

        # Extract the tile's region from the numpy array (not transposing x, y)
        tile_data = self._image.image[
            y : y + self._tilesize[1], x : x + self._tilesize[0]
        ]

        if index not in self._tile_cache:
            tile_data = self._image._idx_image[
                y : y + self._tilesize[1], x : x + self._tilesize[0]
            ]
            self._tile_cache[index] = Tile(tile_data)

        return self._tile_cache[index]

    @property
    def tile(self, index: int) -> Tile:
        return self._tile(index)

    def __getitem__(self, index: int) -> Tile:
        return self._tile(index)

    def __iter__(self):
        self._index = 0
        return self

    def __next__(self) -> Tile:
        if self._index < self._total_tiles:
            tile = self._tile(self._index)
            self._index += 1
            return tile
        else:
            raise StopIteration
