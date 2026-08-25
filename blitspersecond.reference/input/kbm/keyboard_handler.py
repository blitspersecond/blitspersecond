"""Tick-aligned physical keyboard state and an ordered event stream.

Raw window callbacks only enqueue. update() drains them once per simulation
tick, preserving physical pressed/held/released truth and appending every
physical/text event to a replayable journal. The handler creates and drives
one active Input; editing and replay semantics remain on that value object.
"""

from __future__ import annotations

from collections import deque
from typing import Callable, Optional, TYPE_CHECKING

from .stream import KeyboardEvent, KeyboardStream

if TYPE_CHECKING:
    from .input import Input, Predicate
    from blitspersecond.system.events import EventBus


class KeyboardHandler:
    """One coherent physical keyboard per tick, plus its semantic event log.

    Physical queries:

        kb.held(key.LEFT)
        kb.pressed(key.SPACE)
        kb.released(key.SPACE)

    Text and editing motions are retained in kb.stream. KeyboardHandler creates
    and drives one non-blocking Input while remaining ignorant of editable-line
    reduction and presentation.
    """

    def __init__(
        self,
        events: "EventBus",
        tick_rate: int = 60,
    ) -> None:
        if not isinstance(tick_rate, int) or isinstance(tick_rate, bool):
            raise TypeError("tick_rate must be an integer")
        if tick_rate <= 0:
            raise ValueError("tick_rate must be positive")
        self._tick_rate = tick_rate
        self._queue: deque[tuple[str, object, int]] = deque()
        self._stream = KeyboardStream()
        self._input: Optional["Input"] = None
        self._held: set[int] = set()
        self._pressed: set[int] = set()
        self._released: set[int] = set()
        self._press_transitions: list[tuple[int, frozenset[int]]] = []
        self._release_transitions: list[tuple[int, frozenset[int]]] = []
        self._modifiers = 0
        self._ticks = 0
        events.push_handlers(
            on_key_press=self._on_key_press,
            on_key_release=self._on_key_release,
            on_text=self._on_text,
            on_text_motion=self._on_text_motion,
            on_deactivate=self._on_deactivate,
        )

    # -- bus observers: buffer in arrival order, consume nothing ------------

    def _on_key_press(self, symbol: int, modifiers: int) -> None:
        self._queue.append(("press", symbol, modifiers))

    def _on_key_release(self, symbol: int, modifiers: int) -> None:
        self._queue.append(("release", symbol, modifiers))

    def _on_text(self, text: str) -> None:
        self._queue.append(("text", text, 0))

    def _on_text_motion(self, motion: int) -> None:
        self._queue.append(("motion", motion, 0))

    def _on_deactivate(self) -> None:
        self._queue.append(("clear", 0, 0))

    # -- tick snapshot and stream append ------------------------------------

    def update(self) -> None:
        """Drain buffered events into one coherent tick snapshot."""
        self._ticks += 1
        self._pressed.clear()
        self._released.clear()
        self._press_transitions.clear()
        self._release_transitions.clear()

        while self._queue:
            kind, value, modifiers = self._queue.popleft()
            if kind == "press":
                assert isinstance(value, int)
                self._modifiers = modifiers
                if value not in self._held:
                    self._held.add(value)
                    self._pressed.add(value)
                    self._press_transitions.append(
                        (value, frozenset(self._held))
                    )
                    self._publish(kind, value, modifiers)
                else:
                    # Preserve raw chatter without turning an autorepeat into
                    # the fresh edge matched by a bare key predicate.
                    self._publish("repeat", value, modifiers)

            elif kind == "release":
                assert isinstance(value, int)
                self._modifiers = modifiers
                before = frozenset(self._held)
                if value in self._held:
                    self._held.remove(value)
                    self._released.add(value)
                    self._release_transitions.append((value, before))
                self._publish(kind, value, modifiers)

            elif kind == "text":
                assert isinstance(value, str)
                # Preserve order inside a backend's batched callback. In
                # particular, "name\r" must append the name before Return
                # completes the active Input.
                for character in value:
                    self._publish("text", character)

            elif kind == "motion":
                self._publish(kind, value)

            elif kind == "clear":
                self._released.update(self._held)
                before = set(self._held)
                for symbol in tuple(self._held):
                    self._release_transitions.append(
                        (symbol, frozenset(before))
                    )
                    before.remove(symbol)
                self._held.clear()
                self._publish(kind, value)

        entry = self._input
        if entry is not None:
            match = entry._advance(self._ticks)
            if match is not None:
                self._complete_input(
                    entry, match, self._stream.position
                )
            elif not entry.listening and self._input is entry:
                self._input = None
        self._stream.compact()

    def _publish(
        self, kind: str, value: object, modifiers: int = 0
    ) -> KeyboardEvent:
        event = self._stream.append(
            self._ticks,
            kind,
            value,
            modifiers,
        )
        entry = self._input
        if entry is not None:
            match = entry._receive(event)
            if match is not None:
                self._complete_input(entry, match, event.position + 1)
            elif not entry.listening and self._input is entry:
                self._input = None
        return event

    # -- physical queries ---------------------------------------------------

    def held(self, symbol: int) -> bool:
        return symbol in self._held

    def pressed(self, symbol: int) -> bool:
        return symbol in self._pressed

    def released(self, symbol: int) -> bool:
        return symbol in self._released

    @property
    def _held_snapshot(self) -> frozenset[int]:
        return frozenset(self._held)

    # -- replay stream and its one active Input ------------------------------

    @property
    def stream(self) -> KeyboardStream:
        return self._stream

    def _begin_input(
        self,
        value: str = "",
        accepts: Optional[Callable[[str], bool]] = None,
    ) -> "Input":
        from .input import Input

        previous = self._input
        boundary = self._stream.position
        entry = Input(
            self,
            value,
            start=boundary,
            started_tick=self._ticks,
            tick_rate=self._tick_rate,
            accepts=accepts,
        )
        self._input = entry
        if previous is not None:
            previous._interrupt(boundary, "REPLACED")
        return entry

    def _complete_input(
        self, entry: "Input", finalizer: "Predicate", stop: int
    ) -> None:
        entry._complete(finalizer, stop)
        if self._input is entry:
            self._input = None
