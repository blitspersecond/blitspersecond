# Pads

A gamepad is a thing a person owns and brings. This chapter is what the engine
says one has, what it reports, and how a game gets hold of the one somebody
picked up.

```python
pad = bps.pads.gamepad                      # whoever picked one up
if pad and pad.pressed(Button.FACE_SOUTH):
    jump()
```

Finding a pad and reading bytes off it is [evdev.md](evdev.md)'s. Seating four
people on a sofa is [ports.md](ports.md)'s, and is not built. Retention is
[stream.md](stream.md)'s, and the non-blocking question is
[request.md](request.md)'s.

## Limits and costs

### The surface

```text
pad.held(x)                  it crossed its boundary and has not crossed back
pad.pressed(x)               it crossed in this interval
pad.released(x)              it crossed back in this interval

for pad in bps.pads          every CONNECTED pad -- the mainline
len(bps.pads)                how many are connected
bps.pads[id]                 by stable id; IndexError if not connected
bps.pads.get(id)             the same, forgiving
bps.pads.gamepad             the pad a single-player game listens to, or None
bps.pads.known               every pad this session has seen, departed included
bps.pads.identify()          ask which pad somebody is using, without blocking
bps.pads.desyncs             kernel drops across every pad, this frame

pad.stream                   settled history
pad.id                       stable number, or None until it has been used
pad.fingerprint              what this connection is called
pad.name                     the name a person would use
pad.mapper                   this pad's label family
pad.connected                False once its device has gone
pad.desyncs                  kernel drops on this pad, this frame
```

A window's two halves:

```text
PadScan   held / pressed / released   of `Button`
          axes                        every Axis, always present
          direction                   where the d-pad settled
          resynced                    these edges were not observed
          disconnected                the pad went away in this interval

PadFact   kind        PRESS | RELEASE | AXIS | DIRECTION
          button      PRESS and RELEASE only
          axis, value AXIS only -- what it moved TO, normalised
          direction   DIRECTION only
          held        the set this transition's own button is in
```

### What refuses

| | |
|---|---|
| `pads[id]` for a pad that is not connected | `IndexError` |
| `identify().value` before it completes | `RuntimeError` |
| `identify().until(x)` twice, late, or with a non-`Button` | see [request.md](request.md) |
| a pad being given an id it already disagrees with | `ValueError` |

Controls outside the vocabulary are dropped where they arrive rather than
refused, and an uncalibrated axis is dropped rather than reported raw.

### There is no cap

Five pads can sit on a desk while four people sit on the sofa. Capping the
collection would let a pad that was merely powered on take a place from one
somebody picked up — and a cap on *seats* is a different thing at a different
layer.

Footprint is [stream.md](stream.md)'s: 37 KB per pad's ring idle, 97 KB with
both sticks and both triggers in motion.

## The vocabulary is closed

`Button` has ten members and there will never be an eleventh. Understanding why
is the difference between a settled vocabulary and an arbitrary subset somebody
helpfully extends.

**The pad has a fixed point.** The 360 is not the model because Microsoft won
a market; it is **the point at which the form stopped moving**. The SNES
contributed the four-button face diamond and the shoulders, the Sega line the
analogue stick and analogue triggers, Sony the second stick. Everything since
has accepted that shape while varying the layout — where the sticks sit, which
letter is printed where, how the d-pad is built. A DualSense and a Switch Pro
disagree about the arrangement and agree completely about what a pad has.

Before it, the shape was genuinely open and everyone was trying things: a
memory card with a screen in it, a trident with three handles, six face
buttons, a numeric keypad. Those were not bad ideas. They were experiments, and
they ended.

So the enum is closed, and closing it is what buys the rest: **no `UNKNOWN`
member, no escape hatch, and a `match` over it that is total and stays total.**
A control outside the ten has no target, which is why it is dropped where it
arrives rather than carried as a value nothing can name. **A game cannot bind
what it cannot name, and that is the intended end of the sentence.**

### Two tests answer a new button without reopening anything

1. Is it on both of the two pads that witness the settled form?
2. Did any other vendor adopt it?

If it sits where an existing control sits, it **is** that control under a new
name and it maps. Otherwise it is dropped. Sony's touchpad and Microsoft's
Share button have both been put through this and both are ignored.

### Three kinds of name, one rule

**Say the thing that has not changed.**

