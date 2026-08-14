"""Move a white cursor with the first active gamepad.

The d-pad takes priority over the left analogue stick. This deliberately uses
``bps.gamepad`` directly: games which only need whichever pad the player picked
up do not need to manage Ports.

Run from the repository root:

    venv/bin/python examples/hello_gamepad.py

Close the window to quit.
"""

from blitspersecond import BlitsPerSecond
from blitspersecond.colors import TRANSPARENT, WHITE, Pen
from blitspersecond.console import Console
from blitspersecond.graphics import PixelBuffer, TileEngine
from blitspersecond.graphics.glyph import CharSet, GlyphEngine
from blitspersecond.resources import ImageSpec

CURSOR_SIZE = 8
SPEED = 3


def main() -> None:
    bps = BlitsPerSecond()

    pixels = PixelBuffer(ImageSpec((CURSOR_SIZE, CURSOR_SIZE), "RGB"))
    with pixels.edit() as image:
        image[:] = (255, 255, 255)
    # The cursor: one whole-buffer tile on its own layer (auto-added).
    # scroll is a camera register, so content sits at MINUS scroll.
    cursor = TileEngine(pixels, tilewidth=CURSOR_SIZE, tileheight=CURSOR_SIZE)
    cursor.blit(0, 0, 0)
    x = (bps.config.display.width - CURSOR_SIZE) / 2
    y = (bps.config.display.height - CURSOR_SIZE) / 2
    cursor.scroll = (-x, -y)

    status = Console(GlyphEngine(CharSet.ASCII8X12))
    status.pen = Pen(TRANSPARENT, WHITE, TRANSPARENT, TRANSPARENT)
    shown_name = ""

    def move(engine: BlitsPerSecond) -> None:
        nonlocal shown_name, x, y
        pad = engine.gamepad
        name = "No gamepad" if pad is None else pad.name
        if name != shown_name:
            status.write(name[: status.cols].ljust(status.cols), at=(0, 0))
            shown_name = name
        if pad is None:
            return

        dx, dy = pad.dpad()
        if dx == 0 and dy == 0:
            dx, dy = pad.left_stick()

        x = min(
            max(x + dx * SPEED, 0),
            engine.config.display.width - CURSOR_SIZE,
        )
        y = min(
            max(y + dy * SPEED, 0),
            engine.config.display.height - CURSOR_SIZE,
        )
        cursor.scroll = (-x, -y)

    bps.tick = move
    bps.run()


if __name__ == "__main__":
    main()
