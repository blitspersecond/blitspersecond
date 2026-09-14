# The pointer

The window is where the pointer physically is. 640x360 is what receives it.
Everything here exists to turn the first into the second, and to do it so that
a game never sees a window coordinate.

This chapter follows one movement from a window callback to a pixel on the
screen, then the button vocabulary and what a release costs when focus goes.
The ring it settles into is [stream.md](stream.md)'s.

The types are spelled `Mouse` because the device is a mouse; what a game reads
is a pointer, and a trackpad produces one too.

## Limits and costs

### The surface

```text
mouse.held(b)       it crossed its boundary and has not crossed back
mouse.pressed(b)    it crossed in this interval
mouse.released(b)   it crossed back in this interval
mouse.stream        InputStream[MouseScan, MouseFact] -- settled history
mouse.dropped       arrivals lost to the staging limit, since start
mouse.PRIMARY       the button vocabulary, on the object you already have
mouse.SECONDARY     -- PRIMARY, SECONDARY, MIDDLE, BACK, FORWARD
```

A window's two halves:

```text
MouseScan   position   where it finished -- always a pixel on the screen
            delta      internal px moved this interval, float, unclamped
            scroll     wheel clicks this interval, + away from the player
            held / pressed / released
            cleared    those releases were not the player

MouseFact   kind       MOVE | PRESS | RELEASE | SCROLL
            position   where -- never absent
            button     PRESS and RELEASE only
            delta      MOVE only, float, unclamped
            scroll     SCROLL only
            modifiers  pyglet's bitfield, on PRESS and RELEASE
            held       the set this transition's own button is in
```

The three verbs are the keyboard's, defined once and forwarded to the latest
settled window. They answer about BUTTONS: position, delta and scroll are
values and are read off the scan.

### What refuses

**Nothing raises.** Two things are dropped rather than reported:

- **buttons outside the vocabulary** — an MMO thumb-grid, a sniper trigger —
  dropped at ingress, so nothing downstream needs to know they existed and
  `held` stays a set a game can enumerate;
- **horizontal scroll and wheel tilt**, dropped in the handler rather than
  carried and ignored later.

Staging is bounded at **512 arrivals per frame**, dropped and counted in
`dropped` past that. A 1000Hz pointer offers about seventeen events per frame
at 60Hz, so the limit is two orders of magnitude clear.

Footprint is [stream.md](stream.md)'s: 28 KB for a pointer's ring at 60Hz.

## Window pixels to screen pixels

`screen_location` maps one window coordinate onto the internal screen, and it
**always answers with a pixel that is on the screen**.

Three things it has to get right:

**The origins disagree.** Pyglet's is bottom-left with y up; the screen's is
top-left with y down. The flip is `viewport.y + viewport.height - 1 - y`, and
the `- 1` is the difference between a coordinate and an extent — the topmost
row of the viewport is internal row 0.

**Floor, not truncate.** `int()` rounds toward zero, which sends a coordinate
one short of the left edge to column 0 instead of to the −1 the clamp is
expecting. `x` can be fractional at all because pyglet divides by the display
scale on the way out, so a HiDPI window delivers floats.

**The viewport is the only argument**, because it already carries the screen's
size — `width // scale` is the internal width. There is no second copy of
640x360 to pass in, and no way to hand this a viewport and a screen that
disagree.

### Out of bounds clamps, per axis

A pointer out in the letterbox margin is held at the edge it left through, one
axis at a time, so it slides along that edge rather than stopping dead.

**That is the behaviour the player already has when there is no margin.** Fill
the display exactly and the OS confines the pointer to it: push right and x
sticks while y keeps tracking. The margin is an artefact of the window not
being a whole multiple of the screen, which is nothing a player can see — so
the engine synthesises what the OS would have done, and a game cannot tell the
two situations apart.

