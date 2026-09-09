"""The modify path: the arithmetic behind a palette's operators.

Free functions over `(SIZE, 3)` uint8 RGB, so the maths is testable without a
bank and the methods on `Palette` stay thin -- validate, call, write back,
flag dirty. **Alpha never appears here**: it is decided by the index, so the
hole survives every operator without any of them knowing about it.

No `Operator` base class: arity is what the signature says. Costs, and why
`duotone` and `blend` are two operators rather than one: `bps/reference/palette.md`.
"""

from __future__ import annotations

import numpy as np

#: Rec. 601 luma: the weights a CRT-era palette was authored against.
_LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)


def blend(rgb: np.ndarray, other: np.ndarray, strength: float) -> np.ndarray:
    """`rgb` moved `strength` of the way toward `other`, entry by entry.

    Index 7 moves toward index 7, so a ramp blended toward a ramp stays a
    ramp -- which is what makes this different from `duotone`, where the
    target is one colour and the ORDERING has to be defended.
    """
    mine = rgb.astype(np.float32)
    theirs = other.astype(np.float32)
    return _bytes(mine + (theirs - mine) * strength)


def duotone(rgb: np.ndarray, colour: np.ndarray, mix: float) -> np.ndarray:
    """Every colour `mix` of the way into a duotone of `colour`.

    Each entry keeps its own luminance and takes `colour`'s hue, so the art's
    light and shade survive and only its variety collapses. **This is not
    `blend` toward a flat table** -- a lerp drags dark pixels UP toward a
    bright signature, and the ramp stops being monotonic.

    `colour` is normalised by its own brightest channel, so a dim signature
    tints rather than darkens; darkening is `darken`'s job.
    """
    source = rgb.astype(np.float32)
    target = colour.astype(np.float32)
    target = target / max(float(target.max()), 1.0)
    shade = (source @ _LUMA)[:, None] * target
    return _bytes(source + (shade - source) * mix)


def darken(rgb: np.ndarray, amount: float) -> np.ndarray:
    """Every channel scaled by `1 - amount`. `0` leaves it, `1` is black."""
    return _bytes(rgb.astype(np.float32) * (1.0 - amount))


def cycle(rgb: np.ndarray, start: int, stop: int, by: int) -> np.ndarray:
    """Rotate the colours in `[start, stop)` by `by` places, wrapping.

    The palette-cycling primitive: the art does not change and no pixel moves,
    only which colour each index resolves to. It takes a range because a room
    cycles a handful of indices while every other colour holds still.
    """
    out = rgb.copy()
    out[start:stop] = np.roll(rgb[start:stop], by, axis=0)
    return out


def negate(rgb: np.ndarray) -> np.ndarray:
    """Every channel inverted. A flash, and its own undo."""
    return 255 - rgb


def multiply(rgb: np.ndarray, other: np.ndarray) -> np.ndarray:
    """Channel-wise product, normalised -- the darkening blend mode. White
    leaves a colour alone; black takes it to black."""
    return _bytes(rgb.astype(np.float32) * other.astype(np.float32) / 255.0)


def _bytes(values: np.ndarray) -> np.ndarray:
    """Back to uint8, clipped rather than wrapped -- an operator that
    overshot must land on white, not on a colour nobody asked for."""
    return values.clip(0, 255).astype(np.uint8)
