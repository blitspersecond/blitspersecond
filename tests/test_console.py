import pytest
from pyglet.window import key as pyglet_key

from blitspersecond.graphics.common import Pen
from blitspersecond.console import Console
from blitspersecond.console.cursor import Cursor
from blitspersecond.display import Layers
from blitspersecond.graphics.glyph import CharSet, GlyphEngine, GlyphLayer
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


def test_console_rejects_anything_else():
    with pytest.raises(TypeError, match="expected GlyphLayer"):
        Console("ascii8x12")  # type: ignore[arg-type]


def test_console_layer_is_the_same_object_as_glyphs():
    console = Console(CharSet.ASCII8X8)

    assert console.layer is console.glyphs
    assert isinstance(console.layer, Layer)


def test_console_is_unattached_until_added():
    console = Console(CharSet.ASCII8X8)

    assert not console.attached


def test_console_attached_reads_through_to_its_layer():
    layers = Layers()

    console = layers.add(Console(CharSet.ASCII8X8))

    assert console.attached


def test_console_grid_form_picks_a_tiling_cell_size():
    layers = Layers()

    console = layers.add(Console(80, 45))

    assert (console.cols, console.rows) == (80, 45)
    assert (console.glyphs._char_width, console.glyphs._char_height) == (8, 8)


@pytest.mark.parametrize(
    "cols, rows, charset",
    [(80, 45, CharSet.ASCII8X8), (80, 30, CharSet.ASCII8X12)],
)
def test_console_grid_form_loads_a_real_charset_not_blank_glyphs(cols, rows, charset):
    grid = Console(cols, rows)
    named = Console(charset)

    # The whole 256-glyph table, so this cannot pass on a lucky character.
    assert all((grid.layer.glyph[i] == named.layer.glyph[i]).all() for i in range(256))
    assert grid.layer.glyph[ord("R")].any()


def test_console_grid_form_refuses_a_grid_no_system_charset_can_serve():
    with pytest.raises(ValueError, match="no system charset is that size"):
        Console(64, 36)


def test_charset_for_cell_finds_by_glyph_size():
    assert CharSet.for_cell(8, 8) is CharSet.ASCII8X8
    assert CharSet.for_cell(8, 12) is CharSet.ASCII8X12
    assert CharSet.for_cell(10, 10) is None


def test_console_grid_form_rejects_a_grid_that_does_not_tile_the_screen():
    with pytest.raises(ValueError, match="does not tile"):
        Console(7, 45)


def test_console_grid_form_rejects_non_integers():
    with pytest.raises(TypeError, match="two integers"):
        Console(80, 45.0)  # type: ignore[arg-type]


def test_console_tag_reaches_through_to_its_glyph_layer():
    console = Console(CharSet.ASCII8X8)

    console.tag = "hud"

    assert console.tag == "hud"
    assert console.glyphs.tag == "hud"


def test_console_is_built_unattached():
    console = Console(CharSet.ASCII8X8)

    assert isinstance(console.glyphs, GlyphLayer)
    assert not console.glyphs.attached


def test_layers_add_attaches_the_layer_a_bare_console_was_built_over():
    layers = Layers()

    console = Console(CharSet.ASCII8X8)

    assert layers.add(console) is console  # add hands back what you passed
    assert isinstance(console, Console)
    assert console.glyphs.attached
    assert list(layers) == [console.glyphs]
    assert (console.rows, console.cols) == (45, 80)


def test_layers_add_attaches_the_default_charset_for_a_bare_console():
    layers = Layers()

    console = layers.add(Console())

    assert (console.rows, console.cols) == (30, 80)


def test_default_console_uses_full_screen_8x12_charset():
    console = _console()

    assert (console.rows, console.cols) == (30, 80)


def test_console_can_use_8x8_system_charset():
    console = _console(CharSet.ASCII8X8)

    assert (console.rows, console.cols) == (45, 80)


def test_console_cursor_assignment_replaces_a_cursor_over_the_same_layer():
    console = _console()
    replacement = Cursor(console.layer)
    replacement.x = 5

    console.cursor = replacement

    assert console.cursor is replacement
    assert (console.cursor.x, console.cursor.y) == (5, 0)


def test_cursor_takes_a_console_or_its_layer_alike():
    console = _console()

    assert Cursor(console).layer is Cursor(console.layer).layer is console.layer


def test_cursor_refuses_something_that_is_not_a_surface():
    with pytest.raises(TypeError, match="addresses a GlyphLayer"):
        Cursor("console")  # type: ignore[arg-type]


def test_console_cursor_assignment_rejects_a_cursor_over_another_layer():
    console = _console()
    stranger = Cursor(GlyphEngine(CharSet.ASCII8X8))

    with pytest.raises(ValueError, match="different glyph layer"):
        console.cursor = stranger


def test_console_cursor_assignment_still_takes_a_position():
    console = _console()
    original = console.cursor

    console.cursor = (3, 4)

    assert console.cursor is original
    assert (console.cursor.x, console.cursor.y) == (3, 4)


def test_console_pen_assignment_replaces_not_copies():
    console = _console()
    new_pen = Pen(4, 5, 6, 7)

    console.pen = new_pen

    assert console.pen is new_pen


def test_using_pen_restores_the_previous_pen_object():
    console = _console()
    original = console.pen

    with console._using_pen(Pen(4, 5, 6, 7)):
        assert console.pen is not original

    assert console.pen is original


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


def test_writing_a_run_of_cells_blanks_a_line_without_moving_the_cursor():
    console = _console()
    console.write("KEEP", at=(0, 3))
    console.write("GONE", at=(0, 4))
    console.write("KEEP", at=(0, 5))
    console.cursor = (7, 9)

    console.layer.cell[:, 4] = 0

    assert _bytes(console, 3, 0, 4) == b"KEEP"
    assert _bytes(console, 5, 0, 4) == b"KEEP"
    assert all(console.glyphs.cell[x, 4] == 0 for x in range(console.cols))
    # Empty cells, not stamped spaces -- and the cursor is untouched.
    assert (console.cursor.x, console.cursor.y) == (7, 9)


def test_a_run_of_cells_matches_stamping_them_one_at_a_time():
    import numpy as np

    a, b = _console(), _console()
    for c in (a, b):
        for y in range(c.rows):
            c.write("A" * 10, at=(0, y))

    a.layer.cell[:, 6] = 0
    for x in range(b.cols):
        b.glyphs.cell[x, 6] = 0

    assert np.array_equal(a.glyphs._image.pixels, b.glyphs._image.pixels)


def test_a_run_refuses_a_length_that_does_not_fit():
    import numpy as np

    console = _console()

    with pytest.raises(ValueError, match="takes that shape"):
        console.layer.cell[0:5, 2] = np.zeros(9, dtype=np.uint8)


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
