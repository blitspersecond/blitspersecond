"""A line of typing: the console's own buffer, not engine history.

A reducer owns its own buffer, which grows for as long as the line is open,
and the engine retains nothing on its behalf. It is fed by the cut rather than
reading the ring, because it is the one consumer whose correctness depends on
missing nothing and it must not depend on a retention depth it does not
control.

Design record: `bps/reference/input/text.md`.
"""

from __future__ import annotations

from .keys import Motion

from blitspersecond.input.kbm.keyboard import KeyEvent, KeyboardWindow
from blitspersecond.input.request import Request

#: Editing motions the line understands; anything else is ignored. Pyglet
#: resolves these from the platform's own bindings, so backspace on a Mac and
#: on Linux both arrive as MOTION_BACKSPACE whatever key produced them.
_BACKSPACE = Motion.BACKSPACE
_DELETE = Motion.DELETE
_LEFT = Motion.LEFT
_RIGHT = Motion.RIGHT
_HOME = Motion.BEGINNING_OF_LINE
_END = Motion.END_OF_LINE


class TextInput(Request):
    """One line being typed, reduced from settled windows as they are cut.

    The lifecycle comes from `Request`, which the pad's `Identification`
    shares -- see `bps/reference/input/request.md` for why the two are one
    shape.
    """

    __slots__ = ("_caret", "_terminator", "_text")

    def __init__(self, initial: str = "") -> None:
        super().__init__()
        self._text: list[str] = list(initial)
        self._caret = len(self._text)
        self._terminator: int | None = None

    def until(self, symbol: int) -> "TextInput":
        """Name the key that finishes this line. Returns self, so the call
        site reads as one phrase: `keyboard.input().until(key.RETURN)`.

        Narrowing once is the whole of it. A second terminator, or one named
        after the line has stopped listening, is a mistake in the caller
        rather than a change of mind worth honouring -- the same three
        refusals `Identification.until` makes, for the same reason.
        """
        if not isinstance(symbol, int):
            raise TypeError("until() takes a key symbol")
        if self._terminator is not None:
            raise RuntimeError("this request already has a terminator")
        if not self.listening:
            raise RuntimeError("this request is no longer listening")
        self._terminator = symbol
        return self

    # -- what the game asks ------------------------------------------------

    @property
    def value(self) -> str:
        """What the line spells, right now.

        Held still for the whole of a tick: the reducer is advanced at the cut,
        before the game is handed control. Readable in every state -- a
        half-typed line is a perfectly good partial answer.
        """
        return "".join(self._text)

    @property
    def caret(self) -> int:
        """Where the insertion point sits, in characters from the start."""
        return self._caret

    def discard(self) -> None:
        """Abandon the line. It stops consuming and keeps nothing."""
        super().discard()
        self._text.clear()
        self._caret = 0

    # -- driven by the cut -------------------------------------------------

    def _consume(self, window: KeyboardWindow) -> None:
        """Fold one settled window's text and editing into the line.

        Facts in ARRIVAL ORDER, which is what makes `"name\\r"` spell the name
        before the Return completes it.
        """
        if not self.listening:
            return

        for fact in window.facts:
            if fact.kind is KeyEvent.PRESS:
                if self._terminator is not None and fact.value == self._terminator:
                    self._complete()
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
