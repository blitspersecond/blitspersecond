# Input

What the player did, settled onto the engine's frame boundary.

```python
bps.keyboard.stream.latest      what the keyboard did, in the frame just closed
bps.mouse.stream.latest         the same interval, on the pointer
pad.stream.latest               and on a pad -- the same shape, every device
```

## The shape

**Every device produces the same thing: one window per frame, holding what was
true and what happened.** A window is a **scan** — the state as of this
moment — beside its **facts**, the ordered transitions within the interval.
Nothing else about these devices is alike.

```text
     the window system                       the kernel
     keyboard, pointer                       pads, one file each
            │                                       │
     ┌──────┴──────┐                         ┌──────┴──────┐
     │   ingress   │  normalise, stage,      │   ingress   │
     │             │  fold the held-set      │             │
     └──────┬──────┘                         └──────┬──────┘
            │                                       │
    ════════╪═══════════ T H E   C U T ═════════════╪════════
            │            once per frame             │
     ┌──────┴──────┐                         ┌──────┴──────┐
     │   window    │  a scan, and facts      │   window    │
     └──────┬──────┘                         └──────┬──────┘
     ┌──────┴──────┐                         ┌──────┴──────┐
     │    ring     │  half a second          │    ring     │
     └─────────────┘                         └─────────────┘

     storage is per device. the numbering is shared.
```

**The engine says cut.** No source decides when a frame ends. One cut signal
reaches every device, so frame N means the same interval on all of them — which
is what makes per-device storage safe, and what lets two devices' windows for
one frame be read side by side with nothing reconciling clocks afterwards.

**Transitions expire. State does not.** A key weighed down for an hour was
pressed thirty thousand frames ago and that press left the ring almost
immediately; the held-set it was folded into has carried it ever since. The
ring bounds how far back a motion can be reconstructed. It has nothing to do
with how long a control can be held.

## The chapters

### The frame

| | |
|---|---|
| [stream.md](stream.md) | the retention ring: a window per frame, how deep, and what that depth is a statement about |

### The devices

| | |
|---|---|
| [keyboard.md](keyboard.md) | window callbacks in, settled windows out, and the two ways a release fails to arrive |
| [pointer.md](pointer.md) | window pixels to screen pixels, the ideal mouse, and why position clamps where delta does not |
| [pads.md](pads.md) | the closed vocabulary, a pad's window, identity on first deliberate use, and which pad is whose |

### Reading a device

| | |
|---|---|
| [evdev.md](evdev.md) | the Linux transport: find, name, calibrate, read |

### Questions that outlast a frame

| | |
|---|---|
| [request.md](request.md) | a question put to the player, answered without blocking: four states, and who advances them |
| [text.md](text.md) | a line of typing, what it consumes, and why it is not in the ring |

### Not built

[ports.md](ports.md) — four durable routes for four people on a sofa. Deferred
until a game needs it, and the page says why.

## The cut

The cut is the whole subject. Everything above it is a transport and everything
below it is history.

A frame's shape is: cut, then the game runs, then a variable-length tail until
the pacer releases the next one. **So the cut sits at the head**, before the
game is handed control, and the events it finalises arrived during the previous
frame — overwhelmingly during that frame's variable tail.

`Loop._advance` runs `metrics.advance()`, then `clock.tick()`, then the game.
**The cut goes beside `metrics.advance()`, before `clock.tick()`**, where
`clock.frame` still holds the number of the frame just ending. The window is
stamped with it directly. After the tick, the game runs as frame N and reads
the window numbered N−1.

Placing it after the tick would mean storing `clock.frame - 1`, which is the
arithmetic this arrangement exists to avoid. `MetricBuffer`'s rule holds here
unchanged — *no caller ever passes, holds, or looks up a frame number; only the
flush knows one, once.* The cut is that flush, and it asks the clock rather
than calculating.

**It is not an addition to the loop but a member of a phase the loop already
has.** Metrics closes the interval's measurements, input closes the interval's
events, and then the clock turns. Nothing about input needs its own hook.

Two consequences a game can rely on. **A value settled at the cut cannot change
under a tick**: reading the same window twice in one frame gives the same
answer, so nothing needs a lock or an ordering rule. And **every frame appends
a window, including empty ones** — skipping them would quietly turn "the last
thirty frames" into "the last thirty frames that had input", which looks
correct in any test that presses buttons and is wrong exactly when a player
pauses mid-motion.

