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
from blitspersecond.graphics import CYAN, GRAY, WHITE
from blitspersecond.graphics.console import CharSet, Console, Pen
from blitspersecond.graphics.sprite import Sprite, SpriteSheet

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

        self.alive = self.swarm.visible
        self.alive[:] = False

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

            # Whole pixels, floored once, vectorised. The draw and the
            # collision stamp then read the same number rather than each
            # flooring their own copy.
            self.swarm.positions[:, 0] = np.floor(self.fx)
            self.swarm.positions[:, 1] = np.floor(self.fy)


class Readout:
    """Four numbers, top right, refreshed a few times a second.

    Rewritten every few frames rather than every frame -- not to save the
    work, which is trivial, but because digits changing at 60Hz cannot be
    read. The console only re-uploads when its cells actually change, so a
    held readout costs nothing.
    """

    WIDTH = 22
    EVERY = 10

    def __init__(self, bps: BlitsPerSecond, fountain: "Fountain") -> None:
        self.bps = bps
        self.fountain = fountain
        # Added after the fountain, so it draws over it -- the draw order is
        # the order layers are added.
        self.console = Console(CharSet.ASCII8X12)
        bps.layers.add(self.console)
        self.left = self.console.cols - self.WIDTH
        self.frames = 0

    def _ms(self, name: str) -> float:
        summary = self.bps.metrics.report().summarise(name)
        return summary.mean if summary and summary.observed else 0.0

    def tick(self) -> None:
        self.frames += 1
        if self.frames % self.EVERY:
            return
        alive = int(self.fountain.alive.sum())
        rows = (
            ("bullets", f"{alive:,}"),
            ("sim", f"{self.fountain.sim_ms:5.2f}ms"),
            ("compose", f"{self._ms('display.compose.ms'):5.2f}ms"),
            ("frame", f"{self._ms('engine.frame.ms'):5.2f}ms"),
        )
        for row, (label, value) in enumerate(rows):
            self.console.pen = Pen(0, GRAY)
            self.console.write(label.ljust(8), at=(self.left, row))
            self.console.pen = Pen(0, CYAN if row == 0 else WHITE)
            self.console.write(value.rjust(self.WIDTH - 9))


def main() -> None:
    bps = BlitsPerSecond()
    bps.background = (0.02, 0.02, 0.06)
    fountain = Fountain(bps)
    readout = Readout(bps, fountain)

    def tick() -> None:
        fountain.tick()
        readout.tick()

    bps.start(tick)


if __name__ == "__main__":
    main()
