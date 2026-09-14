# Text

Asking somebody to type their name is not the same problem as reading a
keyboard. A key is down or it is not; a line of typing is a thing that
accumulates, gets edited in the middle, and may take a minute. `TextInput` is
the second of those, and it deliberately does not live in the machinery built
for the first.

```python
name = console.echo(bps.keyboard.input().until(key.RETURN))

if name.completed:
    console.print(f"Hello {name.value}")
```

The lifecycle — listening, completed, interrupted, discarded — is
[request.md](request.md)'s. Drawing the line as it is typed is
[console.md](../graphics/console.md)'s. This chapter is what happens in
between: the buffer, what it consumes, and why it is not in the ring.

## Limits and costs

### The surface

```text
keyboard.input(initial="")   begin a line; optionally pre-filled
line.until(symbol)           name the key that finishes it; returns the line
line.value                   what it spells, right now -- readable while typing
line.caret                   insertion point, in characters from the start
line.discard()               abandon it; the text goes too
```

Plus the four states and their predicates, from `Request`.

### What refuses

`until()` refuses the same three things `Identification.until` does —
narrowing twice, narrowing after the line has stopped listening, and a
non-symbol argument. Nothing else raises. `value` is readable in every state,
including while listening, because a half-typed line is a perfectly good
partial answer.

### There is no cap on a line

The buffer grows for as long as the line is open, and nothing truncates it.
That is the point rather than an oversight: a line's length is the game's
business and its lifetime is the console's, and the engine has no figure it
could defend. What the engine bounds is its own history — half a second of it —
and this is deliberately not part of that.

## Why it is not in the ring

The engine retains half a second of input, sized for reconstructing a
quarter-circle on a stick. A person typing their name may take a minute over
it. **One mechanism cannot serve both lifetimes**, and the failure is not
symmetrical — making the ring long enough for typing would make every device's
retention unbounded whenever a console line happened to be open.

So a line reducer owns its own buffer, entirely separate, and the engine
retains nothing on its behalf.

This is the worked example for every long-lived input. Anything else that wants
long history owns it the same way: read the windows every frame and keep your
own state. See [stream.md](stream.md), *Anything long-lived owns its own
history*.

### Fed by the cut, not reading the ring

A reducer is handed its events directly as they are frozen, rather than
fetching them from the stream.

**It is the one consumer whose correctness depends on missing nothing**, so it
must not depend on a retention depth it does not control. Reading the ring
would make a line's correctness a function of the authored frame rate and of
how long the game took to look — which is exactly the coupling the separate
buffer exists to remove.

The window still goes into the ring as well. Capture is a tee, not a diversion:
physical key state stays truthful while a line is listening, so a game can read
held keys and a typed line in the same frame.

## What a line consumes

Three of the five kinds of keyboard fact mean something to a line, and it takes
them in arrival order.

**`TEXT` is content, and is inserted at the caret.** Characters arrive already
through the player's layout with the platform's lock and modifier policy
applied — the engine never assembles a character from a key symbol.

**Control characters are never content.** Anything below space is dropped, and
so is delete. Return is the case that matters: it arrives as *both* a press and
a `"\r"`, the press is what finishes the line, and the character must not be
typed into it either way.

**`MOTION` is editing**, and the motions are the platform's own — backspace,
delete, left, right, home, end. Pyglet resolves them from the platform's key
bindings, so backspace on a Mac and backspace on Linux arrive as the same
motion whatever key produced it. A motion the line does not understand is
ignored.

**`PRESS` of the terminator completes it**, and nothing after that is consumed.

Everything else — releases, repeats, presses that are not the terminator —
passes the line by. They are in the window, and a game reads them there.

### The terminator is not in the line

`until(key.RETURN)` names the key that *ends* the line, and that key's
character never appears in `value`. The line spells what was typed and stops.

That also fixes the ordering that makes a line read correctly. Facts are
consumed in arrival order, so `"name"` followed by `"\r"` spells the name
*before* the Return completes it — rather than completing on the press and
losing the last characters that arrived in the same frame.

## The caret

Editing happens at the caret, not at the end. Backspace removes the character
before it, delete the one after, left and right move it, home and end take it
to either end of the line. Each is clamped rather than refused: backspace at
position 0 does nothing, and right at the end does nothing.

`caret` is exposed because a game drawing the line needs it — it is where a
caret glyph belongs, and where the next character will go.

## The value holds still for a tick

`value` is settled for the whole of a frame. The reducer is advanced at the
cut, before the game is handed control, so a line cannot change halfway down a
function that is reading it, and two reads in one frame agree.

That is what makes the line safe to draw and test in the same frame without
ordering rules — a console can paint it, a game can check `completed`, and both
see the same thing.

## One line at a time

The keyboard holds the active line and holds exactly one. Asking for another
interrupts the one before it with reason `REPLACED`, and a line that has
finished is released by the cut that finished it.

A finished or interrupted line is inert but still readable: `value` keeps what
was typed, so a game that notices completion a frame later still gets its
answer. `discard()` is the one thing that empties it, because a caller
abandoning a line has said it wants nothing from it.

## What it is not

**Not a text field.** There is no selection, no clipboard, no word motions, no
undo, no scrolling and no multi-line. A line is a line. The motions it
understands are the ones a platform sends for a single-line entry, and anything
more is a game's to build on top of `value` and `caret`.

**Not a replay over the stream.** The line holds characters, not a range of
windows to be reconstructed on demand. Reducing as it goes is what keeps its
cost proportional to what was typed rather than to how long the question has
been open, and what lets it outlive a ring it was never stored in.

**Not the console's.** A line exists whether or not anything draws it —
`console.echo` observes one and paints it, and the keyboard is what creates,
feeds and finishes it. A game with no console can ask a question just as well.
