# The Console

The console is a grid of characters you write onto, and the quickest way to get
something on screen in bps. It is where you put a score, a debug readout, a
menu, or the whole game if the game is made of words.

## Hello, world

```python
from blitspersecond import BlitsPerSecond
from blitspersecond.graphics.console import CharSet, Console

bps = BlitsPerSecond()
console = Console(CharSet.ASCII8X12)
bps.layers.add(console)

console.print("Hello, world!")

bps.start()
```

Four lines of machinery and one of output. `BlitsPerSecond()` opens the
window. `Console(CharSet.ASCII8X12)` builds an 80x30 grid of 8x12 characters,
one of the two system character sets bps currently ships with.
`bps.layers.add(...)` puts it on screen.

`bps.start()` shows the window and keeps it there until you close it. Text you
wrote before calling it is simply there when it opens — writing to a console
is not something that has to happen every frame, and most of what you put on
screen you will write once.

## Choosing a grid

You can ask for a character set, or ask for the grid you want and let bps find
the set that gives it to you:

```python
Console(CharSet.ASCII8X12)      # 80x30
Console(CharSet.ASCII8X8)       # 80x45
Console(80, 30)                 # the same 80x30, said the other way round
Console(80, 45)                 # the same 80x45
```

The character size decides the grid: the screen is 640x360 and characters have
to divide it exactly, so 8x12 characters give you 80x30 and 8x8 give you 80x45.
Those are the two sets bundled today, so those are the two grids these forms
offer out of the box.

Other grids are perfectly possible — 64x36 needs 10x10 characters, which divide
the screen just as neatly — but bps has no 10x10 font to write them with, and
says so rather than guessing:

```
>>> Console(64, 36)
ValueError: a 64x36 console needs 10x10 glyphs and no system charset is
that size. Available: 80x45 (ASCII8X8), 80x30 (ASCII8X12). For a console
of blank glyphs to fill yourself, build the layer explicitly: ...
```

Drawing your own characters is what that last suggestion is for, and it is
[the appendix](glyph-layer.md)'s subject.

## Writing

`print` writes a line and moves to the next one. `write` writes without moving
on, so several calls build one line:

```python
console.write("Score: ")
console.write("1000")
console.print()                 # end the line
```

Both take `at=(x, y)` to move first, which is how you write to a fixed spot
without tracking where the cursor got to:

```python
console.write("Score: 1000", at=(2, 1))
```

Text wraps at the right edge and scrolls at the bottom, and `\n` starts a new
line, so a paragraph in a triple-quoted string arrives looking like a paragraph.

`console.clear()` empties the screen and puts the cursor back at the top left.

## The cursor

The cursor is where the next character goes. Read it, and move it:

```python
console.cursor                  # (0, 0) on a fresh console
console.cursor = (10, 5)        # column 10, row 5
console.print("here")
```

Columns first, then rows — the order you would say a screen position out loud.
Writing advances it, `print` returns it to the start of the next line, and
scrolling at the bottom keeps it on the last row.

## Pens

You do not set a colour, you pick up a pen. Everything you write is written
with the pen you are holding:

```python
from blitspersecond.graphics import CYAN, WHITE
from blitspersecond.graphics.console import Pen

console.pen = Pen(0, CYAN)
console.print("THE GREETER")

console.pen = Pen(0, WHITE)
console.print("by candlelight")
```

The two numbers are palette entries. The first is the background, the second is
the ink. Swapping pens changes nothing already on screen; each character keeps
the pen it was written with.

Two colours is the ordinary form, not the whole of a pen. A pen also carries
which way round its characters face, and each of its colours can be a whole
column of colour rather than one. Neither matters for writing text, and both
are [the appendix](glyph-layer.md)'s subject.

**Index 0 is always transparent.** Not by convention — every palette in bps
pins it, and writing a colour there leaves it see-through anyway. It is the
engine's hole, the same one sprites and tiles use, so a pen with `0` as its
background lets whatever is behind the console show through.

For a quick change of ink there is a shorthand that leaves the rest of the pen
alone:

```python
console.foreground = CYAN
console.background = 0
```

A pen can carry much more than two flat colours — a gradient down each letter,
a mask that cuts their bottoms off. That is the subject of
[the appendix](glyph-layer.md).

## Asking a question

A console that only prints is half a console. To read a line of typing, ask the
keyboard for an input, say what finishes it, and hand it to `console.echo` so
the typing appears as it happens:

```python
from blitspersecond.input import key

name = console.echo(bps.keyboard.input().until(key.RETURN))
```

Pressing Return finishes the input. The Return itself is not echoed — it is
the terminator, not a character — and the cursor drops to the start of the next
line, so whatever you print next begins on a clean row. That is why the
greeting below does not need a `print()` in front of it.

`echo` does not block, and this is the point where the loop starts mattering.
Everything so far was written once and left alone. Typing arrives over time, so
something has to look at it repeatedly — and `bps.keyboard.input()` gives you a
line that fills in as the player types, handles their backspacing, and sets
`completed` when they press Return. Your job is to notice.

So `bps.start()` takes a function, and calls it once per tick — sixty times a
second — for exactly this:

```python
def tick():
    global name
    if name.completed:
        console.print(f"Hello, {name.value}.")
        console.print("What is your name?")
        name = console.echo(bps.keyboard.input().until(key.RETURN))

bps.start(tick)
```

Nothing waits. The tick function looks, acts if there is something to act on,
and returns immediately so the frame can be drawn. Every game in bps is that
shape, whether it is reading a name or moving a thousand sprites.

## What `name` actually is

