from numpy import ndarray, zeros, empty, bool_, uint8
from numba import njit, prange


@njit(parallel=False, fastmath=True)
def numba_blit(layer, tile_rgba, tile_mask, x, y, layer_h, layer_w, tile_h, tile_w):
    # Early exit if the tile is completely out of bounds
    if x >= layer_w or y >= layer_h or x + tile_w <= 0 or y + tile_h <= 0:
        return  # Do nothing since the tile is fully outside the layer
    # Check if the tile is fully within bounds
    if 0 <= x <= layer_w - tile_w and 0 <= y <= layer_h - tile_h:
        # Directly overlay the tile without bounds checking
        for i in range(tile_h):
            layer_y = y + i  # Calculate once per row
            for j in range(tile_w):
                if tile_mask[i, j]:  # Check the boolean mask for transparency
                    layer[layer_y, x + j] = tile_rgba[i, j]
    else:
        # Tile is partially out of bounds, so perform bounds checking and make copies
        x_start = max(0, x)
        y_start = max(0, y)
        x_end = min(x + tile_w, layer_w)
        y_end = min(y + tile_h, layer_h)

        tile_x_start = max(0, -x)
        tile_y_start = max(0, -y)

        # Calculate the dimensions of the visible region
        visible_tile_h = y_end - y_start
        visible_tile_w = x_end - x_start

        # Copy the visible portion of tile_rgba and tile_mask to ensure contiguous access
        visible_rgba = tile_rgba[
            tile_y_start : tile_y_start + visible_tile_h,
            tile_x_start : tile_x_start + visible_tile_w,
        ].copy()
        visible_mask = tile_mask[
            tile_y_start : tile_y_start + visible_tile_h,
            tile_x_start : tile_x_start + visible_tile_w,
        ].copy()

        # Blit the copied region to the layer
        for i in range(visible_tile_h):
            layer_y = y_start + i
            for j in range(visible_tile_w):
                if visible_mask[i, j]:  # Check the boolean mask for transparency
                    layer[layer_y, x_start + j] = visible_rgba[i, j]


@njit(fastmath=True)
def numba_rgba(palette, tile_data, tile, height, width):
    if tile is not None:
        rgba = empty((height, width, 4), dtype=uint8)
        for i in range(height):
            for j in range(width):
                rgba[i, j] = palette[tile_data[i, j]]
        return rgba
    return None


@njit(fastmath=True)
def numba_mask(rgba):
    if rgba is not None:
        height, width = rgba.shape[:2]
        mask = zeros((height, width), dtype=bool_)
        for i in range(height):
            for j in range(width):
                mask[i, j] = rgba[i, j, 3] != 0
        return mask
    return None


@njit(parallel=True, fastmath=True)
def numba_compose(framebuffer: ndarray, layers) -> ndarray:
    framebuffer.fill(0)  # Clear the framebuffer at the beginning

    for layer in layers:
        # Assuming layer_image is an (H, W, 4) ndarray
        height, width = layer.shape[:2]
        # Loop over each pixel in the layer
        for y in prange(height):
            for x in range(width):
                alpha = layer[y, x, 3]
                # Only apply if pixel is not fully transparent (alpha > 0)
                if alpha > 0:
                    framebuffer[y, x, :] = layer[y, x, :]
    return framebuffer
