"""Non-blocking, stream-backed text Input.

Input is a small predicate program over the engine-owned keyboard stream. It
returns immediately, advances when the private handler snapshots a tick, and
retains a replayable range rather than owning a mutable line inside the device
handler.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from math import isfinite
from typing import Callable, Collection, Optional, Protocol, TYPE_CHECKING

from pyglet.window import key as _pyglet_key

if TYPE_CHECKING:
    from .keyboard_handler import KeyboardHandler
    from .stream import KeyboardEvent


class InputState(Enum):
    LISTENING = auto()
    COMPLETED = auto()
    INTERRUPTED = auto()
    DISCARDED = auto()


LISTENING = InputState.LISTENING
COMPLETED = InputState.COMPLETED
INTERRUPTED = InputState.INTERRUPTED
DISCARDED = InputState.DISCARDED


@dataclass(frozen=True)
class _Evaluation:
    event: Optional["KeyboardEvent"]
    tick: int
    started_tick: int
    tick_rate: int
    held: frozenset[int]


class Predicate:
    """A composable condition evaluated by an active Input."""

    def _match(self, evaluation: _Evaluation) -> Optional["Predicate"]:
        raise NotImplementedError

    def __or__(self, other: "Predicate") -> "Predicate":
        if not isinstance(other, Predicate):
            return NotImplemented
        return _Any(self, other)

    def __and__(self, other: "Predicate") -> "Predicate":
        if not isinstance(other, Predicate):
            return NotImplemented
        return _All(self, other)

    def __invert__(self) -> "Predicate":
        return _Not(self)


class _Group(Predicate):
    operator = "?"

    def __init__(self, *predicates: Predicate):
        if not all(isinstance(predicate, Predicate) for predicate in predicates):
            raise TypeError("input conditions must be predicates")
        group_type = type(self)
        self.predicates = tuple(
            child
            for predicate in predicates
            for child in (
                predicate.predicates
                if isinstance(predicate, group_type)
                else (predicate,)
            )
        )

    def __repr__(self) -> str:
        return f" {self.operator} ".join(
            f"({predicate})" if isinstance(predicate, _Group) else repr(predicate)
            for predicate in self.predicates
        )


class _Any(_Group):
    operator = "|"

    def _match(self, evaluation: _Evaluation) -> Optional[Predicate]:
        for predicate in self.predicates:
            if match := predicate._match(evaluation):
                return match
        return None


class _All(_Group):
    operator = "&"

    def _match(self, evaluation: _Evaluation) -> Optional[Predicate]:
        key_conditions = tuple(
            predicate
            for predicate in self.predicates
            if _is_key_expression(predicate)
        )
        if key_conditions:
            condition = (
                key_conditions[0]
                if len(key_conditions) == 1
                else _All(*key_conditions)
            )
            key_match = _match_key_expression(condition, evaluation)
            if key_match is None:
                return None
        else:
            key_match = None

        matches: list[Predicate] = []
        for predicate in self.predicates:
            if predicate in key_conditions:
                continue
            match = predicate._match(evaluation)
            if match is None:
                return None
            matches.append(match)

        # Prefer the event-shaped part of an AND as the finalizer. Thus a
        # chord gated by elapsed(...) reports the key which completed it.
        return key_match if key_match is not None else matches[0]


class _Not(Predicate):
    def __init__(self, predicate: Predicate):
        self.predicate = predicate

    def _match(self, evaluation: _Evaluation) -> Optional[Predicate]:
        return self if self.predicate._match(evaluation) is None else None

    def __repr__(self) -> str:
        return f"~({self.predicate!r})"


class KeyPredicate(Predicate):
    """A fresh physical key press (Return also matches committed CR/LF)."""

    def __init__(self, name: str, symbol: int):
        self.name = name
        self.symbol = symbol

    def _match(self, evaluation: _Evaluation) -> Optional[Predicate]:
        event = evaluation.event
        if event is None:
            return None
        if event.kind == "press" and event.value == self.symbol:
            return self
        if (
            self.symbol == _pyglet_key.RETURN
            and event.kind == "text"
            and event.value in ("\r", "\n")
        ):
            return self
        return None

    def __repr__(self) -> str:
        return f"key.{self.name}"


class _KeyPredicates:
    """Lazily wrap pyglet's integer key symbols as predicate values."""

    def __init__(self) -> None:
        self._predicates: dict[str, Predicate] = {}

    def __getattr__(self, name: str) -> Predicate:
        aliases = {
            "CTRL": ("LCTRL", "RCTRL"),
            "ALT": ("LALT", "RALT"),
            "SHIFT": ("LSHIFT", "RSHIFT"),
            "DEL": ("DELETE",),
        }
        if names := aliases.get(name):
            predicate = self._predicates.get(name)
            if predicate is None:
                parts = tuple(getattr(self, part) for part in names)
                predicate = parts[0]
                for part in parts[1:]:
                    predicate |= part
                self._predicates[name] = predicate
            return predicate

        if name.startswith(("MOD_", "MOTION_")):
            raise AttributeError(f"{name!r} is not a physical key")
        try:
            symbol = getattr(_pyglet_key, name)
        except AttributeError:
            raise AttributeError(f"unknown key {name!r}") from None
        if not isinstance(symbol, int):
            raise AttributeError(f"{name!r} is not a key symbol")
        predicate = self._predicates.get(name)
        if predicate is None:
            predicate = self._predicates[name] = KeyPredicate(name, symbol)
        return predicate


