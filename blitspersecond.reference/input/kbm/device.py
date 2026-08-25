"""The one local keyboard-and-pointer input device."""

from __future__ import annotations

from typing import TYPE_CHECKING

from blitspersecond.common import SingletonMeta
from blitspersecond.input.internal import InputDevice

from ._keyboard import Keyboard
from .keyboard_handler import KeyboardHandler
from ._mouse import Mouse
from .mouse_handler import MouseHandler

if TYPE_CHECKING:
    from blitspersecond.blitspersecond import BlitsPerSecond


class KeyboardMouse(InputDevice, metaclass=SingletonMeta):
    """The permanent local keyboard-and-pointer device.

    Pyglet's window callbacks expose one keyboard stream and one pointer
    stream without physical-device identity. BPS therefore routes them as one
    device. ``keyboard`` and ``mouse`` are views of that device, not devices
    which can be assigned independently.

    Construction is deliberately engine-free. Engine initialization attaches
    the permanent device after its EventBus exists; a later live query calls
    ``_connect()``, which uses ``BlitsPerSecond()`` as the idempotent "engine
    exists" barrier. A stopped engine may be replaced while this singleton
    remains permanent, so the connection is keyed by engine identity.
    """

    __slots__ = (
        "_engine",
        "_keyboard_handler",
        "_mouse_handler",
        "keyboard",
        "mouse",
    )

    def __init__(self) -> None:
        super().__init__()
        self._engine: BlitsPerSecond | None = None
        self._keyboard_handler: KeyboardHandler | None = None
        self._mouse_handler: MouseHandler | None = None
        self.keyboard = Keyboard(self)
        self.mouse = Mouse(self)

    def _connect(self) -> "KeyboardMouse":
        """Negotiate a connection with the current engine."""
        from blitspersecond import BlitsPerSecond

        return self._attach(BlitsPerSecond())

    def _attach(self, engine: "BlitsPerSecond") -> "KeyboardMouse":
        """Finish engine construction without re-entering its singleton."""
        if self._engine is engine:
            return self

        keyboard = KeyboardHandler(
            engine.events,
            tick_rate=engine.config.display.fps,
        )
        mouse = MouseHandler(engine.events)
        self._engine = engine
        self._keyboard_handler = keyboard
        self._mouse_handler = mouse
        return self

    def _update(self) -> None:
        """Snapshot both facets once at the engine tick boundary."""
        self._connect()
        assert self._keyboard_handler is not None
        assert self._mouse_handler is not None
        self._keyboard_handler.update()
        self._mouse_handler.update()

    @property
    def _keyboard(self) -> KeyboardHandler:
        self._connect()
        assert self._keyboard_handler is not None
        return self._keyboard_handler

    @property
    def _mouse(self) -> MouseHandler:
        self._connect()
        assert self._mouse_handler is not None
        return self._mouse_handler


kbm = KeyboardMouse()
keyboard = kbm.keyboard
mouse = kbm.mouse
