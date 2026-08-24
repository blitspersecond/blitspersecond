"""Load every layer of a TMX map and scroll the camera across it.

A TMX file is a factory: `maps.tilemap(name)` mints one TileMap per tile
layer, and `maps.names` lists them in the order the artist stacked them, so
adding them in that order makes back-to-front in Tiled back-to-front on
screen. A TileMap is the world grid plus the TileLayer it stamps into.

Minting does not draw. Every graphics type here is built unattached and put
on screen by `bps.layers.add(...)` -- the same law Console obeys.

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
from blitspersecond.graphics import TileFlags
from blitspersecond.resources import ResourceManager

MAP = Path(__file__).resolve().parents[1] / "assets/gorillaz/island.tmx"

SKY = 255
SPEED = 0.6


def main() -> None:
    bps = BlitsPerSecond()

    # The TMX is an asset: cached, shared, immutable. It mints TileMaps, which
    # are game objects -- each with its own camera and its own writable grid.
    maps = ResourceManager().tmx(str(MAP))
    for name in maps.names:  # authored back to front == draw order
        bps.layers.add(maps.tilemap(name))
    island = maps.tilemap("blue_island")  # memoised: the same map, not a copy

    # Rendering state lives on the layer, not the map -- the console.pen
    # equivalent. Transparent pixels in this tileset are open sky.
    island.layer.palette[SKY] = (73, 171, 212, 255)
    island.layer.background = SKY

    island.layer.collidable = True

    # Centre the world, then drift the camera. Sub-cell movement is one GPU
    # translate; crossing a cell boundary is what restamps the window.
    map_width, _ = island.pixel_size
    home = (map_width - bps.config.display.width) / 2
    ticks = 0

    def drift(engine: BlitsPerSecond) -> None:
        nonlocal ticks
        ticks += 1
        island.scroll = (home + sin(ticks * SPEED / 60.0) * home, 0.0)

    tile = island.layer.atlas[0]
    tile = island.layer.atlas[0, 0]

    island.layer.blit(tile, 0, 0, TileFlags.FLIP_H | TileFlags.FLIP_V)

    # A tileset with no TMX behind it: bps.layers.tile() gives you a bound
    # TileLayer and nothing else. There is no plane to auto-populate from, so
    # the layer starts blank -- every tile you want on screen is a manual
    # blit(), same as placing sprites by hand.
    stamps = bps.layers.tile(str(MAP.parent / "island.png"), 16, 16, tag="stamps")
    stamps.blit(0, 0, 0)
    stamps.blit(1, 16, 0)
    stamps.blit(2, 32, 0)

    bps.run(drift)


if __name__ == "__main__":
    main()