key = _KeyPredicates()


def _key_symbols(condition: Predicate) -> frozenset[int]:
    """Return a key expression's physical symbols, rejecting other predicates."""
    if isinstance(condition, KeyPredicate):
        return frozenset((condition.symbol,))
    if isinstance(condition, (_Any, _All)):
        symbols: set[int] = set()
        for predicate in condition.predicates:
            symbols.update(_key_symbols(predicate))
        return frozenset(symbols)
    raise TypeError("keyboard queries require a key predicate")


def _is_key_expression(condition: Predicate) -> bool:
    try:
        _key_symbols(condition)
    except TypeError:
        return False
    return True


def _key_for_symbol(
    condition: Predicate, symbol: int
) -> Optional[KeyPredicate]:
    if isinstance(condition, KeyPredicate):
        return condition if condition.symbol == symbol else None
    if isinstance(condition, (_Any, _All)):
        for predicate in condition.predicates:
            if match := _key_for_symbol(predicate, symbol):
                return match
    return None


def _match_key_expression(
    condition: Predicate, evaluation: _Evaluation
) -> Optional[KeyPredicate]:
    event = evaluation.event
    if event is None:
        return None
    if event.kind == "press" and isinstance(event.value, int):
        match = _key_for_symbol(condition, event.value)
        if match is not None and _key_down(condition, evaluation.held):
            return match
    if event.kind == "text":
        match = _key_for_symbol(condition, _pyglet_key.RETURN)
        if match is not None and event.value in ("\r", "\n"):
            return match
    return None


def _key_down(condition: Predicate, held: Collection[int]) -> bool:
    """Evaluate a key expression against one physical held-state."""
    if isinstance(condition, KeyPredicate):
        return condition.symbol in held
    if isinstance(condition, _Any):
        return any(_key_down(predicate, held) for predicate in condition.predicates)
    if isinstance(condition, _All):
        return all(_key_down(predicate, held) for predicate in condition.predicates)
    raise TypeError("keyboard queries require a key predicate")


def _key_pressed(
    condition: Predicate,
    transitions: Collection[tuple[int, frozenset[int]]],
) -> bool:
    """Whether a relevant press left the expression satisfied this tick."""
    symbols = _key_symbols(condition)
    return any(
        symbol in symbols and _key_down(condition, after)
        for symbol, after in transitions
    )


def _key_released(
    condition: Predicate,
    transitions: Collection[tuple[int, frozenset[int]]],
) -> bool:
    """Whether a relevant release began with the expression satisfied."""
    symbols = _key_symbols(condition)
    return any(
        symbol in symbols and _key_down(condition, before)
        for symbol, before in transitions
    )


class Elapsed(Predicate):
    def __init__(self, seconds: float):
        seconds = float(seconds)
        if not isfinite(seconds) or seconds < 0:
            raise ValueError("elapsed seconds must be finite and non-negative")
        self.seconds = seconds

    def _match(self, evaluation: _Evaluation) -> Optional[Predicate]:
        ticks = evaluation.tick - evaluation.started_tick
        return self if ticks >= self.seconds * evaluation.tick_rate else None

    def __repr__(self) -> str:
        return f"elapsed(seconds={self.seconds:g})"


def elapsed(*, seconds: float) -> Elapsed:
    return Elapsed(seconds)


@dataclass
class _TextState:
    characters: list[str]
    insert: int


class _InputObserver(Protocol):
    def _started(self, entry: "Input") -> None: ...

    def _changed(self, entry: "Input") -> None: ...

    def _finished(self, entry: "Input") -> None: ...

    def _discarded(self, entry: "Input") -> None: ...


