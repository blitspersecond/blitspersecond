# The keyboard

Window events arrive whenever the pump feels like it. A game wants one answer
per frame that does not change underneath it. The keyboard ingress is what
turns the first into the second: it subscribes to the bus, records arrivals
doing as little as possible, and folds them into one settled window at the cut.

This chapter follows one keypress from the window system to a window a game can
read, and then the two ways a release fails to arrive. The ring it settles into
is [stream.md](stream.md)'s; a line of typing is [text.md](text.md)'s.

## Limits and costs

### The surface

```text
keyboard.stream       InputStream[KeyScan, KeyFact] -- settled history
keyboard.dropped      arrivals lost to the staging limit, since start
keyboard.input(...)   begin a line of typing -- see text.md
```

A window's two halves:

```text
KeyScan    held       every symbol currently down
           pressed    went down in this interval
           released   came up in this interval
           cleared    those releases were not the player

KeyFact    kind       PRESS | RELEASE | REPEAT | TEXT | MOTION
           value      a key symbol, a motion constant, or one character
           modifiers  pyglet's modifier bitfield; 0 for TEXT and MOTION
           held       the set this transition's own key is in
```

`bps.keyboard` hands back the ingress itself. That is deliberate and
temporary — the query vocabulary is a later piece, and it waits on a second
kind of device to shape it.

### What refuses

**Nothing.** There is no call on this object that raises.

**Three non-modifier keys at once is the practical ceiling**, and it is the
hardware's rather than the engine's. The OS delivers independent per-key
events, so simultaneous keys are simply the held-set and a chord of any width
composes — but cheap membrane keyboards physically register only two or three
non-modifier keys together, and NKRO boards do not. So `key.A & key.S & key.D`
is expressible and is not reliably *performable*. Modifiers are wired
separately and do not count against it, which is why Ctrl-Alt-Delete has never
been a ghosting problem.

**Debounce is not a software concern.** Keyboard firmware does it, and what
looks like bounce at this level is autorepeat.

Staging is bounded at **512 arrivals in one frame**, which is far above any
real frame's typing. Past that, arrivals are dropped and counted in `dropped`
rather than raising or growing — bounded for the reason `MetricBuffer` is
bounded: a subsystem that can exhaust memory when something goes wrong is worse
than one that says it is going wrong.

Footprint is [stream.md](stream.md)'s: 25 KB for a keyboard's ring at 60Hz.

## Staged on the pump, settled at the cut

The bus delivers keyboard events synchronously, inside the window pump, on
whatever thread pumped it. So the handlers do the least possible work — record
the arrival and return — and everything that needs ordering or state happens
later, at the cut, on the engine's own boundary.

Three consequences, and all three are the point rather than side effects.

**A sub-tick tap survives.** A press and release that both land between two
cuts still produce both edges in the window, because the interval is replayed
in arrival order rather than sampled at its end. A design that polled state
each frame would lose the tap entirely.

**Backend pump differences stop mattering.** X11 drains events at exactly one
point in the loop; Win32 and macOS platform loops can dispatch mid-frame.
Wherever events come from, order is fixed at the cut.

**In a catch-up batch, only the first frame sees edges.** Two cuts against one
pump means the second drains nothing, which is correct: nothing new happened
between them.

### The handlers return `None`, deliberately

A truthy return from a pyglet handler is `EVENT_HANDLED` and stops the event
reaching handlers below it — which here would include `Lifecycle`'s and every
other device's. The keyboard observes and never consumes. The engine assigns no
global meaning to Escape or to any other key, and a game that wants one reads
for it like anything else.

### Whoever builds the ingress must keep it

The bus stores bound handlers as **weak references**, so an ingress nothing
holds is collected and silently unsubscribes itself — input simply stops, with
no error. `__weakref__` is in `__slots__` for the same reason: a slotted class
without it cannot be referred to weakly at all, and `push_handlers` refuses it
outright.

## A transition carries the held-set its own key is in

This is the law the whole predicate layer rests on:

> **A transition carries the held-set in which its own key is present.**

A press carries the set *after* its key was added. A release carries the set
*before* its key was removed. Both therefore contain the key the fact is about,
which is what lets a chord be evaluated in either direction — for a press, *did
adding this make the expression true*; for a release, *was it true while this
was present, and is it not now*. Publishing the after-state for a release
instead would lose the second question.

