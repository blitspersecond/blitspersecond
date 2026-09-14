"""`held`, `pressed` and `released` -- the trigger boundary, one interval.

    pressed   it crossed the boundary in this interval
    held      it crossed and has not crossed back
    released  it crossed back in this interval

The same three sentences apply to a composed question, which is where they
stop being obvious: an EXPRESSION has a boundary too, and crosses it when the
last thing needed to satisfy it lands.

One implementation serves every device, so these drive it through a keyboard
window and a pad window and assert they answer alike.
"""

from blitspersecond.common import fps
from blitspersecond.input import key
from blitspersecond.input.kbm import KeyboardIngress, KeyEvent, KeyFact, KeyScan
from blitspersecond.input.pads import Button, Direction, PadEvent, PadFact, PadScan
from blitspersecond.input.stream import InputWindow
from blitspersecond.internal import EventBus


def _press(symbol, after):
    return KeyFact(KeyEvent.PRESS, int(symbol), 0, frozenset(int(k) for k in after))


def _release(symbol, before):
    return KeyFact(
        KeyEvent.RELEASE, int(symbol), 0, frozenset(int(k) for k in before)
    )


def _window(facts, held=(), pressed=(), released=()):
    scan = KeyScan(
        frozenset(int(k) for k in held),
        frozenset(int(k) for k in pressed),
        frozenset(int(k) for k in released),
    )
    return InputWindow(1, scan, tuple(facts))


# -- one control ----------------------------------------------------------


def test_a_bare_control_is_answered_from_the_scan():
    window = _window(
        [_press(key.SPACE, [key.SPACE])], held=[key.SPACE], pressed=[key.SPACE]
    )
    assert window.pressed(key.SPACE)
    assert window.held(key.SPACE)
    assert not window.released(key.SPACE)


def test_held_outlives_the_facts_that_made_it_true():
    # The press left the ring thirty thousand frames ago. State does not
    # expire, so a window with no facts at all still reports it held.
    window = _window([], held=[key.SPACE])
    assert window.held(key.SPACE)
    assert not window.pressed(key.SPACE)


def test_a_sub_tick_tap_reports_both_edges():
    # Down and up between two cuts. Sampling state once a frame would lose it
    # entirely; the interval is replayed rather than sampled.
    window = _window(
        [_press(key.SPACE, [key.SPACE]), _release(key.SPACE, [key.SPACE])],
        held=[],
        pressed=[key.SPACE],
        released=[key.SPACE],
    )
    assert window.pressed(key.SPACE)
    assert window.released(key.SPACE)
    assert not window.held(key.SPACE)


# -- a composed question ---------------------------------------------------


def test_a_chord_is_pressed_by_whatever_completes_it():
    # LSHIFT was already down; SPACE is what makes the expression true.
    window = _window(
        [_press(key.SPACE, [key.LSHIFT, key.SPACE])],
        held=[key.LSHIFT, key.SPACE],
        pressed=[key.SPACE],
    )
    assert window.pressed(key.LSHIFT & key.SPACE)
    assert window.held(key.LSHIFT & key.SPACE)


def test_an_expression_already_satisfied_does_not_cross_again():
    # LSHIFT is held throughout, so `SHIFT | SPACE` was true before SPACE
    # landed and did not have a boundary to cross. It is held, not pressed.
    window = _window(
        [_press(key.SPACE, [key.LSHIFT, key.SPACE])],
        held=[key.LSHIFT, key.SPACE],
        pressed=[key.SPACE],
    )
    assert window.held(key.LSHIFT | key.SPACE)
    assert not window.pressed(key.LSHIFT | key.SPACE)


def test_releasing_a_negated_control_is_what_presses_the_expression():
    # THE case the transition walk exists for. `SPACE & ~LALT` becomes true at
    # the moment LALT is released -- and a release carries the held-set its own
    # control is still IN, so evaluating that set alone reports false at
    # exactly the transition that satisfies the expression.
    window = _window(
        [_release(key.LALT, [key.SPACE, key.LALT])],
        held=[key.SPACE],
        released=[key.LALT],
    )
    assert window.pressed(key.SPACE & ~key.LALT)
    assert window.held(key.SPACE & ~key.LALT)


def test_pressing_a_negated_control_releases_the_expression():
    window = _window(
        [_press(key.LALT, [key.SPACE, key.LALT])],
        held=[key.SPACE, key.LALT],
        pressed=[key.LALT],
    )
    assert window.released(key.SPACE & ~key.LALT)
    assert not window.held(key.SPACE & ~key.LALT)


def test_a_chord_made_and_broken_inside_one_interval_reports_both():
    window = _window(
        [
            _press(key.LCTRL, [key.LCTRL]),
            _press(key.S, [key.LCTRL, key.S]),
            _release(key.S, [key.LCTRL, key.S]),
        ],
        held=[key.LCTRL],
        pressed=[key.LCTRL, key.S],
        released=[key.S],
    )
    assert window.pressed(key.LCTRL & key.S)
    assert window.released(key.LCTRL & key.S)
    assert not window.held(key.LCTRL & key.S)


def test_the_sided_alias_answers_to_either_hand():
    window = _window([_press(key.RCTRL, [key.RCTRL])], held=[key.RCTRL], pressed=[key.RCTRL])
    assert window.pressed(key.CTRL)
    assert window.held(key.CTRL)


# -- the same code, a different device -------------------------------------


def test_a_pad_window_answers_the_same_way():
    scan = PadScan(
        frozenset({Button.FACE_SOUTH}),
        frozenset({Button.FACE_SOUTH}),
        frozenset(),
        {},
        Direction.NEUTRAL,
    )
    fact = PadFact(
        PadEvent.PRESS, Button.FACE_SOUTH, None, 0.0, None,
        frozenset({Button.FACE_SOUTH, Button.LB}),
    )
    window = InputWindow(1, scan, (fact,))
    assert window.pressed(Button.FACE_SOUTH)
    assert window.pressed(Button.LB & Button.FACE_SOUTH)
    assert not window.pressed(Button.LB)


# -- the device forwards ---------------------------------------------------


def test_a_device_answers_false_before_anything_has_happened():
    # Not a guard: nothing is held and nothing crossed a boundary, because
    # nothing has happened yet.
    keyboard = KeyboardIngress(EventBus(), fps.SIXTY)
    assert keyboard.stream.latest is None
    assert not keyboard.held(key.SPACE)
    assert not keyboard.pressed(key.SPACE)
    assert not keyboard.released(key.SPACE)


def test_a_device_asks_its_latest_window():
    events = EventBus()
    keyboard = KeyboardIngress(events, fps.SIXTY)
    events.dispatch_event("on_key_press", key.LCTRL, 0)
    events.dispatch_event("on_key_press", key.S, 0)
    keyboard.cut(1)

    assert keyboard.pressed(key.LCTRL & key.S)
    assert keyboard.held(key.S)
    assert not keyboard.held(key.SPACE)
