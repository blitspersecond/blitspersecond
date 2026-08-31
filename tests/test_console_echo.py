"""`Console.echo`: typing on screen, and where the cursor is left."""

from pyglet.window import key

from blitspersecond.common import fps
from blitspersecond.graphics.console import CharSet, Console
from blitspersecond.input.kbm import KeyboardIngress
from blitspersecond.internal import EventBus


def _greeter() -> tuple[EventBus, KeyboardIngress, Console]:
    events = EventBus()
    return events, KeyboardIngress(events, fps.SIXTY), Console(CharSet.ASCII8X12)


def _row(console: Console, y: int, width: int = 24) -> str:
    return "".join(
        chr(int(console.cell[x, y])) for x in range(width)
    ).replace("\x00", "").rstrip()


def _frame(keyboard: KeyboardIngress, console: Console, frame: int) -> None:
    """One engine frame: settle input, then let the layer draw."""
    keyboard.cut(frame)
    console._advance()


def test_typing_appears_as_it_happens():
    events, keyboard, console = _greeter()
    console.write("Name: ")
    line = console.echo(keyboard.input().until(key.RETURN))

    events.dispatch_event("on_text", "Rid")
    _frame(keyboard, console, 1)
    assert _row(console, 0) == "Name: Rid"

    events.dispatch_event("on_text", "dick")
    _frame(keyboard, console, 2)
    assert _row(console, 0) == "Name: Riddick"
    assert line.listening


def test_backspace_clears_the_cell_it_gave_up():
    events, keyboard, console = _greeter()
    console.echo(keyboard.input().until(key.RETURN))

    events.dispatch_event("on_text", "abc")
    _frame(keyboard, console, 1)
    events.dispatch_event("on_text_motion", key.MOTION_BACKSPACE)
    _frame(keyboard, console, 2)

    # Not "ab" plus a leftover "c".
    assert _row(console, 0) == "ab"


def test_return_is_not_echoed_and_the_reply_lands_on_the_next_row():
    events, keyboard, console = _greeter()
    console.write("Name: ")
    line = console.echo(keyboard.input().until(key.RETURN))

    events.dispatch_event("on_text", "Jack")
    events.dispatch_event("on_key_press", key.RETURN, 0)
    events.dispatch_event("on_text", "\r")
    keyboard.cut(1)

    # The game sees completion during its tick and replies immediately --
    # before the layer has advanced. The reply must still land below.
    assert line.completed
    console.print(f"Hello, {line.value}.")

    assert _row(console, 0) == "Name: Jack"
    assert _row(console, 1) == "Hello, Jack."


def test_a_finished_line_settles_even_if_the_game_never_looks():
    events, keyboard, console = _greeter()
    console.echo(keyboard.input().until(key.RETURN))

    events.dispatch_event("on_text", "x")
    events.dispatch_event("on_key_press", key.RETURN, 0)
    _frame(keyboard, console, 1)

    assert _row(console, 0) == "x"
    assert (console.cursor.x, console.cursor.y) == (0, 1)


def test_echoing_a_second_line_leaves_the_first_on_screen():
    events, keyboard, console = _greeter()
    console.echo(keyboard.input().until(key.RETURN))
    events.dispatch_event("on_text", "one")
    events.dispatch_event("on_key_press", key.RETURN, 0)
    _frame(keyboard, console, 1)

    console.echo(keyboard.input().until(key.RETURN))
    events.dispatch_event("on_text", "two")
    _frame(keyboard, console, 2)

    assert _row(console, 0) == "one"
    assert _row(console, 1) == "two"