It holds without exception, which costs one thing worth knowing: **a release
for a key the engine does not believe is down produces no fact.** That happens
after a clear — the synthesised release already reported the key going up, and
the player's physical release arrives afterwards. Emitting it would mean a fact
carrying a set its own key is missing from, which is the one thing a fact
cannot be allowed to do; and it would tell a recogniser that one press ended
twice.

## Repeats are facts, never edges

A press for a key already down is a `REPEAT`. It is recorded, because it
genuinely happened, and it is never a fresh edge — holding a key must not read
as pressing it sixty times.

**Do not infer held-ness from it.** Repeat generally applies only to the most
recently pressed key, requires focus, and is user-configurable or switched off
entirely. A held key may produce a steady stream of repeats or none at all, and
`scan.held` is correct either way.

## Text and motion are not derivable from the symbols

Typing and gameplay are different foods, and the window system already splits
them.

**Text arrives as characters, already through the player's layout.** One
callback may carry several, and they are split one fact per character so that
`"name\r"` spells the name before the Return completes it.

**Motions are resolved by the platform, not by the engine.** Arrows, home, end,
backspace and the rest arrive as motion constants that pyglet derives from the
platform's own key bindings — so backspace on a Mac and backspace on Linux both
arrive as the same motion, whatever key produced it. They are not recoverable
from key symbols, which is why they are carried separately.

**A motion is not something to ask about.** It is instantaneous — no boundary
to cross, no held state — so it arrives as a fact and never enters a scan, and
`Motion` is deliberately not composable. The refusal is loud rather than
silent because eight motions share a value with the arrow and editing keys:
`Motion.LEFT` and `key.LEFT` are both `0xff51`, so a predicate about the motion
would sometimes answer True about the key. A game asking whether backspace was
pressed asks about `key.BACKSPACE`; a consumer that wants the editing intent
reads it off the fact, which is what the line reducer does.

**Lock keys are ordinary physical keys.** Caps Lock is a symbol that goes down
and comes up like any other. Its latch state is not a keyboard concept here;
text has already had the platform's lock and modifier policy applied to it by
the time it arrives, and the modifier bitfield on a fact carries the rest.

## The scan survives the ring

`held` is folded forward from every event ever seen and copied into every
window, so a key weighed down for an hour still reports held long after the
press that started it left the ring. The facts expire; the state does not.

The corollary is the reason the next section matters: because the held-set is
long-lived, **a missed release corrupts it permanently**. An event lost on the
wrong side of a boundary does not heal in half a second — the key stays held
until something explicitly clears it.

## When a release never arrives

**Three ways it happens, and one mechanism answers two of them.**

**Focus loss.** Alt-tab away mid-hold and the release lands in somebody else's
window. `on_deactivate` is the signal, and everything held is released as real
edges, so a game watching for a release sees its stop condition rather than a
key that never comes up.

**Resume.** Ticks stop while the engine is suspended but the window keeps
pumping, so staging would otherwise land as one enormous window describing
input that was never aimed at the game. `flush` discards it. Dropping the
events alone would leave the held-set stale for good — anything pressed before
the suspend and released during it would stay held — so the same clear runs.

Two details make the clear honest rather than merely effective:

**It is queued, not applied in the callback.** The clear takes its place in
arrival order, so a press that landed just before focus was lost still gets its
press edge in that interval, and *then* its release from the clear. Applying it
synchronously would lose that ordering.

**It unwinds one key at a time.** Each synthesised release carries the held-set
as it stood at that moment rather than the empty end state, so a recogniser
watching the unwind sees a coherent sequence instead of everything vanishing at
once.

`scan.cleared` says the releases in that window were not the player, and the
keys may well still be physically down.

### The third way, and it is accepted

**A keyboard grab without a focus change.** A compositor overlay — a
screenshot tool, a hotkey popup, a global-hotkey daemon — takes the keyboard
while the window keeps nominal focus. There is no `FocusOut`, so there is no
`on_deactivate`, and the release is swallowed where nothing can see it.

