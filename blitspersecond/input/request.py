"""A question put to the player, answered without blocking.

Two of these exist and they are the same object under different subjects: a
line of typing waiting for Return, and a pad waiting for somebody to press
something on it. Both are asked once, both are answered by input arriving at
some later cut, and neither makes anybody wait.

    name = console.echo(bps.keyboard.input().until(key.RETURN))
    who = bps.pads.identify().until(Button.START)

    if name.completed: ...
    if who.completed: ...

**The shape is shared deliberately.** The pad version was written months after
the console one and came out identical bone for bone -- the same four states,
the same `until()` narrowing, the same non-optional `value` that raises rather
than returning None, the same supersede-on-reasking. Two subjects arriving at
one shape is the signal to write it once; a reader who has understood either
has understood the other.
"""

from __future__ import annotations

from enum import Enum, auto


class InputState(Enum):
    """Where a request has got to. Exactly one is true at a time."""

    LISTENING = auto()
    COMPLETED = auto()
    INTERRUPTED = auto()
    DISCARDED = auto()


class Request:
    """The lifecycle both requests share, and nothing else.

    No `value` here: what a request is FOR differs, and the base has no
    business guessing. What it owns is the state machine -- which is the part
    that was written twice.
    """

    __slots__ = ("_reason", "_state")

    def __init__(self) -> None:
        self._state = InputState.LISTENING
        self._reason: str | None = None

    @property
    def state(self) -> InputState:
        return self._state

    @property
    def listening(self) -> bool:
        """Still waiting. The only state in which an answer can arrive."""
        return self._state is InputState.LISTENING

    @property
    def completed(self) -> bool:
        """It was answered. `value` is the answer."""
        return self._state is InputState.COMPLETED

    @property
    def interrupted(self) -> bool:
        """Something else ended it. `reason` says what."""
        return self._state is InputState.INTERRUPTED

    @property
    def discarded(self) -> bool:
        """The caller abandoned it."""
        return self._state is InputState.DISCARDED

    @property
    def reason(self) -> str | None:
        """Why it ended, when it was not the player who ended it."""
        return self._reason

    def discard(self) -> None:
        """Abandon the request. It stops consuming and keeps nothing."""
        if self._state is InputState.LISTENING:
            self._state = InputState.DISCARDED
            self._reason = "DISCARDED"

    # -- driven by the cut -------------------------------------------------

    def _interrupt(self, reason: str) -> None:
        """End it for a reason of the engine's rather than the player's.

        A newer request of the same kind supersedes an older one: a finished
        question is a finished thing, and the next one deserves its own answer
        rather than a share of this one's.
        """
        if self._state is InputState.LISTENING:
            self._state = InputState.INTERRUPTED
            self._reason = reason

    def _complete(self) -> None:
        if self._state is InputState.LISTENING:
            self._state = InputState.COMPLETED
