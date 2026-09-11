"""Gamepads: the vocabulary first, the transport and the pad to follow.

    button.py    `Button` -- what a pad has, closed and settled.
    mapper.py    which pad is this, and what does it print on its buttons.

The audit this is being rebuilt from is `docs/bps/PADS_PORT.md`.
"""

from .button import Button
from .mapper import (
    DualSenseMapper,
    NintendoMapper,
    PadMapper,
    SonyMapper,
    XboxMapper,
    mapper_for,
    model_for,
)

__all__ = [
    "Button",
    "DualSenseMapper",
    "NintendoMapper",
    "PadMapper",
    "SonyMapper",
    "XboxMapper",
    "mapper_for",
    "model_for",
]