class Input:
    """A keyboard-owned editable reference into its event stream."""

    def __init__(
        self,
        keyboard: "KeyboardHandler",
        value: str = "",
        *,
        start: int,
        started_tick: int,
        tick_rate: int,
        accepts: Optional[Callable[[str], bool]] = None,
    ) -> None:
        if not isinstance(value, str):
            raise TypeError(f"input value must be str, got {type(value)}")
        self._initial = value
        self._keyboard = keyboard
        self._accepts = accepts
        self._condition: Optional[Predicate] = None
        self._state = LISTENING
        self._start = start
        self._stop: Optional[int] = None
        self._started_tick = started_tick
        self._tick_rate = tick_rate
        self._finalizer: Optional[Predicate] = None
        self._reason: Optional[str] = None
        self._pin = keyboard.stream.pin(start)
        self._observers: list[_InputObserver] = []

    def until(self, condition: Predicate) -> "Input":
        """Set this active input's completion condition."""
        if not isinstance(condition, Predicate):
            raise TypeError("until() requires an input predicate")
        if self._condition is not None:
            raise RuntimeError("this Input already has a completion condition")
        if self._state is not LISTENING:
            raise RuntimeError("this Input is no longer listening")
        self._condition = condition
        return self

    @property
    def state(self) -> InputState:
        return self._state

    @property
    def listening(self) -> bool:
        return self._state is LISTENING

    @property
    def completed(self) -> bool:
        return self._state is COMPLETED

    @property
    def interrupted(self) -> bool:
        return self._state is INTERRUPTED

    @property
    def discarded(self) -> bool:
        return self._state is DISCARDED

    @property
    def finalizer(self) -> Optional[Predicate]:
        return self._finalizer

    @property
    def reason(self) -> Optional[str]:
        return self._reason

    @property
    def start(self) -> int:
        return self._start

    @property
    def stop(self) -> Optional[int]:
        return self._stop

    def _text_state(self) -> _TextState:
        if self._state is DISCARDED:
            raise RuntimeError("discarded input is no longer replayable")

        state = _TextState(list(self._initial), len(self._initial))
        for event in self._keyboard.stream.replay(self._start, self._stop):
            if event.kind == "text":
                text = event.value
                assert isinstance(text, str)
                for character in text:
                    if (
                        character not in "\r\n"
                        and character.isprintable()
                        and (self._accepts is None or self._accepts(character))
                    ):
                        state.characters.insert(state.insert, character)
                        state.insert += 1
            elif event.kind == "motion":
                self._edit(state, event.value)
        return state

    @staticmethod
    def _edit(state: _TextState, motion: object) -> None:
        if motion == _pyglet_key.MOTION_BACKSPACE:
            if state.insert:
                state.insert -= 1
                state.characters.pop(state.insert)
        elif motion == _pyglet_key.MOTION_DELETE:
            if state.insert < len(state.characters):
                state.characters.pop(state.insert)
        elif motion == _pyglet_key.MOTION_LEFT:
            state.insert = max(0, state.insert - 1)
        elif motion == _pyglet_key.MOTION_RIGHT:
            state.insert = min(len(state.characters), state.insert + 1)
        elif motion == _pyglet_key.MOTION_BEGINNING_OF_LINE:
            state.insert = 0
        elif motion == _pyglet_key.MOTION_END_OF_LINE:
            state.insert = len(state.characters)

    @property
    def value(self) -> str:
        return "".join(self._text_state().characters)

    @property
    def insert(self) -> int:
        return self._text_state().insert

    def __str__(self) -> str:
        return self.value

    def _evaluation(
        self, event: Optional["KeyboardEvent"], tick: int
    ) -> _Evaluation:
        return _Evaluation(
            event=event,
            tick=tick,
            started_tick=self._started_tick,
            tick_rate=self._tick_rate,
            held=self._keyboard._held_snapshot,
        )

    def _receive(self, event: "KeyboardEvent") -> Optional[Predicate]:
        if self._state is not LISTENING:
            return None
        self._notify("_changed")
        if self._state is not LISTENING:
            return None
        if self._condition is None:
            return None
        return self._condition._match(self._evaluation(event, event.tick))

    def _advance(self, tick: int) -> Optional[Predicate]:
        if self._state is not LISTENING:
            return None
        if self._condition is None:
            return None
        return self._condition._match(self._evaluation(None, tick))

    def _complete(self, finalizer: Predicate, stop: int) -> None:
        if self._state is not LISTENING:
            return
        self._stop = stop
        self._finalizer = finalizer
        self._state = COMPLETED
        self._notify("_finished")

    def _interrupt(self, stop: int, reason: str) -> None:
        if self._state is not LISTENING:
            return
        self._stop = stop
        self._reason = reason
        self._state = INTERRUPTED
        self._notify("_finished")

    def discard(self) -> None:
        if self._state is DISCARDED:
            return
        if self._state is LISTENING:
            self._stop = self._keyboard.stream.position
        self._state = DISCARDED
        if self._pin is not None:
            self._pin.release()
            self._pin = None
        self._notify("_discarded")
        self._observers.clear()

    def _observe(self, observer: _InputObserver) -> None:
        if self._state is DISCARDED:
            raise RuntimeError("discarded input cannot be observed")
        self._observers.append(observer)
        observer._started(self)
        if self._state in (COMPLETED, INTERRUPTED):
            observer._finished(self)

    def _notify(self, method: str) -> None:
        for observer in tuple(self._observers):
            getattr(observer, method)(self)


__all__ = [
    "COMPLETED",
    "DISCARDED",
    "INTERRUPTED",
    "LISTENING",
    "Elapsed",
    "Input",
    "InputState",
    "KeyPredicate",
    "Predicate",
    "elapsed",
    "key",
]
