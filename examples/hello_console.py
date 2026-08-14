"""Non-blocking INPUT and PRINT on a BPS Console layer.

Run from the repository root:

    venv/bin/python examples/hello_console.py

Close the window to quit.
"""

from blitspersecond import BlitsPerSecond, key
from blitspersecond.colors import WHITE, YELLOW
from blitspersecond.console import Console
from blitspersecond.graphics.glyph import CharSet, GlyphEngine


def main() -> None:
    bps = BlitsPerSecond()
    console = Console(GlyphEngine(CharSet.ASCII8X12))

    console.pen[1] = WHITE
    console.pen[2] = YELLOW
    console.write("Enter your name: ", at=(24, 15))

    name = console.echo(bps.keyboard.input()).until(key.RETURN)

    def greet(engine: BlitsPerSecond) -> None:
        if name.completed:
            console.print(f"Hello {name.value}", at=(24, 17))
            engine.tick = None

    bps.tick = greet
    bps.run()


if __name__ == "__main__":
    main()
