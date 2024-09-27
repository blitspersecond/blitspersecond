# lets update ImageMap and Images

import numpy as np
from typing import Tuple
from PIL import Image
from .palette import Palette
from .tile import Tile


class ImageMap:
    def __init__(self, file: str, tilesize=None):
        try:
            image = Image.open(file)
        except FileNotFoundError:
            raise FileNotFoundError(f"File not found: {file}")
        if image.mode != "P":
            raise ValueError("The image is not in palettized (P mode) format.")
        self._index_image = image
        self._index_tile = np.array(image)
        self._palette = Palette()
        _p = image.getpalette()
        for i in range(0, len(_p), 3):
            r, g, b = _p[i : i + 3]
            self._palette[i // 3] = (r, g, b, 255)
        self.tilesize = tilesize or (self._index_image.width, self._index_image.height)

    @property
    def palette(self) -> Palette:
        return self._palette

    @property
    def tilesize(self) -> int:
        return self._tilesize

    @tilesize.setter
    def tilesize(self, size: Tuple[int, int]) -> None:
        if not (
            isinstance(size, tuple)
            and len(size) == 2
            and all(isinstance(s, int) and s > 0 for s in size)
        ):
            raise ValueError("Tile size must be a tuple of two positive integers.")
        if size[0] > self._index_image.width or size[1] > self._index_image.height:
            raise ValueError("Tile size is too large for the image dimensions.")
        self._index = 0
        self._tilesize = size

    def __getitem__(self, index: int) -> Tile:
        tiles_per_row = self._index_image.width // self._tilesize[0]
        row = index // tiles_per_row
        col = index % tiles_per_row
        x = col * self._tilesize[0]
        y = row * self._tilesize[1]
        if x >= self._index_image.width or y >= self._index_image.height:
            raise IndexError("Tile index out of bounds.")
        index_tile = self._index_tile[
            y : y + self._tilesize[1], x : x + self._tilesize[0]
        ]
        return Tile(index_tile, self._palette)

    def __len__(self) -> int:
        tiles_per_row = self._index_image.width // self._tilesize[0]
        tiles_per_col = self._index_image.height // self._tilesize[1]
        return tiles_per_row * tiles_per_col

    def __iter__(self):
        self._index = 0
        return self

    def __next__(self) -> Tile:
        if self._index >= len(self):
            raise StopIteration
        tile = self[self._index]
        self._index += 1
        return tile
