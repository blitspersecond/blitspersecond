from pyglet.window import key

from blitspersecond.common import fps
from blitspersecond.internal import EventBus
from blitspersecond.input.kbm import KeyboardHandler


def _keyboard(rate: fps = fps.SIXTY) -> tuple[EventBus, KeyboardHandler]:
    events = EventBus()
    return events, KeyboardHandler(events, rate)


def test_capture_settles_the_preceding_interval_before_the_game_frame():
    events, keyboard = _keyboard()
    events.dispatch_event("on_key_press", key.S, key.MOD_SHIFT)
    events.dispatch_event("on_text", "S")

    settled = keyboard.capture(7)

    assert settled.frame == 6
    assert settled.keys.held == frozenset({key.S})
    assert settled.keys.pressed == frozenset({key.S})
    assert settled.keys.released == frozenset()
    assert [(fact.kind, fact.value) for fact in settled.facts] == [
        ("press", key.S),
        ("text", "S"),
    ]
    assert list(keyboard.history.after(None)) == [settled]


def test_a_settled_frame_cannot_change_mid_game_frame():
    events, keyboard = _keyboard()
    events.dispatch_event("on_key_press", key.S, 0)
    settled = keyboard.capture(1)

    events.dispatch_event("on_key_release", key.S, 0)

    assert settled.keys.held == frozenset({key.S})
    next_settled = keyboard.capture(2)
    assert next_settled.keys.held == frozenset()
    assert next_settled.keys.released == frozenset({key.S})


def test_sub_tick_direction_roll_preserves_order_and_final_scan():
    events, keyboard = _keyboard()
    events.dispatch_event("on_key_press", key.S, 0)
    events.dispatch_event("on_key_press", key.D, 0)
    events.dispatch_event("on_key_release", key.S, 0)

    settled = keyboard.capture(1)

    assert [(fact.kind, fact.value) for fact in settled.facts] == [
        ("press", key.S),
        ("press", key.D),
        ("release", key.S),
    ]
    assert settled.facts[1].held == frozenset({key.S, key.D})
    assert settled.keys.held == frozenset({key.D})
    assert settled.keys.pressed == frozenset({key.S, key.D})
    assert settled.keys.released == frozenset({key.S})


def test_repeat_is_a_fact_but_not_a_fresh_pressed_edge():
    events, keyboard = _keyboard()
    events.dispatch_event("on_key_press", key.A, 0)
    events.dispatch_event("on_key_press", key.A, 0)

    settled = keyboard.capture(1)

    assert [fact.kind for fact in settled.facts] == ["press", "repeat"]
    assert settled.keys.pressed == frozenset({key.A})


def test_deactivate_releases_every_held_key_on_the_next_boundary():
    events, keyboard = _keyboard()
    events.dispatch_event("on_key_press", key.D, 0)
    events.dispatch_event("on_key_press", key.A, 0)
    keyboard.capture(1)
    events.dispatch_event("on_deactivate")

    settled = keyboard.capture(2)

    assert settled.keys.held == frozenset()
    assert settled.keys.released == frozenset({key.A, key.D})
    assert [(fact.kind, fact.value) for fact in settled.facts] == [
        ("release", key.A),
        ("release", key.D),
        ("deactivate", None),
    ]


def test_text_is_split_and_motion_keeps_arrival_order():
    events, keyboard = _keyboard()
    events.dispatch_event("on_text", "name\r")
    events.dispatch_event("on_text_motion", key.MOTION_LEFT)

    settled = keyboard.capture(1)

    assert [(fact.kind, fact.value) for fact in settled.facts] == [
        ("text", "n"),
        ("text", "a"),
        ("text", "m"),
        ("text", "e"),
        ("text", "\r"),
        ("motion", key.MOTION_LEFT),
    ]