**There is no `inside`.** A flag saying whether to believe the position exists
only if the position can be wrong, and this one cannot. It would also ask about
the wrong rectangle: 640x360 is the receiving surface, not the window. A
pointer off the window simply stops producing events, and there is nothing to
record — which is why `on_mouse_leave` is not handled at all.

## Position clamps, `delta` does not

Two different questions, and the first cannot produce the other.

`position` answers *where is it*. It is clamped and whole, so at the screen
edge consecutive positions report a stationary pointer while the hand keeps
moving.

`delta` answers *what did it do*. It is summed unclamped across the interval,
precisely so a drag that runs into the edge keeps its distance. The implicit
pointer grab delivers motion from outside the window, and clamping must not eat
it.

**`delta` is float, and that is not a violation of the whole-pixel rule.** At a
4x window a single window pixel is a quarter of a screen pixel, and that is
real motion which must not round away. The whole-pixel rule is about what the
engine *draws* — a fractional sprite position samples between texels and the
art shimmers. A pointer reading is a **measurement of where a physical device
is**, and a game casts it to an integer when it draws a cursor or tests a
click.

Before the pointer has been seen at all, `position` is the centre of the
screen. That is a **declared default, not a measurement**: pyglet has no "where
is the pointer", so until an event arrives the engine genuinely does not know.
The centre is the least wrong thing to say — visible, neutral, and not read by
a player as a statement — where `(0, 0)` would look like a reading and put it
in a corner.

## Every fact is positioned

This is the fact type's reason for existing. A click is a question about
*where*, and collapsing a frame's events down to one end-of-interval position
answers it with wherever the pointer finished. Drag quickly and click, and the
click is reported somewhere it never happened.

Because `screen_location` always answers, the field is never absent and a
reader never has to handle a fact that does not know where it was.

## A transition carries the held-set its own button is in

The same law the keyboard keeps, for the same reason:

> **A transition carries the held-set in which its own button is present.**

A press carries the set after its button was added, a release the set before it
was removed. And as on the keyboard, it holds without exception at one cost: a
release for a button the engine does not believe is down produces no fact. That
happens after a clear, where the synthesised release has already reported the
button going up.

## Raw in staging, mapped at the cut

Handlers record **window** coordinates and return. The viewport is read once
per cut and the whole interval is mapped through it.

Two things follow. A window resized mid-frame maps that frame consistently
instead of half one way and half the other. And the mapping cost is paid per
frame rather than per event, which at 1000Hz is seventeen times fewer.

The ingress takes a **callable** for the viewport rather than a display,
because it is built before the window exists — the bus holds no queue, so an
ingress that subscribed later would miss the events the window dispatches as it
comes up. A headless test is the beneficiary rather than the reason, and gets
to state the geometry it maps against instead of inheriting one.

A drag is recorded as plain motion. The buttons have their own press and
release events, so there is nothing a separate drag fact would add.

**Entering the window carries a position and no motion.** The pointer may have
travelled any distance while it was away, so claiming a MOVE would put a jump
in the path of anything reconstructing a gesture.

## The ideal mouse

The same move the pad vocabulary makes: a device class converges on a fixed
point — a settled classic plus the one generation of extensions that proved
load-bearing — and everything bolted on afterwards is overbinding or junk. Two
buttons plus a wheel is the 1996 IntelliMouse, and it is where the form stopped
moving.

| | |
|---|---|
| **Guaranteed** | `PRIMARY`, `SECONDARY`, one vertical wheel, one position — the floor every device meets however cost-shaved, a cheap gaming mouse or a laptop trackpad |
| **Listen, never require** | `BACK` and `FORWARD`, the 1999 Explorer thumb pair — the one extension that became universal, and absent on plenty of honest mice. `MIDDLE` is never physically absent but is a click bound onto an axis control: stiff to press without scrolling, easy to hit while scrolling |
| **Refused** | everything else — MMO grids, sniper triggers. Dropped at ingress |

