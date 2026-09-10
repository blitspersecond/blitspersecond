# The clock

Sixty lines of source, and every tuned constant in a game is denominated in
what it counts. The clock is **a rate and a count of completed ticks**, and it
derives one number from them:

    time = frame * step

That is the whole of it. There is no wall clock in this module, no delta, no
elapsed-seconds reading, and nothing that can be set. `frame` is the only
state, and it moves one way, one step at a time.

The engine measures in elapsed **frames**, not seconds. A second is sixty
frames, and it is sixty frames whatever the hardware did to earn them. What a
tick is *worth* is settled here; [pacing.md](pacing.md) settles *when* one
happens, and the two never negotiate.

---

## Limits and costs

### The surface

| | |
|---|---|
| `bps.clock.rate` | the rate this game was authored at — an `fps` member, so `int(rate)` is 15, 20, 30, 40 or 60 |
| `bps.clock.step` | seconds of simulation time in one tick: `1 / rate`. `0.016666666666666666` at 60 |
| `bps.clock.frame` | completed ticks. **THE clock**; everything else derives |
| `bps.clock.time` | `frame * step`, exactly |
| `bps.framerate` | a forward to `rate`, for a game that only wants the number |

`tick()` is the one mutator and the loop is what calls it. Calling it from a
game tick is not defended against, and the consequence is arithmetic rather
than damage: `time` advances by a step no frame was drawn for, and the pacer's
next deadline — `origin + frame · step` — moves a whole step further out, so
the game shows one frame per two steps while reporting the elapsed time of
both.

### The arithmetic

| | |
|---|---|
| storage | one `int`, one `float`, one enum member. Python ints do not overflow, so `frame` has no ceiling |
| cost of `time` | one multiply, evaluated per read. Nothing is cached and nothing is stale |
| exactness | `frame · step` stays within **2.2e-12 s** of the true rational `frame/rate` across eight hours at 60fps |
| whole seconds | exact. `frame · step` is `1.0` at frame 60, `3600.0` at frame 216000, at every authored rate |
| what it is not | *measured*. Two runs that tick the same number of times report the same `time`, whatever the wall clock did |

### What refuses

