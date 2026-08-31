"""Bounded frame history shared by every input source."""

from __future__ import annotations

from collections import deque
from typing import Generic, Iterator, Protocol, TypeVar

from blitspersecond.common import fps


class InputFrame(Protocol):
    """The one fact the ring requires of a settled source snapshot."""

    frame: int


_Frame = TypeVar("_Frame", bound=InputFrame)


class InputBuffer(Generic[_Frame]):
    """A fixed ring of immutable, frame-addressed input snapshots.

    The buffer owns retention, not device meaning. Keyboard, mouse, pads, and
    later sources provide their own frozen frame records and share this exact
    half-second/cursor contract.
    """

    def __init__(self, rate: fps) -> None:
        if not isinstance(rate, int) or isinstance(rate, bool):
            raise TypeError("rate must be an integer")
        if rate <= 0:
            raise ValueError("rate must be positive")
        self._capacity = (int(rate) + 1) // 2
        self._frames: deque[_Frame] = deque(maxlen=self._capacity)

    @property
    def capacity(self) -> int:
        """Number of retained frames: at least half an authored second."""
        return self._capacity

    @property
    def newest(self) -> int | None:
        """The newest retained frame number, or ``None`` before capture."""
        return self._frames[-1].frame if self._frames else None

    def append(self, frame: _Frame) -> None:
        """Append the next settled frame; frame identity must not skip."""
        if self._frames and frame.frame != self._frames[-1].frame + 1:
            raise ValueError("input frames must be appended in sequence")
        self._frames.append(frame)

    def after(self, cursor: int | None) -> Iterator[_Frame]:
        """Yield immutable frames strictly after a reader's cursor.

        A cursor older than the ring is an explicit loss, never a partial
        replay presented as complete input history.
        """
        if not self._frames:
            return
        first = self._frames[0].frame
        if cursor is not None and cursor < first - 1:
            raise ValueError("input history before this cursor has expired")
        for frame in self._frames:
            if cursor is None or frame.frame > cursor:
                yield frame
