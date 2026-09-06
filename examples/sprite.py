"""Four Fumikos walk back and forth, each in her own colours, each trailing.

Run from the repository root:

    venv/bin/python examples/sprite.py

Close the window to quit.

## One sprite is one layer

There is no `SpriteLayer` holding instances. **A `Sprite` IS a layer**, and
there is exactly one per layer, because two things told apart by a collision
question have to be two layers -- the OR'd collision bits do not say whose is
whose. So four characters are four sprites, four layers, four identities.

They still cost one set of pixels between them: naming a package twice gives
you THE sheet for that file, so these four share one `SpriteSheet`, one
texture upload, and four palette REGISTERS reading different tables out of it.
That is the whole trick behind Fumiko's four hair colours -- a register write,
not a copy of the art.

## What a trail is, and is not

The four ghosts behind each Fumiko are not sprites. They are PRESENTATIONS of
her: the same identity drawn more than once, sharing her texture and her
collision answer. `sprite.before` is the chain drawn before her and so behind
her; `sprite.after` would be in front.

Each ghost carries its own position and pose, and `collide = False`, because
an afterimage that could be hit would be a bug the art tells you nothing
about.

## How the trail fades, since it cannot fade with alpha

A sprite pixel is drawn or it is not. There is no per-presentation alpha and
no blending inside a sprite pass, because the engine paints back to front and
colour information is last-writer-wins -- so **an afterimage is the same art
drawn again through a dimmer table**, which is what the hardware this engine
models actually did.

The package ships four tables, one per sister, and they are costumes rather
than shades. So this generates the shades: six per sister, each pulled further
toward her signature colour and darker than the last, appended to the bank at
load with `PaletteBank.extend`. A ghost then costs one register write, exactly
like a costume swap, because that is what it now is.

Pulling toward her colour rather than only darkening is what makes the trail
read as a silhouette of HER rather than as a dim copy: the palette's variety
collapses toward one hue as the ghost ages. Ruby trails red, Blake trails
almost nothing.

**Twenty-four generated tables between the four of them, and one bank.** The
sheet is interned by path, so the sisters share a tileset and therefore share
its bank -- each takes the six rows she asked for and leaves the rest alone.

Draw order is the chain, not the slot number. A presentation's slot id is its
identity and never moves; where it draws is its place in the chain. That split
is what makes reordering free and renumbering unnecessary.

## The whole of the animation is `play("walk")`

The package carries CLIPS -- named sequences of `(assembly, duration_ticks)`
-- and `play` starts one. It advances itself on the fixed tick from there:
there is no frame counter in this file, nothing called per tick to keep it
running, and four sprites means four cursors all advancing without a line of
bookkeeping here.

`play` is also the cancel, the interruption and the reissue, which is why it
is the only animation verb. Writing `sprite.assembly` by hand takes a sprite
off its clip -- two things writing one pose every tick is a fight, and the
explicit write wins.

The GHOSTS are posed by hand, and that is the distinction: a clip belongs to
the sprite, never to a presentation. A trail ghost showing a snapshot of the
past is exactly a thing that must not be playing anything.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from blitspersecond import BlitsPerSecond
from blitspersecond.graphics import Orientation
from blitspersecond.graphics.sprite import Sprite, SpriteSheet

SHEET = Path(__file__).resolve().parents[1] / "assets/fumiko/fumiko.sprite.json"

SPEED = 1.0
MARGIN = 40
STAGGER = 120
GHOSTS = 6
#: Ticks between a ghost and the one in front of it. Wide enough that the
#: trail reads as motion rather than as a blur.
TRAIL_GAP = 3

#: Fumiko's palette tables, in the order the package stores them, with the
#: colour each sister's trail collapses toward. The table that changes between
#: them is her hair, so the four of them are RWBY -- red, white, black, yellow
#: -- which is the mnemonic doing the job a name would.
#:
#: The signature colour is AUTHORED here rather than read out of the art. It
#: could be found by diffing one table against another, since the hair is the
#: only thing that differs, and that would be clever and brittle: it would
#: silently pick something else the day a package changed two things.
TEAM = (
    ("Ruby", 0, (188, 32, 40)),
    ("Weiss", 1, (232, 232, 240)),
    ("Blake", 2, (56, 52, 64)),
    ("Yang", 3, (232, 188, 72)),
)

#: How dark the OLDEST ghost gets. Not 1.0: a trail that ends at pure black
#: ends by disappearing against a dark stage, and the point of the last ghost
#: is to still be the shape of her.
DEEPEST = 0.82


@dataclass
class Walker:
    """A sprite plus what the GAME owns about her: who she is, where she is
    along her row, which way she is going, and the recent past her trail is
    made of.

    The sprite owns everything about how she is drawn and nothing else -- it
    has no name, because a character's identity is the game's business and not
    the renderer's. The history is here for the same reason: a ghost presents
    a snapshot, and deciding what is worth remembering is a game decision.
    """

    name: str
    sprite: Sprite
    x: float
    step: float
    ghosts: list
    history: List[tuple] = field(default_factory=list)


def main() -> None:
    bps = BlitsPerSecond()
    sheet = SpriteSheet(str(SHEET))

    left = MARGIN
    # 640x360 is the engine's fixed shape rather than a setting, so these
    # are read off the engine and not out of config.
    right = bps.width - MARGIN
    spacing = bps.height // (len(TEAM) + 1)

    walkers = []
    for row, (name, palette, signature) in enumerate(TEAM, start=1):
        # One sheet, four sprites: shared pixels, separate identities.
        sprite = Sprite(sheet, tag=name.lower())
        bps.layers.add(sprite)
        sprite.play("walk")

        # A sprite draws from its origin and mirrors about it too. Left at the
        # default (0, 0) that is the art's own top-left, so turning round would
        # swing her a full width sideways -- which is what mirroring about a
        # corner means, and not what a character wants. Moving the origin to
        # the middle of the art makes her turn on the spot, and makes her
        # position mean her middle rather than her corner.
        #
        # Read ONCE, at setup: it is derived from the pose being shown, and
        # reassigning it every tick would make her wander.
        sprite.origin = sprite.centre

        # The register, not the pixels. Her sisters on the other rows are
        # reading different tables out of the same tileset.
        sprite.palettes["character"] = palette

        # Her six shades, derived once and added to THIS SPRITE's table. Step
        # 1 is the faintest change and step GHOSTS is the deepest, so the list
        # reads oldest-last and is indexed backwards below -- `before[0]` is
        # drawn first and is therefore the oldest.
        #
        # `add` copies the sheet's bank for this sprite rather than growing
        # the sheet's own: a `SpriteSheet` is immutable and interned by path,
        # so appending to it would reach every other holder -- her sister two
        # layers up, or the same level loaded again. The copy shares every
        # existing row; what it really costs is one more palette texture.
        costume = sheet.tilesets["character"].palettes[palette]
        shades = sprite.palettes.add(
            "character",
            (
                costume.shadowed(
                    signature,
                    mix=step / GHOSTS,
                    dim=DEEPEST * step / GHOSTS,
                )
                for step in range(1, GHOSTS + 1)
            ),
        )

        # The trail. Allocated once and never grown: what changes per tick is
        # the CONTENTS of these four, not how many there are or where they sit
        # in the order. They arrive parked, so nothing appears until there is
        # a history to put in them.
        #
        # Allocated AFTER the palette register above, because a presentation
        # is seeded from the sprite -- these are drawings of Weiss, so they
        # arrive with Weiss's hair without being told. Only `visible` and
        # `collide` are not inherited.
        ghosts = list(sprite.before.extend(GHOSTS))
        for index, ghost in enumerate(ghosts):
            # Furthest back is oldest, because `before[0]` draws first -- so
            # distance along the chain IS age, and the deepest shade goes at
            # the far end. Read the index backwards rather than reversing the
            # list: the chain's order is the thing being expressed.
            ghost.palettes["character"] = shades[GHOSTS - 1 - index]
            ghost.collide = False

        start = float(left + (row - 1) * STAGGER)
        sprite.position = (start, row * spacing)
        walkers.append(Walker(name, sprite, start, SPEED, ghosts))

    def walk() -> None:
        for walker in walkers:
            walker.x += walker.step
            if not left <= walker.x <= right:
                walker.step = -walker.step
                walker.x = min(max(walker.x, left), right)

            sprite = walker.sprite
            sprite.x = walker.x
            sprite.orientation = (
                Orientation.FLIP_H if walker.step < 0 else Orientation.NONE
            )

            # Remember where she was, then hand each ghost a snapshot from
            # that far back. The oldest goes furthest back in the chain.
            # The pose comes off the clip the engine is advancing, so the
            # snapshot records what she actually looked like this tick.
            walker.history.append(
                (sprite.x, sprite.orientation, sprite.assembly)
            )
            del walker.history[: -(GHOSTS + 1) * TRAIL_GAP]
            for index, ghost in enumerate(walker.ghosts):
                age = (len(walker.ghosts) - index) * TRAIL_GAP
                if len(walker.history) <= age:
                    ghost.visible = False
                    continue
                x, orientation, snapshot = walker.history[-1 - age]
                ghost.position = (x, sprite.y)
                ghost.orientation = orientation
                ghost.assembly = snapshot
                ghost.visible = True

    bps.start(walk)


if __name__ == "__main__":
    main()
