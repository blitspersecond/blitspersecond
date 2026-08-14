"""One physical gamepad connection and its state on the current tick.

A Pad owns its physical identity, mapper, button edges, held state and axes.
Pads owns discovery and feeds raw events into it at the tick boundary.

When the device vanishes, held buttons force-release for that tick and
``connected`` becomes false. The Pad remains as a zombie proxy so a Port can
keep referring to it; reconnection revives the same object. Virtual Port
mapping remains separate.
"""

from __future__ import annotations

from math import hypot
from typing import NamedTuple, Optional

from blitspersecond.input.internal import InputDevice

from .codes import (
    ABS_HAT0X,
    ABS_HAT0Y,
    ABS_RX,
    ABS_RY,
    ABS_X,
    ABS_Y,
    EV_ABS,
    EV_KEY,
    Button,
    ButtonCondition,
    ButtonPredicate,
)
from .mappers import PadMapper

_XINPUT_STICK_MAX = 32767
_LEFT_THUMB_DEADZONE = 7849 / _XINPUT_STICK_MAX
_RIGHT_THUMB_DEADZONE = 8689 / _XINPUT_STICK_MAX
_ACTIVATION = 2 / 3  # a deliberate shove, not centre noise, identifies a Pad
# Triggers arrive as an axis (ABS_Z/ABS_RZ), rest at 0. Many fightpads (the
# Hori among them) wire a face button to a trigger and report it DIGITALLY
# (0 <-> full), so a single threshold turns the axis into clean button edges;
# a genuinely-analog trigger just becomes a slightly light button. (No
# hysteresis yet -- digital doesn't need it; revisit if an analog trigger held
# near the line chatters.)
_TRIGGER_ON = 30 / 255  # XINPUT_GAMEPAD_TRIGGER_THRESHOLD


class _AxisRange(NamedTuple):
    """The useful calibration facts from Linux ``input_absinfo``."""

    minimum: int
    maximum: int


