"""The local keyboard and mouse, as delivered by the window."""

from .keys import Key, Modifier, Motion
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
    MouseButton,
    MouseEvent,
    MouseFact,
    MouseIngress,
    MouseScan,
    MouseWindow,
    screen_location,
)
from .text import TextInput

__all__ = [
    "BACK",
    "Key",
    "Modifier",
    "Motion",
    "BUTTONS",
    "FORWARD",
    "KeyEvent",
    "KeyFact",
    "KeyScan",
    "KeyboardIngress",
    "KeyboardWindow",
    "MIDDLE",
    "MouseButton",
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