**Listen-never-require is design law rather than a check**, and it has to be:
window events cannot reveal a button's absence, because **a mouse only proves a
button by pressing it**. A pad states the same law about its axes for the same
reason.

Wheel tilt and horizontal scroll are refused on the `MIDDLE` grounds taken
further — the wheel already has a job, tilt is a second input bound onto the
same part, and it is undetectably absent on many mice, so it could only ever
duplicate an input that has to exist anyway.

### Names mean, they do not locate

`PRIMARY` and `SECONDARY` alias pyglet's `LEFT` and `RIGHT` **values**, and the
names are the point. Handedness is the OS's job: libinput and the compositor
apply the left-handed mapping below the engine, so by the time an event arrives
pyglet's LEFT already *is* the player's primary button. Swapping again here
would double-swap a left-handed setup, and an in-engine swap toggle must never
be added.

This is the keyboard's layout rule in another costume: the OS has applied the
player's own configuration, and the engine reads the result rather than the
hardware.

**There are no mappers here and never will be**, which is the sharpest
difference from pads. The OS already normalises every mouse onto the ideal —
every side button on every surveyed mouse arrives as `BACK` or `FORWARD` — so
there is no family to name and no label to print. Capability bitmaps disagree
with reality (one surveyed mouse claims sixteen buttons and sends five), which
is another way of saying the same thing: events are truth.

## Focus loss, and what a resume keeps

Focus loss releases every held button as real edges, so a drag watched for its
release cannot stick. The clear is queued rather than applied in the callback,
so a press that landed just before focus was lost still gets its press edge in
that interval and *then* its release; and it unwinds one button at a time, so
each release carries the set as it stood at that moment. `cleared` says the
releases were not the player.

**A resume keeps the position.** Staged motion is discarded, because it
describes a game nobody was playing — but wherever the pointer is now is the
truth whether or not anybody was watching. Position is state, not history.

That is one of three different answers the devices give to one `flush` call,
and the difference is the reason it is one call: the keyboard assumes neutral
because it cannot ask, the pointer keeps its position, and a pad asks the
device. See [input.md](input.md).

## What it is not

**Not a cursor.** Replacing the OS cursor image is refused: a hardware cursor
moves in window pixels outside the present transform, with no palette, no CRT
pass and the wrong pixel grid. The route is hide and draw — the engine's entire
share is hiding the OS pointer, and the drawn cursor is game code: a sprite at
`position`, palettised, shaded, integer-scaled, collidable, and
context-sensitive in a way a hardware cursor can never be. The price is that it
trails the OS pointer by one frame, which is what every DOS game paid for a
cursor that belongs to the picture.

**Not a UI toolkit.** Dragging, hovering, hit-testing and click targets are
built from positioned facts and the collision planes. The engine produces masks
and facts; the game does the ANDs and the meaning.

**Not captured, and never will be.** There is no pointer grab, no exclusive
mode and no mouselook. Stealing the pointer is hostile to the machine the game
is running on — the same law as handedness, respect the layer above you — and
the lived test is a video on a second screen: skipping it mid-game is a normal
act, and capture taxes it with a release combo the player has to know. In
fullscreen, alt-tab already does everything such a combo would.

The one job capture genuinely has is **unbounded relative motion with no
button held** — the pointer as a dial, mouselook or a paddle. A button-held
drag already gets motion from outside the window through the implicit grab,
and `delta` is unclamped precisely so it survives the edge. Even the genre
capture was invented for shipped its founding game on keyboard turning.

One residue, accepted: with the OS pointer hidden in a window, it can walk off
that window while the drawn cursor clamps, and a stray click lands elsewhere.
That is the desktop working as designed, not something to fix with a jail.

**Not a UI framework.** `mouse.pressed(PRIMARY)` says a button crossed its
boundary somewhere; *what was under it* is a question for a collision plane,
and the engine does not answer it for you.
