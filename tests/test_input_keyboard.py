"""Keyboard ingress: ordering, edges, the held set, and the clear."""

from blitspersecond.input import Modifier, Motion, key

from blitspersecond.common import fps
from blitspersecond.input.kbm import KeyEvent, KeyboardIngress
from blitspersecond.internal import EventBus


def _keyboard(rate: fps = fps.SIXTY) -> tuple[EventBus, KeyboardIngress]:
    events = EventBus()
    return events, KeyboardIngress(events, rate)


def _kinds(window) -> list[tuple[KeyEvent, object]]:
    return [(fact.kind, fact.value) for fact in window.facts]


def test_a_press_and_its_text_settle_in_arrival_order():
    events, keyboard = _keyboard()
    events.dispatch_event("on_key_press", key.S, Modifier.SHIFT)
    events.dispatch_event("on_text", "S")

    window = keyboard.cut(6)

    assert window.frame == 6
    assert window.scan.held == frozenset({key.S})
    assert window.scan.pressed == frozenset({key.S})
    assert window.scan.released == frozenset()
    assert _kinds(window) == [(KeyEvent.PRESS, key.S), (KeyEvent.TEXT, "S")]


def test_a_settled_window_does_not_change_when_more_arrives():
    events, keyboard = _keyboard()
    events.dispatch_event("on_key_press", key.S, 0)
    window = keyboard.cut(1)

    events.dispatch_event("on_key_release", key.S, 0)

    assert window.scan.held == frozenset({key.S})
    later = keyboard.cut(2)
    assert later.scan.held == frozenset()
    assert later.scan.released == frozenset({key.S})


def test_a_sub_tick_roll_keeps_its_order_and_its_final_state():
    events, keyboard = _keyboard()
    events.dispatch_event("on_key_press", key.S, 0)
    events.dispatch_event("on_key_press", key.D, 0)
    events.dispatch_event("on_key_release", key.S, 0)

    window = keyboard.cut(1)

    assert _kinds(window) == [
        (KeyEvent.PRESS, key.S),
        (KeyEvent.PRESS, key.D),
        (KeyEvent.RELEASE, key.S),
    ]
    assert window.scan.held == frozenset({key.D})
    assert window.scan.pressed == frozenset({key.S, key.D})
    assert window.scan.released == frozenset({key.S})


def test_a_transition_carries_the_held_set_its_own_key_is_in():
    events, keyboard = _keyboard()
    events.dispatch_event("on_key_press", key.LCTRL, 0)
    events.dispatch_event("on_key_press", key.S, 0)
    events.dispatch_event("on_key_release", key.S, 0)

    press_ctrl, press_s, release_s = keyboard.cut(1).facts

    # A press carries the set AFTER its key was added.
    assert press_ctrl.held == frozenset({key.LCTRL})
    assert press_s.held == frozenset({key.LCTRL, key.S})
    # A release carries the set BEFORE its key was removed, so a chord's
    # release edge is still answerable from the fact alone.
    assert release_s.held == frozenset({key.LCTRL, key.S})


def test_a_repeat_is_a_fact_but_never_a_fresh_edge():
    events, keyboard = _keyboard()
    events.dispatch_event("on_key_press", key.A, 0)
    events.dispatch_event("on_key_press", key.A, 0)

    window = keyboard.cut(1)

    assert [fact.kind for fact in window.facts] == [
        KeyEvent.PRESS,
        KeyEvent.REPEAT,
    ]
    assert window.scan.pressed == frozenset({key.A})


def test_a_key_stays_held_long_after_its_press_left_the_ring():
    events, keyboard = _keyboard(fps.FIFTEEN)
    events.dispatch_event("on_key_press", key.W, 0)
    keyboard.cut(0)

    for frame in range(1, 400):
        keyboard.cut(frame)

    latest = keyboard.stream.latest
    assert latest is not None
    assert latest.facts == ()          # the press cycled out long ago
    assert latest.scan.held == frozenset({key.W})   # the state did not
    assert keyboard.stream.capacity == 8


def test_focus_loss_releases_everything_and_says_it_was_not_the_player():
    events, keyboard = _keyboard()
    events.dispatch_event("on_key_press", key.D, 0)
    events.dispatch_event("on_key_press", key.A, 0)
    keyboard.cut(1)
    events.dispatch_event("on_deactivate")

    window = keyboard.cut(2)

    assert window.scan.held == frozenset()
    assert window.scan.released == frozenset({key.A, key.D})
    assert window.scan.cleared is True
    assert _kinds(window) == [
        (KeyEvent.RELEASE, key.A),
        (KeyEvent.RELEASE, key.D),
    ]


def test_the_clear_unwinds_one_key_at_a_time():
    events, keyboard = _keyboard()
    events.dispatch_event("on_key_press", key.A, 0)
    events.dispatch_event("on_key_press", key.B, 0)
    events.dispatch_event("on_deactivate")

    first, second = [
        fact for fact in keyboard.cut(1).facts
        if fact.kind is KeyEvent.RELEASE
    ]

    assert first.held == frozenset({key.A, key.B})
    assert second.held == frozenset({key.B})


