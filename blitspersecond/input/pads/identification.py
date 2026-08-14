"""A non-blocking request to identify the next deliberately used Pad."""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from .codes import Button, ButtonCondition, ButtonPredicate

if TYPE_CHECKING:
    from .pad import Pad


class Identification:
    """The Pad selected by a future deliberate input event.

    With no narrowing condition, any deliberate Pad activity completes the
    request. ``until()`` restricts completion to a selected button press.
    """

    def __init__(self) -> None:
        self._condition: Optional[ButtonCondition] = None
        self._value: Optional[Pad] = None
        self._interrupted = False

    def until(self, condition: ButtonCondition) -> "Identification":
        """Restrict this request to a press of one of the selected buttons."""
        if not isinstance(condition, (Button, ButtonPredicate)):
            raise TypeError("until() requires a Button predicate")
        if self._condition is not None:
            raise RuntimeError("this Identification already has a condition")
        if not self.listening:
            raise RuntimeError("this Identification is no longer listening")
        self._condition = condition
        return self

    @property
    def listening(self) -> bool:
        return self._value is None and not self._interrupted

    @property
    def completed(self) -> bool:
        return self._value is not None

    @property
    def interrupted(self) -> bool:
        return self._interrupted

    @property
    def value(self) -> "Pad":
        """The selected Pad.

        The return type is deliberately non-optional: games check ``completed``
        before reading it, and a premature read is a lifecycle error rather
        than an absent physical device.
        """
        if self._value is None:
            raise RuntimeError("Identification has not completed")
        return self._value

    def _consider(
        self,
        pad: "Pad",
        *,
        activated: bool,
        pressed: Optional[Button],
    ) -> None:
        """Complete if this one raw event satisfies the request."""
        if not self.listening:
            return
        if self._condition is None:
            matched = activated
        else:
            matched = pressed is not None and pressed in pad._buttons(self._condition)
        if matched:
            self._value = pad

    def _interrupt(self) -> None:
        if self.listening:
            self._interrupted = True
