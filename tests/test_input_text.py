"""The console line reducer: its own buffer, fed by the cut."""

import pytest

from blitspersecond.input import Motion, key

from blitspersecond.common import fps
from blitspersecond.input import InputState
from blitspersecond.input.kbm import KeyboardIngress
from blitspersecond.internal import EventBus


def _keyboard() -> tuple[EventBus, KeyboardIngress]:
    events = EventBus()
    return events, KeyboardIngress(events, fps.SIXTY)


def _type(events: EventBus, text: str) -> None:
    events.dispatch_event("on_text", text)


def _motion(events: EventBus, motion: int) -> None:
    events.dispatch_event("on_text_motion", motion)


def test_a_line_spells_what_was_typed_across_several_frames():
    events, keyboard = _keyboard()
    line = keyboard.input().until(key.RETURN)

    _type(events, "Rid")
    keyboard.cut(1)
    assert line.value == "Rid"

    _type(events, "dick")
    keyboard.cut(2)
    assert line.value == "Riddick"
    assert line.listening


def test_return_completes_the_line_without_typing_itself():
    events, keyboard = _keyboard()
    line = keyboard.input().until(key.RETURN)

    _type(events, "Jack")
    events.dispatch_event("on_key_press", key.RETURN, 0)
    _type(events, "\r")
    keyboard.cut(1)

    assert line.completed
    assert line.value == "Jack"


def test_backspacing_leaves_the_intended_word():
    events, keyboard = _keyboard()
    line = keyboard.input().until(key.RETURN)

    _type(events, "Riddik")
    _motion(events, Motion.BACKSPACE)
    _type(events, "ck")
    keyboard.cut(1)

    assert line.value == "Riddick"


def test_editing_happens_at_the_caret_not_the_end():
    events, keyboard = _keyboard()
    line = keyboard.input().until(key.RETURN)

    _type(events, "Riddck")
    _motion(events, Motion.LEFT)
    _motion(events, Motion.LEFT)
    _type(events, "i")
    keyboard.cut(1)

    assert line.value == "Riddick"
    assert line.caret == 5


def test_home_end_and_delete():
    events, keyboard = _keyboard()
    line = keyboard.input().until(key.RETURN)

    _type(events, "abc")
    _motion(events, Motion.BEGINNING_OF_LINE)
    _motion(events, Motion.DELETE)
    _motion(events, Motion.END_OF_LINE)
    _type(events, "d")
    keyboard.cut(1)

    assert line.value == "bcd"


def test_the_line_holds_still_for_the_whole_of_a_tick():
    events, keyboard = _keyboard()
    line = keyboard.input().until(key.RETURN)
    _type(events, "ab")
    keyboard.cut(1)

    # Typing arriving mid-frame is staged, not folded in.
    _type(events, "cd")
    assert line.value == "ab"

    keyboard.cut(2)
    assert line.value == "abcd"


def test_asking_a_new_question_interrupts_the_old_one():
    events, keyboard = _keyboard()
    first = keyboard.input().until(key.RETURN)
    _type(events, "abc")
    keyboard.cut(1)

    second = keyboard.input().until(key.RETURN)
    _type(events, "xyz")
    keyboard.cut(2)

    assert first.interrupted and first.reason == "REPLACED"
    assert first.value == "abc"
    assert second.value == "xyz"


def test_a_finished_line_stops_consuming():
    events, keyboard = _keyboard()
    line = keyboard.input().until(key.RETURN)
    _type(events, "done")
    events.dispatch_event("on_key_press", key.RETURN, 0)
    keyboard.cut(1)

    _type(events, "more")
    keyboard.cut(2)

    assert line.value == "done"
    assert line.state is InputState.COMPLETED


def test_a_discarded_line_keeps_nothing():
    events, keyboard = _keyboard()
    line = keyboard.input().until(key.RETURN)
    _type(events, "secret")
    keyboard.cut(1)

    line.discard()

    assert line.discarded
    assert line.value == ""


def test_a_line_outlives_the_ring_it_is_not_stored_in():
    events, keyboard = _keyboard()
    line = keyboard.input().until(key.RETURN)

    # Far longer than the thirty-window ring: a person taking their time.
    for frame in range(300):
        _type(events, "x")
        keyboard.cut(frame)

    assert len(line.value) == 300
    assert keyboard.stream.capacity == 30


def test_narrowing_twice_is_a_mistake_and_says_so():
    _, keyboard = _keyboard()
    line = keyboard.input().until(key.RETURN)
    with pytest.raises(RuntimeError):
        line.until(key.TAB)


def test_narrowing_a_finished_line_is_a_mistake():
    _, keyboard = _keyboard()
    line = keyboard.input()
    line.discard()
    with pytest.raises(RuntimeError):
        line.until(key.RETURN)


def test_narrowing_to_something_that_is_not_a_key_symbol_is_a_mistake():
    _, keyboard = _keyboard()
    with pytest.raises(TypeError):
        keyboard.input().until("return")
