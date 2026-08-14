"""The one local keyboard-and-pointer device and its two honest facets."""

from .device import KeyboardMouse, kbm, keyboard, mouse
from .input import (
    COMPLETED,
    DISCARDED,
    INTERRUPTED,
    LISTENING,
    InputState,
    Predicate,
    elapsed,
    key,
)
from ._keyboard import Keyboard
from ._mouse import Mouse
from .stream import KeyboardEvent, KeyboardStream

__all__ = [
    "COMPLETED",
    "DISCARDED",
    "INTERRUPTED",
    "LISTENING",
    "InputState",
    "Keyboard",
    "KeyboardEvent",
    "KeyboardMouse",
    "KeyboardStream",
    "Mouse",
    "Predicate",
    "elapsed",
    "kbm",
    "key",
    "keyboard",
    "mouse",
]
