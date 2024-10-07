from numba import jit, njit, prange


@njit(parallel=True)
def numba_blit(layer, tile_rgba, tile_mask, x, y, layer_h, layer_w, tile_h, tile_w):
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
