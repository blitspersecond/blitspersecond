"""The local keyboard and mouse, as delivered by the window."""

from .keyboard import (
    KeyboardIngress,
    KeyboardWindow,
    KeyEvent,
    KeyFact,
    KeyScan,
)
from .mouse import (
    BACK,
    BUTTONS,
    FORWARD,
    MIDDLE,
    PRIMARY,
    SECONDARY,
    MouseEvent,
    MouseFact,
    MouseIngress,
    MouseScan,
    MouseWindow,
    screen_location,
)
from .text import InputState, TextInput

__all__ = [
    "BACK",
    "BUTTONS",
    "FORWARD",
    "InputState",
    "KeyEvent",
    "KeyFact",
    "KeyScan",
    "KeyboardIngress",
    "KeyboardWindow",
    "MIDDLE",
    "MouseEvent",
    "MouseFact",
    "MouseIngress",
    "MouseScan",
    "MouseWindow",
    "PRIMARY",
    "SECONDARY",
    "TextInput",
    "screen_location",
]
