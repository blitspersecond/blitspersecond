import pytest
from pyglet.window import key as pyglet_key

from blitspersecond.colors import Pen
from blitspersecond.console import Console
from blitspersecond.graphics.glyph import CharSet, GlyphEngine
from blitspersecond.display.layer import Layer
from blitspersecond.input import key
from blitspersecond.input.kbm.keyboard_handler import KeyboardHandler
from blitspersecond.system.events import EventBus


def _bytes(console: Console, y: int, start: int, count: int) -> bytes:
    return bytes(console.glyphs.cell[x, y] for x in range(start, start + count))


def _console(charset: CharSet = CharSet.ASCII8X12) -> Console:
    return Console(GlyphEngine(charset))


def test_console_controls_supplied_glyph_engine_but_is_not_layerable():
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    console = Console(glyphs)

    assert console.glyphs is glyphs
    assert not isinstance(console, Layer)


def test_console_requires_a_glyph_layer():
    with pytest.raises(TypeError, match="expected GlyphLayer"):
        Console(CharSet.ASCII8X12)


def test_default_console_uses_full_screen_8x12_charset():
    console = _console()

    assert (console.rows, console.cols) == (30, 80)


def test_console_can_use_8x8_system_charset():
    console = _console(CharSet.ASCII8X8)

    assert (console.rows, console.cols) == (45, 80)


def test_clear_can_copy_pen_and_paint_every_cell():
    console = _console()
    console.cursor = (2, 1)
    console.write("X")

    console.clear(Pen(4, 5, 6, 7))

    assert tuple(console.pen) == (4, 5, 6, 7)
    assert (console.cursor.x, console.cursor.y) == (0, 0)
    assert all(
        console.glyphs.cell[x, y] == 0
        for y in range(console.rows)
        for x in range(console.cols)
    )


def test_print_converts_an_object_and_starts_a_new_line():
    console = _console()

    assert console.print(123) is None

    assert _bytes(console, 0, 0, 3) == b"123"
    assert (console.cursor.x, console.cursor.y) == (0, 1)


def test_print_without_a_value_writes_a_blank_line():
    console = _console()

    console.print()

    assert (console.cursor.x, console.cursor.y) == (0, 1)


def test_cursor_char_reads_and_writes_a_full_byte_without_moving():
    console = _console()
    console.cursor = (4, 1)

    console.cursor.char = 0xDA

    assert console.cursor.char == 0xDA
    assert (console.cursor.x, console.cursor.y) == (4, 1)


def test_character_codes_are_exactly_one_byte():
    console = _console()

    console.cursor.char = 0xFF
    assert console.cursor.char == 0xFF

    with pytest.raises(ValueError):
        console.cursor.char = 0x100


def test_write_rejects_character_outside_byte_charset():
    console = _console()

    with pytest.raises(ValueError):
        console.write("\u0100")


def test_newline_scrolls_in_whole_character_rows():
    console = _console()
    console.cursor = (0, console.rows - 2)
    console.write("A")
    console.cursor = (0, console.rows - 1)

    console.print("B")

    assert console.glyphs.cell[0, console.rows - 3] == ord("A")
    assert console.glyphs.cell[0, console.rows - 2] == ord("B")
    assert (console.cursor.x, console.cursor.y) == (0, console.rows - 1)


def test_write_can_position_before_output():
    console = _console()

    console.write("READY", at=(3, 2))

    assert _bytes(console, 2, 3, 5) == b"READY"
    assert (console.cursor.x, console.cursor.y) == (8, 2)


def test_print_can_position_before_output():
    console = _console()

    console.print("READY", at=(3, 2))

    assert _bytes(console, 2, 3, 5) == b"READY"
    assert (console.cursor.x, console.cursor.y) == (0, 3)


def test_input_echoes_replayed_edits_and_completes_without_a_callback():
    bus = EventBus()
    keyboard = KeyboardHandler(bus)
    console = _console()

    console.write("Name? ")
    name = console.echo(keyboard._begin_input()).until(key.RETURN)

    bus.dispatch_event("on_text", "Kris")
    keyboard.update()
    assert _bytes(console, 0, 0, 10) == b"Name? Kris"
    assert name.value == "Kris"
    assert not name.completed

    bus.dispatch_event("on_text_motion", pyglet_key.MOTION_BACKSPACE)
    bus.dispatch_event("on_text", "s\r")
    keyboard.update()

    assert name.completed
    assert name.value == "Kris"
    assert name.finalizer is key.RETURN
    console.print(f"Hello {name.value}")
    assert _bytes(console, 1, 0, 10) == b"Hello Kris"
    assert (console.cursor.x, console.cursor.y) == (0, 2)


def test_console_echoes_keyboard_input_but_does_not_create_it():
    console = _console()

    assert callable(console.echo)
    assert not hasattr(console, "input")


def test_console_input_tracks_insert_motion_when_replaying():
    bus = EventBus()
    keyboard = KeyboardHandler(bus)
    console = _console()
    entry = console.echo(keyboard._begin_input()).until(key.RETURN)

    bus.dispatch_event("on_text", "ac")
    bus.dispatch_event("on_text_motion", pyglet_key.MOTION_LEFT)
    bus.dispatch_event("on_text", "b")
    keyboard.update()

    assert entry.value == "abc"
    assert entry.insert == 2
    assert _bytes(console, 0, 0, 3) == b"abc"
    assert (console.cursor.x, console.cursor.y) == (2, 0)
