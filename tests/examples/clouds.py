"""CLOUDS: fake volumetrics built out of console block graphics.

This is deliberately not a fluid simulation.  A small pile of animated 2D
metaballs makes the cloud mass, periodic value noise roughens its outline, and
samples taken towards the sun fake self-shadowing.  The result is collapsed
onto the runtime quadrant font: 160x90 fake pixels on an 80x45 text grid.

The final dirty trick is ConsolePalette.  Its specially sanctioned alpha lets
the thin density bands remain genuinely translucent over the sunset while the
body is still a tiny, fixed, old-machine palette.

Controls: LEFT/RIGHT move the light, SPACE changes the cloud formation.
BPS_MAX_TICKS closes automatically for the test harness.

    python tests/examples/clouds.py
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pyglet

from blitspersecond import BlitsPerSecond
from blitspersecond.input import key, keyboard
from blitspersecond.graphics import GlyphLayer, PixelBuffer, TileEngine
from blitspersecond.graphics.glyph import GlyphEngine
from blitspersecond.resources import ConsolePalette, ImageSpec

SCREEN_W, SCREEN_H = 640, 360
FAKE_W, FAKE_H = 160, 90
COLS, ROWS = FAKE_W // 2, FAKE_H // 2
MAX_TICKS = int(os.environ.get("BPS_MAX_TICKS", "0"))
CAPTURE = os.environ.get("BPS_CAPTURE")

# One colour per text cell, selected from this ramp.  The four quadrants in
# that cell provide coverage; ConsolePalette alpha supplies the last little
# bit of haze around the silhouette.
SHADES = (
    (67, 56, 82),
    (91, 70, 96),
    (122, 91, 116),
    (154, 112, 130),
    (187, 139, 145),
    (216, 169, 153),
    (239, 197, 164),
    (255, 226, 183),
)
ALPHAS = (68, 119, 170, 221, 255)
SHADE_COUNT = len(SHADES)

# Reading-order thresholds match quadrant_font's bit order.  The offset stops
# exact threshold values from producing unstable boundary pixels.
BAYER = (np.array([[0, 2], [3, 1]], dtype=np.float32) + 0.5) / 4.0
BAYER_FIELD = np.tile(BAYER, (FAKE_H // 2, FAKE_W // 2))


def smoothstep(lo: float, hi: float, value: np.ndarray) -> np.ndarray:
    value = np.clip((value - lo) / (hi - lo), 0.0, 1.0)
    return value * value * (3.0 - 2.0 * value)


def periodic_noise(
    rng: np.random.Generator, grid_h: int, grid_w: int
) -> np.ndarray:
    """Periodic smooth value noise at exactly the fake-pixel resolution."""
    grid = rng.uniform(-1.0, 1.0, (grid_h, grid_w)).astype(np.float32)

    yf = np.arange(FAKE_H, dtype=np.float32) * grid_h / FAKE_H
    xf = np.arange(FAKE_W, dtype=np.float32) * grid_w / FAKE_W
    y0 = np.floor(yf).astype(np.int32)
    x0 = np.floor(xf).astype(np.int32)
    ty = yf - y0
    tx = xf - x0
    ty = ty * ty * (3.0 - 2.0 * ty)
    tx = tx * tx * (3.0 - 2.0 * tx)
    y1 = (y0 + 1) % grid_h
    x1 = (x0 + 1) % grid_w

    top = grid[y0[:, None], x0] * (1.0 - tx) + grid[y0[:, None], x1] * tx
    bottom = grid[y1[:, None], x0] * (1.0 - tx) + grid[y1[:, None], x1] * tx
    return top * (1.0 - ty[:, None]) + bottom * ty[:, None]


def shifted(field: np.ndarray, x: float, y: float) -> np.ndarray:
    """Cheap fractional, wrapping translation for the animated noise."""
    ix, iy = int(np.floor(x)), int(np.floor(y))
    fx, fy = x - ix, y - iy
    a = np.roll(field, (iy, ix), axis=(0, 1))
    b = np.roll(field, (iy, ix + 1), axis=(0, 1))
    c = np.roll(field, (iy + 1, ix), axis=(0, 1))
    d = np.roll(field, (iy + 1, ix + 1), axis=(0, 1))
    return (a * (1.0 - fx) + b * fx) * (1.0 - fy) + (
        c * (1.0 - fx) + d * fx
    ) * fy


@dataclass(frozen=True)
class Blob:
    x: float
    y: float
    rx: float
    ry: float
    weight: float
    phase: float


class CloudField:
    """A coherent density field and its fake volumetric lighting."""

    def __init__(self, seed: int = 4) -> None:
        self.seed = seed
        self.light_x = -0.72
        self.reseed(seed)

    def reseed(self, seed: int) -> None:
        self.seed = seed
        rng = np.random.default_rng(seed)
        self.noise = (
            periodic_noise(rng, 7, 12) * 0.58
            + periodic_noise(rng, 15, 27) * 0.29
            + periodic_noise(rng, 31, 53) * 0.13
        )

        # A low bank plus several deliberately art-directed towers.  Jittering
        # these numbers on reseed changes the formation without losing the
        # towering Feel Good Inc silhouette.
        shapes = (
            (-8, 83, 38, 19, 0.92),
            (26, 82, 35, 22, 0.93),
            (66, 85, 44, 21, 0.95),
            (112, 83, 42, 22, 0.95),
            (151, 84, 39, 20, 0.92),
            (12, 61, 24, 24, 0.78),
            (38, 52, 27, 30, 0.86),
            (65, 36, 25, 38, 0.98),
            (88, 29, 22, 34, 0.92),
            (111, 52, 31, 28, 0.84),
            (143, 43, 27, 35, 0.88),
            (158, 25, 19, 28, 0.76),
        )
        blobs = []
        for x, y, rx, ry, weight in shapes:
            blobs.append(
                Blob(
                    x + rng.uniform(-5.0, 5.0),
                    y + rng.uniform(-3.0, 3.0),
                    rx * rng.uniform(0.88, 1.12),
                    ry * rng.uniform(0.88, 1.12),
                    weight * rng.uniform(0.92, 1.08),
                    rng.uniform(0.0, np.pi * 2.0),
                )
            )
        self.blobs = tuple(blobs)
        yy, xx = np.mgrid[0:FAKE_H, 0:FAKE_W]
        self.xx = xx.astype(np.float32)
        self.yy = yy.astype(np.float32)

    def density(self, tick: int) -> np.ndarray:
        t = tick / 60.0
        mass = np.zeros((FAKE_H, FAKE_W), dtype=np.float32)
        for i, blob in enumerate(self.blobs):
            bx = blob.x + t * (0.55 + (i % 3) * 0.08)
            by = blob.y + np.sin(t * 0.18 + blob.phase) * 1.3
            dx = (self.xx - bx + FAKE_W / 2.0) % FAKE_W - FAKE_W / 2.0
            dy = self.yy - by
            bell = np.exp(
                -0.5 * ((dx / blob.rx) ** 2 + (dy / blob.ry) ** 2)
            ).astype(np.float32)
            mass += bell * blob.weight

        moving_noise = shifted(self.noise, t * 1.7, -t * 0.16)
        # Noise deforms existing mass rather than spawning detached static.
        # Keep density in a useful optical range.  The broad ellipses overlap
        # heavily by design; without this exposure step the whole frame would
        # become one featureless solid cloud.
        return (mass + moving_noise * np.clip(mass * 0.34, 0.0, 0.38)) * 0.45

    def render(self, tick: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        density = self.density(tick)
        coverage = smoothstep(0.58, 1.02, density)

        # Look through the density towards the upper-left light.  A short
        # stack of offset samples is a very cheap optical-depth estimate:
        # exposed lobes go cream, cloud piled between a point and the sun goes
        # plum.  The local gradient adds the bright silver edge.
        sx = 1 if self.light_x < 0 else -1
        optical_depth = (
            np.roll(density, (2, 2 * sx), axis=(0, 1))
            + np.roll(density, (5, 5 * sx), axis=(0, 1))
            + np.roll(density, (9, 9 * sx), axis=(0, 1))
            + np.roll(density, (14, 14 * sx), axis=(0, 1))
        ) * 0.25
        dy, dx = np.gradient(density)
        edge_light = np.clip(-(dx * self.light_x + dy * -0.85) * 1.8, -0.35, 0.8)
        light = 0.90 - optical_depth * 0.30 + edge_light
        light += (1.0 - self.yy / FAKE_H) * 0.10
        shade = np.clip(light, 0.0, 1.0)
        return coverage, shade, density


def quadrant_codes(lit: np.ndarray) -> np.ndarray:
    return (
        lit[0::2, 0::2].astype(np.uint8)
        | lit[0::2, 1::2].astype(np.uint8) << 1
        | lit[1::2, 0::2].astype(np.uint8) << 2
        | lit[1::2, 1::2].astype(np.uint8) << 3
    )


def cell_mean(field: np.ndarray) -> np.ndarray:
    return field.reshape(ROWS, 2, COLS, 2).mean(axis=(1, 3))


def make_palette() -> ConsolePalette:
    palette = ConsolePalette()
    for alpha_index, alpha in enumerate(ALPHAS):
        for shade_index, colour in enumerate(SHADES):
            palette[1 + alpha_index * SHADE_COUNT + shade_index] = (*colour, alpha)
    return palette


def install_quadrants(glyphs: GlyphLayer) -> None:
    """Define byte values 0..15 as the sixteen 2x2 quadrant combinations."""
    height, width = glyphs.glyph[0].shape
    half_h, half_w = height // 2, width // 2
    for code in range(16):
        pattern = np.zeros((height, width), dtype=np.uint8)
        if code & 0b0001:
            pattern[:half_h, :half_w] = 1
        if code & 0b0010:
            pattern[:half_h, half_w:] = 1
        if code & 0b0100:
            pattern[half_h:, :half_w] = 1
        if code & 0b1000:
            pattern[half_h:, half_w:] = 1
        glyphs.glyph[code] = pattern


def make_sky() -> np.ndarray:
    y, x = np.mgrid[0:SCREEN_H, 0:SCREEN_W]
    amount = (y / (SCREEN_H - 1))[:, :, None]
    top = np.array((69, 170, 187), dtype=np.float32)
    bottom = np.array((229, 165, 151), dtype=np.float32)
    sky = top * (1.0 - amount) + bottom * amount

    # A broad, low sun glow—not a drawn disc, just warm air behind the cloud.
    glow = np.exp(-((x - 105.0) ** 2 + (y - 285.0) ** 2) / (2.0 * 150.0**2))
    warm = np.array((255, 222, 155), dtype=np.float32)
    sky = sky * (1.0 - glow[:, :, None] * 0.52) + warm * glow[:, :, None] * 0.52
    return np.clip(sky, 0, 255).astype(np.uint8)


def paint(text: GlyphLayer, clouds: CloudField, tick: int) -> None:
    coverage, shade, _density = clouds.render(tick)
    lit = coverage > BAYER_FIELD
    codes = quadrant_codes(lit)

    cell_coverage = cell_mean(coverage)
    cell_shade = cell_mean(shade)
    alpha_index = np.minimum((cell_coverage * len(ALPHAS)).astype(np.uint8), len(ALPHAS) - 1)
    shade_index = np.minimum((cell_shade * SHADE_COUNT).astype(np.uint8), SHADE_COUNT - 1)
    colours = 1 + alpha_index * SHADE_COUNT + shade_index

    pen = text.pen
    pen.background = 0
    pen[2] = 0
    pen[3] = 0
    for y in range(text.rows):
        for x in range(text.cols):
            pen.foreground = int(colours[y, x])
            text.cell[x, y] = int(codes[y, x])


def main() -> None:
    bps = BlitsPerSecond()
    sky = PixelBuffer(
        ImageSpec(size=(SCREEN_W, SCREEN_H), mode="RGB", data=make_sky())
    )
    sunset = TileEngine(sky, tilewidth=SCREEN_W, tileheight=SCREEN_H)
    sunset.blit(0, 0, 0)
    sunset.tag = "sunset"
    cloud_text = GlyphEngine((8, 8))
    cloud_text.palette = make_palette()
    install_quadrants(cloud_text)
    cloud_text.tag = "clouds"

    clouds = CloudField()
    tick = 0
    capture_scheduled = False

    def capture_frame(_dt: float) -> None:
        """Grab the presented pyglet colour buffer, including the CRT pass."""
        assert CAPTURE is not None
        pyglet.image.get_buffer_manager().get_color_buffer().save(CAPTURE)
        bps.logger.info(f"Captured framebuffer to {CAPTURE}.")

    def loop(engine: BlitsPerSecond) -> None:
        nonlocal tick, capture_scheduled
        tick += 1
        kb = keyboard
        if kb.held(key.LEFT):
            clouds.light_x = max(-1.0, clouds.light_x - 0.025)
        if kb.held(key.RIGHT):
            clouds.light_x = min(1.0, clouds.light_x + 0.025)
        if kb.pressed(key.SPACE):
            clouds.reseed(clouds.seed + 1)

        # The movement is intentionally held for two ticks, like a lavish
        # background effect on hardware that has better things to draw.
        if tick == 1 or tick % 2 == 0:
            paint(cloud_text, clouds, tick)
        # clock.tick() runs after Display.present(), so a zero-delay pyglet
        # callback sees the completed window colour buffer rather than the
        # still-uncomposited game callback state.
        if CAPTURE and not capture_scheduled:
            pyglet.clock.schedule_once(capture_frame, 0.0)
            capture_scheduled = True
        if MAX_TICKS and tick >= MAX_TICKS:
            engine.stop()

    paint(cloud_text, clouds, tick)
    bps.run(loop)


if __name__ == "__main__":
    main()
