"""Twenty-five thousand bullets from one sprite, in one draw call.

Run from the repository root:

    venv/bin/python examples/fountain.py

Close the window to quit. Borrowed from the `sprite_purist` prototype, which
is where this pillar's dense case was first proved.

## One sprite, 25,000 drawings of it

The fountain is ONE `Sprite` -- one layer, one texture, one collision
identity. The bullets are PRESENTATIONS of it: the same identity drawn many
times. That is why they cost one bind between them, and it is also the honest
statement of what they are, since "did a bullet hit" has one subject here and
not twenty-five thousand.

Two tiles, blue and pink, are two ASSEMBLIES rather than two materials. Both
live in the sheet's one tileset, so the whole population coalesces into a
single run -- assembly varies per presentation for free, because it is a
gather into the vertex stream rather than a change of GPU state.

## The simulation writes the arrays, not the sprite

    with self.swarm.edit():
        self.fy[live] += GRAVITY
        ...
        self.swarm.positions[:, 1] = np.floor(self.fy)

Reaching every bullet through its own property setter costs 21.8 ms a frame
at this size, against 3.1 ms writing the arrays -- 1.3 frames against a fifth
of one. So `Presentations` hands out real NumPy views and `edit()` marks the
sprite dirty once around the batch.

**Position is integer**, because the engine draws whole pixels; there is no
sub-pixel anything in this API. A simulation with sub-pixel velocity keeps its
own float arrays -- `fx`, `fy` below -- and floors on the way in. The engine
declines to keep that fraction because it cannot know what it meant: velocity,
fixed-tick interpolation and an easing curve all want it integrated somewhere
different.

## The readout

A `Console` layer added AFTER the fountain, so it draws over it. The numbers
worth watching are separated on purpose:

- **sim** is this file's own work -- the vectorised integrate, spawn and reap.
- **compose** is every layer's draw, the fountain's 25,000 quads included.
- **frame** is the engine clock between game ticks, which should sit flat on
  the authored period however busy the frame is; if it picks up the display
  grid's unevenness that is a defect, and `display.frame.ms` is the quantity
  that is SUPPOSED to be uneven.

## Press M to see what the collision system actually holds

The fountain composes a packed silhouette every frame -- 25,000 presentations
ORed into one bitmap, which is the whole point of a plane: "did enemy fire
touch the player" has one subject, not twenty-five thousand. `M` cycles how
much of that you see:

    off     the fountain, as it draws
    split   sprites on the left, the plane on the right
    full    the plane over the whole screen

**Split is the one that finds faults.** A plane that is a pixel off, a turn
applied the wrong way round, a stamp that lost its high carry word -- all of
those look plausible on their own and obvious against the picture they are
supposed to match, with the seam running down the middle of the same fountain.

The overlay is a second `Console` with its pixels written directly. A console
is already a full-screen indexed surface drawn as one quad, and one that is
never given a glyph never recomposes over what you put there -- so it is the
picture layer this engine has not written yet, reached through the back.

## Liveness is a bit, not a list

Nothing is created or destroyed per frame. A dead bullet sets `visible` False
and keeps its slot; the next spawn finds it with `flatnonzero(~visible)` and
reuses it. Removing presentations instead would be 1500 array memmoves a tick
and would destroy the thing the arrays exist for.
"""

from pathlib import Path
from time import perf_counter

import numpy as np

from blitspersecond import BlitsPerSecond
from blitspersecond.graphics import CYAN, GRAY, NAVY, TRANSPARENT, WHITE
from blitspersecond.graphics.console import CharSet, Console, Pen
from blitspersecond.graphics.sprite import Sprite, SpriteSheet
from blitspersecond.input import key

SHEET = Path(__file__).resolve().parents[1] / "assets/bullets/bullets.sprite.json"

