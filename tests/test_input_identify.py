"""Which pad is this person using -- answered by them using it.

The wiggle-a-pad mode. Discovery is faked; what is exercised is the question.
"""

import pytest

from blitspersecond.common import fps
from blitspersecond.input import InputState
from blitspersecond.input.kbm import TextInput
from blitspersecond.input.pads import Button, Identification
from blitspersecond.input.pads.evdev import (
    ABS_HAT0X,
    ABS_X,
    BTN_SOUTH,
    BTN_START,
    EV_ABS,
    EV_KEY,
    Report,
)
from blitspersecond.input.pads import pads as module

from tests.test_input_pads import Reader, device  # the same fake machine


@pytest.fixture
def world(monkeypatch):
    state = {"present": {}, "readers": {}}

    def fake_discover():
        return dict(state["present"])

    def fake_open(dev):
        reader = Reader()
        state["readers"][dev.fingerprint] = reader
        return reader

    class Transport:
        discover = staticmethod(fake_discover)
        open_pad = staticmethod(fake_open)

        def close(self):
            pass

    monkeypatch.setattr(
        module, "_transport_for_platform", lambda _logger: Transport()
    )
    return state


def machine(world, *fingerprints):
    for fingerprint in fingerprints:
        world["present"][fingerprint] = device(fingerprint)
    pads = module.Pads(fps.SIXTY, refresh_frames=1)
    pads.cut(0)
    return pads, world["readers"]


def press(reader, code=BTN_SOUTH):
    reader.arrive(Report(((EV_KEY, code, 1),)))


def test_any_deliberate_use_answers_an_unnarrowed_question(world):
    pads, readers = machine(world, "bt:aa")
    asking = pads.identify()
    assert asking.listening

    press(readers["bt:aa"])
    pads.cut(1)

    assert asking.completed
    assert asking.value.fingerprint == "bt:aa"


def test_nothing_happening_leaves_it_listening(world):
    pads, readers = machine(world, "bt:aa")
    asking = pads.identify()
    for frame in range(1, 10):
        pads.cut(frame)
    assert asking.listening
    assert not asking.completed


def test_narrowing_waits_for_the_button_that_was_asked_for(world):
    # "Press Start to begin", with two pads on the table and one person ready.
    pads, readers = machine(world, "bt:aa", "bt:bb")
    asking = pads.identify().until(Button.START)

    press(readers["bt:aa"], BTN_SOUTH)     # the wrong button
    pads.cut(1)
    assert asking.listening

    press(readers["bt:bb"], BTN_START)
    pads.cut(2)
    assert asking.completed
    assert asking.value.fingerprint == "bt:bb"


def test_a_narrowed_question_ignores_sticks_and_the_dpad(world):
    pads, readers = machine(world, "bt:aa")
    asking = pads.identify().until(Button.START)
    readers["bt:aa"].arrive(
        Report(((EV_ABS, ABS_X, -30000),)),
        Report(((EV_ABS, ABS_HAT0X, 1),)),
    )
    pads.cut(1)
    assert asking.listening


def test_a_drifting_stick_does_not_answer_an_open_question(world):
    # The pad on this desk rests around 1050 and wanders. Answering with the
    # wrong pad is worse than making somebody push.
    pads, readers = machine(world, "bt:aa")
    asking = pads.identify()
    readers["bt:aa"].arrive(Report(((EV_ABS, ABS_X, 1050),)))
    pads.cut(1)
    assert asking.listening


def test_asking_again_supersedes_the_question_before_it(world):
    pads, readers = machine(world, "bt:aa")
    first = pads.identify()
    second = pads.identify()

    assert first.interrupted
    assert first.reason == "REPLACED"
    assert second.listening

    press(readers["bt:aa"])
    pads.cut(1)
    assert second.completed
    assert not first.completed


def test_a_finished_question_stops_consuming(world):
    pads, readers = machine(world, "bt:aa", "bt:bb")
    asking = pads.identify()
    press(readers["bt:aa"])
    pads.cut(1)
    answered = asking.value

    press(readers["bt:bb"])
    pads.cut(2)
    assert asking.value is answered


def test_reading_early_says_so_rather_than_handing_back_nothing(world):
    pads, _ = machine(world, "bt:aa")
    asking = pads.identify()
    with pytest.raises(RuntimeError):
        asking.value


def test_a_discarded_question_keeps_nothing(world):
    pads, readers = machine(world, "bt:aa")
    asking = pads.identify()
    asking.discard()
    press(readers["bt:aa"])
    pads.cut(1)
    assert asking.discarded
    assert not asking.completed


def test_narrowing_twice_is_a_mistake_and_says_so(world):
    pads, _ = machine(world, "bt:aa")
    asking = pads.identify().until(Button.START)
    with pytest.raises(RuntimeError):
        asking.until(Button.FACE_SOUTH)


def test_narrowing_to_something_that_is_not_a_button_is_a_mistake(world):
    pads, _ = machine(world, "bt:aa")
    with pytest.raises(TypeError):
        pads.identify().until("start")


def test_accept_resolves_against_the_pad_that_answers(world):
    # "Start, or whatever this pad calls yes." On an Xbox pad that is
    # FACE_SOUTH; on a Nintendo one it would be FACE_EAST, and the request is
    # written without knowing which will turn up.
    pads, readers = machine(world, "bt:aa")
    asking = pads.identify().until(Button.START | Button.ACCEPT)
    press(readers["bt:aa"], BTN_SOUTH)
    pads.cut(1)
    assert asking.completed
    assert asking.value.mapper.ACCEPT is Button.FACE_SOUTH


def test_closing_interrupts_an_open_question(world):
    pads, _ = machine(world, "bt:aa")
    asking = pads.identify()
    pads.close()
    assert asking.interrupted
    assert asking.reason == "CLOSED"


def test_the_answer_knows_whose_glyphs_to_draw(world):
    # The reason to identify a pad at all: not to assign a seat, but to know
    # which letters the plastic has on it.
    pads, readers = machine(world, "bt:aa")
    asking = pads.identify()
    press(readers["bt:aa"])
    pads.cut(1)
    assert asking.value.mapper.label(Button.START) == "Menu"


def test_the_two_requests_are_one_shape():
    # A line of typing and a pad being identified were written months apart
    # and came out identical. The shared base is that, made once.
    from blitspersecond.input.request import Request

    assert issubclass(TextInput, Request)
    assert issubclass(Identification, Request)
    for surface in ("listening", "completed", "interrupted", "discarded",
                    "reason", "state", "discard", "until", "value"):
        assert hasattr(TextInput, surface), surface
        assert hasattr(Identification, surface), surface
    assert TextInput("").state is InputState.LISTENING
    assert Identification().state is InputState.LISTENING