class Pad(InputDevice):
    """A gamepad that is (or was) plugged in, read one tick at a time."""

    def __init__(
        self,
        fingerprint: str,
        *,
        name: str,
        mapper: type[PadMapper],
        model: Optional[str] = None,
        axis_ranges: Optional[dict[int, _AxisRange]] = None,
    ) -> None:
        super().__init__()
        self._fingerprint = fingerprint
        self._kernel_name = name
        self._model = model
        self._mapper = mapper
        self._connected = True
        self._id: Optional[int] = None
        self._ticks = 0
        self._held: dict[Button, int] = {}  # -> the tick it went down
        self._pressed: set[Button] = set()
        self._released: set[Button] = set()
        self._axes: dict[int, int] = {}  # raw evdev axis code -> raw value
        self._axis_ranges = axis_ranges or {}

    # -- identity ------------------------------------------------------------

    @property
    def fingerprint(self) -> str:
        """This physical pad's stable transport identity.

        Pads uses it to revive this same Pad when the controller reconnects.
        """
        return self._fingerprint

    @property
    def name(self) -> str:
        """The name a person would use ('DualSense', 'Hori Fighting Commander
        (Xbox)') when the mapper table knows this pad, else the transport's raw
        string. For HUDs. Identity is `fingerprint`."""
        return self._model or self._kernel_name

    @property
    def kernel_name(self) -> str:
        """The raw transport device string, unprettified.

        The historical property name is retained across native backends.
        """
        return self._kernel_name

    @property
    def mapper(self) -> type[PadMapper]:
        """The named mapper chosen for this pad at discovery -- XboxMapper for
        anything the table doesn't recognise, which is the duck test working
        rather than the duck test failing."""
        return self._mapper

    @property
    def connected(self) -> bool:
        """False once this connection vanishes.

        Held state and axes become inert immediately; release edges remain
        readable for the disconnect tick.
        """
        return self._connected

    @property
    def id(self) -> Optional[int]:
        """This pad's stable Pads index, or None before its first input."""
        return self._id

    def _identify(self, id: int) -> None:
        if self._id is not None and self._id != id:
            raise ValueError("Pad already has a different id")
        self._id = id

    def label(self, condition: ButtonCondition) -> str:
        """What THIS pad prints on `button` -- 'A' on an Xbox pad, 'cross' on a
        Sony one, 'B' on a Nintendo one, all for FACE_SOUTH. So a HUD can say
        "Press cross" instead of "Press FACE_SOUTH".

        ACCEPT resolves only the mapper family's conventional confirm position;
        a game or profile can always ask for the physical FACE position."""
        buttons = self._buttons(condition)
        if len(buttons) != 1:
            raise ValueError("label() needs exactly one button")
        return self._mapper.label(buttons[0])

    # -- queries -------------------------------------------------------------

    def held(self, condition: ButtonCondition) -> bool:
        """Is any selected button down right now?"""
        return any(button in self._held for button in self._buttons(condition))

    def pressed(self, condition: ButtonCondition) -> bool:
        """Did any selected button go down on this tick? (An edge.)"""
        return any(button in self._pressed for button in self._buttons(condition))

    def released(self, condition: ButtonCondition) -> bool:
        """Did any selected button come up on this tick? (An edge -- including the
        force-release when the pad disconnects mid-hold.)"""
        return any(button in self._released for button in self._buttons(condition))

    def held_for(self, condition: ButtonCondition) -> int:
        """Ticks this button has been down; 0 on the press tick. Ticks, never
        wall time -- the engine's only notion of time is the fixed 1/60 step."""
        buttons = self._buttons(condition)
        if len(buttons) != 1:
            raise ValueError("held_for() needs exactly one button")
        button = buttons[0]
        start = self._held.get(button)
        return 0 if start is None else self._ticks - start

    def _buttons(self, condition: ButtonCondition) -> tuple[Button, ...]:
        if isinstance(condition, Button):
            selected = (condition,)
        elif isinstance(condition, ButtonPredicate):
            selected = condition.buttons
        else:
            raise TypeError("expected a Button predicate")
        physical: list[Button] = []
        for button in selected:
            if button not in physical:
                physical.append(button)
        if (
            isinstance(condition, ButtonPredicate)
            and condition.conventional_accept
            and self._mapper.ACCEPT not in physical
        ):
            physical.append(self._mapper.ACCEPT)
        return tuple(physical)

    def axis(self, code: int) -> float:
        """A stick/analog axis, normalised to -1..1 (raw for d-pad hats)."""
        if code in (ABS_HAT0X, ABS_HAT0Y):
            return float(self._axes.get(code, 0))
        if code not in self._axes:
            return 0.0
        value = self._axes[code]
        axis_range = self._axis_ranges.get(
            code, _AxisRange(-32768, 32767)
        )
        minimum, maximum = axis_range
        # The integer midpoint is 0 for -32768..32767 and 128 for 0..255.
        # Using a floating midpoint would make both common neutral values
        # slightly non-zero.
        centre = (minimum + maximum + 1) // 2
        extent = centre - minimum if value < centre else maximum - centre
        if extent <= 0:
            return 0.0
        return max(-1.0, min(1.0, (value - centre) / extent))

    def left_stick(self) -> tuple[float, float]:
        """The left stick as a deadzoned (x, y) vector. y is up-negative."""
        return self._stick(ABS_X, ABS_Y, _LEFT_THUMB_DEADZONE)

    def right_stick(self) -> tuple[float, float]:
        """The right stick as a deadzoned (x, y) vector. y is up-negative."""
        return self._stick(ABS_RX, ABS_RY, _RIGHT_THUMB_DEADZONE)

    def _stick(
        self, x_code: int, y_code: int, deadzone: float
    ) -> tuple[float, float]:
        x, y = self.axis(x_code), self.axis(y_code)
        magnitude = hypot(x, y)
        if magnitude <= deadzone:
            return (0.0, 0.0)

        direction_x, direction_y = x / magnitude, y / magnitude
        magnitude = min(magnitude, 1.0)
        magnitude = (magnitude - deadzone) / (1.0 - deadzone)
        return (direction_x * magnitude, direction_y * magnitude)

    def dpad(self) -> tuple[int, int]:
        """(x, y), each in {-1, 0, 1} -- the d-pad hat. y is up-negative."""
        return (self._axes.get(ABS_HAT0X, 0), self._axes.get(ABS_HAT0Y, 0))

    def direction(self) -> int:
        """Numpad direction 1-9 from the d-pad, falling back to the left stick.
        Neutral is 5. (evdev y is up-negative; numpad up is 8.)"""
        dx, dy = self.dpad()
        if dx == 0 and dy == 0:  # d-pad neutral: consult the stick
            ax = self.axis(ABS_X)
            ay = self.axis(ABS_Y)
            dx = (
                -1
                if ax < -_LEFT_THUMB_DEADZONE
                else 1 if ax > _LEFT_THUMB_DEADZONE else 0
            )
            dy = (
                -1
                if ay < -_LEFT_THUMB_DEADZONE
                else 1 if ay > _LEFT_THUMB_DEADZONE else 0
            )
        row = 8 if dy < 0 else 2 if dy > 0 else 5
        if dx < 0:
            return {8: 7, 5: 4, 2: 1}[row]
        if dx > 0:
            return {8: 9, 5: 6, 2: 3}[row]
        return row

    # -- driven by the handler (the bus) -------------------------------------

    def _begin_tick(self, tick: int) -> None:
        """New tick: the edges from last tick are over, the holds are not."""
        self._ticks = tick
        self._pressed.clear()
        self._released.clear()

    def _feed(
        self, etype: int, code: int, value: int
    ) -> tuple[bool, Optional[Button]]:
        """Read one raw event and report deliberate activity plus a press edge."""
        if etype == EV_KEY:
            button = self._mapper.BUTTON_MAP.get(code)
            if button is None:
                return False, None
            if value == 1:  # 2 == autorepeat, which is not an edge
                return True, button if self._press(button) else None
            elif value == 0:
                self._release(button)
            return False, None
        elif etype == EV_ABS:
            self._axes[code] = value
            trigger = self._mapper.TRIGGER_MAP.get(code)
            if trigger is not None:  # threshold the axis into button edges
                if self._trigger(code) > _TRIGGER_ON:
                    return True, trigger if self._press(trigger) else None
                else:
                    self._release(trigger)
            elif code in (ABS_HAT0X, ABS_HAT0Y):
                return value != 0, None
            elif code in (ABS_X, ABS_Y, ABS_RX, ABS_RY):
                return abs(self.axis(code)) > _ACTIVATION, None
        return False, None

    def _trigger(self, code: int) -> float:
        """A trigger's travel normalised to 0..1."""
        if code not in self._axes:
            return 0.0
        value = self._axes[code]
        axis_range = self._axis_ranges.get(code, _AxisRange(0, 255))
        extent = axis_range.maximum - axis_range.minimum
        if extent <= 0:
            return 0.0
        return max(
            0.0,
            min(1.0, (value - axis_range.minimum) / extent),
        )

    def _press(self, button: Button) -> bool:
        if button not in self._held:
            self._held[button] = self._ticks
            self._pressed.add(button)
            return True
        return False

    def _release(self, button: Button) -> None:
        if self._held.pop(button, None) is not None:
            self._released.add(button)

    def _disconnect(self) -> None:
        """The device vanished. Force-release everything held, as real edges on
        this tick -- a dead battery mid-hold must read as a release, never as a
        stuck button -- then go inert."""
        for button in tuple(self._held):
            self._release(button)
        self._axes.clear()
        self._connected = False

    def _connect(
        self,
        *,
        name: str,
        mapper: type[PadMapper],
        model: Optional[str],
        axis_ranges: Optional[dict[int, _AxisRange]],
    ) -> None:
        """Revive this permanent proxy for its reconnected physical device."""
        self._kernel_name = name
        self._model = model
        self._mapper = mapper
        self._axis_ranges = axis_ranges or {}
        self._axes.clear()
        self._connected = True

    def __repr__(self) -> str:
        state = "connected" if self._connected else "disconnected"
        return f"<Pad {self.name!r} {self._mapper.__name__} {state}>"