A variable that changes without anyone assigning to it looks like magic, so it
is worth a paragraph on who is writing to it.

`bps.keyboard.input()` does not give you a string — it gives you **the line
itself**, an object that holds the text as it is typed. Once a frame the engine
hands it whatever keys arrived, and it applies them: characters go in at the
insertion point, backspace takes one out again, the arrows move where the next
one lands.

```python
name.value          # "Riddick"
name.completed      # False -- still being typed
```

That is why editing works without you doing anything. A player who types
`Riddik`, backspaces, and types `ck` leaves you a `name.value` of `"Riddick"`,
because the backspace was applied when it arrived. You never see the
intermediate mess unless you look during it.

The line keeps its own text, and keeps it for as long as the question is open.
That is worth knowing, because the engine itself does not: it remembers only
the last half-second of input, which is plenty for spotting a player rolling a
joystick and nowhere near enough for someone taking their time over their name.
Your line is not stored in that half-second. It is its own, and it can take all
the time it likes.

The engine settles all of this once a tick, before your tick function runs. So
the answers hold still while your code is looking at them: `name.value` will
not change halfway down your function.

An input is in exactly one of four states, and there are properties for all of
them:

| | |
|---|---|
| `listening` | still being typed |
| `completed` | the `until()` condition matched — read `value` |
| `interrupted` | something ended it first; `reason` says what |
| `discarded` | you called `discard()`, and it kept nothing |

**The keyboard listens to one input at a time.** Asking for a new one
interrupts the one before it with `reason` `"REPLACED"`. That is why the tick
function above reassigns `name` rather than keeping a list — a finished input
is a finished thing, and the next question needs a new bookmark.

## The whole thing

```python
from blitspersecond import BlitsPerSecond
from blitspersecond.graphics import CYAN, WHITE, YELLOW
from blitspersecond.graphics.console import CharSet, Console, Pen
from blitspersecond.input import key

bps = BlitsPerSecond()
console = Console(CharSet.ASCII8X12)
bps.layers.add(console)

console.pen = Pen(0, CYAN)
console.print("THE GREETER")
console.pen = Pen(0, WHITE)
console.print()
console.print("What is your name?")

console.pen = Pen(0, YELLOW)
name = console.echo(bps.keyboard.input().until(key.RETURN))


def tick():
    global name
    if name.completed:
        console.pen = Pen(0, WHITE)
        console.print(f"Hello, {name.value}.")
        console.print("What is your name?")
        console.pen = Pen(0, YELLOW)
        name = console.echo(bps.keyboard.input().until(key.RETURN))


bps.start(tick)
```

Type a name, press Return, and it greets you and asks again. Typed input is
yellow because the pen was yellow when the echo started; the greeting is white
because the pen was white when it was printed.

> **TODO — imports (draft note, not for readers).** Four lines from four
> modules now, down from the six this note first recorded. Two thirds of the
> scatter went away on its own: `Pen` and `CharSet` sit beside `Console` in
> `blitspersecond.graphics.console`, and there is no `keyboard` to import at
> all — it is reached through the engine.
>
> What is left is that the top level exports none of the three things you need
> before you can write a single character. From the reader's side the natural
> surface looks close to:
>
> ```
> from blitspersecond import BlitsPerSecond, CharSet, Console, Pen   # does not work yet
> from blitspersecond.input import key
> ```
>
> `Console`, `CharSet` and `Pen` plainly belong together, because you cannot
> write one character without all three. Settle this before the chapter ships;
> the fix belongs in the package, not in the writing.

## Reference

**Building**

| | |
|---|---|
| `Console(charset)` | a console over one of `CharSet.ASCII8X8`, `CharSet.ASCII8X12` |
| `Console(cols, rows)` | a console of that grid, on whichever set tiles it |
| `bps.layers.add(console)` | put it on screen |

**Writing**

| | |
|---|---|
| `console.print(value="", at=None)` | write, then start a new line |
| `console.write(text, at=None)` | write without ending the line |
| `console.clear()` | blank the screen, cursor to `(0, 0)` |

`at=(x, y)` moves the cursor there before writing; leave it out to carry on
from wherever the cursor is.

**Position**

| | |
|---|---|
| `console.cursor` | where the next character goes, as `(x, y)` |
| `console.cursor = (x, y)` | move it |
| `console.cols`, `console.rows` | the grid size, in characters |

**Pens**

| | |
|---|---|
| `Pen(background, foreground)` | the ordinary two-colour form of a pen; index 0 is always transparent |
| `console.pen = pen` | hold a different pen from now on |
| `console.foreground = index` | change the ink, keep the rest of the pen |
| `console.background = index` | change the ground, keep the rest of the pen |
| `console.palette` | the 256 colours the indices refer to |

**Typing**

| | |
|---|---|
| `bps.keyboard.input()` | a line that fills in as it is typed, editing included |
| `.until(key.RETURN)` | what finishes it |
| `console.echo(entry)` | draw it at the cursor as it is typed; on finishing, moves to the next line without echoing the terminator |
| `entry.value` | what it spells so far |
| `entry.caret` | where the next character will go |
| `entry.listening` | still being typed |
| `entry.completed` | the `until()` condition matched |
| `entry.interrupted` | ended by something else; `entry.reason` says what |
| `entry.discard()` | abandon it |

## See also

- [Glyph layer appendix](glyph-layer.md) — the surface underneath: writing
  cells directly, per-pixel paint, custom character sets, gradient and mask
  pens. Where you go when you want a console to do something a console does
  not do.
- [Input](input.md) — keys, the mouse, and pads, beyond typed lines.
