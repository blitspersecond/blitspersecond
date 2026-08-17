"""Display the same TMX island using three TileEngine interfaces.

``tile`` blits concrete tiles one at a time, ``slice`` takes a range from the
tileset first, and ``full`` blits the complete map plane. Change ``mode`` in
``main`` to compare them.

Run from the repository root:

    venv/bin/python examples/hello_tmx.py

Close the window to quit.
"""

from pathlib import Path

from blitspersecond import BlitsPerSecond
from blitspersecond.graphics import TileEngine, TileFlags, TileMap

MAP = Path(__file__).resolve().parents[1] / "blitspersecond/assets/gorillaz/island.tmx"

examples = ["tile", "slice", "full"]


def main() -> None:
    mode = examples[0]  # choose one of the three examples above

    bps = BlitsPerSecond()
    island = TileMap.load(str(MAP))

    tileset = island.tilesets["island"]
    plane = island.layers["blue_island"]
    layer = TileEngine(tileset)
    layer.color = (73, 171, 212)  # the transparent pixels are open sky

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

    elif mode == "slice":
        tiles = tileset[:]
        for row, line in enumerate(plane):
            for column, cell in enumerate(line):
                index = int(cell["tile"])
                if index >= 0:
                    layer.blit(
                        tiles[index],
                        ox + column * island.tile_size[0],
                        oy + row * island.tile_size[1],
                        TileFlags(int(cell["flags"])),
                    )

    elif mode == "full":
        layer.blit(plane, ox, oy)

    else:
        raise ValueError(f"unknown example mode: {mode}")

    bps.run()


if __name__ == "__main__":
    main()
