from numpy import ndarray, zeros, uint8, array_equal, zeros
from .config import Config
from .logger import Logger
from .palette import Palette
from .numba import numba_blit, numba_rgba, numba_mask


class Layer(object):
    _count = 0

    def __init__(
        self, width: int = None, height: int = None, palette: Palette = None
    ) -> None:
        self._c = Config()
        if width is None:
            self._width = self._c.window.width
        if height is None:
            self._height = self._c.window.height
        if isinstance(palette, Palette):
            self._palette = palette
        else:
            self._palette = Palette()
        self._layer = zeros([self._height, self._width, 4], dtype=uint8)
        self._unique_tiles = []
        self._palette_version = -1
        self._tainted = True

    def _reset_tiles(self):
        """Reset all tiles and clear the unique tile list when the palette changes."""
        self.clear()
        for tile in self._unique_tiles:
            tile.invalidate()
        self._unique_tiles = []
        # Update the stored palette version to the latest one
        self._palette_version = self._palette.version

    def blit(self, tile, x, y):
        # Check palette version to see if we need to reset
        if self._palette.version != self._palette_version:
            self._reset_tiles()

        # Track the unique tile
        if tile not in self._unique_tiles:
            self._unique_tiles.append(tile)

        # Generate RGBA if not present, using vectorized palette indexing
        if tile._rgba is None:
            tile._rgba = numba_rgba(
                self._palette, tile._tile, tile._tile, *tile._tile.shape
            )

        # Check if _rgba is still None and handle the case
        if tile._rgba is None:
            Logger().error("Tile RGBA data could not be generated.")
            raise ValueError("Tile RGBA data could not be generated.")

        # Generate boolean mask if not present
        if tile._mask is None:
            tile._mask = numba_mask(tile._rgba)

        # Check if _mask is None and handle the case
        if tile._mask is None:
            Logger().error("Tile mask data could not be generated.")
            raise ValueError("Tile mask data could not be generated.")

        # Now that tile._rgba and tile._mask are prepared, call the Numba function
        numba_blit(
            self._layer,
            tile._rgba,
            tile._mask,
            x,
            y,
            *self._layer.shape[:2],
            *tile._rgba.shape[:2],
        )

    def clear(self) -> None:
        self._layer = zeros(self._layer.shape, dtype=uint8)

    @property
    def palette(self) -> Palette:
        return self._palette

    @palette.setter
    def palette(self, palette: Palette) -> None:
        if palette is not self._palette or not array_equal(palette, self._palette):
            self._palette = palette
            self._reset_tiles()
            # Update to the new palette version
            self._palette_version = palette.version

    @property
    def image(self) -> ndarray:
        return self._layer
