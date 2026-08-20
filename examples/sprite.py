"""Four Fumikos walk back and forth, each in her own colours.

A sprite package carries the sheet, the named poses and the clips, so the
whole of the animation is `play("walk")` -- the clip advances itself on the
game tick from there. What the game keeps is initiation and position: `play`
is the cancel and the reissue too, and nothing needs calling per tick to keep
a clip running. Four sprites means four clips, each with its own frame
counter, all advancing without a line of bookkeeping here.

`palettes` is why there are four of her and still only one set of pixels. A
palette register is per instance, per tileset -- the sheet holds the tables,
the sprite holds which one it is reading -- so recolouring costs a register
write, not a copy of the art, and the four of them coalesce into the same
drawing run regardless. Fumiko's four tables recolour her hair, which is the
one thing an indexed sprite can do that a truecolour one cannot do for free.

`orientation` is facing, and it is the same eight axis-aligned transforms a
tile placement uses. It mirrors the drawn parts and the collision planes
together about the origin, and never rewrites the asset, so one walk cycle
drawn facing right serves both directions.

Run from the repository root:

    venv/bin/python examples/sprite.py

Close the window to quit.
"""

from dataclasses import dataclass
from pathlib import Path

from blitspersecond import BlitsPerSecond
from blitspersecond.graphics import Orientation, Sprite

SHEET = Path(__file__).resolve().parents[1] / "assets/fumiko/fumiko.sprite.json"

SPEED = 1.0
MARGIN = 40
STAGGER = 120

#: Fumiko's palette tables, in the order the package stores them. The colour
#: that changes between them is her hair, so the four of them are RWBY -- red,
#: white, black, yellow -- which is the mnemonic doing the job a name would.
#: The format has no name for a palette table: registers are integers, and at
#: four entries a mnemonic is a fair trade for a lookup table.
TEAM = (("Ruby", 0), ("Weiss", 1), ("Blake", 2), ("Yang", 3))


@dataclass
class Walker:
    """A sprite plus what the game owns about her: who she is, where she is
    along her row, and which way she is going. The sprite owns everything
    about how she is drawn and nothing else -- it has no name, because a
    character's identity is the game's business, not the renderer's."""

    name: str
    sprite: Sprite
    x: float
    step: float


def main() -> None:
    bps = BlitsPerSecond()

    # One layer, one sheet, one set of pixels -- four instances on it.
    fumiko = bps.layers.sprite(str(SHEET))

    left = MARGIN
    right = bps.config.display.width - MARGIN
    spacing = bps.config.display.height // (len(TEAM) + 1)

    walkers = []
    for row, (name, palette) in enumerate(TEAM, start=1):
        sprite = fumiko.sprite()
        sprite.play("walk")

        # A sprite draws from its origin and mirrors about it too. Left at the
        # default (0, 0) that is the art's own top-left, so turning round would
        # swing her a full width sideways -- which is what mirroring about a
        # corner means, and not what a character wants. Moving the origin to
        # the middle of the art makes her turn on the spot, and makes her
        # position mean her middle rather than her corner.
        sprite.origin = sprite.centre

        # The register, not the pixels. Her sisters on the other rows are
        # reading different tables out of the same tileset.
        sprite.palettes["character"] = palette

        # The row never changes, so it is written once here and only `x` moves
        # per tick.
        start = float(left + (row - 1) * STAGGER)
        sprite.position = (start, row * spacing)
        walkers.append(Walker(name, sprite, start, SPEED))

    def walk(engine: BlitsPerSecond) -> None:
        for walker in walkers:
            walker.x += walker.step
            if not left <= walker.x <= right:
                walker.step = -walker.step
                walker.x = min(max(walker.x, left), right)
            walker.sprite.x = walker.x
            walker.sprite.orientation = (
                Orientation.FLIP_H if walker.step < 0 else Orientation.NONE
            )

    bps.run(walk)


if __name__ == "__main__":
    main()
