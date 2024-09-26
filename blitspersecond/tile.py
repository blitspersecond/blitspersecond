from numpy import ndarray
from .palette import Palette


class Tile:
    def __init__(self, index_image: ndarray, palette: Palette):
        self._index_image = index_image
        self._palette = palette.copy()
        self._rgba_image = self._palette[self._index_image]
        self._target = None
        self._tainted = True

    @property
    def width(self):
        return self._index_image.shape[1]

    @property
    def height(self):
        return self._index_image.shape[0]

    @property
    def image(self):
        return self._index_image

    @property
    def palette(self):
        return self._palette

    @palette.setter
    def palette(self, palette: Palette):
        self._palette = palette
        self._tainted = True

    def _untaint(self):
        # remap the rgba image to the new palette
        self._rgba_image = self._palette[self._index_image]
        self._tainted = False

    def blit(self, rgba_target: ndarray, palette: Palette, x: int, y: int) -> None:
        if rgba_target is not self._target or palette is not self._palette:
            self._palette = palette
            self._target = rgba_target
            self._tainted = True

        if self._tainted:
            self._rgba_image = palette[self._index_image]

        # Calculate the region to blit onto the target
        x_start = max(x, 0)
        y_start = max(y, 0)
        x_end = min(x + self.width, rgba_target.shape[1])
        y_end = min(y + self.height, rgba_target.shape[0])

        # Calculate the region to copy from the tile
        tile_x_start = max(0, -x)
        tile_y_start = max(0, -y)
        tile_x_end = tile_x_start + (x_end - x_start)
        tile_y_end = tile_y_start + (y_end - y_start)

        # Get the pixel data from the tile
        rgba_tile = self._rgba_image[tile_y_start:tile_y_end, tile_x_start:tile_x_end]

        # Create a mask for non-transparent pixels
        non_transparent_mask = rgba_tile[..., 3] != 0

        # Blit the tile pixels onto the target where the mask is true
        rgba_target[y_start:y_end, x_start:x_end][non_transparent_mask] = rgba_tile[
            non_transparent_mask
        ]
