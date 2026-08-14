"""Input machinery used to build devices, not ordinary game vocabulary.

Games interact with concrete ``KeyboardMouse`` and ``Pad`` objects and map
them through ``Port``. ``InputDevice`` exists only to give those implementations
one unique mapping slot and a common runtime identity.
"""

from .device import InputDevice

__all__ = ["InputDevice"]
