"""Three ways to put tiles from a TMX tileset onto a layer.

``tile`` walks the map plane and blits one concrete tile per occupied cell.
``full`` hands the whole plane to a single blit -- same picture, one call.
``region`` is the other direction: it cuts a rectangle out of the *tileset*
rather than the map, and stamps that in one blit, so you see the sheet itself
laid out in atlas order.

The first two draw the island; the third draws the tiles it is made of. Change
``mode`` in ``main`` to compare them.

Run from the repository root:

    venv/bin/python examples/tmx.py

Close the window to quit.
"""

from pathlib import Path

import numpy as np

from blitspersecond import BlitsPerSecond
from blitspersecond.graphics import TileFlags
from blitspersecond.resources import ResourceManager

MAP = Path(__file__).resolve().parents[1] / "assets/gorillaz/island.tmx"

examples = ["tile", "full", "region"]
SKY = 255


def main() -> None:
    mode = examples[0]  # choose one of the three examples above

    bps = BlitsPerSecond()
    island = ResourceManager().tmx(str(MAP)).tilemap("blue_island")
    bps.layers.add(island)

    # A TileMap holds both halves: the layer it stamps into, and the world it
    # stamps from. Everything below works them by hand, so start from empty --
    # minting has already filled the visible window, which is the normal path
    # (see examples/layers.py).
    layer, tiles = island.layer, island.tiles
    tileset = island.tileset
    layer.clear()
    layer.palette[SKY] = (73, 171, 212, 255)
    layer.background = SKY  # transparent pixels are open sky

    map_width, map_height = island.pixel_size
    ox = (bps.config.display.width - map_width) // 2
    oy = (bps.config.display.height - map_height) // 2

    if mode == "tile":
        tw, th = island.tile_size
        # The grid is x-major, so argwhere hands back (x, y) pairs directly --
        # no transposing, and column/row mean what they say.
        for column, row in np.argwhere(tiles["tile"] >= 0):
            cell = tiles[column, row]
            layer.blit(
                tileset[int(cell["tile"])],
                ox + int(column) * tw,
                oy + int(row) * th,
                TileFlags(int(cell["flags"])),
            )

    elif mode == "full":
        layer.blit(tiles, ox, oy)

    elif mode == "region":
        # A rectangle of the TILESET, not the map: every tile the sheet has,
        # in atlas order, stamped by one blit. region() hands back the same
        # kind of grid TileMap.tiles does, which is why blit takes both.
        sheet = tileset.region(0, 0, tileset.cols, tileset.rows)
        tw, th = tileset.tile_size
        layer.blit(
            sheet,
            (bps.config.display.width - tileset.cols * tw) // 2,
            (bps.config.display.height - tileset.rows * th) // 2,
        )

    else:
        raise ValueError(f"unknown example mode: {mode}")

    bps.run()


if __name__ == "__main__":
    main()