- **Face buttons and shoulders are named for POSITION**, because position is
  the only thing that held. The letters move between vendors and the function
  moves between games — `FACE_SOUTH` is jump in one game and confirm in the
  next. It is the bottom button on every pad ever made; Xbox prints an A on
  it, Sony a cross, Nintendo a B.
- **`START` and `SELECT` are named for FUNCTION**, the one place the
  vocabulary does that, because for these two *both* have held. The pair
  arrives with the Famicom — older than the face diamond — and has been
  renamed every generation since: Start/Select, Start/Back, Menu/View,
  Options/Share then Create, plus/minus. Through all of it the
  Select-equivalent has been left of centre and the Start-equivalent right of
  centre, and neither has ever meant anything but start-or-pause and the other
  thing. When two invariants are available the vocabulary takes the legible
  one: `CENTRE_RIGHT` would be equally true and tell a reader nothing.
- **`ACCEPT` is named for neither, because it genuinely is not invariant** —
  south for Xbox and Sony, east for Nintendo. A thing that varies by family
  cannot be a fixed name, so it is a predicate the mapper resolves.

`Button.FACE_SOUTH | Button.START` composes with `|`, and
`Button.START | Button.ACCEPT` reads as one phrase — "start, or whatever this
pad calls yes". `ACCEPT` is an unresolved TERM rather than a
resolved button, because it cannot be resolved until there is a pad to resolve
it against and a question is written before anybody has touched one. Asking
with it unresolved refuses rather than quietly never matching.

### What is not there, each for a stated reason

**`L3`/`R3`.** The stick clicks are the one input class only ever triggered at
inopportune moments and never by player agency — you push the stick you are
already steering with, and it clicks. The cruelty is structural: the click
fires precisely when the stick is pressed hardest, so the accidental input
correlates with maximum stakes, and steering *well* is what triggers it. The
false negative is as bad: clicking a stick you are already deflecting is
unreliable, so a deliberate press is a fumble. One input class that is
accidental when unwanted and unreliable when wanted.

It is also an accessibility position, and there it stops being taste. Applying
that sharp downward force *while maintaining a directional hold* ranges from
painful to impossible for a player dealing with arthritis, hypermobility or
limited hand strength — a bad mechanical leverage on a joint already working
to steer. **An engine that cannot name the button makes every game on it
accessible on this axis by construction**, rather than hoping each game's
options menu offers a remap.

**Guide / PS / Home.** This one passes both tests — every vendor has it. It is
refused because it is already the player's: it turns their pad off and controls
their media, and a game binding it does not gain a control, it takes one away.

**Touchpads, motion, mute, paddles.** Added after the convergence, one vendor
at a time, adopted by nobody else.

## The continuous controls

**The signal is carried, not reduced.** A trigger is reported as travel *and*
as `Button.LT`/`RT`: the same control has two honest readings and which one a
game wants is the game's business. A fightpad that wires a face button to a
trigger axis reports 0 or full, so one threshold gives clean edges; a genuinely
analogue trigger becomes a slightly light button. The threshold is XInput's own
figure, 30/255, with no hysteresis — digital does not need it, and an analogue
trigger held exactly on the line has not been seen to chatter.

**No deadzone is applied anywhere.** The scan records what the pad reported. A
deadzone is a judgement about intent and belongs to whatever reads it — which
is also why the kernel's declared `flat` is not used, see [evdev.md](evdev.md).

**A stick is two axes, not a vector.** That is what the hardware reports. A
deadzoned `(x, y)` pair is a *derived* reading: the deadzone is radial, which
is a judgement applied to the two together, and a scan that stored the result
would have thrown away what the hardware actually said.

### A pad will not admit which controls it lacks

A Hori Fighting Commander has one stick and it is digital — and it declares all
six axes over exactly the ranges a real Xbox pad declares. `EVIOCGABS` does not
report the absence, because **the pad does not admit to it**: presenting as an
Xbox controller is what makes it work everywhere, and the claim covers
capabilities as much as identity.

So a control that is not there reads as permanently neutral, and nothing can
distinguish that from one at rest. This is the same law the pointer states —
*a mouse only proves a button by pressing it* — and a pad only proves an axis
by moving it.

Which makes **require-never** design law rather than a runtime check: a game
may read an axis and must never depend on one, because there is no test it
could perform first.

