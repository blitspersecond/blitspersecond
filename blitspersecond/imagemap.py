# lets update ImageMap and Images

import numpy as np
from PIL import Image
from .palette import Palette
from .tile import Tile


class ImageMap:
    def __init__(self, file, tilesize=None):
        try:
            image = Image.open(file)
        except FileNotFoundError:
            raise FileNotFoundError(f"File not found: {file}")
        if image.mode != "P":
            raise ValueError("The image is not in palettized (P mode) format.")
        self._image = image
        self._index_image = np.array(image)
        self._palette = Palette()
        _p = image.getpalette()
        for i in range(0, len(_p), 3):
            r, g, b = _p[i : i + 3]
            self._palette[i // 3] = (r, g, b, 255)
        self.tilesize = tilesize or (self._image.width, self._image.height)

    @property
    def palette(self):
        return self._palette

    @property
    def tilesize(self):
        return self._tilesize

    @tilesize.setter
    def tilesize(self, size):
        if not (
            isinstance(size, (tuple, list))
            and len(size) == 2
            and all(s > 0 for s in size)
        ):
            raise ValueError("Tile size must be a tuple/list of two positive integers.")
        if size[0] > self._image.width or size[1] > self._image.height:
            raise ValueError("Tile size is too large for the image dimensions.")
        self._index = 0
        self._tilesize = size

    def __getitem__(self, index):
        tiles_per_row = self._image.width // self._tilesize[0]
        row = index // tiles_per_row
        col = index % tiles_per_row
        x = col * self._tilesize[0]
        y = row * self._tilesize[1]
        if x >= self._image.width or y >= self._image.height:
            raise IndexError("Tile index out of bounds.")
        index_image = self._index_image[
            y : y + self._tilesize[1], x : x + self._tilesize[0]
        ]
        return Tile(index_image, self._palette)

    def __len__(self):
        tiles_per_row = self._image.width // self._tilesize[0]
        tiles_per_col = self._image.height // self._tilesize[1]
        return tiles_per_row * tiles_per_col

    def __iter__(self):
        self._index = 0
        return self

    def __next__(self):
        if self._index >= len(self):
            raise StopIteration
        tile = self[self._index]
        self._index += 1
        return tile
