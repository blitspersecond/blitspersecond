"""Fumiko walks back and forth.

A sprite package carries the sheet, the named poses and the clips, so the
whole of the animation is `play("walk")` -- the clip advances itself on the
game tick from there. What the game keeps is initiation and position: `play`
is the cancel and the reissue too, and nothing needs calling per tick to keep
a clip running.

`flip_x` is facing. It mirrors the drawn parts and the collision planes
together about the anchor, and never rewrites the asset, so one walk cycle
drawn facing right serves both directions.

Run from the repository root:

    venv/bin/python examples/sprite.py

Close the window to quit.
"""

from pathlib import Path

from blitspersecond import BlitsPerSecond

SHEET = Path(__file__).resolve().parents[1] / "assets/fumiko/fumiko.sprite.json"

SPEED = 1.0
MARGIN = 40


def main() -> None:
    bps = BlitsPerSecond()

    fumiko = bps.layers.sprite(str(SHEET))
    walker = fumiko.sprite()
    walker.play("walk")

    # Fumiko's cells are anchored between her feet, so her position is where
    # she stands -- and flipping turns her on the spot rather than shifting
    # her sideways.
    floor = bps.config.display.height // 2
    left, right = MARGIN, bps.config.display.width - MARGIN
    x = float(left)
    step = SPEED

    def walk(engine: BlitsPerSecond) -> None:
        nonlocal x, step
        x += step
        if not left <= x <= right:
            step = -step
            x = min(max(x, left), right)
        walker.present_at(x, floor, flip_x=step < 0)

    bps.run(walk)


if __name__ == "__main__":
    main()