## Events, not samples

A sampled history — one value per control per frame — can answer *was this held
six frames ago* and can never answer *what order did these arrive in*. An event
history answers both, because **a sample is derivable from events and the
converse is false.**

That asymmetry is why `facts` is a sequence rather than a table, and it is what
a quarter-circle needs: forward, down, down-forward, two of which can land
inside one 16.67ms frame.

**Order survives the cut; time does not.** Both pad transports stamp every
event in microseconds and the engine discards it. Within a frame everything is
simultaneous by construction, and a sub-frame timestamp would be a second game
clock — it would let a game derive a velocity the simulation cannot act on,
because the next thing that happens is a fixed step regardless of when inside
the interval the input landed.

## One flush, three answers

When ticking stops and starts again — a suspend, a resume — wall time has
passed that no frame accounts for. What accumulated meanwhile describes a game
nobody was playing, and every device discards it. What they cannot agree on is
what is true **now**:

| | |
|---|---|
| the **keyboard** assumes neutral | there is no way to ask the window system what is still down, and a key let go during the pause would stay held for good |
| the **pointer** keeps its position | that is state rather than history, and it is wherever it is whether or not anybody was watching |
| a **pad** asks the device | it can. A player holding a direction through a pause still holds it, and synthesising a release they did not perform would be inventing input |

One call, three answers, each correct for its device — and the disagreement is
the reason it is one call rather than three.

## A transition carries the held-set its own control is in

The law the query layer rests on, and it holds on every device:

> **A transition carries the held-set in which its own control is present.**

A press carries the set *after* its control was added; a release the set
*before* its control was removed. Both therefore contain the control the fact
is about, which is what lets a question be evaluated at each transition rather
than by replaying the interval — for a press, *did adding this make the
expression true*; for a release, *was it true while this was present, and is it
not now*.

It holds without exception, at one stated cost: **a release for something the
engine does not believe is down produces no fact.** Emitting one would mean a
fact carrying a set its own control is missing from, which is the single thing
a fact cannot be allowed to be.

## Asking about controls

One algebra, every device. A `Button`, a key symbol, a `MouseButton` and a
`Direction` are all terms, and the shape of a question does not depend on which
kind it holds:

```python
Button.FACE_SOUTH | Button.START        either of these
key.LCTRL & key.LALT                    both at once
mouse.PRIMARY & ~mouse.SECONDARY        one, without the other
~Direction.NEUTRAL                      the d-pad is doing something
```

**The operators are defined rather than left alone, and they must be.** Three
of the four vocabularies are integer-valued, each for its own good reason: a
key symbol and a mouse button carry the transport's own number, so nothing is
renumbered at ingress, and `Direction` is an `IntEnum` because numpad notation
*is* the vocabulary there, so that 236 means on screen what it means on paper.
Integers already implement `|`, `&` and `~` arithmetically, so an undefined
operator does not raise: `LCTRL & LALT` evaluates to `LSHIFT`, and `DOWN |
DOWN_RIGHT` to `DOWN_RIGHT`. Both are real members, so nothing downstream can
tell. Defining the operators is what removes that, and a vocabulary lists
`Composable` before its enum base so they shadow `int` rather than sit behind
it.

A term whose meaning only a device can supply is resolved late and refuses
until it is. `Button.ACCEPT` is the case: which physical button means confirm
is a fact about a pad's *family* — south for Xbox and Sony, east for Nintendo —
and a question is written before anybody has picked a pad up.

**Three verbs ask it, and one sentence defines each.**

| | |
|---|---|
| `pressed(x)` | it crossed its boundary in this interval |
| `held(x)` | it crossed and has not crossed back |
| `released(x)` | it crossed back in this interval |

**An expression has a boundary too**, and crosses it when the last thing needed
to satisfy it lands — so a chord is *pressed* by whatever completes it, and
something already satisfied is held rather than pressed again. That is one rule
rather than a separate set for compositions.

**Only a term from a vocabulary may be asked**, and a bare number is refused by
all three verbs rather than tested. An unrelated value can look exactly like a
control — `Motion.LEFT` and `key.LEFT` are both 0xff51 — so membership would
answer questions nobody asked. A device that published transport integers as
its public spelling would therefore be publishing something its own verbs
refuse, which is why every vocabulary a game names is a type.