`Button` does not have this problem, and the asymmetry is real: the ten buttons
*are* the converged form, and a pad missing one would not be a gamepad. The
continuous controls are where hardware varies, and where it also lies.

## The d-pad is a d-pad

It reaches the engine three different ways — two evdev axes reading −1/0/1,
four button bits, or a single switch position on the hardware that needs that
quirk — and none of those is the control. "Hat" is a wire format. Eight
directions and a centre is the thing in your hand.

**It is reported as a numpad position, neutral at 5.** That is not an arbitrary
encoding, it is the one the subject already uses: a quarter-circle forward is
236 in every move list ever printed, so a roll in the fact stream reads as 2,
3, 6 and a recogniser looks like the move it implements. For a ring whose
stated purpose is reconstructing exactly that, nothing else comes close.

`Direction` is an `IntEnum` where `Button` deliberately is not. There the
integer is a transport's private numbering that must never leak; here the
integer **is** the vocabulary.

## A pad's settled interval

The same two halves every device produces — a scan of state and ordered facts —
so a reader who has understood the keyboard has understood this.

**Folding happens per transport packet, not per changed control.** On Linux an
evdev `SYN_REPORT` is the kernel's own simultaneity group. On Windows a
GameInput reading is the complete device state for one raw input packet; each
reading is diffed against the preceding settled state. A diagonal therefore
becomes **one** `DIRECTION` fact rather than a spurious intermediate position
the player never passed through. Only a transport with packet boundaries makes
that available, which is why the keyboard could never have shown it.

GameInput's half-second history is walked from the previous reading on every
cut. On an Xbox pad exercised at a 60 Hz cut rate, a current-state poll observed
405 changes while the walk retained 521 ordered changes, with as many as three
packets arriving in one cut and no cursor errors. An idle end-of-history walk
cost 3.38 microseconds per device on that machine.

An axis fact carries the value it moved *to*; the scan carries where everything
settled. That split is what keeps a stick's path through a frame rather than
flattening it to wherever the stick finished.

`resynced` says this interval's edges did not all come from the player: the
transport dropped events and the pad was asked what was true instead, so a
release here may be a button nobody let go of. It is the pad's version of the
keyboard's `cleared`, and the two should read alike.

The first GameInput cut is a snapshot and is therefore `resynced`. If a hitch
outlives the runtime's history, the expired cursor contributes one desync and a
fresh snapshot; a measured ten-second hitch did exactly that. Disconnecting and
reconnecting twice revived the same `Pad` object and fingerprint with a fresh
cursor.

`disconnected` is beside it and is a different event — the pad went away, its
held buttons were released as real edges so a drag or a charge cannot stick,
and nothing more will arrive.

## Which pad is this, and what does it print

A mapper answers two questions a `Button` cannot: the name a person would use,
and the label on the plastic under their thumb. `Button` says `FACE_SOUTH`; the
mapper says the vendor printed an A there, or a cross, or a B.

```python
prompt = f"Press {pad.mapper.label(Button.START)}"   # "Menu" / "Options" / "+"
```

**Nothing here is a compatibility check.** A pad that matches nothing falls
through to `XboxMapper` and works perfectly. The table only ever adds a name
and a label.

Selection is two tiers and a default: an exact model can refine the mapper,
failing that a known **vendor** picks that vendor's family, failing that Xbox.
The vendor tier is the interesting one — an unreleased Sony pad still says
"cross", because that is a fact about Sony rather than about the model, and it
is what lets the table stay small and stay right about hardware that does not
exist yet.

Xbox is the fallback because it is the **modal** answer rather than a lowest
common denominator: a pad nobody has seen is more likely Xbox-shaped than
anything else, and generics deliberately present as one.

**Nintendo is why `Button` speaks positions.** The letters are the same four,
rotated: A sits east and B south, X north and Y west. A vocabulary built on
letters would have Nintendo's A and Microsoft's A mean different thumbs.

### The tables are researched, not scraped

Do not improve them by pasting in a community controller database. Those are
**behavioural** — they decide which button is which — and they collide with
themselves: one such database is 2139 entries over 2066 distinct GUIDs, 70
duplicated, with three entries sharing one GUID, all called "Xbox One
Controller", disagreeing about whether one button is Back or a stick click, and
no rule for resolving it.

