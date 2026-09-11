"""The boundary every device settles on, together.

`InputBoundary` exists for one guarantee: frame N means the same interval on
every device, so two devices' windows for that frame can be read side by side.
Until the mouse landed there was one device and the guarantee was asserted
rather than exercised. These are the tests that exercise it.
"""

from pyglet.window import key

from blitspersecond.common import Location, fps
from blitspersecond.common.screen import SCREEN
from blitspersecond.input import InputBoundary
from blitspersecond.input.kbm import PRIMARY, KeyEvent, MouseEvent
from blitspersecond.internal import EventBus
from blitspersecond.internal.window import integer_viewport


def _boundary(rate: fps = fps.SIXTY):
    events = EventBus()
    fit = integer_viewport(SCREEN.width, SCREEN.height, 1280, 720)
    return events, InputBoundary(events, rate, lambda: fit)


def test_one_cut_settles_every_device_on_the_same_frame_number():
    events, inputs = _boundary()
    events.dispatch_event("on_key_press", key.SPACE, 0)
    events.dispatch_event("on_mouse_press", 0, 719, PRIMARY, 0)

    inputs.cut(7)

    keyboard = inputs.keyboard.stream.newest
    mouse = inputs.mouse.stream.newest
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
    assert [w.frame for w in inputs.keyboard.stream.recent(20)] == [
        w.frame for w in inputs.mouse.stream.recent(20)
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

    keyboard = inputs.keyboard.stream.newest.scan
    mouse = inputs.mouse.stream.newest.scan
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

    assert inputs.keyboard.stream.newest.scan.cleared is True
    assert inputs.mouse.stream.newest.scan.cleared is True


def test_the_devices_describe_the_same_interval_not_the_same_events():
    events, inputs = _boundary()
    events.dispatch_event("on_mouse_press", 0, 719, PRIMARY, 0)
    events.dispatch_event("on_key_press", key.X, 0)
    inputs.cut(3)

    # Per-device storage: each ring holds only its own facts, and there is no
    # merged stream to demultiplex.
    assert [f.kind for f in inputs.mouse.stream.newest.facts] == [
        MouseEvent.PRESS
    ]
    assert [f.kind for f in inputs.keyboard.stream.newest.facts] == [
        KeyEvent.PRESS
    ]