| | |
|---|---|
| a rate outside the set | `fps` is an `IntEnum`, so 37 is unrepresentable rather than rejected. [The set's own docstring](../../blitspersecond/common/framerate.py) carries the test a candidate has to pass |
| changing the rate | there is no setter and no override hook. Not guarded — absent |
| reading elapsed real time | not here. `metrics` measures wall time; this reports what was owed |

## The unit of time is the frame

Jump arcs, gravity, coyote time, input windows and animation timing are all
counted in frames. That is not a convention the engine imposes — it is what
those constants *are*, in every game that has ever been tuned by feel. Varying
the rate silently redefines all of them at once, which is what PAL conversions
did and why they are a separate speedrun category.

So the rate is settled at construction and never after, and the count is
authoritative over the wall clock rather than derived from it. Two rules fall
out, and both are the pacer's to enforce — [pacing.md](pacing.md) has the
arithmetic:

**Frame one is due one full step in.** No frame is asked to be produced in
less than the budget it was promised.

**There is no catch-up.** A tick that overruns costs exactly what it overran
by, and the frame after it still gets a whole step. Under sustained load the
simulation runs *slow* rather than compressed. The price is that the frame
grid slides against the wall clock by every overshoot, and it is affordable
precisely because nothing authoritative is measured in wall time.

## `time` is derived, never accumulated

`time` is recomputed from the count on every read rather than advanced by
`+= step`. The numerical case for that is weaker than it looks and worth
stating honestly: an accumulator drifts **0.33 microseconds over eight hours**
at 60fps, which nothing in a game could notice.

The structural case is the load-bearing one. Deriving leaves the clock with
exactly one piece of state, so there is nothing for a second number to
disagree with. `time` is a pure function of `frame`, which means any two runs
holding the same frame number hold the same time *by construction* rather than
by an argument about their histories — and it means a number in a log, a
capture or a save file can be checked against the count that produced it.

An accumulator makes `time` a function of the whole run instead. It is then
not recomputable, not checkable, and it carries whatever it has accumulated
across anything that would otherwise reset the clock.

## The module imports nothing from `time`

That is the test of whether the engine really measures in frames, and it is
worth keeping mechanical rather than aspirational: one `grep` says whether the
clock has grown a second opinion about what a tick is worth.

Deciding *when* to tick is the pacer's problem, and everything the scheduling
half of a clock would hold — the origin, the deadline, the slot grid, the
overrun rule — lives with the thing that owns the presentation grid, because
that is the only place with the information to make those decisions. A clock
that needed a wall clock to say what a frame is would not be measuring in
frames.

The clock therefore has **no hardware dependency at all**: no window, no GL
context, no refresh rate. That is what lets it be constructed first, which is
the subject of the next section.

**Every clock the engine does consult is monotonic.** 14 `perf_counter()` call
sites and 6 `perf_counter_ns()`, and no `time.time()`, `datetime` or
``time.monotonic` anywhere in the package. Pacing against a wall clock an NTP
step can move backwards is its own class of defect, so this is worth
re-checking rather than assuming; `perf_counter_ns` is the same clock in
integer nanoseconds for the frame-pacing capture, so mixing the two is safe.

## Who owns 60

One declaration, and no copies:

    _FRAMERATE = fps.SIXTY        blitspersecond.py

`Clock` is built from it **first, before anything else the engine owns** — it
has nothing to wait for — and every other holder reads the rate off the clock
rather than being handed a number:

| reader | what it wants the rate for |
|---|---|
| `Metrics(framerate=clock.rate)` | the frames-to-seconds fold ratio |
| `InputBoundary(rate=clock.rate)` | the retention ring's depth, `ceil(rate · 0.5)` windows |
| `_presentation_session(target_rate=int(clock.rate), step=clock.step)` | the window grid, and the capture header |
| `bps.framerate` | a game asking |

Construction order is doing real work there rather than being tidy. `Metrics`
is a singleton and takes the fold ratio as a constructor argument — each
stream is built with it — so it keeps whatever the *first* caller passed. A
module-level `Metrics()` in anything imported early would silently pin the
ratio to the default. Building the clock first removes that race rather than
guarding it, because there is then always a rate to read.

`Metrics.__init__(framerate: int = 60, ...)` still spells the number, as its
default. Harmless while the authored rate is a fixed 60, and the one thing
that has to move the day it is not.

## A second is sixty ticks. How long that takes is not the clock's business.

A 60fps game on a 59.94Hz panel is paced at 59.94, because a rate that does
not divide the panel leaves a frame with nowhere to go, periodically, forever.
None of that reaches here. **`step` stays `1/60` exactly and `time` stays
`frame · step`**; what moves is how long the pacer makes one second last. On
that panel a game-second is still sixty ticks and takes 1001ms of wall time.

Kris, 2026-09-10: *"the simulation is 1/60 — that doesn't change ever. The
pacer is adjusting how long the 1 second is."*

This is the sharpest illustration of the split, so it is worth reading twice:
the panel changed the engine's real-time behaviour and changed nothing at all
that a game can observe. `pace` reads 99.90% and is telling the truth.
[pacing.md](pacing.md) owns the re-timing.

## What the count guarantees, and what it does not

**Guaranteed.** `time` is a pure function of `frame`. Nothing a scheduler,
compositor or panel does can change what a tick was worth, on any platform, in
any run. A game that reads `bps.clock.frame` and `bps.clock.time` sees the
same numbers on a 144Hz machine and a 59.94Hz one.

**Not guaranteed, and not claimed.** There is no replay facility, and the
clock alone would not be enough for one. It gives the property such a thing
needs — a reproducible timeline — and says nothing about the rest: input is
frame-cut and reproducible, but float determinism across machines, iteration
order, and anything a game seeds from the system are all outside this module.
Reproducible *time* is one of the preconditions, not the feature.

**Two things deliberately do not advance it.** A **suspended** engine stops
ticking, so `time` is game time rather than session time — a paused game does
not age. A **backgrounded** one keeps ticking and stops presenting, because
hiding a window changes what is displayed and not how fast the game runs.
[lifecycle.md](lifecycle.md) owns that axis.

## Audio does not contradict this

Worth stating because it is the obvious objection: audio is a second real-time
domain, paced by a hardware sample clock, and a frame grid that slides against
the wall clock might be expected to slide against it too.

The two never share a timeline to diverge on. Audio is either handed a
schedule that fills minutes, expressed in its own units — the engine takes an
integer sample count and refuses anything else — or told to play something
now, which it does on its next audio-frame boundary. Game ticks issue audio
*events*, never timestamps on a shared clock. There is nothing for a sliding
grid to slide against.

## What it is not

**Not a source of delta time.** There is no `dt` and nothing computes one.
A variable timestep makes every tuned constant a function of frame duration,
which is the thing the fixed step exists to prevent; and `step` is available
to anything that wants to multiply by it.

**Not a scheduler.** Gameplay timers key off integer tick counts and run from
the game loop — count down in frames, act at zero. A clock that scheduled
would need a queue, a scan, and a notion of "now" it does not have.

**Not rebaseable.** `metrics.rebase()` exists and this has no counterpart,
because rebasing means discarding accumulated wall time and there is none here
to discard. Resuming from suspension re-anchors the *pacer's* origin; the
frame count simply continues.

**Not a place to put elapsed real time.** The two questions look adjacent and
are not: this reports what was **owed**, `metrics` reports what was
**delivered**. An object answering both starts quantising, and a step that
rounds 16.667ms to a whole 16 is a game running 4% slow while every number it
publishes about itself agrees. See `engine.frame.ms` and `pace` in
[metrics.md](metrics.md).

## Still open

**How a game declares its rate.** `_FRAMERATE` is a module constant, and
config is where a per-game rate belongs — read before the engine is built,
with no ambiguity about when it is settled. A constructor argument is not the
answer: `BlitsPerSecond()` is the idempotent "engine exists" barrier, reachable
from anywhere, so an argument there would be silently dropped by every call
after the first and leave a game running at a rate its own source contradicts.
The board card *Let the game author declare its tick rate* carries this.

**What the metrics report calls a second at 15fps.** The fold ratio is the
authored rate, so "per second" stays honest, but the frame counts get small and
the percentiles thin. Nothing has been measured at a non-60 rate.
