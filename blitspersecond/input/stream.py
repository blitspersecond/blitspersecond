"""Retention: a bounded ring of frozen, frame-numbered input windows.

The one device-agnostic piece, and deliberately small. It knows how many
windows to keep and how to hand them out; it knows nothing about keys,
buttons, axes, or what a fact means. A device supplies its own scan type and
its own fact type, and this holds them.

Staging lives with the device rather than here, because folding arrivals into
state is device-specific work and the fold is what produces a window's facts.

Design record: `bps/reference/input/stream.md`.
"""

from __future__ import annotations

from collections import deque
from blitspersecond.input.condition import Condition, Term, matches
from dataclasses import dataclass
from math import ceil
from typing import Generic, TypeVar

from blitspersecond.common import fps


#: How much history the ring keeps, in seconds of authored time.
#:
#: A DURATION, not a frame count: a person takes the same span of real time to
#: roll a quarter-circle at 15 or at 60, so capacity is this times the rate.
#:
#: **Not a leniency setting.** Holding an input so a move still comes out when
#: the player was early is game feel and belongs to the game. What may move
#: this number, and what may not: `bps/reference/input/stream.md`, *How deep,
#: and why that deep*.
WINDOW_SECONDS = 0.5

_Scan = TypeVar("_Scan")
_Fact = TypeVar("_Fact")


@dataclass(frozen=True, slots=True)
class InputWindow(Generic[_Scan, _Fact]):
    """One frame's settled input: what the state was, and what happened.

    Frozen because readers hold it: the cut is the only thing that creates
    one, and nothing mutates it afterwards.

    `scan` is state and survives the ring -- a key held for an hour is still
    reported held, because every cut copies the device's live set forward.
    `facts` are history and expire with the window.
    """

    frame: int
    scan: _Scan
    facts: tuple[_Fact, ...]

    # -- asking about it ---------------------------------------------------
    #
    # One implementation for every device. What these need of a fact is a
    # protocol rather than device knowledge: a `kind` whose enum has PRESS and
    # RELEASE, the `held` set the law puts on it, and `control`.

    def held(self, condition: Term) -> bool:
        """Did this cross its boundary and not cross back?

        State, so it is answered from the scan and is correct however long ago
        the crossing happened.
        """
        return matches(condition, self.scan.held)

    def pressed(self, condition: Term) -> bool:
        """Did this cross its boundary during this interval?"""
        if not isinstance(condition, Condition):
            return condition in self.scan.pressed
        return any(
            not matches(condition, before) and matches(condition, after)
            for before, after in self._transitions()
        )

    def released(self, condition: Term) -> bool:
        """Did this cross back during this interval?"""
        if not isinstance(condition, Condition):
            return condition in self.scan.released
        return any(
            matches(condition, before) and not matches(condition, after)
            for before, after in self._transitions()
        )

    def _transitions(self):
        """The held-set either side of each transition, in arrival order.

        **This is what the held-set law is for.** A press carries the set
        AFTER its control was added and a release the set BEFORE its was
        removed, so each fact hands over one side and the other is that set
        with its own control taken out. No replay of the interval, and no
        state carried between facts.

        Computing both sides rather than reading `fact.held` directly is what
        makes negation work. The moment a negated control is released is the
        moment `jump & ~crouch` becomes TRUE, and a release's `held` still
        contains the control it is about -- so evaluating that set alone would
        report the expression false at exactly the transition that satisfies
        it.
        """
        for fact in self.facts:
            kinds = type(fact.kind)
            if fact.kind is kinds.PRESS:
                after = fact.held
                before = after - {fact.control}
            elif fact.kind is kinds.RELEASE:
                before = fact.held
                after = before - {fact.control}
            else:
                continue
            yield before, after


class InputStream(Generic[_Scan, _Fact]):
    """A device's bounded history of settled windows, oldest first."""

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
    def latest(self) -> InputWindow[_Scan, _Fact] | None:
        """The most recently cut window, or None before the first cut.

        The usual read. "Is W down" is `latest.scan`, and it is correct
        however long ago the key went down.
        """
        return self._windows[-1] if self._windows else None

    @property
    def all(self) -> tuple[InputWindow[_Scan, _Fact], ...]:
        """Every retained window, oldest first. The reconstruction read.

        **There is no count to ask for**, and adding one would be a caller
        re-deciding a depth the engine has already derived: the ring is sized
        to the motion requirement and keeps nothing longer.
        """
        return tuple(self._windows)

    def __len__(self) -> int:
        return len(self._windows)

    def append(self, window: InputWindow[_Scan, _Fact]) -> None:
        """Retain a settled window. Called by the cut, and by nothing else.

        Frame sequence is enforced rather than assumed: a gap would make "the
        last thirty frames" describe a longer interval without saying so.
        """
        if self._windows and window.frame != self._windows[-1].frame + 1:
            raise ValueError(
                "input windows must be appended in frame sequence: "
                f"{self._windows[-1].frame} then {window.frame}"
            )
        self._windows.append(window)


class Queryable:
    """The verbs, forwarded to a device's latest settled window.

    So `bps.keyboard.held(key.SPACE)` and
    `bps.keyboard.stream.latest.held(key.SPACE)` are the same question, and a
    game reaches for the window only when it wants a frame that is not the
    most recent one.

    **Before the first cut every answer is False**, which is the truth rather
    than a guard: nothing is held, and nothing crossed a boundary, because
    nothing has happened yet.
    """

    __slots__ = ()

    def held(self, condition: Term) -> bool:
        window = self.stream.latest
        return window is not None and window.held(condition)

    def pressed(self, condition: Term) -> bool:
        window = self.stream.latest
        return window is not None and window.pressed(condition)

    def released(self, condition: Term) -> bool:
        window = self.stream.latest
        return window is not None and window.released(condition)
