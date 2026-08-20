"""Move a Gorillaz head around with the first active gamepad.

The d-pad takes priority over the left analogue stick. This deliberately uses
``bps.gamepad`` directly: games which only need whichever pad the player picked
up do not need to manage Ports. (``bps.ports`` has four of them, and
``band.png`` has four heads -- 0 2-D, 1 Noodle, 2 Murdoc, 3 Russel -- so the
four-player version of this is `bps.ports.p1.gamepad` and `band.sprite(1)`.)

The sheet is the bare-image sprite path: an indexed PNG read as a uniform grid,
with no ``.sprite.json`` package behind it, which is what
``bps.layers.sprite(path, w, h)`` means. Poses are addressed by cell index the
way ``atlas[i]`` addresses a tileset, there are no animations to play and no
named collision planes -- just the silhouette. A sprite is placed at its
position, so unlike a one-tile tile layer there is no camera register to
invert.

Run from the repository root:

    venv/bin/python examples/gamepad.py

Close the window to quit.
"""

from pathlib import Path

from blitspersecond import BlitsPerSecond
from blitspersecond.colors import TRANSPARENT, WHITE, Pen
from blitspersecond.console import Console
from blitspersecond.graphics.glyph import CharSet

BAND = Path(__file__).resolve().parents[1] / "blitspersecond/assets/gorillaz/band.png"

HEAD = 12  # one cell of band.png, in pixels
TWO_D = 0  # 1 Noodle, 2 Murdoc, 3 Russel
SPEED = 3


def main() -> None:
    bps = BlitsPerSecond()

    band = bps.layers.sprite(str(BAND), HEAD, HEAD)
    cursor = band.sprite(TWO_D)
    x = (bps.config.display.width - HEAD) / 2
    y = (bps.config.display.height - HEAD) / 2
    cursor.present_at(x, y)

    status = Console(bps.layers.glyph(CharSet.ASCII8X12))
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

        x = min(max(x + dx * SPEED, 0), engine.config.display.width - HEAD)
        y = min(max(y + dy * SPEED, 0), engine.config.display.height - HEAD)
        cursor.present_at(x, y)

    bps.tick = move
    bps.run()


if __name__ == "__main__":
    main()