The reason is that **vendor:product is a claim, not ground truth.** Third-party
pads report a first-party id because that is what gets them working everywhere,
and clones report whatever they were cloned from. A bigger table does not
become more authoritative; it just holds more confident answers about ids that
several unrelated devices are all asserting.

The model table here is **editorial only** — nothing in it decides behaviour,
and an error costs one wrong word in a HUD. That allowance has been spent once
and cost exactly that.

## A pad that persists

**The object outlives the connection.** A pad that vanishes becomes inert
rather than disappearing: `connected` goes false, its held buttons release as
real edges, and anything holding it keeps a valid reference. The same
fingerprint reconnecting revives *this* object, so a game holding `pad` still
holds the player's pad afterwards.

That matters because **a pad vanishing is ordinary.** Wireless pads power
themselves off after about fifteen minutes untouched — measured, and it
happened twice during the port — so a long cutscene is enough.

The ring is kept across the gap rather than started again: the windows from
before it are still honest history of what this pad did, and a recogniser
mid-motion when the battery died should see the gap rather than a pad with no
past.

**Every KNOWN pad is cut, not every connected one**, for the same reason. A
departed pad's ring has to stay contiguous or "the last thirty frames" would
mean thirty frames of *presence*, and a recogniser reading across a dropout
would silently splice two moments together.

The engine does not distinguish sleep, battery loss, radio loss, power-off or
destruction. Linux presents all of them as disappearance, and nothing useful
follows from guessing which happened.

Discovery runs every 60 frames — once a second at the authored rate — because
Linux offers no hot-plug callback a game's own loop can service. It runs before
the cut, so a pad plugged in between frames is cut on this one.

## Identity is handed out on first deliberate use

A pad's `id` is `None` until somebody uses it, and then it is the next number.
Never on discovery.

That is the whole point: **a pad that was merely powered on must not take the
number belonging to the pad somebody picked up.** Discovery order varies run to
run, and five pads on a desk enumerate differently each launch.

What counts as deliberate:

- any mapped button press;
- the d-pad leaving neutral;
- a stick shoved more than two-thirds of its travel.

The stick threshold is deliberately far past any deadzone, because **a pad at
rest drifts** — the one on the test desk sits around 1050 of 32767 and wanders
while nobody touches it. Identifying the wrong pad because one twitched is a
worse failure than making a player push a bit harder.

It is read off the settled window rather than signalled up from the transport,
because the window already says what happened and a second channel for the same
information is a second thing to keep in step.

## Two ways in, and they are different modes

**Wiggle a pad.** A single-player game never thinks about identity. Whatever
somebody used is `pads.gamepad`, and the title screen asks
`pad.mapper.label(...)` so it prompts with the letters on *that* plastic.
Nothing has to be assigned and nothing has to be configured.

```python
who = bps.pads.identify().until(Button.START | Button.ACCEPT)
...
if who.completed:
    prompt = f"Press {who.value.mapper.label(Button.START)}"
```

`pads.gamepad` is the lowest id among connected pads that have been used — so
it is whoever picked one up first, and it does **not** change when somebody
else joins later. That stability is the point: a title screen showing the right
glyph must not swap to another pad's letters because a second controller woke
up.

**Ports.** Four people on a sofa and which pad is whose is the whole problem.
That is a separate subsystem above this one, it maps explicitly rather than
inferring from who pressed something first, and it is [not built](ports.md).

The discovery layer both rest on is this one.

## What it is not

**Not seats.** Nothing here knows about players, lobbies, reservations or
replacement policy. That combination has been built once and deleted: putting
it here made input responsible for **guessing who a person was**. Neither side
infers the other — ports does not assign pads and does not transfer a mapping
across a reconnect, and a game manages that relationship.

**Not a leniency layer.** `pad.pressed(Button.LB | Button.RB)` answers about
one settled interval. There is no `held_for`, no buffering and no window of
forgiveness — how long a move stays available is game feel, and the engine
does not have an opinion.

**Not a behavioural mapper.** No mapper overrides which evdev code means which
button, and none ever should — that translation belongs at the transport, where
each platform's numbering is its own business.

## Still open

**A stick cannot be asked, and that is a position rather than a gap.** The
verbs are over controls with a boundary — buttons, triggers at their
threshold, the d-pad leaving a position. No deadzone is applied anywhere, so
an axis has no boundary the engine could name, and it stays a value that
whoever reads it judges.
