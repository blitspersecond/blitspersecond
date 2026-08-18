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

from blitspersecond import BlitsPerSecond
from blitspersecond.graphics import TileFlags, TileMap

MAP = Path(__file__).resolve().parents[1] / "blitspersecond/assets/gorillaz/island.tmx"

examples = ["tile", "full", "region"]
SKY = 255


def main() -> None:
    mode = examples[0]  # choose one of the three examples above

    bps = BlitsPerSecond()
    island = TileMap.open(str(MAP), bps.layers)["blue_island"]

    # A TileMap holds both halves: the layer it stamps into, and the world it
    # stamps from. Everything below works them by hand, so start from empty --
    # open() has already filled the visible window, which is the normal path
    # (see examples/layers.py).
    layer, plane = island.layer, island.plane
    tileset = layer.atlas
    layer.clear()
    layer.palette[SKY] = (73, 171, 212, 255)
    layer.background = SKY  # transparent pixels are open sky

    map_width, map_height = island.pixel_size
    ox = (bps.config.display.width - map_width) // 2
    oy = (bps.config.display.height - map_height) // 2

    if mode == "tile":
        for row, line in enumerate(plane):
            for column, cell in enumerate(line):
                index = int(cell["tile"])
                if index >= 0:
                    layer.blit(
                        tileset[index],
                        ox + column * island.tile_size[0],
                        oy + row * island.tile_size[1],
                        TileFlags(int(cell["flags"])),
                    )

    elif mode == "full":
        layer.blit(plane, ox, oy)

    elif mode == "region":
        # A rectangle of the TILESET, not the map: every tile the sheet has,
        # in atlas order, stamped by one blit. region() hands back the same
        # kind of plane TileMap does, which is why blit takes both.
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
