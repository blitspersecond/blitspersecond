"""Which pad is this person using? Answered by them using it.

The wiggle-a-pad mode, and the whole of it. A single-player game asks once,
carries on drawing its title screen, and at some later frame the answer is
there -- whichever pad somebody pressed something on.

    who = bps.pads.identify().until(Button.START)
    ...
    if who.completed:
        prompt = f"Press {who.value.mapper.label(Button.START)}"

which says "Press Menu" on an Xbox pad and "Press Options" on a DualSense,
because by then we know which pad we are talking to. That is the entire point
of identifying one: not to assign a seat, but to know whose glyphs to draw.

Seats are a different subsystem: `bps/reference/input/ports.md`.
Design record: `bps/reference/input/pads.md`, *Two ways in*.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from blitspersecond.input.request import Request

from blitspersecond.input.condition import Condition, matches, resolve

from .button import Button, ButtonCondition
from .pad import PadEvent, PadWindow

if TYPE_CHECKING:
    from .pads import Pad


class Identification(Request):
    """A request for the next pad somebody deliberately uses.

    Non-blocking, like every question this engine puts to a player: it is
    advanced by the cut and read whenever the game gets round to it. The
    lifecycle is `Request`'s -- `bps/reference/input/request.md`.
    """

    __slots__ = ("_condition", "_value")

    def __init__(self) -> None:
        super().__init__()
        self._condition: ButtonCondition | None = None
        self._value: "Pad | None" = None

    def until(self, condition: ButtonCondition) -> "Identification":
        """Narrow this to a press of one of these. Returns self, so the call
        site reads as one phrase.

        Without it, any deliberate use answers -- a button, the d-pad leaving
        neutral, a stick shoved most of the way. With it, only those presses
        do, which is what "Press Start to begin" means when two pads are on
        the table and only one person is ready.

            pads.identify().until(Button.START | Button.ACCEPT)

        reads as "start, or whatever this pad calls yes", and the second half
        resolves differently per family -- south on an Xbox or Sony pad, east
        on a Nintendo one. That resolution happens per pad when the answer
        arrives, because it cannot happen when the question is written.
        """
        if not isinstance(condition, (Button, Condition)):
            raise TypeError("until() takes a Button condition")
        if self._condition is not None:
            raise RuntimeError("this request already has a condition")
        if not self.listening:
            raise RuntimeError("this request is no longer listening")
        self._condition = condition
        return self

    @property
    def value(self) -> "Pad":
        """The pad that answered.

        Deliberately not optional: a game checks `completed` first, and
        reading early is a mistake in the caller rather than a pad that failed
        to exist. Saying so with an exception is more use than a None that
        propagates somewhere else before it breaks.
        """
        if self._value is None:
            raise RuntimeError("this identification has not completed")
        return self._value

    # -- driven by the cut -------------------------------------------------

    def _consider(self, pad: "Pad", window: PadWindow, deliberate: bool) -> None:
        """Did this pad's settled interval answer the question?"""
        if not self.listening:
            return
        if self._condition is None:
            matched = deliberate
        else:
            wanted = resolve(self._condition, {Button.ACCEPT: pad.mapper.ACCEPT})
            matched = any(
                fact.kind is PadEvent.PRESS and matches(wanted, fact.held)
                for fact in window.facts
            )
        if matched:
            self._value = pad
            self._complete()
