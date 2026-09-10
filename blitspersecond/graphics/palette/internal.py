"""Validation: one door per shape of thing a caller may write.

Internal because **you never name any of this.** It is reached by writing
a colour, and its whole job is that a bad one is refused where it was
written rather than resolved into the wrong picture three subsystems away.

Every refusal, by name and with its message: `bps/reference/graphics/palette.md`.
"""

from __future__ import annotations

import numpy as np

from ..color import ColorQuad
from .system import SIZE

_CHANNELS = ("red", "green", "blue", "alpha")


def _byte(value, what: str) -> int:
    """One channel: an int in 0..255, and nothing else.

    Checked here rather than left to numpy, which raises `OverflowError:
    Python integer 300 out of bounds for uint8` -- true, but it names neither
    the channel nor the colour. And a numpy int64 ARRAY out of range does not
    raise at all; it wraps, so 300 becomes 44 in silence. One door means the
    wrap has nowhere to happen.
    """
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise TypeError(f"{what} must be a whole number 0..255; got {value!r}")
    value = int(value)
    if not 0 <= value <= 255:
        raise ValueError(f"{what} is 0..255; got {value}")
    return value


def _quad(value, what: str = "a colour") -> np.ndarray:
    """One colour as uint8 channels: a `ColorQuad`, or `(r, g, b, a)`."""
    if isinstance(value, ColorQuad):
        return value.channels
    if isinstance(value, np.ndarray):
        value = value.tolist()
    if not isinstance(value, (list, tuple)):
        raise TypeError(
            f"{what} must be a ColorQuad or (r, g, b, a); got "
            f"{type(value).__name__}"
        )
    if len(value) not in (3, 4):
        raise ValueError(
            f"{what} is (r, g, b) or (r, g, b, a); got {len(value)} channels"
        )
    channels = list(value) + [255] * (4 - len(value))
    return np.array(
        [_byte(channel, name) for channel, name in zip(channels, _CHANNELS)],
        dtype=np.uint8,
    )


def _palette(value) -> np.ndarray:
    """A whole palette: exactly SIZE colours.

    A `Palette` is handled one level up, in `PaletteBank.__setitem__`, which
    already has the name -- reaching for it here would import the module that
    imports this one.
    """
    if isinstance(value, np.ndarray):
        if value.shape != (SIZE, 4):
            raise ValueError(
                f"a palette is {SIZE} colours of four channels -- expected "
                f"shape ({SIZE}, 4); got {value.shape}"
            )
        if value.dtype != np.uint8:
            if value.min() < 0 or value.max() > 255:
                raise ValueError("a channel is 0..255; this array leaves it")
            value = value.astype(np.uint8)
        return value
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"a palette must be {SIZE} colours; got {type(value).__name__}")
    if len(value) != SIZE:
        raise ValueError(f"a palette is exactly {SIZE} colours; got {len(value)}")
    return np.stack(
        [_quad(colour, f"colour {index}") for index, colour in enumerate(value)]
    )
