"""Private implementation of the public pointer facet."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .mouse_handler import BACK, FORWARD, MIDDLE, PRIMARY, SECONDARY

if TYPE_CHECKING:
    from blitspersecond.common import Location

    from .device import KeyboardMouse
    from .mouse_handler import MouseHandler


class Mouse:
    """The pointer-shaped view of ``KeyboardMouse``."""

    PRIMARY = PRIMARY
    SECONDARY = SECONDARY
    MIDDLE = MIDDLE
    BACK = BACK
    FORWARD = FORWARD

    __slots__ = ("_device",)

    def __init__(self, device: "KeyboardMouse") -> None:
        self._device = device

    def _implementation(self) -> "MouseHandler":
        return self._device._connect()._mouse

    @property
    def position(self) -> "Location":
        return self._implementation().position

    @property
    def delta(self) -> tuple[float, float]:
        return self._implementation().delta

    @property
    def scroll(self) -> float:
        return self._implementation().scroll

    @property
    def inside(self) -> bool:
        return self._implementation().inside

    @property
    def modifiers(self) -> int:
        return self._implementation().modifiers

    @property
    def pointer_visible(self) -> bool:
        return self._implementation().pointer_visible

    @pointer_visible.setter
    def pointer_visible(self, value: bool) -> None:
        self._implementation().pointer_visible = value

    def held(self, button: int) -> bool:
        return self._implementation().held(button)

    def pressed(self, button: int) -> bool:
        return self._implementation().pressed(button)

    def released(self, button: int) -> bool:
        return self._implementation().released(button)

    def held_for(self, button: int) -> int:
        return self._implementation().held_for(button)

    @property
    def held_buttons(self) -> tuple[int, ...]:
        return self._implementation().held_buttons
