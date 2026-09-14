# The input stream

Every device this engine reads produces the same thing: a **window** per frame,
holding what was true and what happened. The stream is what keeps them — a
bounded ring of frozen, frame-numbered windows, one ring per device.

It is the only piece of the input subsystem that is genuinely device-agnostic,
and it is deliberately small. **The ring knows how many windows to keep and how
to hand them out, and nothing else** — a device supplies its own scan type and
its own fact type, and this holds them.

A window can also answer questions about itself, and that is the one thing here
that reads a fact. What it needs is a **protocol rather than a device**: a
`kind` whose enum has `PRESS` and `RELEASE`, the held-set the law puts on every
transition, and a `control` naming what the fact is about. All three devices
already meet it, which is why `held`, `pressed` and `released` are written once
— see [input.md](input.md), *Asking about controls*.

This chapter follows one window from the cut that freezes it to the moment it
cycles out. What a scan contains and what a fact is belong to the device
chapters; what the cut is and where it sits in the frame belongs to
[input.md](input.md).

## Limits and costs

### The surface

```text
stream.latest      InputWindow | None   what is true now
stream.all         tuple, oldest first  what happened recently
len(stream)        windows retained right now
stream.capacity    windows retained when full
stream.append(w)   retain a settled window -- the cut calls this, nothing else

window.held(x)     it crossed its boundary and has not crossed back
window.pressed(x)  it crossed in this interval
window.released(x) it crossed back in this interval
```

Five names on the ring, and `append` is not a game's to call. Three on a
window, and a device forwards all three to its latest one, so
`bps.keyboard.held(key.SPACE)` and `...stream.latest.held(key.SPACE)` are the
same question. Before the first cut a device answers `False` to each, which is
the truth rather than a guard.

### How many windows

Capacity is `ceil(rate * WINDOW_SECONDS)` with `WINDOW_SECONDS = 0.5`, so it is
a function of the authored rate and nothing else. Exhaustively, over the five
rates a game may be authored at:

| authored rate | windows retained | span |
|---|---|---|
| 15 | 8 | 0.53s |
| 20 | 10 | 0.50s |
| 30 | 15 | 0.50s |
| 40 | 20 | 0.50s |
| 60 | 30 | 0.50s |

Fifteen rounds up rather than down, which is the only rate where the span is
not exactly half a second.

### What refuses

**One thing.** `append` requires the next frame number in sequence and raises
`ValueError` naming both frames if it does not get one:

```text
input windows must be appended in frame sequence: 41 then 43
```

A gap means a device was not cut on some frame, which would make "the last
thirty frames" describe an interval longer than thirty frames without saying
so. It is a defect in the boundary rather than a condition to handle, so it is
loud.

Nothing else refuses. Before the first cut `latest` is `None` and `all` is
empty; neither raises, and there is no state in which a read has to be guarded
by a length check.

### Footprint

A ring is a bounded steady state: it replaces rather than grows, so the cost is
paid once. Measured with `tracemalloc` over 400 independent rings at 60Hz:

| ring, 30 windows | facts per frame | footprint |
|---|---|---|
| keyboard, idle | 0 | 25 KB |
| pointer, idle | 0 | 28 KB |
| pad, idle | 0 | 37 KB |
| pad, both sticks and both triggers in motion | 6 | 97 KB |

A fact costs about 343 B. **Six a frame is a ceiling the hardware sets, not a
measurement of how hard somebody was trying**: a pad has exactly six continuous
controls, and continuous controls are the only ones that can produce a fact
every frame. A button contributes two facts across an entire hold however long
it is held, and a d-pad one per direction change.

So the worst cases are:

- keyboard, pointer and one pad fully worked — **150 KB**
- keyboard, pointer and four pads fully worked — **441 KB**

**It never reaches the collector.** Six devices, four of them fully worked, over
3600 frames: zero collections in every generation. That is structural rather
than lucky — a window is a frozen dataclass over frozensets, tuples, floats and
enum members, with no cycle in it, so each one dies by refcount the instant it
leaves the ring, and an allocation balanced by a deallocation never moves gen0
towards its threshold.

## A window is two things

A window carries a **scan** and a tuple of **facts**, and they answer different
questions. Neither is derivable from the other in the direction that matters.

The scan is **state**: what was held as of this cut, plus the edges belonging to
this interval. It is not stored in the ring so much as copied into it — the
device keeps a live held-set folded forward from everything it has ever seen,
and every cut copies that set into the window.

The facts are **history**: the transitions within the interval, in the order
they arrived.

**Transitions expire. State does not.** A key held for an hour was pressed
thirty thousand frames ago and that press left the ring almost immediately.
Nothing is lost, because the press was folded into the held-set when it
arrived and every window since has carried it forward, so reading the latest
scan gives the right answer indefinitely. The ring bounds how far back a
motion can be reconstructed; it has nothing to do with how long a key can be
held.

The facts are ordered rather than sampled, and the asymmetry is the reason: a
sample is derivable from events and the converse is false. One value per
control per frame can answer whether a button was down six frames ago and can
never answer what order things arrived in. [input.md](input.md) owns that
argument; here it is why `facts` is a sequence and not a table.

A window is frozen because readers hold it. The cut is the only thing that
creates one and nothing mutates it afterwards, so game logic reading twice in
one frame sees the same answer both times.

## Numbered, not merely ordered

Windows carry the frame number they belong to, taken from the clock. The ring
computes nothing and checks one thing: that each window follows the last.

