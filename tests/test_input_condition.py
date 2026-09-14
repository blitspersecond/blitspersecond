"""The shared condition algebra: `|`, `&`, `~`, and what satisfies them.

The tripwire in here is `test_operators_shadow_integer_arithmetic`. Two of the
three vocabularies are integer-valued, so an operator nobody defines does not
raise -- it quietly does arithmetic and lands on another real member. That is
the failure this module exists to remove, and it is invisible without a test.
"""

from enum import Enum, IntEnum

import pytest

from blitspersecond.input.condition import (
    AllOf,
    AnyOf,
    Composable,
    Not,
    Unresolved,
    matches,
    resolve,
)


class Face(Composable, Enum):
    SOUTH = 1
    EAST = 2
    WEST = 3


class Way(Composable, IntEnum):
    """Stands in for `Direction` -- numpad positions, and int-valued."""

    DOWN = 2
    DOWN_RIGHT = 3
    NEUTRAL = 5


MAYBE = Unresolved("MAYBE")


def test_operators_shadow_integer_arithmetic():
    # Undefended, `DOWN | DOWN_RIGHT` is `3` and `DOWN & DOWN_RIGHT` is `2` --
    # both real members, so nothing downstream could tell it went wrong.
    assert isinstance(Way.DOWN | Way.DOWN_RIGHT, AnyOf)
    assert isinstance(Way.DOWN & Way.DOWN_RIGHT, AllOf)
    assert isinstance(~Way.NEUTRAL, Not)


def test_the_integer_identity_survives_the_operators():
    # 236 is how a quarter-circle is printed in every move list, and a
    # recogniser should be able to be written against the notation.
    assert Way.DOWN_RIGHT == 3
    assert int(Way.DOWN) == 2


def test_composition_flattens_and_keeps_first_order():
    # A question's shape should depend on what was asked, not on how it was
    # typed, so `a | b | c` is one three-operand question and not a tree.
    assert (Face.SOUTH | Face.EAST | Face.WEST).operands == (
        Face.SOUTH,
        Face.EAST,
        Face.WEST,
    )
    assert (Face.SOUTH | Face.SOUTH).operands == (Face.SOUTH,)
    assert (Face.SOUTH & Face.EAST & Face.SOUTH).operands == (Face.SOUTH, Face.EAST)


@pytest.mark.parametrize(
    "condition, held, satisfied",
    [
        (Face.SOUTH, {Face.SOUTH}, True),
        (Face.SOUTH, {Face.EAST}, False),
        (Face.SOUTH | Face.EAST, {Face.EAST}, True),
        (Face.SOUTH | Face.EAST, {Face.WEST}, False),
        (Face.SOUTH & Face.EAST, {Face.SOUTH, Face.EAST}, True),
        (Face.SOUTH & Face.EAST, {Face.SOUTH}, False),
        (Face.SOUTH & ~Face.EAST, {Face.SOUTH}, True),
        (Face.SOUTH & ~Face.EAST, {Face.SOUTH, Face.EAST}, False),
        (~Way.NEUTRAL, {Way.DOWN}, True),
        ((Face.SOUTH | Face.EAST) & ~Face.WEST, {Face.EAST}, True),
    ],
)
def test_what_satisfies_a_condition(condition, held, satisfied):
    assert matches(condition, held) is satisfied


def test_a_negation_is_true_at_rest_and_nothing_else_is():
    # The one condition that holds when nobody is touching anything, which is
    # why it earns its place inside an expression rather than alone.
    assert matches(~Face.SOUTH, set()) is True
    assert matches(Face.SOUTH, set()) is False
    assert matches(Face.SOUTH | Face.EAST, set()) is False


def test_an_unresolved_term_refuses_even_from_a_branch_nobody_reaches():
    # `any()` short-circuits, so a satisfied first operand would hide the
    # unresolved second one and the mistake would surface or not depending on
    # what the player pressed. The flag is worked out when the node is built.
    condition = Face.SOUTH | MAYBE
    assert condition.incomplete
    with pytest.raises(RuntimeError, match="resolve"):
        matches(condition, {Face.SOUTH})


def test_resolving_substitutes_through_nesting_and_completes_it():
    condition = (Face.SOUTH | MAYBE) & ~MAYBE
    assert condition.incomplete
    resolved = resolve(condition, {MAYBE: Face.WEST})
    assert not resolved.incomplete
    assert matches(resolved, {Face.WEST}) is False   # ~WEST fails
    assert matches(resolved, {Face.SOUTH}) is True


def test_resolving_a_condition_that_needs_nothing_hands_it_straight_back():
    condition = Face.SOUTH | Face.EAST
    assert resolve(condition, {}) is condition
