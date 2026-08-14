"""MouseHandler unit tests: python -m pytest tests/test_mouse.py

Headless by design, like the keyboard's: the handler is EventBus + snapshot
with no GL anywhere. Events are dispatched onto a real EventBus (the exact
seam the window feeds) in WINDOW coordinates (pyglet: origin bottom-left,
OS pixels), and update() is called by hand as the tick boundary. The probe
pins the window at 2560x1440 -- the configured 640x360 at scale 4 -- so the
inverse present-transform is exercised with exact integer expectations."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import blitspersecond.input as input_api
from blitspersecond.input import mouse as buttons
from blitspersecond.input.kbm import Mouse
from blitspersecond.input.kbm.mouse_handler import MouseHandler
from blitspersecond.system.events import EventBus

WIN = (2560, 1440)  # 640x360 at scale 4


def make(win=WIN):
    bus = EventBus()
    mouse = MouseHandler(bus, probe=lambda: win)
    return bus, mouse


class _Device:
    def __init__(self, mouse):
        self._mouse = mouse

    def _connect(self):
        return self


def view(mouse):
    return Mouse(_Device(mouse))


def move(bus, x, y, dx=0, dy=0):
    bus.dispatch_event("on_mouse_motion", x, y, dx, dy)


def press(bus, button, x=0, y=0, mods=0):
    bus.dispatch_event("on_mouse_press", x, y, button, mods)


def release(bus, button, x=0, y=0, mods=0):
    bus.dispatch_event("on_mouse_release", x, y, button, mods)


# -- buttons: same law as keyboard keys --------------------------------------


def test_vocabulary_is_pyglets_values():
    # PRIMARY/SECONDARY are MEANING names over pyglet's left/right VALUES:
    # the OS applies the left-handed swap below us, so pyglet's LEFT is
    # already the user's primary button. Aliasing (not remapping) is the
    # design -- a swap here would double-swap a lefty's setup. BACK/FORWARD
    # are the Explorer thumb pair: in the vocabulary, OPTIONAL on devices.
    from pyglet.window import mouse as pyglet_mouse

    assert buttons.PRIMARY == pyglet_mouse.LEFT
    assert buttons.SECONDARY == pyglet_mouse.RIGHT
    assert buttons.MIDDLE == pyglet_mouse.MIDDLE
    assert buttons.BACK == pyglet_mouse.MOUSE4
    assert buttons.FORWARD == pyglet_mouse.MOUSE5


def test_button_vocabulary_is_namespaced_not_flattened():
    assert input_api.mouse is buttons
    assert isinstance(buttons, Mouse)
    assert not hasattr(input_api, "MouseHandler")
    assert not hasattr(input_api, "PRIMARY")
    assert not hasattr(input_api, "SECONDARY")


def test_facet_combines_button_vocabulary_and_device_pointer_state():
    bus, handler = make()
    proxy = view(handler)

    press(bus, proxy.PRIMARY, x=8, y=1439)
    handler.update()

    assert proxy.pressed(proxy.PRIMARY)
    assert proxy.position == (2, 0)


def test_optional_thumb_pair_edges_like_any_button():
    bus, mouse = make()
    press(bus, buttons.BACK)
    press(bus, buttons.FORWARD)
    mouse.update()
    assert mouse.pressed(buttons.BACK) and mouse.pressed(buttons.FORWARD)
    assert mouse.held_buttons == (buttons.BACK, buttons.FORWARD)


def test_edges_and_held():
    bus, mouse = make()
    press(bus, buttons.PRIMARY)
    mouse.update()
    assert (
        mouse.pressed(buttons.PRIMARY)
        and mouse.held(buttons.PRIMARY)
        and not mouse.released(buttons.PRIMARY)
    )
    assert mouse.held_for(buttons.PRIMARY) == 0  # 0 on the press tick

    mouse.update()
    assert not mouse.pressed(buttons.PRIMARY)  # edge lasts one tick
    assert mouse.held(buttons.PRIMARY) and mouse.held_for(buttons.PRIMARY) == 1

    release(bus, buttons.PRIMARY)
    mouse.update()
    assert mouse.released(buttons.PRIMARY) and not mouse.held(buttons.PRIMARY)


def test_sub_tick_click_still_edges():
    bus, mouse = make()
    press(bus, buttons.PRIMARY)
    release(bus, buttons.PRIMARY)  # both between ticks
    mouse.update()
    assert mouse.pressed(buttons.PRIMARY) and mouse.released(buttons.PRIMARY)
    assert not mouse.held(buttons.PRIMARY)


def test_press_order():
    bus, mouse = make()
    press(bus, buttons.SECONDARY)
    mouse.update()
    press(bus, buttons.PRIMARY)
    press(bus, buttons.MIDDLE)
    mouse.update()
    assert mouse.held_buttons == (
        buttons.SECONDARY,
        buttons.PRIMARY,
        buttons.MIDDLE,
    )


def test_ideal_mouse_drops_extra_buttons():
    # Beyond the five ideal buttons, a Naga's extras never enter the
    # snapshot -- but the pointer facts riding on their events are kept.
    GRID_1, GRID_2 = 32, 64  # button values past pyglet's MOUSE5

    bus, mouse = make()
    press(bus, GRID_1)
    press(bus, GRID_2, x=8, y=1439)
    mouse.update()
    assert not mouse.held_buttons
    assert not mouse.pressed(GRID_1)
    assert mouse.position == (2, 0)  # the event still moved the pointer
    release(bus, GRID_1)  # never held: no phantom edge
    mouse.update()
    assert not mouse.released(GRID_1)


def test_deactivate_releases_everything():
    bus, mouse = make()
    press(bus, buttons.PRIMARY)
    press(bus, buttons.SECONDARY)
    mouse.update()
    bus.dispatch_event("on_deactivate")
    mouse.update()
    assert mouse.released(buttons.PRIMARY) and mouse.released(buttons.SECONDARY)
    assert not mouse.held_buttons


# -- position: the inverse present transform ---------------------------------


def test_position_maps_and_flips():
    bus, mouse = make()
    # Window top-left corner: pyglet y = wh-1 -> internal (0, 0).
    move(bus, 0, 1439)
    mouse.update()
    assert mouse.position == (0, 0)
    # Window bottom-right corner -> internal (639, 359).
    move(bus, 2559, 0)
    mouse.update()
    assert mouse.position == (639, 359)
    # One internal pixel = a 4x4 window block: all four rows agree.
    for wy in range(4):
        move(bus, 8, wy)
        mouse.update()
        assert mouse.position == (2, 359)


def test_position_clamps_but_delta_does_not():
    bus, mouse = make()
    # A drag past the window edge (implicit grab): negative window coords.
    bus.dispatch_event(
        "on_mouse_drag", -100, -80, -40, -20, buttons.PRIMARY, 0
    )
    mouse.update()
    assert mouse.position == (0, 359)  # clamped onto the screen
    dx, dy = mouse.delta
    assert dx == -10.0 and dy == 5.0  # scaled 1/4, dy sign flipped, unclamped


def test_letterbox_margins_are_removed_from_position_and_delta():
    # 1920x1200 fits the 640x360 target at 3x, centred with 60px bars.
    bus, mouse = make((1920, 1200))

    move(bus, 0, 1139, dx=3, dy=-6)
    mouse.update()
    assert mouse.position == (0, 0)
    assert mouse.delta == (1.0, 2.0)

    # Positions in either black bar clamp to the nearest logical edge.
    move(bus, 100, 1199)
    mouse.update()
    assert mouse.position == (33, 0)
    move(bus, 100, 0)
    mouse.update()
    assert mouse.position == (33, 359)


def test_pillarbox_margins_are_removed_from_position():
    # 2560x1080 fits at 3x, centred with 320px bars.
    bus, mouse = make((2560, 1080))

    move(bus, 319, 1079)
    mouse.update()
    assert mouse.position == (0, 0)
    move(bus, 320, 1079)
    mouse.update()
    assert mouse.position == (0, 0)
    move(bus, 2239, 0)
    mouse.update()
    assert mouse.position == (639, 359)
    move(bus, 2559, 0)
    mouse.update()
    assert mouse.position == (639, 359)


def test_position_is_last_event_and_freezes():
    bus, mouse = make()
    move(bus, 0, 1439)
    move(bus, 400, 1439)  # same tick: last one wins
    mouse.update()
    assert mouse.position == (100, 0)
    mouse.update()  # quiet tick: position holds
    assert mouse.position == (100, 0)


def test_delta_accumulates_within_a_tick_and_resets():
    bus, mouse = make()
    move(bus, 100, 100, 8, 4)
    move(bus, 108, 96, 8, -4)
    mouse.update()
    dx, dy = mouse.delta
    assert dx == 4.0  # (8+8)/4
    assert dy == 0.0  # (-4+4)/4, up-positive flipped to down-positive
    mouse.update()
    assert mouse.delta == (0.0, 0.0)


# -- scroll and presence ------------------------------------------------------


def test_scroll_sums_per_tick():
    bus, mouse = make()
    bus.dispatch_event("on_mouse_scroll", 0, 0, 0.0, 1.0)
    bus.dispatch_event("on_mouse_scroll", 0, 0, 0.0, 2.0)
    mouse.update()
    assert mouse.scroll == 3.0
    mouse.update()
    assert mouse.scroll == 0.0


def test_enter_and_leave():
    bus, mouse = make()
    assert not mouse.inside  # unknown until the first event
    bus.dispatch_event("on_mouse_enter", 5, 5)
    mouse.update()
    assert mouse.inside
    bus.dispatch_event("on_mouse_leave", 5, 5)
    mouse.update()
    assert not mouse.inside


def test_pointer_visible_remembered_headless():
    bus, mouse = make()
    assert mouse.pointer_visible  # OS pointer shows by default
    mouse.pointer_visible = False  # no window open: remembered, no error
    assert not mouse.pointer_visible


def test_headless_fallback_window():
    bus = EventBus()
    mouse = MouseHandler(bus, probe=lambda: None)  # no window to ask
    # Falls back to the configured width*scale x height*scale window.
    move(bus, 8, 3)
    mouse.update()
    assert mouse.position == (2, 359)


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