def test_focus_loss_takes_its_place_in_arrival_order():
    events, keyboard = _keyboard()
    events.dispatch_event("on_key_press", key.J, 0)
    events.dispatch_event("on_deactivate")

    window = keyboard.cut(1)

    # The press that landed just before focus was lost still edges, and only
    # then is released by the clear.
    assert _kinds(window) == [
        (KeyEvent.PRESS, key.J),
        (KeyEvent.RELEASE, key.J),
    ]
    assert window.scan.pressed == frozenset({key.J})
    assert window.scan.released == frozenset({key.J})


def test_text_is_split_per_character_and_keeps_order_against_motion():
    events, keyboard = _keyboard()
    events.dispatch_event("on_text", "name\r")
    events.dispatch_event("on_text_motion", Motion.LEFT)

    assert _kinds(keyboard.cut(1)) == [
        (KeyEvent.TEXT, "n"),
        (KeyEvent.TEXT, "a"),
        (KeyEvent.TEXT, "m"),
        (KeyEvent.TEXT, "e"),
        (KeyEvent.TEXT, "\r"),
        (KeyEvent.MOTION, Motion.LEFT),
    ]


def test_every_frame_cuts_even_with_nothing_to_report():
    events, keyboard = _keyboard()
    for frame in range(4):
        keyboard.cut(frame)

    assert len(keyboard.stream) == 4
    assert [w.frame for w in keyboard.stream.all] == [0, 1, 2, 3]
    assert all(w.facts == () for w in keyboard.stream.all)


def test_a_flush_drops_staged_input_and_does_not_leave_keys_stuck():
    events, keyboard = _keyboard()
    events.dispatch_event("on_key_press", key.W, 0)
    keyboard.cut(1)

    # Suspended: the window keeps pumping, the engine does not tick.
    for _ in range(50):
        events.dispatch_event("on_text", "x")
    events.dispatch_event("on_key_press", key.SPACE, 0)
    keyboard.flush()

    window = keyboard.cut(2)

    assert not any(fact.kind is KeyEvent.TEXT for fact in window.facts)
    assert window.scan.held == frozenset()
    assert window.scan.released == frozenset({key.W})
    assert window.scan.cleared is True


def test_staging_is_bounded_and_says_how_much_it_lost():
    events, keyboard = _keyboard()
    for _ in range(600):
        events.dispatch_event("on_text", "x")

    window = keyboard.cut(1)

    assert len(window.facts) == 512
    assert keyboard.dropped == 88


def test_an_ingress_handler_never_swallows_an_event():
    # A truthy return is EVENT_HANDLED, which would stop the event reaching
    # Lifecycle and any other device on the same bus.
    #
    # The observer is pushed BEFORE the ingress deliberately. Pyglet
    # dispatches most-recently-pushed first, so an observer pushed after the
    # ingress runs first and sees the event whether or not the ingress
    # swallowed it -- which is what this test did until 2026-09-11, and it
    # would have passed on an engine where the keyboard ate every key.
    # Pushed first, it sits under the ingress and is reached only if the
    # ingress falls through.
    events = EventBus()
    seen = []
    events.push_handlers(on_key_press=lambda symbol, mods: seen.append(symbol))
    keyboard = KeyboardIngress(events, fps.SIXTY)

    events.dispatch_event("on_key_press", key.Q, 0)

    assert seen == [key.Q]
    assert keyboard.cut(1).scan.pressed == frozenset({key.Q})


def test_a_release_after_a_clear_does_not_report_itself_twice():
    # Hold X, lose focus, come back and let go. The clear already released X;
    # the real release must not arrive as a second fact carrying a held-set
    # that its own key is not in.
    events, keyboard = _keyboard()
    events.dispatch_event("on_key_press", key.X, 0)
    keyboard.cut(0)
    events.dispatch_event("on_deactivate")
    cleared = keyboard.cut(1)
    events.dispatch_event("on_key_release", key.X, 0)
    after = keyboard.cut(2)

    assert [(f.kind, f.value) for f in cleared.facts] == [(KeyEvent.RELEASE, key.X)]
    assert cleared.scan.released == frozenset({key.X})
    assert after.facts == ()
    assert after.scan.released == frozenset()


def test_every_transition_carries_the_held_set_its_own_key_is_in():
    events, keyboard = _keyboard()
    for symbol in (key.A, key.B):
        events.dispatch_event("on_key_press", symbol, 0)
    events.dispatch_event("on_key_release", key.A, 0)
    events.dispatch_event("on_key_release", key.A, 0)   # a second, unmatched
    events.dispatch_event("on_deactivate")
    window = keyboard.cut(0)

    for fact in window.facts:
        if fact.kind in (KeyEvent.PRESS, KeyEvent.RELEASE):
            assert fact.value in fact.held, fact


def test_what_was_lost_is_this_frames_loss_not_a_session_total():
    # It is published as a counter every frame, and a counter wants what
    # happened in THIS frame.
    events, keyboard = _keyboard()
    for _ in range(600):
        events.dispatch_event("on_text", "x")
    keyboard.cut(0)
    assert keyboard.dropped == 88

    keyboard.cut(1)
    assert keyboard.dropped == 0
