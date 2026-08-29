"""Two roads to the same picture: a world read from a file, and one built by
hand.

``file`` loads the island out of ``island.tmx``. ``sheet`` builds a world with
``numpy`` that lays the tileset out in atlas order. They differ only in where
the array came from, and nothing downstream can tell -- a TileMap does not
know or care which road its world arrived by.

That is the point of the example. TMX is a convenience and not the way in: a
game generating its world procedurally is doing an ordinary thing, and the
loader adds no capability that ``empty_map`` and an assignment do not have.

Change ``MODE`` to compare them. Run from the repository root:

    venv/bin/python examples/tmx.py

The scene is static, so there is no loop to write: `start()` services a
do-nothing one on the display's cadence. Close the window to quit.
"""

import numpy as np

from blitspersecond import BlitsPerSecond
from blitspersecond.common import PACKAGE_ROOT, SCREEN
from blitspersecond.graphics import TiledFile, TileMap, empty_map

MAP = PACKAGE_ROOT.parent / "assets/gorillaz/island.tmx"
MODE = "file"  # "file" or "sheet"

#: The sky, as the artist's own blue. A tilemap draws tiles and nothing else --
#: its empty cells are genuinely empty, so what shows through them is whatever
#: is behind, which here is the framebuffer itself.
SKY = (73 / 255, 171 / 255, 212 / 255)


def from_file() -> TileMap:
    """The island, exactly as Tiled has it."""
    return TiledFile(str(MAP)).tilemap("blue_island")


def from_hand() -> TileMap:
    """Every tile of the same sheet, in atlas order, as a world nobody
    authored.

    `empty_map` gives the grid, `arange` fills it, and the result is the same
    kind of array the loader produces -- which is why it can be handed to the
    same constructor. Reshaping to (cols, rows) and transposing is what makes
    the sheet read left-to-right then down: a world is x-major, and atlas
    order is not.
    """
    tileset = TiledFile(str(MAP)).layer("blue_island").tileset
    world = empty_map(tileset.cols, tileset.rows)
    world["tile"] = np.arange(len(tileset)).reshape(tileset.rows, tileset.cols).T
    return TileMap(tileset, world, tag="sheet")


def centre(tilemap: TileMap) -> None:
    """Put the world in the middle of the screen.

    `origin` is where the world SITS; `scroll` would be where you are looking,
    and parking the camera at a negative offset to centre a small map would
    say the game is looking somewhere outside its own world.

    Note what is not here: nothing stamps anything. A map holds the viewport
    and one cell of border, and paints that itself as the camera reaches it --
    so placing the world is the whole job, even for one that fits on screen.
    """
    width, height = tilemap.pixel_size
    tilemap.origin = (
        (SCREEN.width - width) // 2,
        (SCREEN.height - height) // 2,
    )


def main() -> None:
    bps = BlitsPerSecond()
    bps.background = SKY

    tilemap = from_file() if MODE == "file" else from_hand()
    bps.layers.add(tilemap)
    centre(tilemap)

    bps.start()


if __name__ == "__main__":
    main()
