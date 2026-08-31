"""Retention: a bounded ring of frozen, frame-numbered input windows.

This is the part that is genuinely device-agnostic, and it is deliberately
small. It knows how many windows to keep and how to hand them out. It knows
nothing about keys, buttons, axes, or what a fact means -- a device supplies
its own scan and its own facts, and this holds them.

Staging lives with the device rather than here, because folding arrivals into
state is device-specific work and the fold is what produces a window's facts.
See `docs/bps/INPUT_STREAM.md`.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import ceil
from typing import Generic, Iterator, Sequence, TypeVar

from blitspersecond.common import fps


#: How much history the ring keeps, in seconds of authored time.
#:
#: A duration rather than a frame count, because a person takes the same span
#: of real time to roll a quarter-circle whether the game is authored at 15 or
#: at 60. Half a second is comfortably more than reconstructing one motion
#: input needs, and generous on purpose: the costs are asymmetric. Too short
#: silently fails to reconstruct a slow player's motion with no symptom to
#: notice; too long holds thirty small objects per device at 60Hz.
#:
#: This is NOT a leniency setting. Holding an input so a move still comes out
#: when the player was early is game feel, and belongs to the game.
WINDOW_SECONDS = 0.5

_Scan = TypeVar("_Scan")
_Fact = TypeVar("_Fact")


@dataclass(frozen=True, slots=True)
class InputWindow(Generic[_Scan, _Fact]):
    """One frame's settled input: what the state was, and what happened.

    Frozen because readers hold it. The cut is the only thing that creates
    one, and nothing mutates it afterwards -- game logic reading twice in a
    frame sees the same answer both times.

    `scan` and `facts` answer different questions and neither replaces the
    other. The scan is state, and survives the ring: a key held for an hour
    was pressed long before the oldest retained window, and is still reported
    held because every cut copies the device's live set forward. The facts are
    history, and expire: they are what makes a roll through down,
    down-forward, forward reconstructible, and they are only retained for
    `WINDOW_SECONDS`.
    """

    frame: int
    scan: _Scan
    facts: tuple[_Fact, ...]


class InputStream(Generic[_Scan, _Fact]):
    """A device's bounded history of settled windows, newest last."""

    __slots__ = ("_capacity", "_windows")

    def __init__(self, rate: fps) -> None:
        self._capacity = ceil(int(rate) * WINDOW_SECONDS)
        self._windows: deque[InputWindow[_Scan, _Fact]] = deque(
            maxlen=self._capacity
        )

    @property
    def capacity(self) -> int:
        """Windows retained: `ceil(rate * WINDOW_SECONDS)`."""
        return self._capacity

    @property
    def newest(self) -> InputWindow[_Scan, _Fact] | None:
        """The most recently cut window, or None before the first cut.

        The usual read. "Is W down" is `newest.scan`, and it is correct
        however long ago the key went down.
        """
        return self._windows[-1] if self._windows else None

    def __len__(self) -> int:
        return len(self._windows)

    def append(self, window: InputWindow[_Scan, _Fact]) -> None:
        """Retain a settled window. Called by the cut, and by nothing else."""
        if self._windows and window.frame != self._windows[-1].frame + 1:
            raise ValueError(
                "input windows must be appended in frame sequence: "
                f"{self._windows[-1].frame} then {window.frame}"
            )
        self._windows.append(window)

    def recent(self, count: int) -> Sequence[InputWindow[_Scan, _Fact]]:
        """The last `count` windows, oldest first.

        Cannot fail and cannot be starved: asking for more than the ring holds
        returns what it holds. This is the reconstruction read -- a recogniser
        wants "the last fifteen frames" and does not care what came before.
        """
        if count < 0:
            raise ValueError("count must not be negative")
        if count == 0:
            return ()
        return tuple(self._windows)[-count:]

    def after(self, cursor: int | None) -> Iterator[InputWindow[_Scan, _Fact]]:
        """Every retained window after `cursor`, oldest first.

        For a reader that must not miss anything. A cursor older than the ring
        raises rather than returning a partial run that looks complete --
        losing input silently is worse than saying so.

        `None` means "everything retained", which is honest only for a reader
        that has just started. A reader that has fallen behind holds a number,
        not None, and gets told.
        """
        if not self._windows:
            return
        oldest = self._windows[0].frame
        if cursor is not None and cursor < oldest - 1:
            raise ValueError(
                f"input history after frame {cursor} has expired; "
                f"the ring holds {oldest} onwards"
            )
        for window in self._windows:
            if cursor is None or window.frame > cursor:
                yield window