That check is what makes `all` mean an interval rather than a collection. A
recogniser reading a roll needs to know that consecutive windows describe
consecutive frames, and there is no way to recover that from the windows
themselves once a gap has been absorbed silently.

Where the number comes from — the cut sitting at the head of the frame, before
the clock turns, so the window is stamped directly rather than calculated — is
the boundary's business, in [input.md](input.md) and [loop.md](../engine/loop.md).

## Two reads, two questions

`latest` is **state**. It is the every-frame mainline: `latest.held(key.W)`
answers "is W down", and it is correct however long ago W went down. It is
`None` only before the first cut, which is why a device forwarding the verbs
answers `False` rather than reaching into it.

`all` is **history**. It is every retained window, oldest first, and it is the
reconstruction read — the reason the ring has depth at all.

**There is no count to ask for.** The ring is already sized to the motion
requirement, and nothing longer is kept, so a `recent(n)` would be a caller
re-deciding a depth the engine has derived. A recogniser takes all of it and
narrows however its own move needs.

Neither read can fail and neither can be starved. `all[-1] is latest` whenever
the ring holds anything.

## How deep, and why that deep

The depth is derived rather than chosen, and what derives it is a statement
about hands.

**The ring exists to reconstruct a motion input** — a quarter-circle, a
dragon-punch — from however unevenly a person performed it. It does not exist
to hold a combo. A combo is a state machine, and that is game-side work: the
game advances it every frame off the windows and keeps whatever long-lived
state it wants. The engine's job stops at making the motion reconstructible.

**The window is wall-clock, not frames.** A person takes the same span of real
time to roll a quarter-circle whether the game is authored at 15 or at 60, so
the capacity is a duration times the authored rate. Spelling it
`ceil(rate * WINDOW_SECONDS)` rather than `ceil(rate / 2)` is what makes the
duration a thing with a name that can be read and changed.

**Half a second is generous on purpose, because the costs are asymmetric.**
Motion-input allowances in commercial fighting games run on the order of 8-20
frames at 60Hz, so half a second is comfortably more than the requirement
needs. Too short silently fails to reconstruct the motion of a slow or unsteady
player, with no symptom anyone would notice. Too long costs the footprint
above, once. Round up.

**GameInput's runtime retains exactly half a second too**, and gives the same
reason in the same breath: *"The input stream maintains the last half second of
historical readings in its buffer"*, for games that *"need to walk the input
stream to get a complete picture of all the input state changes that have
occurred since the previous frame."* Two processes reaching one number is the
strongest evidence available that it is a fact about hands rather than a taste.

**A low authored rate gets coarser reconstruction, and that is the author's
choice.** At 15fps a 200ms quarter-circle spans about three frames, so three
windows describe it. `common/framerate.py` already states that a lower rate
means coarser input granularity; this is that, arriving where it was predicted.

### Anything long-lived owns its own history

A short ring is safe because nothing is asked to live in it that does not fit.

A console line may stay open for minutes, and its text is not engine history:
the console keeps its own buffer, entirely separate, and reduces text and edit
motions into it as they arrive. It grows for as long as that line is open,
because that is the console's business and its lifetime.

**A reducer is not a ring reader.** It is the one consumer whose correctness
depends on missing nothing, so it must not depend on a ring depth it does not
control — the cut hands it its events directly as they are frozen. The ring
stays purely a reconstruction window, and nothing about it has to be sized for
typing.

Anything else that wants long history owns it the same way: read the windows
every frame and keep your own state. The engine retains no more than the motion
requirement needs.

## Every frame appends, including empty ones

A frame in which nothing arrived still appends a window, holding no facts.

Skipping empty frames would silently change what the ring means. It would stop
holding "the last N frames" and start holding "the last N frames **that had
input**", which at rest can span many seconds of wall time. The failure is
quiet: it looks correct in any test that presses buttons, and is wrong exactly
when a player pauses mid-motion, which is the case the ring exists for.

An empty window is one small object and the interned empty tuple. There is no
saving worth the ambiguity.

## What it is not

**Not a leniency buffer.** In fighting-game usage an input buffer holds an
input so the move still comes out when the player was slightly early. That is
game feel and it belongs to the game. What this holds is history for
reconstruction, and the distinction is worth the name: call it a buffer and its
depth is eventually tuned as leniency, at which point the engine is deciding
how a game feels. `WINDOW_SECONDS` is not a difficulty setting.

**Not one event ring with frame markers.** The tempting optimisation stores
events contiguously and records an index per cut, so an empty frame costs an
integer rather than an object. It also makes retention non-deterministic: an
event-sized ring evicts faster while somebody is mashing, so "the last N
frames" quietly becomes "the last N frames unless it was busy" — which is
precisely the condition the ring exists to survive. The footprint above is what
the deterministic version costs, and it is not enough to trade for.

**Not a merged stream.** Each device owns its own ring. The transports are
already per-device, and one frame number means the same interval on all of
them, so a merged stream would be work spent building a representation that is
immediately undone. [input.md](input.md) owns the boundary that guarantees the
numbering lines up.

## Still open

**`all` has no caller yet.** The verbs answer from `latest`, and the depth
behind it is there for a recogniser that does not exist — a game writing one
today reads `all` and folds it itself. The shape is not speculative for being
unread: three devices that agree on almost nothing else — a bus-delivered
keyboard, a pointer that needs the viewport, and a pad the engine polls from a
file descriptor — all settle into it unchanged, and one implementation of the
verbs serves all three.

**`stream.all` against `numpy`'s callable `.all()`.** They are unrelated and
both appear in this tree. Recorded rather than resolved.
