"""Gamepads: the vocabulary first, then the transport, then the pad.

    button.py    `Button` -- what a pad has, closed and settled.
    mapper.py    which pad is this, and what does it print on its buttons.
    evdev.py     the Linux transport. Discovery and identity so far.

The audit this is being rebuilt from is `docs/bps/PADS_PORT.md`.
"""

from .button import Button
from .evdev import PadDevice, discover
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
    "PadDevice",
    "PadMapper",
    "SonyMapper",
    "XboxMapper",
    "discover",
    "mapper_for",
    "model_for",
]
