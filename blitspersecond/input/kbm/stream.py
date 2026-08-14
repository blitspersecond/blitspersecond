"""The keyboard's ordered, tick-stamped event journal.

KeyboardHandler writes physical and semantic text events here. Input objects
hold positions into the journal and replay their bounded range; the handler
does not interpret that range as text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator
from weakref import WeakSet


@dataclass(frozen=True)
class KeyboardEvent:
    """One keyboard event at a stable stream position."""

    position: int
    tick: int
    kind: str
    value: object
    modifiers: int = 0


class _StreamPin:
    def __init__(self, stream: "KeyboardStream", start: int) -> None:
        self.stream = stream
        self.start = start
        self.active = True

    def release(self) -> None:
        if self.active:
            self.active = False
            self.stream.compact()


class KeyboardStream:
    """Append-only keyboard events addressed by positions between events."""

    def __init__(self) -> None:
        self._events: list[KeyboardEvent] = []
        self._base = 0
        self._next = 0
        self._pins: WeakSet[_StreamPin] = WeakSet()

    @property
    def position(self) -> int:
        """The current tail position, suitable as a capture boundary."""
        return self._next

    def append(
        self,
        tick: int,
        kind: str,
        value: object,
        modifiers: int = 0,
    ) -> KeyboardEvent:
        event = KeyboardEvent(
            position=self._next,
            tick=tick,
            kind=kind,
            value=value,
            modifiers=modifiers,
        )
        self._events.append(event)
        self._next += 1
        return event

    def replay(self, start: int, stop: int | None = None) -> Iterator[KeyboardEvent]:
        """Iterate a stable half-open stream range."""
        end = self._next if stop is None else stop
        if start < self._base or end > self._next or end < start:
            raise ValueError("keyboard stream range is no longer available")
        yield from self._events[start - self._base : end - self._base]

    def pin(self, start: int) -> _StreamPin:
        """Retain events from start while a replaying Input remains alive."""
        if start < self._base or start > self._next:
            raise ValueError("cannot pin an unavailable stream position")
        pin = _StreamPin(self, start)
        self._pins.add(pin)
        return pin

    def compact(self) -> None:
        """Drop the prefix no live Input can replay."""
        starts = [pin.start for pin in self._pins if pin.active]
        keep_from = min(starts, default=self._next)
        if keep_from > self._base:
            del self._events[: keep_from - self._base]
            self._base = keep_from
