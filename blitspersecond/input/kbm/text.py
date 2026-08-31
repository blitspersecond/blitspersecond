"""A line of typing: the console's own buffer, not engine history.

The engine keeps half a second of input so a motion can be reconstructed. A
person typing their name may take a minute over it, and none of that belongs
in a ring sized for quarter-circles. So a line reducer owns its own buffer,
which grows for as long as the line is open, and the engine retains nothing
on its behalf.

The reference did this the other way round: its `Input` pinned a range of the
keyboard stream at construction and replayed it on every read, which made the
engine's short, hardware-shaped history unbounded whenever a console line
happened to be open. One mechanism serving two lifetimes. This is the split.

A reducer is fed by the cut rather than reading the ring, so its correctness
does not depend on a retention depth it does not control -- it is the one
consumer that must not miss anything.
"""

from __future__ import annotations

from enum import Enum, auto

from pyglet.window import key as _key

from blitspersecond.input.kbm.keyboard import KeyEvent, KeyboardWindow


class InputState(Enum):
    """Where a line has got to. Exactly one is true at a time."""

    LISTENING = auto()
    COMPLETED = auto()
    INTERRUPTED = auto()
    DISCARDED = auto()


#: Editing motions the line understands. Pyglet resolves these from the
#: platform's own bindings, so backspace on a Mac and backspace on Linux both
#: arrive here as MOTION_BACKSPACE whatever key produced them.
_BACKSPACE = _key.MOTION_BACKSPACE
_DELETE = _key.MOTION_DELETE
_LEFT = _key.MOTION_LEFT
_RIGHT = _key.MOTION_RIGHT
_HOME = _key.MOTION_BEGINNING_OF_LINE
_END = _key.MOTION_END_OF_LINE


class TextInput:
    """One line being typed, reduced from settled windows as they are cut."""

    __slots__ = ("_caret", "_reason", "_state", "_terminator", "_text")

    def __init__(self, initial: str = "") -> None:
        self._text: list[str] = list(initial)
        self._caret = len(self._text)
        self._state = InputState.LISTENING
        self._terminator: int | None = None
        self._reason: str | None = None

    def until(self, symbol: int) -> "TextInput":
        """Name the key that finishes this line. Returns self, to read as one
        phrase at the call site: `keyboard.input().until(key.RETURN)`."""
        self._terminator = symbol
        return self

    # -- what the game asks ------------------------------------------------

    @property
    def value(self) -> str:
        """What the line spells, right now.

        Held still for the whole of a tick: the reducer is advanced at the
        cut, before the game is handed control, so this cannot change halfway
        down a function that is reading it.
        """
        return "".join(self._text)

    @property
    def caret(self) -> int:
        """Where the insertion point sits, in characters from the start."""
        return self._caret

    @property
    def state(self) -> InputState:
        return self._state

    @property
    def listening(self) -> bool:
        return self._state is InputState.LISTENING

    @property
    def completed(self) -> bool:
        """The terminator arrived. `value` is the answer."""
        return self._state is InputState.COMPLETED

    @property
    def interrupted(self) -> bool:
        """Something else ended it. `reason` says what."""
        return self._state is InputState.INTERRUPTED

    @property
    def discarded(self) -> bool:
        return self._state is InputState.DISCARDED

    @property
    def reason(self) -> str | None:
        """Why it ended, when it was not the player who ended it."""
        return self._reason

    def discard(self) -> None:
        """Abandon the line. It stops consuming and keeps nothing."""
        if self._state is InputState.LISTENING:
            self._state = InputState.DISCARDED
            self._reason = "DISCARDED"
        self._text.clear()
        self._caret = 0

    # -- driven by the cut -------------------------------------------------

    def _interrupt(self, reason: str) -> None:
        if self._state is InputState.LISTENING:
            self._state = InputState.INTERRUPTED
            self._reason = reason

    def _consume(self, window: KeyboardWindow) -> None:
        """Fold one settled window's text and editing into the line.

        Facts in the order they arrived, which is what makes `"name\\r"` spell
        the name before the Return completes it.
        """
        if self._state is not InputState.LISTENING:
            return

        for fact in window.facts:
            if fact.kind is KeyEvent.PRESS:
                if self._terminator is not None and fact.value == self._terminator:
                    self._state = InputState.COMPLETED
                    return

            elif fact.kind is KeyEvent.TEXT:
                assert isinstance(fact.value, str)
                # Control characters are never content. Return arrives as both
                # a press and a "\r"; the press is what completes the line, and
                # the character must not be typed into it either way.
                if fact.value >= " " and fact.value != "\x7f":
                    self._text.insert(self._caret, fact.value)
                    self._caret += 1

            elif fact.kind is KeyEvent.MOTION:
                self._edit(int(fact.value))

    def _edit(self, motion: int) -> None:
        if motion == _BACKSPACE:
            if self._caret > 0:
                self._caret -= 1
                del self._text[self._caret]
        elif motion == _DELETE:
            if self._caret < len(self._text):
                del self._text[self._caret]
        elif motion == _LEFT:
            self._caret = max(0, self._caret - 1)
        elif motion == _RIGHT:
            self._caret = min(len(self._text), self._caret + 1)
        elif motion == _HOME:
            self._caret = 0
        elif motion == _END:
            self._caret = len(self._text)
