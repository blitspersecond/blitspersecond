"""The button vocabulary and the raw evdev codes behind it -- the leaf the
whole package stands on.

This module exists to break a cycle, and the cycle was telling us something:
`Button` is nobody's property in particular. A `Pad` produces Button edges; a
mapper decides which physical code means which Button and what the vendor
prints on it; game code reads Button. So it sits underneath all of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import ClassVar, TYPE_CHECKING, TypeAlias


class Button(Enum):
    """The normalised pad-button vocabulary (physical positions, Xbox layout).

    A game maps these to its own actions; a pad doesn't know 'punch'. Names are
    POSITIONS, never letters, because the letters move between vendors and the
    positions never do: FACE_SOUTH is the bottom button on every pad ever made
    -- Xbox prints an A on it, Sony a cross, Nintendo a B. (Ask for the printed
    label with `pad.label(button)`; see mappers.PadMapper.)
    """

    FACE_SOUTH = auto()
    FACE_EAST = auto()
    FACE_WEST = auto()
    FACE_NORTH = auto()
    LB = auto()
    RB = auto()
    LT = auto()  # left trigger, thresholded to a button
    RT = auto()  # right trigger, thresholded to a button
    START = auto()
    SELECT = auto()
    if TYPE_CHECKING:
        ACCEPT: ClassVar["ButtonPredicate"]
    # NB: no L3/R3. The stick-clicks are deliberately absent from the
    # vocabulary, not an oversight: they are the one input class that is "only
    # triggered at inopportune moments and never by player agency" -- you push
    # the stick you are already steering with, and it clicks. A game cannot
    # bind what it cannot name. Their codes go unmapped, same as a touchpad's.
    # See docs/CONTROLLERS.md, "The idealised pad".

    def __or__(self, other: "ButtonCondition") -> "ButtonPredicate":
        return ButtonPredicate._combine(self, other)


@dataclass(frozen=True)
class ButtonPredicate:
    """One-of-these button condition, composed with ``|``."""

    buttons: tuple[Button, ...]
    conventional_accept: bool = False

    @classmethod
    def _combine(
        cls,
        left: "ButtonCondition",
        right: "ButtonCondition",
    ) -> "ButtonPredicate":
        parts: list[Button] = []
        conventional_accept = False
        for condition in (left, right):
            if isinstance(condition, Button):
                candidates = (condition,)
            elif isinstance(condition, ButtonPredicate):
                candidates = condition.buttons
                conventional_accept |= condition.conventional_accept
            else:
                return NotImplemented
            for button in candidates:
                if button not in parts:
                    parts.append(button)
        return cls(tuple(parts), conventional_accept)

    def __or__(self, other: "ButtonCondition") -> "ButtonPredicate":
        return self._combine(self, other)

    def __repr__(self) -> str:
        parts = [f"Button.{button.name}" for button in self.buttons]
        if self.conventional_accept:
            parts.append("Button.ACCEPT")
        return " | ".join(parts)


ButtonCondition: TypeAlias = Button | ButtonPredicate
Button.ACCEPT = ButtonPredicate((), conventional_accept=True)


# -- evdev codes (Linux input-event-codes.h) ---------------------------------

EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03

SYN_REPORT = 0
SYN_DROPPED = 3

# NB: 0x133/0x134 are the kernel's BTN_X/BTN_Y. We name them by PHYSICAL
# POSITION -- the kernel's own BTN_NORTH/BTN_WEST aliases are Nintendo-
# positioned and mismatch the Xbox layout physically.
BTN_SOUTH, BTN_EAST, BTN_WEST, BTN_NORTH = 0x130, 0x131, 0x133, 0x134
BTN_TL, BTN_TR = 0x136, 0x137
BTN_SELECT, BTN_START = 0x13A, 0x13B
BTN_THUMBL, BTN_THUMBR = 0x13D, 0x13E  # the stick-clicks: named, never mapped

ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ = 0x00, 0x01, 0x02, 0x03, 0x04, 0x05
ABS_HAT0X, ABS_HAT0Y = 0x10, 0x11
