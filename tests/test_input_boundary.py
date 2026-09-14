"""The boundary every device settles on, together.

`InputBoundary` exists for one guarantee: frame N means the same interval on
every device, so two devices' windows for that frame can be read side by side.
Until the mouse landed there was one device and the guarantee was asserted
rather than exercised. These are the tests that exercise it.
"""

import pytest
from blitspersecond.input import key

from blitspersecond.common import Location, fps
from blitspersecond.common.screen import SCREEN
from blitspersecond.input import InputBoundary
from blitspersecond.input.kbm import PRIMARY, KeyEvent, MouseEvent
from blitspersecond.internal import EventBus
from blitspersecond.internal.window import integer_viewport
from blitspersecond.input.pads import pads as pads_module


@pytest.fixture(autouse=True)
def no_real_pads(monkeypatch):
    """A machine with no gamepads, whatever is plugged into this one.

    Without this the boundary opens every pad on the developer's desk, and
    the suite would pass or fail depending on what was charged.
    """
    monkeypatch.setattr(pads_module, "discover", dict)


def _boundary(rate: fps = fps.SIXTY):
    events = EventBus()
    fit = integer_viewport(SCREEN.width, SCREEN.height, 1280, 720)
    return events, InputBoundary(events, rate, lambda: fit)


def test_one_cut_settles_every_device_on_the_same_frame_number():
    events, inputs = _boundary()
    events.dispatch_event("on_key_press", key.SPACE, 0)
    events.dispatch_event("on_mouse_press", 0, 719, PRIMARY, 0)

    inputs.cut(7)

    keyboard = inputs.keyboard.stream.latest
    mouse = inputs.mouse.stream.latest
    assert keyboard.frame == mouse.frame == 7
    assert keyboard.scan.held == frozenset({key.SPACE})
    assert mouse.scan.held == frozenset({PRIMARY})
    assert mouse.facts[0].position == Location(0, 0)


def test_the_rings_stay_in_step_frame_for_frame():
    events, inputs = _boundary()
    for frame in range(20):
        # Input on some frames and not others; both rings must still hold a
        # window per frame, or "the last N frames" means two different spans.
        if frame % 3 == 0:
            events.dispatch_event("on_mouse_motion", 640, 360, 1, 0)
        if frame % 4 == 0:
            events.dispatch_event("on_key_press", key.A, 0)
            events.dispatch_event("on_key_release", key.A, 0)
        inputs.cut(frame)

    assert len(inputs.keyboard.stream) == len(inputs.mouse.stream)
    assert [w.frame for w in inputs.keyboard.stream.all] == [
        w.frame for w in inputs.mouse.stream.all
    ]


def test_a_flush_reaches_every_device():
    events, inputs = _boundary()
    events.dispatch_event("on_key_press", key.LEFT, 0)
    events.dispatch_event("on_mouse_press", 0, 719, PRIMARY, 0)
    inputs.cut(1)

    # Input that arrived while the engine was not ticking.
    events.dispatch_event("on_key_press", key.RIGHT, 0)
    events.dispatch_event("on_mouse_motion", 1278, 0, 1278, -719)
    inputs.flush()
    inputs.cut(2)

    keyboard = inputs.keyboard.stream.latest.scan
    mouse = inputs.mouse.stream.latest.scan
    # Neither device kept what arrived during the suspension...
    assert key.RIGHT not in keyboard.held
    assert mouse.delta == (0.0, 0.0)
    # ...and neither was left holding something the player may have let go.
    assert keyboard.held == frozenset()
    assert mouse.held == frozenset()
    assert keyboard.cleared is mouse.cleared is True


def test_neither_device_swallows_the_other_s_events():
    # Both ingresses subscribe to on_deactivate. If either returned
    # EVENT_HANDLED the one beneath it would never see focus loss, and its
    # held set would stay stuck for good.
    events, inputs = _boundary()
    events.dispatch_event("on_key_press", key.DOWN, 0)
    events.dispatch_event("on_mouse_press", 0, 719, PRIMARY, 0)
    inputs.cut(1)

    events.dispatch_event("on_deactivate")
    inputs.cut(2)

    assert inputs.keyboard.stream.latest.scan.cleared is True
    assert inputs.mouse.stream.latest.scan.cleared is True


def test_the_devices_describe_the_same_interval_not_the_same_events():
    events, inputs = _boundary()
    events.dispatch_event("on_mouse_press", 0, 719, PRIMARY, 0)
    events.dispatch_event("on_key_press", key.X, 0)
    inputs.cut(3)

    # Per-device storage: each ring holds only its own facts, and there is no
    # merged stream to demultiplex.
    assert [f.kind for f in inputs.mouse.stream.latest.facts] == [
        MouseEvent.PRESS
    ]
    assert [f.kind for f in inputs.keyboard.stream.latest.facts] == [
        KeyEvent.PRESS
    ]


def test_one_cut_reaches_the_pads_as_well():
    _, inputs = _boundary()
    for frame in range(4):
        inputs.cut(frame)
    # No pads here, but the collection was cut on every frame -- which is what
    # keeps a pad plugged in at frame 200 from being discovered at 260.
    assert len(inputs.pads) == 0


def test_the_three_devices_answer_a_resume_differently_on_purpose():
    # One call, three answers, each correct for its device. The keyboard
    # assumes neutral because it cannot ask; the pointer keeps its position
    # because that is state rather than history; pads ask, because they can.
    events, inputs = _boundary()
    events.dispatch_event("on_key_press", key.LEFT, 0)
    events.dispatch_event("on_mouse_motion", 1278, 0, 1278, -719)
    inputs.cut(1)
    where = inputs.mouse.stream.latest.scan.position
    assert where == Location(639, 359)

    inputs.flush()
    inputs.cut(2)

    keyboard = inputs.keyboard.stream.latest.scan
    mouse = inputs.mouse.stream.latest.scan
    assert keyboard.held == frozenset()      # assumed neutral
    assert keyboard.cleared is True
    assert mouse.position == where           # kept: it is wherever it is
    assert mouse.delta == (0.0, 0.0)         # but the motion was discarded


def test_closing_releases_what_the_devices_hold():
    _, inputs = _boundary()
    inputs.cut(0)
    inputs.close()
    inputs.close()      # twice is safe; teardown runs on half-built things
    assert len(inputs.pads) == 0
