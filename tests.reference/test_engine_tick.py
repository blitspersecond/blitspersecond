import importlib.util

from blitspersecond import BlitsPerSecond
from blitspersecond.display.internal.surface import Surface
from blitspersecond.input import Pad
from blitspersecond.input.pads import XboxMapper
from blitspersecond.lifecycle import EngineState
from blitspersecond.system.events import EventBus
from pyglet.window import key


def bare_engine() -> BlitsPerSecond:
    """An unbooted instance is enough to exercise the callback binding."""
    engine = BlitsPerSecond.__new__(BlitsPerSecond)
    engine._tick = None
    return engine


def test_player_is_not_a_public_engine_or_package_surface():
    assert not hasattr(BlitsPerSecond, "players")
    assert importlib.util.find_spec("blitspersecond.player") is None


def test_tick_can_be_bound_replaced_and_cleared():
    engine = bare_engine()

    def menu(_engine: BlitsPerSecond) -> None:
        pass

    def game(_engine: BlitsPerSecond) -> None:
        pass

    engine.tick = menu
    assert engine.tick is menu
    engine.tick = game
    assert engine.tick is game
    engine.tick = None
    assert engine.tick is None


def test_latched_tick_can_replace_the_next_scene_without_replacing_itself():
    engine = bare_engine()
    calls = []

    def game(_engine: BlitsPerSecond) -> None:
        calls.append("game")

    def menu(current: BlitsPerSecond) -> None:
        calls.append("menu")
        current.tick = game

    engine.tick = menu
    current_tick = engine.tick
    assert current_tick is not None
    current_tick(engine)

    assert calls == ["menu"]
    assert engine.tick is game


def test_os_window_close_request_enters_the_clean_shutdown_path():
    engine = bare_engine()
    reasons = []
    engine._shutdown = reasons.append

    assert engine._on_window_close() is True
    assert reasons == ["window close request (OS/WM)"]


def test_background_events_are_coalesced_without_changing_engine_state():
    engine = bare_engine()
    engine._backgrounded = False
    engine._state = EngineState.RUNNING
    engine._events = EventBus()
    messages = []
    transitions = []

    class FakeLogger:
        def info(self, message):
            messages.append(message)

    def background():
        transitions.append("background")

    def foreground():
        transitions.append("foreground")

    engine._logger = FakeLogger()
    engine._events.push_handlers(
        on_background=background,
        on_foreground=foreground,
    )

    engine._set_backgrounded(True, "test")
    engine._set_backgrounded(True, "duplicate")
    assert engine.backgrounded
    assert engine.state is EngineState.RUNNING

    engine._set_backgrounded(False, "test")
    engine._set_backgrounded(False, "duplicate")
    assert not engine.backgrounded
    assert engine.state is EngineState.RUNNING
    assert transitions == ["background", "foreground"]
    assert len(messages) == 2


def test_window_visibility_handlers_publish_policy_events():
    engine = bare_engine()
    engine._backgrounded = False
    transitions = []

    engine._set_backgrounded = lambda state, reason: transitions.append(
        (state, reason)
    )

    assert engine._on_window_background() is None
    assert engine._on_window_foreground() is None
    assert transitions == [
        (True, "window hidden"),
        (False, "window exposed"),
    ]


def test_escape_is_a_gameplay_key_not_a_window_close_request():
    class FakeSurface:
        def dispatch_event(self, *args):
            raise AssertionError(f"Escape dispatched {args!r}")

    assert Surface.on_key_press(FakeSurface(), key.ESCAPE, 0) is None


def test_gamepad_prefers_ports_then_falls_back_to_pads():
    engine = bare_engine()
    routed = Pad("routed", name="routed", mapper=XboxMapper)
    active = Pad("active", name="active", mapper=XboxMapper)

    class FakePorts:
        gamepad: Pad | None = routed

    class FakePads:
        gamepad: Pad | None = active

    ports = FakePorts()
    pads = FakePads()
    object.__setattr__(engine, "ports", ports)
    object.__setattr__(engine, "_pads", pads)

    assert engine.gamepad is routed

    ports.gamepad = None
    assert engine.gamepad is active

    pads.gamepad = None
    assert engine.gamepad is None
