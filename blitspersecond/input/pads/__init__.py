"""Gamepads: the vocabulary first, then the transport, then the pad.

    button.py    `Button` -- what a pad has, closed and settled.
    mapper.py    which pad is this, and what does it print on its buttons.
    evdev.py     the Linux transport: discovery, identity, calibration,
                 and reading reports at the cut.

The audit this is being rebuilt from is `docs/bps/PADS_PORT.md`.
"""

from .button import Button
from .evdev import AxisRange, Desync, PadDevice, PadReader, Report, discover, open_pad
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
    "AxisRange",
    "Button",
    "Desync",
    "DualSenseMapper",
    "NintendoMapper",
    "PadDevice",
    "PadMapper",
    "PadReader",
    "Report",
    "SonyMapper",
    "XboxMapper",
    "discover",
    "open_pad",
    "mapper_for",
    "model_for",
]
