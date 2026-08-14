"""Private implementation of the public keyboard facet."""

from __future__ import annotations

from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .device import KeyboardMouse
    from .keyboard_handler import KeyboardHandler
    from .input import Input, Predicate
    from .stream import KeyboardStream


class Keyboard:
    """The one window keyboard, updated by the engine at tick boundaries.

    This object is the keyboard-shaped view of ``KeyboardMouse``. It is not an
    independently routable device: the keyboard and pointer always move
    together, while retaining the APIs that honestly describe each half.
    """

    __slots__ = ("_device",)

    def __init__(self, device: "KeyboardMouse") -> None:
        self._device = device

    def _implementation(self) -> "KeyboardHandler":
        return self._device._connect()._keyboard

    def held(self, condition: "Predicate") -> bool:
        from .input import _key_down, _key_symbols

        handler = self._implementation()
        _key_symbols(condition)
        return _key_down(condition, handler._held_snapshot)

    def pressed(self, condition: "Predicate") -> bool:
        from .input import _key_pressed

        return _key_pressed(
            condition, self._implementation()._press_transitions
        )

    def released(self, condition: "Predicate") -> bool:
        from .input import _key_released

        return _key_released(
            condition, self._implementation()._release_transitions
        )

    def input(
        self,
        value: str = "",
        *,
        accepts: "Callable[[str], bool] | None" = None,
    ) -> "Input":
        """Create the keyboard's one active, non-blocking text input."""
        return self._implementation()._begin_input(value, accepts)

    # Input uses the stream only to replay its keyboard-owned bounded range.

    @property
    def stream(self) -> "KeyboardStream":
        return self._implementation().stream