COUNT = 25_000
PER_TICK = 190
GRAVITY = 0.12
SPREAD = 2.2
#: Negative is UP: the engine's y grows downward, the same as every pixel
#: coordinate in it. Tuned so the arc fills the screen and a bullet lives
#: about two seconds, which at PER_TICK keeps the population just under COUNT
#: -- the interesting case, where the free list is nearly empty.
LIFT = (-7.6, -5.6)


class Fountain:
    """The simulation. It owns float position and velocity; the sprite owns
    how the result is drawn and nothing else."""

    def __init__(self, bps: BlitsPerSecond) -> None:
        self.sprite = Sprite(SpriteSheet(str(SHEET)), tag="fountain")
        bps.layers.add(self.sprite)
        # The sprite's own presentation draws nothing: this population has no
        # principal, and a swarm's slot 0 would just be an arbitrary peer.
        self.sprite.assembly = None

        self.swarm = self.sprite.after.extend(COUNT)
        # Two tiles in one tileset, so two assemblies and still one run. The
        # NAME is validated once here; the ids then broadcast across 25,000.
        blue = self.sprite.assembly_id("blue")
        pink = self.sprite.assembly_id("pink")
        self.swarm.assemblies[0::2] = blue
        self.swarm.assemblies[1::2] = pink

        # TWO bits, and this simulation wants them moving together: a bullet
        # that is drawn is a bullet that can hit. They are separate in the
        # engine because they answer different questions -- a ghost is drawn
        # and hits nothing, a cloak hits and is not drawn -- and the first
        # thing the mask view showed when it was added to this demo was a
        # completely empty plane, because only `visible` was being set.
        self.alive = self.swarm.visible
        self.hits = self.swarm.collide
        self.alive[:] = False
        self.hits[:] = False

        # The float half of the simulation, which the engine deliberately does
        # not keep. Everything below integrates here and floors on the way out.
        self.fx = np.zeros(COUNT, dtype=np.float32)
        self.fy = np.zeros(COUNT, dtype=np.float32)
        self.vx = np.zeros(COUNT, dtype=np.float32)
        self.vy = np.zeros(COUNT, dtype=np.float32)
        self.rng = np.random.default_rng(0xB011E7)

        self.origin = (bps.width // 2, bps.height - 24)
        self.bounds = (-8.0, float(bps.width + 8), -8.0, float(bps.height + 8))
        self.sim_ms = 0.0

    def _spawn(self) -> None:
        """Reuse dead slots. No allocation, no relinking, no reordering."""
        free = np.flatnonzero(~self.alive)[:PER_TICK]
        count = len(free)
        if not count:
            return
        self.fx[free] = self.origin[0]
        self.fy[free] = self.origin[1]
        self.vx[free] = self.rng.uniform(-SPREAD, SPREAD, count)
        self.vy[free] = self.rng.uniform(*LIFT, count)
        self.alive[free] = True
        self.hits[free] = True

    def tick(self) -> None:
        started = perf_counter()
        self._step()
        self.sim_ms = (perf_counter() - started) * 1000.0

    def _step(self) -> None:
        with self.swarm.edit():
            self._spawn()
            live = self.alive
            self.vy[live] += GRAVITY
            self.fx[live] += self.vx[live]
            self.fy[live] += self.vy[live]

            left, right, top, bottom = self.bounds
            gone = live & (
                (self.fx < left)
                | (self.fx > right)
                | (self.fy < top)
                | (self.fy > bottom)
            )
            self.alive[gone] = False
            self.hits[gone] = False

            # Whole pixels, floored once, vectorised. The draw and the
            # collision stamp then read the same number rather than each
            # flooring their own copy.
            self.swarm.positions[:, 0] = np.floor(self.fx)
            self.swarm.positions[:, 1] = np.floor(self.fy)


class MaskView:
    """The collision plane, drawn over the fountain. `M` cycles the mode.

    ## Why a Console is the canvas

    A console is already what this needs: a full-screen indexed surface,
    uploaded when dirty and drawn as one quad. Giving it no glyphs means it
    never recomposes -- `_dirty_top > _dirty_bottom` is an empty span -- so
    pixels written straight into its image survive. Until the picture layer
    lands this is the engine's picture layer, reached through the back, and
    the demo says so rather than pretending it is an API.

    ## Why split, and not a toggle

    A blink test compares two pictures separated in TIME and the eye is bad at
    it. Side by side, with the seam through the middle of one continuous
    fountain, a plane that is a pixel out or a word short reads instantly --
    the arcs stop lining up across the seam.

    The left half is left at index 0, which the indexed stage discards, so the
    sprites below show through untouched. The right half is painted opaque, so
    what you see there is the plane and only the plane.
    """

    OFF, SPLIT, FULL = 0, 1, 2
    MODES = ("off", "split", "full")

    #: Palette slots this writes. 0 stays transparent -- it is the hole the
    #: sprites show through, and the whole left half is made of it.
    GROUND = 4
    BIT = 5
    SEAM = 6

    def __init__(self, bps: BlitsPerSecond, fountain: "Fountain") -> None:
        self.bps = bps
        self.fountain = fountain
        self.mode = self.SPLIT
        self.plane_ms = 0.0
        self.paint_ms = 0.0
        self.bits = 0

        fountain.sprite.collidable = True
        self.canvas = Console(CharSet.ASCII8X12)
        palette = self.canvas.palette[0]
        palette[self.GROUND] = (10, 6, 20)
        palette[self.BIT] = (255, 240, 120)
        palette[self.SEAM] = (255, 64, 64)
        bps.layers.add(self.canvas)

        self.pixels = self.canvas._image.pixels
        self.middle = self.pixels.shape[1] // 2
        self.cursor = None
        self.painted = False

    def _pressed(self) -> bool:
        """Did M go down in the window that just closed?

        `pressed` is a fact, not a state -- reading the scan's `held` instead
        would toggle the mode sixty times a second for as long as the key was
        down. The cursor is what makes a fact read exactly once.
        """
        window = self.bps.keyboard.stream.newest
        if window is None or window.frame == self.cursor:
            return False
        self.cursor = window.frame
        return key.M in window.scan.pressed

    def tick(self) -> None:
        if self._pressed():
            self.mode = (self.mode + 1) % len(self.MODES)

        if self.mode == self.OFF:
            # Cleared once on the way out rather than every frame, and driven
            # by what is actually on the canvas rather than by the transition
            # -- so setting the mode by hand leaves nothing stale behind.
            if self.painted:
                self.pixels[:] = 0
                self.canvas._image.dirty = True
                self.painted = False
            self.plane_ms = self.paint_ms = 0.0
            return
        self.painted = True

        sprite = self.fountain.sprite
        # Composed HERE rather than left to the render, purely so it can be
        # timed on its own -- the sim has already moved everything, so this is
        # the same work at the same moment, just measured. Clearing the flag
        # is what stops `_sync` doing it a second time.
        started = perf_counter()
        sprite._collision_mask().compose()
        sprite._collision_dirty = False
        self.plane_ms = (perf_counter() - started) * 1000.0

        started = perf_counter()
        plane = sprite.collider().unpacked
        self.bits = int(plane.sum())
        left = self.middle if self.mode == self.SPLIT else 0
        self.pixels[:, :left] = 0
        self.pixels[:, left:] = np.where(
            plane[:, left:], self.BIT, self.GROUND
        )
        if self.mode == self.SPLIT:
            self.pixels[:, self.middle] = self.SEAM
        self.canvas._image.dirty = True
        self.paint_ms = (perf_counter() - started) * 1000.0


class Readout:
    """The corner numbers, refreshed a few times a second.

    Rewritten every few frames rather than every frame -- not to save the
    work, which is trivial, but because digits changing at 60Hz cannot be
    read. The console only re-uploads when its cells actually change, so a
    held readout costs nothing.

    Separated on purpose, because they are different questions:

    - **sim** is this file's own work -- the vectorised integrate and reap.
    - **plane** is composing 25,000 stamps into one packed bitmap. It is the
      number this demo was extended to show, and it is CPU on the frame
      thread, so it comes out of the same budget as everything else.
    - **paint** is the overlay itself, and belongs to the demo rather than to
      the engine: unpacking a plane to 640x360 bool is a debug convenience and
      nothing in a game does it.
    - **compose** is every layer's draw.
    - **engine** should sit flat on the authored rate however busy the frame
      is. **display** is what reached the screen and is BIMODAL by design on a
      panel whose refresh is not a multiple of the game rate.
    - **dropped** is the one to watch: a frame composed and then overwritten
      before it was ever shown.
    """

    WIDTH = 24
    EVERY = 10

    def __init__(
        self, bps: BlitsPerSecond, fountain: "Fountain", masks: "MaskView"
    ) -> None:
        self.bps = bps
        self.fountain = fountain
        self.masks = masks
        # Added last, so it draws over both the fountain and the overlay.
        # Transparent background with a dark outline plane, so the numbers
        # stay legible over the bright plane and the dark ground alike.
        self.console = Console(CharSet.ASCII8X12)
        bps.layers.add(self.console)
        # Built once, not per row. Slot 2 is the font's outline plane, and a
        # fresh `Pen` would not carry it -- reassigning per line would quietly
        # drop the outline that keeps white text legible over a bright plane.
        self.pens = {}
        for color in (WHITE, GRAY, CYAN):
            pen = Pen(TRANSPARENT, color)
            pen[2] = NAVY
            self.pens[color] = pen
        self.left = self.console.cols - self.WIDTH - 1
        self.frames = 0

    def _ms(self, name: str) -> float:
        summary = self.bps.metrics.report().summarise(name)
        return summary.mean if summary and summary.observed else 0.0

    def _rate(self, name: str) -> str:
        mean = self._ms(name)
        return f"{1000.0 / mean:.1f} fps" if mean else "--"

    def _total(self, name: str) -> str:
        summary = self.bps.metrics.report().summarise(name)
        return "--" if summary is None else f"{int(summary.total)}"

    def tick(self) -> None:
        self.frames += 1
        if self.frames % self.EVERY:
            return
        alive = int(self.fountain.alive.sum())
        masks = self.masks
        rows = (
            ("bullets", f"{alive:,}", CYAN),
            ("masks", masks.MODES[masks.mode].upper(), CYAN),
            ("bits set", f"{masks.bits:,}", WHITE),
            ("", "", WHITE),
            ("sim", f"{self.fountain.sim_ms:5.2f} ms", WHITE),
            ("plane", f"{masks.plane_ms:5.2f} ms", WHITE),
            ("paint", f"{masks.paint_ms:5.2f} ms", GRAY),
            ("compose", f"{self._ms('display.compose.ms'):5.2f} ms", WHITE),
            ("", "", WHITE),
            ("engine", self._rate("engine.frame.ms"), WHITE),
            ("display", self._rate("display.frame.ms"), WHITE),
            ("dropped", self._total("display.frames.dropped"), WHITE),
        )
        # `defer()` batches the block into one composite of the console
        # surface rather than one per line.
        with self.console.defer():
            for row, (label, value, color) in enumerate(rows):
                self.console.pen = self.pens[GRAY]
                self.console.write(label.ljust(9), at=(self.left, row))
                self.console.pen = self.pens[color]
                self.console.write(value.rjust(self.WIDTH - 10))
            self.console.pen = self.pens[GRAY]
            self.console.write(
                "M cycles masks".rjust(self.WIDTH),
                at=(self.left, self.console.rows - 1),
            )


def main() -> None:
    bps = BlitsPerSecond()
    bps.background = (0.02, 0.02, 0.06)
    fountain = Fountain(bps)
    # Draw order is the order layers are added: fountain, then the plane over
    # it, then the numbers over both.
    masks = MaskView(bps, fountain)
    readout = Readout(bps, fountain, masks)

    def tick() -> None:
        fountain.tick()
        masks.tick()
        readout.tick()

    bps.start(tick)


if __name__ == "__main__":
    main()
