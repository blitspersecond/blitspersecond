"""Gamepads: the vocabulary first, then the transport, then the pad.

    button.py    `Button` -- what a pad has, closed and settled.
    mapper.py    which pad is this, and what does it print on its buttons.
    control.py   sticks, triggers and the d-pad, named for the control.
    evdev.py     the Linux transport: discovery, identity, calibration,
                 and reading reports at the cut.

The audit this is being rebuilt from is `docs/bps/PADS_PORT.md`.
"""

from .button import Button
from .control import Axis, Direction, direction
from .evdev import AxisRange, PadDevice, PadReader, Report, Snapshot, discover, open_pad
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
    "Axis",
    "AxisRange",
    "Button",
    "Direction",
    "DualSenseMapper",
    "NintendoMapper",
    "PadDevice",
    "PadMapper",
    "PadReader",
    "Report",
    "Snapshot",
    "SonyMapper",
    "XboxMapper",
    "direction",
    "discover",
    "open_pad",
    "mapper_for",
    "model_for",
]
