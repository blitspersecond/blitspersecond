"""KeyboardHandler unit tests: python tests/test_keyboard.py

Headless by design -- the handler is EventBus + snapshot with no GL
anywhere, so unlike the engine-session tests these run as ordinary pytest
functions, many per process. Events are dispatched onto a real EventBus
(the exact seam the window feeds) and update() is called by hand as the
tick boundary."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyglet.window import key as pyglet_key

import blitspersecond.input as input_api
from blitspersecond import key as root_key
from blitspersecond.input import (
    COMPLETED,
    INTERRUPTED,
    elapsed,
    key,
    keyboard,
)
from blitspersecond.input.kbm import Keyboard
from blitspersecond.input.kbm.keyboard_handler import KeyboardHandler
from blitspersecond.system.events import EventBus


def make():
    bus = EventBus()
    kb = KeyboardHandler(bus)
    return bus, kb


class _Device:
    def __init__(self, keyboard):
        self._keyboard = keyboard

    def _connect(self):
        return self


def view(keyboard):
    return Keyboard(_Device(keyboard))


def test_public_keyboard_is_a_facet_not_a_handler_export():
    assert root_key is key
    assert isinstance(keyboard, Keyboard)
    assert not hasattr(input_api, "KeyboardHandler")
    assert not hasattr(input_api, "Input")
    assert not hasattr(Keyboard, "capslock")
    assert not hasattr(Keyboard, "numlock")
    assert not hasattr(Keyboard, "modifiers")
    assert not hasattr(Keyboard, "ticks")
    assert not hasattr(Keyboard, "tick_rate")
    assert not hasattr(Keyboard, "held_keys")
    assert not hasattr(Keyboard, "held_for")


def test_facet_reads_the_device_tick_snapshot():
    bus, kb = make()
    proxy = view(kb)

    press(bus, pyglet_key.X)
    kb.update()

    assert proxy.pressed(key.X)
    assert proxy.held(key.X)


def test_public_keyboard_rejects_raw_backend_symbols():
    _, kb = make()
    proxy = view(kb)

    try:
        proxy.held(pyglet_key.X)
    except TypeError as error:
        assert "key predicate" in str(error)
    else:
        raise AssertionError("public keyboard accepted a raw pyglet symbol")


def test_public_keyboard_composes_alternatives_and_chords():
    bus, kb = make()
    proxy = view(kb)

    press(bus, pyglet_key.LCTRL)
    press(bus, pyglet_key.LALT)
    kb.update()
    assert proxy.held(key.CTRL & key.ALT)
    assert proxy.pressed(key.CTRL & key.ALT)
    assert not proxy.pressed(key.CTRL & key.ALT & key.DELETE)

    press(bus, pyglet_key.DELETE)
    kb.update()
    salute = key.CTRL & key.ALT & key.DELETE
    assert proxy.held(salute)
    assert proxy.pressed(salute)
    assert proxy.pressed(key.SPACE | key.DELETE)

    release(bus, pyglet_key.DELETE)
    kb.update()
    assert proxy.released(salute)
    assert not proxy.held(salute)


def test_public_chords_preserve_sub_tick_press_and_release_edges():
    bus, kb = make()
    proxy = view(kb)

    press(bus, pyglet_key.LCTRL)
    kb.update()
    press(bus, pyglet_key.A)
    release(bus, pyglet_key.A)
    kb.update()

    chord = key.CTRL & key.A
    assert proxy.pressed(chord)
    assert proxy.released(chord)
    assert not proxy.held(chord)


def test_modifier_aliases_accept_either_physical_side():
    bus, kb = make()
    proxy = view(kb)

    press(bus, pyglet_key.RSHIFT)
    press(bus, pyglet_key.RCTRL)
    press(bus, pyglet_key.RALT)
    kb.update()

    assert proxy.held(key.SHIFT & key.CTRL & key.ALT)
    assert key.DEL is key.DELETE


def test_public_keyboard_creates_and_owns_input():
    bus, kb = make()
    proxy = view(kb)
    entry = proxy.input().until(key.RETURN)

    bus.dispatch_event("on_text", "look\r")
    kb.update()

    assert entry.completed
    assert entry.value == "look"


def press(bus, sym, mods=0):
    bus.dispatch_event("on_key_press", sym, mods)


def release(bus, sym, mods=0):
    bus.dispatch_event("on_key_release", sym, mods)


def test_edges_and_held():
    bus, kb = make()
    press(bus, pyglet_key.X)
    kb.update()
    assert (
        kb.pressed(pyglet_key.X)
        and kb.held(pyglet_key.X)
        and not kb.released(pyglet_key.X)
    )

    kb.update()
    assert not kb.pressed(pyglet_key.X)  # edge lasts one tick
    assert kb.held(pyglet_key.X)

    release(bus, pyglet_key.X)
    kb.update()
    assert kb.released(pyglet_key.X) and not kb.held(pyglet_key.X)


def test_sub_tick_tap_still_edges():
    bus, kb = make()
    press(bus, pyglet_key.SPACE)
    release(bus, pyglet_key.SPACE)  # both between ticks
    kb.update()
    assert kb.pressed(pyglet_key.SPACE) and kb.released(pyglet_key.SPACE)
    assert not kb.held(pyglet_key.SPACE)


def test_repeat_presses_are_not_edges():
    bus, kb = make()
    press(bus, pyglet_key.A)
    kb.update()
    press(bus, pyglet_key.A)  # a repeat the platform failed to suppress
    kb.update()
    assert not kb.pressed(pyglet_key.A)
    assert kb.held(pyglet_key.A)


def test_repeat_press_does_not_satisfy_a_fresh_key_predicate():
    bus, kb = make()
    press(bus, pyglet_key.A)
    kb.update()
    entry = kb._begin_input().until(key.A)

    press(bus, pyglet_key.A)
    kb.update()

    assert entry.listening


def test_focus_loss_releases_everything():
    bus, kb = make()
    press(bus, pyglet_key.W)
    press(bus, pyglet_key.LSHIFT)
    kb.update()
    bus.dispatch_event("on_deactivate")
    kb.update()
    assert kb.released(pyglet_key.W) and kb.released(pyglet_key.LSHIFT)
    assert not kb.held(pyglet_key.W)
    assert not kb.held(pyglet_key.LSHIFT)


def test_input_replays_text_editing_and_return_completion():
    bus, kb = make()
    entry = kb._begin_input().until(key.RETURN)
    bus.dispatch_event("on_text", "loop")
    bus.dispatch_event("on_text_motion", pyglet_key.MOTION_BACKSPACE)
    bus.dispatch_event("on_text", "k")
    kb.update()
    assert entry.value == "look"
    assert not entry.completed

    bus.dispatch_event("on_text", "\r")  # Enter arrives inside on_text
    kb.update()
    assert entry.state is COMPLETED
    assert entry.value == "look"
    assert entry.finalizer is key.RETURN


def test_input_replays_navigation_delete_home_and_end():
    bus, kb = make()
    entry = kb._begin_input().until(key.RETURN)
    bus.dispatch_event("on_text", "abcd")
    bus.dispatch_event("on_text_motion", pyglet_key.MOTION_LEFT)
    bus.dispatch_event("on_text_motion", pyglet_key.MOTION_LEFT)
    bus.dispatch_event("on_text_motion", pyglet_key.MOTION_DELETE)
    bus.dispatch_event("on_text_motion", pyglet_key.MOTION_BEGINNING_OF_LINE)
    bus.dispatch_event("on_text", "X")
    bus.dispatch_event("on_text_motion", pyglet_key.MOTION_END_OF_LINE)
    bus.dispatch_event("on_text_motion", pyglet_key.MOTION_BACKSPACE)
    kb.update()

    assert entry.value == "Xab"
    assert entry.insert == 3


def test_unbound_text_is_journalled_and_a_new_input_starts_at_the_tail():
    bus, kb = make()
    bus.dispatch_event("on_text", "before")
    kb.update()

    entry = kb._begin_input().until(key.RETURN)
    bus.dispatch_event("on_text", "a\tb")  # tab is not printable
    kb.update()
    assert entry.value == "ab"


def test_new_input_interrupts_old_at_the_same_stream_boundary():
    bus, kb = make()
    first = kb._begin_input().until(key.RETURN)
    bus.dispatch_event("on_text", "first")
    kb.update()
    second = kb._begin_input().until(key.RETURN)
    assert first.state is INTERRUPTED
    assert first.stop == second.start
    assert first.value == "first"

    bus.dispatch_event("on_text", "second")
    kb.update()

    assert first.value == "first"
    assert second.value == "second"


def test_elapsed_gate_requires_a_fresh_escape_after_five_seconds():
    bus = EventBus()
    kb = KeyboardHandler(bus, tick_rate=1)
    entry = kb._begin_input().until(key.ESCAPE & elapsed(seconds=5))

    press(bus, pyglet_key.ESCAPE)
    release(bus, pyglet_key.ESCAPE)
    kb.update()
    for _ in range(4):
        kb.update()
    assert entry.listening

    press(bus, pyglet_key.ESCAPE)
    kb.update()
    assert entry.completed
    assert entry.finalizer is key.ESCAPE


def test_elapsed_can_complete_without_an_event():
    bus = EventBus()
    kb = KeyboardHandler(bus, tick_rate=1)
    timeout = elapsed(seconds=2)
    entry = kb._begin_input().until(key.RETURN | timeout)

    kb.update()
    assert entry.listening
    kb.update()

    assert entry.completed
    assert entry.finalizer is timeout


def test_parentheses_group_key_choices_behind_an_elapsed_gate():
    bus = EventBus()
    kb = KeyboardHandler(bus, tick_rate=1)
    entry = kb._begin_input().until(
        (key.RETURN | key.ESCAPE) & elapsed(seconds=2)
    )

    bus.dispatch_event("on_text", "\r")
    kb.update()
    assert entry.listening
    kb.update()
    bus.dispatch_event("on_text", "\r")
    kb.update()

    assert entry.completed
    assert entry.finalizer is key.RETURN


def test_input_key_chord_completes_when_its_last_member_is_pressed():
    bus, kb = make()
    entry = kb._begin_input().until(key.CTRL & key.ALT & key.DELETE)

    press(bus, pyglet_key.LCTRL)
    kb.update()
    press(bus, pyglet_key.RALT)
    kb.update()
    assert entry.listening

    press(bus, pyglet_key.DELETE)
    kb.update()

    assert entry.completed
    assert entry.finalizer is key.DELETE


def test_input_key_chord_can_be_gated_by_elapsed():
    bus = EventBus()
    kb = KeyboardHandler(bus, tick_rate=1)
    entry = kb._begin_input().until(
        key.CTRL & key.RETURN & elapsed(seconds=2)
    )

    press(bus, pyglet_key.LCTRL)
    press(bus, pyglet_key.RETURN)
    kb.update()
    assert entry.listening
    release(bus, pyglet_key.RETURN)
    kb.update()

    press(bus, pyglet_key.RETURN)
    kb.update()

    assert entry.completed
    assert entry.finalizer is key.RETURN


def test_discarded_input_releases_its_replayable_value():
    bus, kb = make()
    entry = kb._begin_input().until(key.RETURN)
    bus.dispatch_event("on_text", "temporary")
    kb.update()

    entry.discard()

    assert entry.discarded
    try:
        entry.value
    except RuntimeError as error:
        assert "no longer replayable" in str(error)
    else:
        raise AssertionError("discarded input remained replayable")


def test_keyscan_remains_truthful_while_input_listens():
    bus, kb = make()
    kb._begin_input().until(key.RETURN)
    press(bus, pyglet_key.X)
    kb.update()

    assert kb.pressed(pyglet_key.X)
    assert kb.held(pyglet_key.X)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")
