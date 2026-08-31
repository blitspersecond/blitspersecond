"""Non-blocking typed input: ask a name, echo it, greet, ask again.

Run from the repository root:

    venv/bin/python examples/greeter.py

Type a name and press Return. Backspace, arrows, home and end all work while
you type. Close the window to quit.

**Nothing here blocks or polls.** `keyboard.input()` hands back a line that
fills in as the player types; `console.echo` draws it while it does. The tick
function looks once per frame, acts if there is something to act on, and
returns -- which is the shape of every game in bps, whether it is reading a
name or moving a thousand sprites.

The line is settled at the frame boundary before your tick runs, so `value`
and `completed` hold still for the whole of it. They cannot change halfway
down a function that is reading them.
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

    def ask() -> object:
        """Print the question, then start a line where the answer will go."""
        console.pen = Pen(0, WHITE)
        console.write("What is your name? ")
        # Typed input is yellow because the pen is yellow when the echo
        # starts -- the echo draws with whatever pen it was given.
        console.pen = Pen(0, YELLOW)
        return console.echo(bps.keyboard.input().until(key.RETURN))

    name = ask()

    def tick() -> None:
        nonlocal name
        if name.completed:
            console.pen = Pen(0, WHITE)
            console.print(f"Hello, {name.value}.")
            name = ask()

    bps.start(tick)


if __name__ == "__main__":
    main()
