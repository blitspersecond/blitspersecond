"""PITCH BLACK: a one-verb text adventure, starring the grue's worst night.

The prompt owns one non-blocking Input reference. The private handler journals
tick-aligned physical/text events; Input replays editing and finalizes on
Return. Each tick the prompt row is redrawn from command.value, and completion
advances the game's ordinary DISPLAY/PROMPT/RESOLVE state machine.

This prompt declares only Return as its finalizer. Add key.ESCAPE to the
predicate if the adventure should give Escape a meaning.

Run from the repo root:  python tests/examples/pitch_black.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from blitspersecond import BlitsPerSecond
from blitspersecond.colors import CYAN, GRAY, NAVY, SILVER, YELLOW
from blitspersecond.console import Console
from blitspersecond.graphics.glyph import CharSet, GlyphEngine
from blitspersecond.input import key, keyboard

# Roles mapped onto the BPS system palette.
NARRATION = SILVER
INPUT_FG = YELLOW
RIDDICK = CYAN
GRUE = GRAY
OUTLINE = NAVY
TITLE_GLOW = CYAN

PROMPT = "> "
LINE_MAX = 60  # what fits on the prompt row without wrapping

TITLE = "PITCH BLACK\n"

INTRO = """\
a one-verb adventure

It is pitch black. You are likely to be eaten by a grue.
"""

MONOLOGUE = """\
You'd think the dark would be a problem. It's not.
Not for me.

Got the shine job in Butcher Bay. Cost me a pack of
Kools and a favour I'd rather not talk about.
Best deal I ever made.

So you look. And you see all of it: the wet stone,
the dripping pipe, the four exits nobody's taking...

...and ten feet away, pressed flat against the wall,
claws over its mouth to keep the whimpering in:
"""

GRUE_ART = """\

         .-~~~~~-.
        /  0   0  \\
       |     .     |     "eep."
        \\  ,===,  /
         VvvvvvvvV
"""

PUNCHLINE = """\
A terrified-looking grue.

It is pitch black. The grue is likely to be punched by YOU.
"""

LOOK_AGAIN = "Still there. Still terrified. He is not having a good night.\n"

REBUFFS = (
    "It's a one-verb kind of night. Try 'look'.\n",
    "You can't do that. It's pitch black.\n",
    "Somewhere in the dark, the grue whimpers. Try 'look'.\n",
)


class Game:
    """One room, one verb, one very unhappy grue -- fed submitted lines,
    writes responses into the transcript."""

    def __init__(self, console: Console):
        self.console = console
        self.looked = False
        self.rebuff = 0

    def respond(self, line: str) -> None:
        console = self.console
        words = line.strip().lower().split()
        if not words:
            pass  # empty line: nothing to say
        elif words[0] == "look":
            if not self.looked:
                self.looked = True
                console.foreground = RIDDICK
                console.write(MONOLOGUE)
                console.foreground = GRUE
                console.write(GRUE_ART)
                console.foreground = NARRATION
                console.write(PUNCHLINE)
            else:
                console.foreground = NARRATION
                console.write(LOOK_AGAIN)
        else:
            console.foreground = NARRATION
            console.write(REBUFFS[self.rebuff])
            self.rebuff = (self.rebuff + 1) % len(REBUFFS)


class PromptView:
    """Render a live Input value as a teletype-style prompt."""

    def __init__(self, console: Console):
        self.console = console
        self._drawn: str | None = None  # last rendered (line, blink) key

    def draw(self, line: str, tick: int) -> None:
        cursor = "_" if (tick // 20) % 2 == 0 else " "
        rendered = PROMPT + line[:LINE_MAX] + cursor
        if rendered == self._drawn:
            return
        self._drawn = rendered
        console = self.console
        y = console.cursor.y
        console.foreground = INPUT_FG
        width = len(PROMPT) + LINE_MAX + 1
        for x in range(width):
            console.glyphs.cell[x, y] = ord(" ")
        for i, ch in enumerate(rendered):
            console.glyphs.cell[i, y] = ord(ch)

    def commit(self, line: str) -> None:
        """The line was submitted: stamp it into the transcript for good and
        advance to a fresh row."""
        self._drawn = None
        console = self.console
        y = console.cursor.y
        console.foreground = INPUT_FG
        for x in range(console.cols):
            console.glyphs.cell[x, y] = ord(" ")
        console.cursor = (0, y)
        console.write(PROMPT + line[:LINE_MAX])
        console.write("\n")


def main() -> None:
    bps = BlitsPerSecond()
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    console = Console(glyphs)
    console.pen[2] = OUTLINE
    console.background = 0

    game = Game(console)
    view = PromptView(console)

    console.foreground = NARRATION
    console.pen[2] = TITLE_GLOW
    console.write(TITLE)
    console.pen[2] = OUTLINE
    console.write(INTRO + "\n")

    command = keyboard.input().until(key.RETURN)

    tick = 0

    def main_loop(engine) -> None:
        nonlocal command, tick
        if command.completed:
            line = command.value
            view.commit(line)
            game.respond(line)
            command = keyboard.input().until(key.RETURN)
        view.draw(command.value, tick)
        tick += 1

    bps.layers.add(glyphs)
    bps.run(main_loop)


if __name__ == "__main__":
    main()
