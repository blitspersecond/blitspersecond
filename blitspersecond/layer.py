from numpy import ndarray, clip, ones, zeros, uint8, array_equal
from .tile import Tile
from .palette import Palette
from .numba import numba_blit
from typing import Tuple


class Layer(object):
    def __init__(
        self, width: int = 640, height: int = 360, palette: Palette = None
    ) -> None:
        if isinstance(palette, Palette):
            self._palette = palette
        else:
            self._palette = Palette()
        self._layer = zeros([height, width, 4], dtype=uint8)
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
            tile._rgba = self._palette[tile._tile] if tile._tile is not None else None

        # Check if _rgba is still None and handle the case
        if tile._rgba is None:
            raise ValueError("Tile RGBA data could not be generated.")

        # Generate boolean mask if not present
        if tile._mask is None:
            tile._mask = tile._rgba[..., 3] != 0 if tile._rgba is not None else None

        # Check if _mask is None and handle the case
        if tile._mask is None:
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

    # def old_blit(self, tile, x, y):
    #     # Check palette version to see if we need to reset
    #     if self._palette.version != self._palette_version:
    #         self._reset_tiles()
    #     # Generate RGBA if not present, using vectorized palette indexing
    #     if tile._rgba is None:
    #         tile._rgba = self._palette[tile._tile]
    #     # Generate boolean mask if not present
    #     if tile._mask is None:
    #         tile._mask = (
    #             tile.rgba[..., 3] != 0
    #         )  # True for opaque, False for transparent

    #     # Check if the tile is fully within the layer bounds
    #     h, w = tile._rgba.shape[:2]
    #     layer_h, layer_w, _ = self._layer.shape

    #     if 0 <= x <= layer_w - w and 0 <= y <= layer_h - h:
    #         # Tile is completely within bounds; apply directly using boolean mask
    #         self._layer[y : y + h, x : x + w][tile._mask] = tile._rgba[tile._mask]
    #     else:
    #         # Perform boundary checks for partially out-of-bounds tiles
    #         x_end = clip(x + w, 0, layer_w)
    #         y_end = clip(y + h, 0, layer_h)
    #         x_start = clip(x, 0, layer_w)
    #         y_start = clip(y, 0, layer_h)

    #         tile_x_start = max(0, -x)
    #         tile_y_start = max(0, -y)
    #         tile_x_end = tile_x_start + (x_end - x_start)
    #         tile_y_end = tile_y_start + (y_end - y_start)

    #         # Sliced overlay using boolean mask for direct copying
    #         overlay_rgba = tile._rgba[tile_y_start:tile_y_end, tile_x_start:tile_x_end]
    #         overlay_mask = tile._mask[tile_y_start:tile_y_end, tile_x_start:tile_x_end]

    #         # Apply only where mask is True
    #         self._layer[y_start:y_end, x_start:x_end][overlay_mask] = overlay_rgba[
    #             overlay_mask
    #         ]

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
