# Look at the FIXME comments, we want to blit the palette indices to RGBA values at the layer level, not the tile level.

import numpy as np
from .palette import Palette
from .config import Config
from .tile import Tile


class Layer:
    def __init__(self):
        self.height = Config().window.height  # 360
        self.width = Config().window.width  # 640
        self._palette = Palette()
        self._palette_changed = True
        self._image = np.zeros((self.height, self.width, 4), dtype=np.uint8)
        self._mask = np.zeros((self.height, self.width))
        self._cache = np.zeros((self.height, self.width, 4), dtype=np.uint8)
        self._tainted = True

    def clear(self):
        self._image.fill(0)
        self._mask.fill(0)

    @property
    def palette(self) -> Palette:
        return self._palette

    @palette.setter
    def palette(self, palette: Palette):
        """
        Sets the palette, ensuring that it is copied to avoid modifying the original.
        Optionally, you can add a type check to ensure palette is valid.
        """
        if isinstance(palette, Palette):  # Validate that the input is a Palette
            self._palette_changed = True
            self._tainted = True
        else:
            raise TypeError(
                f"Expected a Palette instance, got {type(palette).__name__}"
            )

    def blit(self, tile: Tile, x: int, y: int):
        tile.blit(self._image, self._palette, x, y)
        self._tainted = True

    @property
    def image(self):
        if self._tainted:
            self._cache = self._image.copy()
            self._tainted = False
        return self._cache
