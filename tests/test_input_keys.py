"""The keyboard's vocabularies, and the property the whole design rests on.

`Key` exists so that symbols carry the condition operators. It must add
NOTHING else -- in particular it must not become a second numbering, because
then ingress would owe a translation table and the keyboard's "normalisation
is the identity function" ruling would quietly stop being true.

`test_every_symbol_is_pyglets_own_value` is the tripwire for that. If a value
drifts, a raw symbol off the transport stops answering to the name a game
wrote, and nothing else in the suite would say so.
"""

from enum import IntEnum, IntFlag

import pytest
from pyglet.window import key as pyglet_key

from blitspersecond.input import Modifier, Motion, key
from blitspersecond.input.condition import AllOf, AnyOf, Condition, Not, matches


def test_every_symbol_is_pyglets_own_value():
    for name, symbol in key.__members__.items():
        assert int(symbol) == getattr(pyglet_key, name), name


def test_every_motion_is_pyglets_own_value():
    for name, motion in Motion.__members__.items():
        assert int(motion) == getattr(pyglet_key, f"MOTION_{name}"), name


def test_a_raw_symbol_off_the_transport_answers_to_our_name():
    # The reason no table is owed at ingress: pyglet hands over plain ints, a
    # scan stores them, and an IntEnum member hashes equal to its value -- so
    # membership works across the boundary untranslated.
    held = {pyglet_key.LEFT, pyglet_key.LCTRL}
    assert matches(key.LEFT, held)
    assert matches(key.LEFT | key.RIGHT, held)
    assert matches(key.LCTRL & key.LEFT, held)
    assert not matches(key.RIGHT, held)


def test_operators_shadow_integer_arithmetic():
    # Undefended, `LCTRL & LALT` is 65505 -- which is LSHIFT, a real symbol,
    # so nothing downstream could tell the question went wrong.
    assert isinstance(key.LEFT | key.RIGHT, AnyOf)
    assert isinstance(key.LCTRL & key.LALT, AllOf)
    assert isinstance(~key.SPACE, Not)
    assert (key.LCTRL & key.LALT) != pyglet_key.LSHIFT


def test_sided_modifiers_are_predicates_over_both_sides():
    # Nobody means "the left one specifically", so this is the spelling to
    # reach for -- and it is a predicate, not a member, because a name that
    # stands for two controls is not itself a control.
    assert key.CTRL not in list(key)
    assert matches(key.CTRL, {pyglet_key.LCTRL})
    assert matches(key.CTRL, {pyglet_key.RCTRL})
    assert not matches(key.CTRL, {pyglet_key.LALT})
    assert matches(key.SHIFT & ~key.CTRL, {pyglet_key.RSHIFT})


@pytest.mark.parametrize(
    "alias, canonical",
    [("ENTER", "RETURN"), ("QUOTELEFT", "GRAVE"), ("POUND", "HASH")],
)
def test_pygets_duplicate_names_collapse_to_one_member(alias, canonical):
    assert key[alias] is key[canonical]


def test_modifiers_are_a_bitfield_and_stay_one():
    # `|` on a mask is union and is the arithmetic being RIGHT. Making these
    # composable would break the one place the integer behaviour is wanted.
    assert isinstance(Modifier.CTRL | Modifier.SHIFT, IntFlag)
    assert Modifier.SHIFT in (Modifier.CTRL | Modifier.SHIFT)
    assert int(Modifier.SHIFT) == pyglet_key.MOD_SHIFT


def test_a_motion_is_not_something_to_ask_about():
    """The nastiest collision in the file, and why `Motion` is not composable.

    Motions arrive as facts and never enter a scan, so a motion predicate
    could only ever answer False -- except that eight motions share a value
    with an arrow or editing KEY, so it would sometimes answer True about
    entirely the wrong thing.
    """
    assert int(Motion.LEFT) == int(key.LEFT)
    colliding = {m.name for m in Motion if int(m) in {int(k) for k in key}}
    assert colliding == {
        "BACKSPACE", "DELETE", "DOWN", "LEFT",
        "NEXT_PAGE", "PREVIOUS_PAGE", "RIGHT", "UP",
    }

    # Not composable: no operators, so no predicate can be built from one.
    assert not isinstance(Motion.LEFT | Motion.RIGHT, Condition)

    # And asking directly is refused rather than answered against a key.
    with pytest.raises(TypeError, match="queryable vocabulary"):
        matches(Motion.LEFT, {int(key.LEFT)})


def test_a_bare_number_is_refused():
    # A raw symbol is what a scan HOLDS, never what a question is built from.
    with pytest.raises(TypeError, match="queryable vocabulary"):
        matches(pyglet_key.LEFT, {pyglet_key.LEFT})