A bare control is answered from the scan. A composed one is folded over the
interval's facts, evaluating the expression at each transition — which is what
the held-set law is for, and why each fact's set is turned into the state
either side of it rather than read as it stands. The moment a negated control
is released is the moment `jump & ~crouch` becomes true, and a release carries
the set its own control is still in.

**A control with no boundary is not asked about.** A trigger has one at its
threshold and the d-pad has one at each position, so both answer. A stick does
not: no deadzone is applied anywhere, because a deadzone is a judgement about
intent belonging to whoever reads it — so an axis stays a value.

## Where the devices genuinely differ

| | keyboard | pointer | pads |
|---|---|---|---|
| transport | the window system | the window system | evdev on Linux; GameInput on Windows |
| how many | one, by construction | one, by construction | unbounded, no cap |
| identity | none needed | none needed | a fingerprint, and a stable id on first deliberate use |
| normalisation at ingress | the identity function | window pixels to screen pixels, transport button values to the vocabulary | transport codes to the closed vocabulary |
| after a gap in ticking | assumes neutral | keeps its position | asks the device |
| absence of a control | — | a mouse only proves a button by pressing it | a pad only proves an axis by moving it |
| vendor differences | the OS has already applied them | the OS has already normalised them; no mappers, ever | a mapper per family, editorial only |

**The keyboard and the pointer go through the window system; pads go direct**,
and the requirements are what differ rather than the histories. Symbols arrive
already resolved through whatever layout the player chose and buttons through
their handedness setting, so reading those devices directly would bypass work
the OS has already done correctly — and doing it while another window has focus
is keylogger-shaped besides. A pad has no such personalisation to preserve, and
it does have an identity question the window system cannot answer.

## Transport fidelity is not uniform, and is not hidden

The ordered facts are only as faithful as the transport beneath them, and the
three do not agree on what an input event even is.

| | evdev (Linux pads) | GameInput (Windows pads) | pyglet (keyboard, pointer) |
|---|---|---|---|
| the unit | a `SYN_REPORT` batch of `(type, code, value)` | a *reading*: one raw device packet | one dispatched window event |
| what it carries | only the controls that changed | the complete device state | that event's full context |
| history kept | none: read the file or lose it | half a second, by the runtime | none: the bus holds no queue |

**Only a transport with simultaneity groups can report a diagonal honestly.**
evdev hands over the kernel's own groups, so two axes the kernel says moved
together become *one* `DIRECTION` fact rather than an intermediate position the
player never passed through. The keyboard could never have shown that.

On Windows the reader walks `GetNextReading` from its previous packet rather
than sampling `GetCurrentReading` once per tick. At 60 Hz on the measured Xbox
pad, current-state polling saw 405 changes while the history walk retained 521,
including batches of three packets in one tick. The history cursor therefore
preserves paths that a final-state poll has already flattened.

## Where input stops

The engine reports which physical controls did what, and when. **It assigns no
meaning to any of them.** There is no global Escape, no binding table, no
actions layer, and no toggle-on-press — a game reads windows and decides what
they mean.

The boundaries with the neighbours, each owned on the other side: the frame the
cut sits in is [loop.md](../engine/loop.md)'s, and the number it stamps is
[clock.md](../engine/clock.md)'s. Drawing a typed line is
[console.md](../graphics/console.md)'s; a line exists whether or not anything
draws it. Hit-testing a pointer is a question for a collision plane in
[collision.md](../graphics/collision.md), and a drawn cursor is a sprite,
because a hardware one moves in window pixels outside the present transform.

## Still open

**The query vocabulary is built.** What is not, and belongs a storey up, is a
**binding layer** — rebinding tables, toggle-on-press, and an action that means
the same thing on a key or a button. This reports which physical controls did
what, and when.
Until they land, `bps.keyboard`, `bps.mouse` and a `Pad` hand back the ingress
itself and a game reads `stream.latest.scan` directly. Every device chapter
says the same thing under *What it is not*, and this is the one place it is
said once.

**Ports is deferred**, not missing — see [ports.md](ports.md).

**The wider Windows hardware survey remains open.** GameInput history,
disconnect/reconnect and cursor expiry are measured; names, control claims and
link identity still need the rest of the physical pad set.
