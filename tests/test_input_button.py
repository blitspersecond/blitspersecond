"""The pad vocabulary, and the tripwire that keeps it closed.

`Button` is a ruling as much as a type: ten members, and the engine will never
add another. A doctrine nobody can trip over is a doctrine that gets quietly
edited, so the closure is asserted here -- adding a member fails this file and
sends whoever did it to read why.

See `docs/bps/PADS_PORT.md`.
"""

import pytest

from blitspersecond.input.condition import matches
from blitspersecond.input.pads import Button

#: Every button the engine reports. Not a fixture -- a specification.
SETTLED = (
    "FACE_SOUTH",
    "FACE_EAST",
    "FACE_WEST",
    "FACE_NORTH",
    "LB",
    "RB",
    "LT",
    "RT",
    "START",
    "SELECT",
)


def test_the_vocabulary_is_exactly_these_and_will_not_grow():
    assert tuple(button.name for button in Button) == SETTLED, (
        "The pad vocabulary is closed: it is the shape pads converged on, not "
        "a subset of what they offer. Before changing this, read "
        "docs/bps/PADS_PORT.md -- a new control is either where an existing "
        "one sits, in which case it maps to that one, or it is a vendor "
        "addition after the convergence, in which case it is dropped."
    )


@pytest.mark.parametrize(
    "absent, because",
    [
        ("L3", "a stick click is never pressed by player agency"),
        ("R3", "a stick click is never pressed by player agency"),
        ("DPAD_UP", "the d-pad is an axis pair, read as a direction"),
        ("TOUCHPAD", "added by one vendor after the convergence"),
        ("SHARE", "Xbox's sits in a new place; Sony's IS Select"),
        ("MUTE", "added by one vendor after the convergence"),
        ("GUIDE", "it is the player's -- it turns their pad off"),
        ("HOME", "it is the player's -- it turns their pad off"),
        ("PS", "it is the player's -- it turns their pad off"),
        ("CAPTURE", "added by one vendor after the convergence"),
    ],
)
def test_what_is_deliberately_not_a_button(absent, because):
    # Each of these is a real control on a real pad, and each is absent for
    # its own reason. The parametrisation IS the record: a name here is a
    # question already answered, not one nobody thought of.
    assert not hasattr(Button, absent), because


def test_accept_exists_but_is_not_an_eleventh_button():
    # "Whatever this pad calls confirm" is not a position, and every other
    # name in the vocabulary is one. It is a predicate with nothing in it but
    # the flag, so `Button.START | Button.ACCEPT` reads as one phrase and
    # resolves per pad when there is a pad to resolve against.
    from blitspersecond.input.condition import Unresolved, matches, resolve

    assert isinstance(Button.ACCEPT, Unresolved)
    assert Button.ACCEPT not in list(Button)
    assert len(Button) == len(SETTLED)
    # The same condition, two families, two answers.
    condition = Button.START | Button.ACCEPT
    xbox = resolve(condition, {Button.ACCEPT: Button.FACE_SOUTH})
    nintendo = resolve(condition, {Button.ACCEPT: Button.FACE_EAST})
    assert matches(xbox, {Button.FACE_SOUTH}) and not matches(xbox, {Button.FACE_EAST})
    assert matches(nintendo, {Button.FACE_EAST}) and not matches(nintendo, {Button.FACE_SOUTH})
    assert matches(xbox, {Button.START}) and matches(nintendo, {Button.START})


def test_an_unresolved_term_refuses_to_answer_rather_than_saying_no():
    # ACCEPT with no pad behind it has no meaning. Answering False would be a
    # question that silently never matches; raising is the visible failure.
    with pytest.raises(RuntimeError, match="resolve"):
        matches(Button.START | Button.ACCEPT, {Button.START})


def test_a_button_is_not_an_integer_in_disguise():
    # Deliberately not an IntEnum. A raw transport code is not a Button and
    # must be translated at ingress, so the two must not compare equal by
    # accident -- BTN_SOUTH is 0x130 and FACE_SOUTH is not.
    assert Button.FACE_SOUTH != 1
    assert not isinstance(Button.FACE_SOUTH, int)


def test_every_button_is_distinct_and_hashable():
    # The held-set and the edge sets are sets of these.
    assert len(set(Button)) == len(SETTLED)