The repro is one line: **hold a key, press PrtScr, release the key inside the
overlay.** That key now reads as held while the player is not touching it, and
it stays that way until they use it again — a missed release does not heal on
its own, because the held-set is folded forward and nothing here expires.

**Event-derived state cannot detect this**, and the absence is not a bug in
the ingress: pyglet dispatches `on_deactivate` on every `FocusOut`
unconditionally, so the event genuinely never happened. Under a Wayland
compositor the XWayland window keeps focus while the overlay, a layer-shell
surface, takes the keys.

**It resyncs the next time that key is used, and that is why it is accepted.**
The key is believed held, so the player's next press of it is a `REPEAT` and
produces no edge; their release then produces a real `RELEASE` and takes it out
of the held-set. One press edge is lost and the state is correct again — where
a key stuck until the process ended would be a different severity of problem.

Fixing it properly means reconciling against ground truth rather than events —
asking the display server what is physically down, which is a different kind of
question from anything else this device does, and one only X11 can answer. The
cure is X11-only, costs a round trip on a thread with 16.67ms to spend, and
leaves the same hole on every other platform. Declined; see the board.

**The keyboard assumes neutral because it cannot ask.** There is no way to
question the window system about what is still down, and a key let go during a
pause would otherwise stay held for good. A pad is the opposite case and
resyncs against the device instead — see [input.md](input.md), where one call
gets three different answers for three devices.

## Symbols are pyglet's, and there is only one keyboard

`key` is `Key`, an `IntEnum` whose values are pyglet's own. There is one
keyboard transport and there always will be, so a parallel BPS numbering would
be a table mapping a hundred and seventy symbols to themselves — and this is
not one. Nothing is translated at ingress: a scan holds whatever the window
system sent, and a member hashes equal to its value, so `key.LEFT in
scan.held` answers untranslated.

What the names add is the condition operators, which a plain `int` cannot
carry — `key.LCTRL & key.LALT` on a bare symbol is arithmetic and evaluates to
`LSHIFT`. Game code writes `key.RETURN`; what it must not write is
`pyglet.window.key.RETURN`.

**The number row is the one place a name differs from pyglet's.** pyglet calls
those keys `_0` to `_9`, because an identifier cannot begin with a digit, and
BPS calls them `DIGIT_0` to `DIGIT_9` — `NUM_0` to `NUM_9` being the keypad
already. The values are unchanged, so this is still not a translation: only
the spelling moves, and it moves because a leading underscore reads as private
on a member game code types constantly. They were missing outright until
2026-09-18, which is what a filter on ordinary spellings does to the only ten
members that have not got one.

**There are no scancodes.** pyglet symbols are layout-translated, so WASD on an
AZERTY keyboard is literally ZQSD. There is no positional key API to reach for,
and rebinding is the escape hatch.

## Why it comes through the window system

A keyboard could be read straight off the device the way a pad is. It must not
be, and the reason is worth stating before somebody unifies the two families
for tidiness.

**Symbols arrive already resolved through whatever layout the player chose.**
Reading the device directly would bypass that, handing back hardware positions
and making the engine responsible for a translation the OS has already done
correctly.

**A keyboard is focus-bound and a pad is not.** Reading keystrokes from the
device means reading them while another window has focus, which is
keylogger-shaped, and on Linux it needs device permissions besides.

Pads are the opposite case on both counts — a pad has no OS-level
personalisation to preserve, and it does have an identity question the window
system cannot answer — which is why they go direct. The two families differ
because their requirements differ, not because of how each happened to be
built.

## What it is not

**Not a binding table.** `keyboard.pressed(key.SPACE)` and
`key.LCTRL & key.LALT` answer what the physical keys did. Which key means jump
is a game's, and there is nothing here to configure it with. Toggle-on-press
belongs there too.

**Not where Alt+F4 lives.** The window manager usually eats it before the
window sees a key, and on Windows it is a system command — so what arrives is
a close request, which the engine routes through its ordinary shutdown. Do not
reconstruct the chord here: a keyboard that recognised it would fire in
addition to the real one on some platforms and instead of nothing on others.

**Not a binding layer.** Toggle-on-press, rebinding tables, and actions that
mean the same thing on a key or a button all belong a storey up, shared with
pads. What this reports is which physical keys did what, and when.
