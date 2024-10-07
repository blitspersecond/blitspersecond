import numpy as np
from .palette import Palette
from .config import Config
from .tile import Tile


class Layer:
    def __init__(self):
        self.height = Config().window.height  # 360
        self.width = Config().window.width  # 640
        self._palette = Palette()
        self._palette_changed = False
        self._image = np.zeros((self.height, self.width, 4), dtype=np.uint8)
        self._mask = np.zeros((self.height, self.width))
        self._cache = {}
        self._tainted = True

    def invalidate_cache(self):
        # Mark all cached data as invalid due to palette change
        self._cache.clear()
        self._palette_changed = True

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

    # def blit(self, tile: Tile, x: int, y: int):
    #     tile_id = id(tile)
    #     if tile_id not in self._cache or self._palette_changed:
    #         # Cache RGBA and mask data for this tile
    #         rgba_image = tile.rgba_image
    #         # Or use tile's _untaint() method
    #         mask = rgba_image[..., 3] != 0  # Compute transparency mask
    #         self._cache[tile_id] = (rgba_image, mask)

    #     # Retrieve cached data and perform the blit operation
    #     rgba_image, mask = self._cache[tile_id]
    #     # Perform the blit with cached data...
    #     self._perform_blit(rgba_image, mask, x, y)

    # def _perform_blit(self, rgba_tile, mask, x, y):
    #     # Calculate the region to blit onto the target (Layer)
    #     x_start = max(x, 0)
    #     y_start = max(y, 0)
    #     x_end = min(x + rgba_tile.shape[1], self.width)
    #     y_end = min(y + rgba_tile.shape[0], self.height)

    #     # Calculate the region to copy from the tile
    #     tile_x_start = max(0, -x)
    #     tile_y_start = max(0, -y)
    #     tile_x_end = tile_x_start + (x_end - x_start)
    #     tile_y_end = tile_y_start + (y_end - y_start)

    #     # Get the pixel data from the tile, accounting for bounds
    #     rgba_tile_region = rgba_tile[tile_y_start:tile_y_end, tile_x_start:tile_x_end]
    #     mask_region = mask[tile_y_start:tile_y_end, tile_x_start:tile_x_end]

    #     # Blit the tile pixels onto the target where the mask is true
    #     layer_region = self.rgba_image[y_start:y_end, x_start:x_end]

    #     # Apply the mask for non-transparent pixels (where mask is True)
    #     np.copyto(layer_region, rgba_tile_region, where=mask_region)

    # def blit(self, tile, x, y):
    #     # Get the cached RGBA image and mask from the tile
    #     rgba_image = tile.rgba_image
    #     mask = tile.mask

    #     # Perform the blit with bounds checking
    #     self._perform_blit(rgba_image, mask, x, y)

    @property
    def image(self):
        if self._tainted:
            self._cache = self._image.copy()
            self._tainted = False
        return self._cache
