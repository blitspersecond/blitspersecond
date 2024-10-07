from numpy import ndarray, clip, ones, zeros, uint8, array_equal
from .tile import Tile
from .palette import Palette
from typing import Tuple
from numba import njit, prange


@njit(parallel=True)
def _numba_blit(layer, tile_rgba, tile_mask, x, y, layer_h, layer_w, tile_h, tile_w):
    # Check if the tile is fully within bounds
    if 0 <= x <= layer_w - tile_w and 0 <= y <= layer_h - tile_h:
        # Directly overlay the tile without bounds checking
        for i in range(tile_h):
            for j in range(tile_w):
                if tile_mask[i, j]:  # Check the boolean mask for transparency
                    layer[y + i, x + j] = tile_rgba[i, j]
    else:
        # Tile is partially out of bounds, so perform bounds checking
        x_end = min(x + tile_w, layer_w)
        y_end = min(y + tile_h, layer_h)
        x_start = max(0, x)
        y_start = max(0, y)

        tile_x_start = max(0, -x)
        tile_y_start = max(0, -y)

        for i in prange(y_start, y_end):
            for j in range(x_start, x_end):
                tile_i = i - y_start + tile_y_start
                tile_j = j - x_start + tile_x_start
                if tile_mask[tile_i, tile_j]:  # Check the boolean mask for transparency
                    layer[i, j] = tile_rgba[tile_i, tile_j]


@njit(parallel=True)
def _experimental_numba_blit(
    layer, tile_rgba, tile_mask, x, y, layer_h, layer_w, tile_h, tile_w
):
    if 0 <= x <= layer_w - tile_w and 0 <= y <= layer_h - tile_h:
        # When tile is fully within bounds, flatten and apply the mask directly
        flat_layer = layer[y : y + tile_h, x : x + tile_w].reshape(-1, 4)
        flat_rgba = tile_rgba.reshape(-1, 4)
        flat_mask = tile_mask.flatten()

        for idx in range(flat_mask.size):
            if flat_mask[idx]:
                flat_layer[idx] = flat_rgba[idx]

        layer[y : y + tile_h, x : x + tile_w] = flat_layer.reshape(tile_h, tile_w, 4)
    else:
        # Bounds checking for out-of-bounds tiles
        x_end = min(x + tile_w, layer_w)
        y_end = min(y + tile_h, layer_h)
        x_start = max(0, x)
        y_start = max(0, y)

        tile_x_start = max(0, -x)
        tile_y_start = max(0, -y)

        for i in prange(y_start, y_end):
            for j in range(x_start, x_end):
                tile_i = i - y_start + tile_y_start
                tile_j = j - x_start + tile_x_start
                if tile_mask[tile_i, tile_j]:  # Check the boolean mask for transparency
                    layer[i, j] = tile_rgba[tile_i, tile_j]


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
        self._call_numba_blit(tile, x, y)

    def _call_numba_blit(self, tile, x, y):
        # Isolate the Numba call to ensure the inputs are validated
        _numba_blit(
            self._layer,
            tile._rgba,
            tile._mask,
            x,
            y,
            *self._layer.shape[:2],
            *tile._rgba.shape[:2],
        )

    def old_blit(self, tile, x, y):
        # Check palette version to see if we need to reset
        if self._palette.version != self._palette_version:
            self._reset_tiles()
        # Generate RGBA if not present, using vectorized palette indexing
        if tile._rgba is None:
            tile._rgba = self._palette[tile._tile]
        # Generate boolean mask if not present
        if tile._mask is None:
            tile._mask = (
                tile.rgba[..., 3] != 0
            )  # True for opaque, False for transparent

        # Check if the tile is fully within the layer bounds
        h, w = tile._rgba.shape[:2]
        layer_h, layer_w, _ = self._layer.shape

        if 0 <= x <= layer_w - w and 0 <= y <= layer_h - h:
            # Tile is completely within bounds; apply directly using boolean mask
            self._layer[y : y + h, x : x + w][tile._mask] = tile._rgba[tile._mask]
        else:
            # Perform boundary checks for partially out-of-bounds tiles
            x_end = clip(x + w, 0, layer_w)
            y_end = clip(y + h, 0, layer_h)
            x_start = clip(x, 0, layer_w)
            y_start = clip(y, 0, layer_h)

            tile_x_start = max(0, -x)
            tile_y_start = max(0, -y)
            tile_x_end = tile_x_start + (x_end - x_start)
            tile_y_end = tile_y_start + (y_end - y_start)

            # Sliced overlay using boolean mask for direct copying
            overlay_rgba = tile._rgba[tile_y_start:tile_y_end, tile_x_start:tile_x_end]
            overlay_mask = tile._mask[tile_y_start:tile_y_end, tile_x_start:tile_x_end]

            # Apply only where mask is True
            self._layer[y_start:y_end, x_start:x_end][overlay_mask] = overlay_rgba[
                overlay_mask
            ]

    def clear(self) -> None:
        self._layer = zeros(self._layer.shape, dtype=uint8)

    @property
    def palette(self) -> Palette:
        return self._palette()

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
