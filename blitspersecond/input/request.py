"""A question put to the player, answered without blocking.

Two of these exist -- a line of typing waiting for Return, and a pad waiting
for somebody to press something on it:

    name = console.echo(bps.keyboard.input().until(key.RETURN))
    who = bps.pads.identify().until(Button.START)

Both are asked once, both are answered by input arriving at some later cut,
and neither makes anybody wait.

Design record: `bps/reference/input/request.md`.
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

    **No `value` and no `until` here.** What a request is FOR differs, and so
    does whether a partial answer exists -- a half-typed line is worth
    reading, a half-identified pad is not. Each subject defines both; a base
    that defined either would have to pick one subject and be wrong for the
    other. See the chapter, *`value` is each subject's own*.
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
    #
    # The engine's two setters. A subject completes through `_complete()`
    # rather than assigning `_state`, so there is one place a request can
    # finish.

    def _interrupt(self, reason: str) -> None:
        """End it for a reason of the engine's rather than the player's."""
        if self._state is InputState.LISTENING:
            self._state = InputState.INTERRUPTED
            self._reason = reason

    def _complete(self) -> None:
        if self._state is InputState.LISTENING:
            self._state = InputState.COMPLETED
