"""Load every layer of a TMX map and scroll the camera across it.

`TileMap.open` mints one layer per TMX layer, in the order the artist stacked
them, so back-to-front in Tiled is back-to-front on screen. What you get back
is a TileMap per layer: the world plane plus the TileLayer it stamps into.

TileMap is to TileLayer what Console is to GlyphLayer -- a controller over a
layer somebody else placed. Rendering state stays on the layer, which is why
the palette and background below are set through ``island.layer``. The camera
belongs to the map, because the map owns the world the camera moves over: a
TileLayer only ever holds the cells you can see, and the TileMap refills the
edge as you cross into new ones.

Run from the repository root:

    venv/bin/python examples/layers.py

Close the window to quit.
"""

from math import sin
from pathlib import Path

from blitspersecond import BlitsPerSecond
from blitspersecond.graphics import TileMap

MAP = Path(__file__).resolve().parents[1] / "blitspersecond/assets/gorillaz/island.tmx"

SKY = 255
SPEED = 0.6


def main() -> None:
    bps = BlitsPerSecond()

    # One TileMap per TMX layer, already in the draw order, keyed by the name
    # the layer has in Tiled.
    maps = TileMap.open(str(MAP), bps.layers)
    island = maps["blue_island"]

    # Rendering state lives on the layer, not the map -- the console.pen
    # equivalent. Transparent pixels in this tileset are open sky.
    island.layer.palette[SKY] = (73, 171, 212, 255)
    island.layer.background = SKY

    # Centre the world, then drift the camera. Sub-cell movement is one GPU
    # translate; crossing a cell boundary is what restamps the window.
    map_width, _ = island.pixel_size
    home = (map_width - bps.config.display.width) / 2
    ticks = 0

    def drift(engine: BlitsPerSecond) -> None:
        nonlocal ticks
        ticks += 1
        island.scroll = (home + sin(ticks * SPEED / 60.0) * home, 0.0)

    bps.run(drift)


if __name__ == "__main__":
    main()
