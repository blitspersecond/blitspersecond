"""Gamepads: the vocabulary first, then the transport, then the pad.

    button.py    `Button` -- what a pad has, closed and settled.
    mapper.py    which pad is this, and what does it print on its buttons.
    control.py   sticks, triggers and the d-pad, named for the control.
    pad.py       one pad's settled interval: a scan, and ordered facts.
    pads.py      a pad that persists, and the collection of them.
    evdev.py     the Linux transport: discovery, identity, calibration,
                 and reading reports at the cut.

The audit this is being rebuilt from is `docs/bps/PADS_PORT.md`.
"""

from .button import Button
from .control import Axis, Direction, direction
from .pads import ACTIVATION, REFRESH_FRAMES, Pad, Pads
from .pad import (
    NEUTRAL_AXES,
    TRIGGER_ON,
    PadEvent,
    PadFact,
    PadIngress,
    PadScan,
    PadWindow,
)
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
    "NEUTRAL_AXES",
    "DualSenseMapper",
    "NintendoMapper",
    "PadDevice",
    "ACTIVATION",
    "REFRESH_FRAMES",
    "Pad",
    "PadEvent",
    "PadFact",
    "PadIngress",
    "Pads",
    "PadMapper",
    "PadReader",
    "PadScan",
    "PadWindow",
    "Report",
    "Snapshot",
    "SonyMapper",
    "TRIGGER_ON",
    "XboxMapper",
    "direction",
    "discover",
    "open_pad",
    "mapper_for",
    "model_for",
]
