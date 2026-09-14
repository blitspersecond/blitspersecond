"""A question about controls, composed with `|`, `&` and `~`.

One algebra for every device. A `Button`, a `Key` and a `Direction` are all
just terms, and the shape of a question about them does not depend on which
kind it holds:

    Button.FACE_SOUTH | Button.START        either of these
    key.LCTRL & key.LALT                    both at once
    key.LSHIFT & ~key.LALT                  one, without the other
    ~Direction.NEUTRAL                      the d-pad is doing something

**The operators had to be defined, not merely left alone.** Every vocabulary
the engine reports is an enum, and two of the three are integer-valued -- a
pyglet key symbol is a plain `int`, and `Direction` is an `IntEnum` because
numpad notation IS the vocabulary there. Integers already implement `|`, `&`
and `~`, arithmetically, so an undefined operator does not raise: `LCTRL &
LALT` quietly evaluates to `LSHIFT` and `DOWN | DOWN_RIGHT` to `DOWN_RIGHT`.
Both are real members, so nothing downstream can tell it went wrong. Defining
these is what removes that, and it is why `Composable` must come first in a
vocabulary's bases -- it has to shadow `int`.

This module is the algebra and its satisfaction against a set of terms. What
`pressed` and `held` MEAN over a frame's window is the device layer's, and is
built on this rather than in it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, TypeAlias


class Condition:
    """A composed question. The operators live here so they are written once.

    Every node knows whether anything under it is still `Unresolved`, worked
    out once when it is built. **Asking at match time would be worse than a
    cost**: `any()` and `all()` short-circuit, so an unresolved term inside a
    satisfied branch is never reached, and the mistake would surface or not
    depending on what the player happened to press.
    """

    __slots__ = ()

    #: Overridden per node in __post_init__. False for anything fully resolved.
    incomplete = False

    def __or__(self, other) -> "Condition":
        return _combine(AnyOf, self, other)

    def __and__(self, other) -> "Condition":
        return _combine(AllOf, self, other)

    def __invert__(self) -> "Condition":
        return Not(self)

    def matches(self, held: Iterable) -> bool:
        raise NotImplementedError


class Composable:
    """Gives a vocabulary enum the same operators.

    **List it before the enum base**, so that on an `IntEnum` these shadow
    `int`'s arithmetic rather than sitting behind it.
    """

    def __or__(self, other) -> Condition:
        return _combine(AnyOf, self, other)

    def __and__(self, other) -> Condition:
        return _combine(AllOf, self, other)

    def __invert__(self) -> Condition:
        return Not(self)

    def matches(self, held: Iterable) -> bool:
        """A bare control is satisfied by being in the set."""
        return self in held


#: Anything a condition can be built from: one control, or a composition.
Term: TypeAlias = "Composable | Condition"


@dataclass(frozen=True)
class AnyOf(Condition):
    """Satisfied when at least one operand is."""

    operands: tuple

    def __post_init__(self) -> None:
        object.__setattr__(self, "incomplete", any(map(_incomplete, self.operands)))

    def matches(self, held: Iterable) -> bool:
        _require_resolved(self)
        return any(matches(operand, held) for operand in self.operands)

    def __repr__(self) -> str:
        return " | ".join(repr(operand) for operand in self.operands)


@dataclass(frozen=True)
class AllOf(Condition):
    """Satisfied when every operand is."""

    operands: tuple

    def __post_init__(self) -> None:
        object.__setattr__(self, "incomplete", any(map(_incomplete, self.operands)))

    def matches(self, held: Iterable) -> bool:
        _require_resolved(self)
        return all(matches(operand, held) for operand in self.operands)

    def __repr__(self) -> str:
        return " & ".join(repr(operand) for operand in self.operands)


@dataclass(frozen=True)
class Not(Condition):
    """Satisfied when its operand is not.

    **True at rest, which nothing else here is.** Every positive condition is
    false when nobody is touching anything; a negation is true, and stays true
    forever unless composed with something that is not. That is why it earns
    its place inside an expression -- `jump & ~crouch` -- rather than alone.
    """

    operand: Term

    def __post_init__(self) -> None:
        object.__setattr__(self, "incomplete", _incomplete(self.operand))

    def matches(self, held: Iterable) -> bool:
        _require_resolved(self)
        return not matches(self.operand, held)

    def __repr__(self) -> str:
        return f"~{self.operand!r}"


@dataclass(frozen=True)
class Unresolved(Condition):
    """A term whose meaning only a device can supply.

    `Button.ACCEPT` is the case this exists for: which physical button means
    confirm is a fact about a pad's FAMILY, and a question is written before
    anybody has picked a pad up. It carries a name rather than a value, and
    `resolve` is what turns it into one.
    """

    name: str
    incomplete = True

    def matches(self, held: Iterable) -> bool:
        raise RuntimeError(
            f"{self.name} has no meaning until it is resolved against a "
            "device; call resolve() first"
        )

    def __repr__(self) -> str:
        return self.name


def _incomplete(term: Term) -> bool:
    return isinstance(term, Condition) and term.incomplete


def _require_resolved(condition: Condition) -> None:
    if condition.incomplete:
        raise RuntimeError(
            f"{condition!r} still holds a term that only a device can give "
            "meaning to; call resolve() first"
        )


def matches(condition: Term, held: Iterable) -> bool:
    """Is this condition satisfied by the controls in `held`?

    The one entry point, because a condition may be a bare control or a
    composition and a caller should not have to know which.
    """
    if isinstance(condition, (Condition, Composable)):
        return condition.matches(held)
    return condition in held


def resolve(condition: Term, substitutions: Mapping) -> Term:
    """Replace device-specific terms with what they mean on this device.

    Deliberately late and deliberately generic: this walks a tree and swaps
    terms it is given, and knows nothing about what any of them are for.
    """
    if not substitutions:
        return condition
    replacement = substitutions.get(condition)
    if replacement is not None:
        return replacement
    if isinstance(condition, (AnyOf, AllOf)):
        kind = type(condition)
        return kind(tuple(resolve(term, substitutions) for term in condition.operands))
    if isinstance(condition, Not):
        return Not(resolve(condition.operand, substitutions))
    return condition


def _combine(kind: type, left: Term, right: Term) -> Condition:
    """Flatten same-kind nestings and drop repeats, preserving order.

    `a | b | c` is one three-operand question rather than a tree, so that a
    condition's shape depends on what was asked and not on how it was typed.
    """
    operands: list[Term] = []
    for side in (left, right):
        parts = side.operands if isinstance(side, kind) else (side,)
        for part in parts:
            if part not in operands:
                operands.append(part)
    return kind(tuple(operands))
