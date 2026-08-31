"""Keyboard ingress and immutable, frame-indexed input history.

The window callback path is asynchronous.  The handler deliberately is not:
callbacks append facts to a FIFO and :meth:`KeyboardHandler.capture` settles
them once, before a game tick.  The game therefore never reads input midway
through a frame.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, TYPE_CHECKING

from blitspersecond.common import fps
from blitspersecond.input.buffer import InputBuffer

if TYPE_CHECKING:
    from blitspersecond.internal.events import EventBus


@dataclass(frozen=True, slots=True)
class KeyScan:
    """The physical keyboard truth settled at one frame boundary."""

    held: frozenset[int]
    pressed: frozenset[int]
    released: frozenset[int]


@dataclass(frozen=True, slots=True)
class KeyboardFact:
    """One normalised keyboard fact, in arrival order within an input frame.

    ``held`` is the physical state after this fact.  A recogniser that cares
    about a release's before-state keeps its own tiny state machine, just as
    it does across frames; the engine does not turn those rules into global
    input policy.
    """

    kind: str
    value: object
    modifiers: int
    held: frozenset[int]


@dataclass(frozen=True, slots=True)
class KeyboardFrame:
    """All keyboard truth from the interval immediately before ``frame``."""

    frame: int
    keys: KeyScan
    facts: tuple[KeyboardFact, ...]


class KeyboardHandler:
    """Buffer window keyboard events and settle them into an ``InputBuffer``."""

    def __init__(self, events: EventBus, rate: fps) -> None:
        self._queue: Deque[tuple[str, object, int]] = deque()
        self._history: InputBuffer[KeyboardFrame] = InputBuffer(rate)
        self._held: set[int] = set()
        events.push_handlers(
            on_key_press=self._on_key_press,
            on_key_release=self._on_key_release,
            on_text=self._on_text,
            on_text_motion=self._on_text_motion,
            on_deactivate=self._on_deactivate,
        )

    @property
    def history(self) -> InputBuffer[KeyboardFrame]:
        """The bounded, immutable record; readers never drain it."""
        return self._history

    def _on_key_press(self, symbol: int, modifiers: int) -> None:
        self._queue.append(("press", symbol, modifiers))

    def _on_key_release(self, symbol: int, modifiers: int) -> None:
        self._queue.append(("release", symbol, modifiers))

    def _on_text(self, text: str) -> None:
        self._queue.append(("text", text, 0))

    def _on_text_motion(self, motion: int) -> None:
        self._queue.append(("motion", motion, 0))

    def _on_deactivate(self) -> None:
        self._queue.append(("deactivate", None, 0))

    def capture(self, frame: int) -> KeyboardFrame:
        """Settle queued facts before game frame ``frame`` starts.

        The resulting record is numbered ``frame - 1`` because it describes
        the just-ended input interval.  Nothing mutates until the next call.
        """
        if not isinstance(frame, int) or isinstance(frame, bool):
            raise TypeError("frame must be an integer")

        pressed: set[int] = set()
        released: set[int] = set()
        facts: list[KeyboardFact] = []

        def publish(kind: str, value: object, modifiers: int = 0) -> None:
            facts.append(
                KeyboardFact(kind, value, modifiers, frozenset(self._held))
            )

        while self._queue:
            kind, value, modifiers = self._queue.popleft()
            if kind == "press":
                assert isinstance(value, int)
                if value in self._held:
                    publish("repeat", value, modifiers)
                else:
                    self._held.add(value)
                    pressed.add(value)
                    publish("press", value, modifiers)
            elif kind == "release":
                assert isinstance(value, int)
                if value in self._held:
                    self._held.remove(value)
                    released.add(value)
                publish("release", value, modifiers)
            elif kind == "text":
                assert isinstance(value, str)
                for character in value:
                    publish("text", character)
            elif kind == "motion":
                publish("motion", value)
            else:
                assert kind == "deactivate"
                for symbol in sorted(self._held):
                    self._held.remove(symbol)
                    released.add(symbol)
                    publish("release", symbol)
                publish("deactivate", None)

        settled = KeyboardFrame(
            frame=frame - 1,
            keys=KeyScan(
                held=frozenset(self._held),
                pressed=frozenset(pressed),
                released=frozenset(released),
            ),
            facts=tuple(facts),
        )
        self._history.append(settled)
        return settled
