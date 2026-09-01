"""Non-blocking INPUT and PRINT on a BPS Console layer.

Run from the repository root:

    venv/bin/python examples/console.py

Type a name and press Return; it greets you and asks again. Backspace,
delete, the arrows, home and end all work while you type. Close the window
to quit.

The BASIC allusion is the point, and so is the word NON-BLOCKING. `INPUT A$`
stopped the machine dead until the player pressed Return, and nothing else
could happen meanwhile. Here the line fills in as it is typed while the frame
carries on around it: `bps.keyboard.input()` hands back a line, `console.echo`
draws it, and the tick function looks once a frame and acts if there is
anything to act on.

The line is settled at the frame boundary before the tick runs, so `value` and
`completed` hold still for the whole of it -- they cannot change halfway down
a function that is reading them.
"""

from blitspersecond import BlitsPerSecond
from blitspersecond.graphics import CYAN, GRAY, WHITE, YELLOW
from blitspersecond.graphics.console import CharSet, Console, Pen
from blitspersecond.input import key


def main() -> None:
    bps = BlitsPerSecond()
    console = Console(CharSet.ASCII8X12)
    bps.layers.add(console)

    console.pen = Pen(0, CYAN)
    console.print("THE GREETER")
    console.pen = Pen(0, GRAY)
    console.print("type a name and press return")
    console.print()

    def ask():
        """Print the question, then open a line where the answer will go."""
        console.pen = Pen(0, WHITE)
        console.write("What is your name? ")
        # Typing comes out yellow because the pen is yellow when the echo
        # starts -- an echo draws with the pen it was handed.
        console.pen = Pen(0, YELLOW)
        return console.echo(bps.keyboard.input().until(key.RETURN))

    name = ask()

    def tick() -> None:
        nonlocal name
        if name.completed:
            console.pen = Pen(0, WHITE)
            console.print(f"Hello, {name.value}.")
            # A finished line is a finished thing; the next question needs
            # its own. Asking interrupts any line still listening.
            name = ask()

    bps.start(tick)


if __name__ == "__main__":
    main()
