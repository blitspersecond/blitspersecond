# The lifecycle

Nine states, one object, and one direction of causation: **something requests a
change, the machine publishes it, and the subsystems react.** Nothing tears
itself down and then reports it.

    the spine    CREATED ──► STARTING ──► RUNNING ──► STOPPING ──► STOPPED
                                                          │            │
                                                      teardown    sys.exit(code)

    the arc                   RUNNING ──► SUSPENDING ──► SUSPENDED
                                 ▲                            │
                                 └───────── RESUMING ◄────────┘

    the fault    STARTING ┐
                 RUNNING  ├──► FAILED ──► STOPPING
                 SUSPENDED┘

`RUNNING` and `SUSPENDED` also reach `STOPPING` directly — that is the ordinary
quit. `FAILED` is the other way in, and the only one that leaves with a
non-zero status.

Running *beside* all of this, and deliberately not in it, is **backgrounded**:
whether the compositor is presenting us at all. It is a `bool`, not a state,
and the section that argues why is the one to read before changing anything
here.

[loop.md](loop.md) owns what happens inside `RUNNING`. [pacing.md](pacing.md)
owns how a withheld presentation is *detected*; this page owns what the engine
does with the answer.

**(Alpha) — this chapter is thinner than its neighbours, and the subsystem is
why.** The lifecycle has not had a full design pass. Gathering the machine
into one object is as far as it has been taken, and that is a consolidation
rather than a design. What follows is what runs today, honestly described —
not a specification anybody has worked through. The transition table is enforced
nowhere, there are no tests over any of it, and whether the suspension arc
should exist at all is open with a card against it. Read *Still open* as a
live list rather than a tidy-up.

---

## Limits and costs

### The states

| | kind | who moves it | how long it lasts |
|---|---|---|---|
| `CREATED` | marker | `Lifecycle.__init__` | not one line of code — the constructor that made it advances it |
| `STARTING` | working | `BlitsPerSecond.__init__` | as long as warm-up takes. Blocking, and the only place slow hardware is allowed to cost time |
| `RUNNING` | settled | the constructor, on success | the run |
| `SUSPENDING` | momentary | `settle()` | settles within the same call |
| `SUSPENDED` | settled | `settle()` | until something clears the request |
| `RESUMING` | momentary | `settle()` | settles within the same call |
| `FAILED` | marker | `fail()` | issues its own stop on entry |
| `STOPPING` | working | `stop()` / `fail()` | as long as teardown takes |
| `STOPPED` | marker | `stopped()` | published immediately before the process leaves |

### The game-facing surface

| | |
|---|---|
| `bps.state` | the current `EngineState`, which is exported from the package root — compare against the member, not a string |
| `bps.backgrounded` | whether presentation is hidden or being withheld |
| `bps.suspend()` / `bps.resume()` | set and clear the suspend request. Deferred to the next loop boundary |
| `bps.stop()` | request shutdown. Idempotent, safe from a tick or an event handler |

That is the whole of it — nothing else should reach `_lifecycle`.

### What is published

| event | when |
|---|---|
| `on_engine_state(previous, current)` | every transition, including the momentary ones |
| `on_background` | the compositor stopped presenting us |
| `on_foreground` | it started again |

The momentary states earn their place here and nowhere else: `SUSPENDING` and
`RESUMING` exist so an observer gets a hook *during* a transition rather than
only after it.

### Exit status

| | |
|---|---|
| `0` | a clean stop — a game calling `bps.stop()`, or the window being closed |
| `1` | anything routed through `fail()`: a failed warm-up, a main-loop exception |
| `130` | `KeyboardInterrupt`, by the shell's `128 + SIGINT` convention |

The status is set by **whoever requested the stop**, because only they know
whether it was one, and a loop reaching its own exit cannot overwrite a
non-zero status already recorded by whatever stopped it.

### What refuses

Nothing, yet, and that is deliberate rather than an omission.
`_LEGAL_TRANSITIONS` records what the machine does today; an unexpected move is
**logged loudly and then made**. A half-rebuilt engine should not gain a new
crash path from rules nobody has signed off on, so the table is a description
first and a specification later. See *Still open* — it is also the part with
no tests.

## Four kinds of state, and only two of them are places

This is the organising fact, and it is what stops the enum reading as nine
equivalent things.

**Markers** — `CREATED`, `FAILED`, `STOPPED` — record that the engine passed
through, rather than naming somewhere it sits. None survives a single line of
code. They earn their place by being *observable on the bus*, not by having any
duration. (`CREATED` is deliberately not `INITIALISED`: initialisation is
exactly what has not happened yet.)

**Working states** — `STARTING`, `STOPPING` — take real time and do real work.

**Settled states** — `RUNNING`, `SUSPENDED` — are where the engine actually
sits.

**Momentary states** — `SUSPENDING`, `RESUMING` — exist for the observer hook
and always settle into the adjacent settled state within the same loop
iteration.

A reader looking for "where is the engine right now" is asking about four of
the nine. The other five are announcements.

## Warm-up happens in the constructor

`BlitsPerSecond()` walks `CREATED → STARTING → RUNNING` before it returns, so
**a constructed engine is already running**. Opening the display happens in
that stretch, and the audio readiness gate will.

Everything slow belongs there, and the reason is a pacing one: presentation
deadlines and the first tick baseline deliberately do not exist yet. A grid
anchored during warm-up would already be far in the past by the time the loop
started, so slow hardware startup would read as a first-frame stall or a
catch-up batch. `Loop` establishes those when it actually starts.

Two consequences worth having in front of you:

**"Never run" cannot be read off the state.** An engine that was built and then
thought better of is `RUNNING`, exactly like one being serviced. That is why
`stop()` consults a separate `_servicing` flag rather than the state — see
*Stopping is not exiting*.

**Teardown runs against a half-built engine as readily as a whole one.** Warm-up
can fail part-way and stop from inside the constructor, so every subsystem is
declared before warm-up and *checked for existence* in `_teardown`, never
assumed. The `on_engine_state` handler is subscribed before warm-up for the
same reason: a stop during it still tears down.

## One way in to an unclean shutdown

`FAILED` exists so that "this run ended badly" is a fact about the machine
rather than an argument someone remembered to pass. Warm-up dying and the loop
throwing both route through it.

That is what keeps `STARTING` and `RUNNING` from each needing their own edge
straight into `STOPPING` with a status attached. There is one way in, it is
named, and entering it is what makes the shutdown unclean — so no caller has to
remember a code. Everything else about the shutdown is `stop()`'s.

## Stopping is not exiting

`stop()` records a status, moves the machine to `STOPPING`, and returns. It
does **not** leave the process, and the distinction is load-bearing.

A stop can be requested from anywhere — inside a game tick, inside an event
handler, from the window's close button. Exiting from there would unwind
straight past the loop's epilogue, and the epilogue is not optional:

    presentation.finish()      the pacing session's own shutdown
    metrics.advance()          or the last tick's work is still staged
    metrics.report().log(...)  reported while logging is still alive
    lifecycle.stopped()        publish STOPPED, then leave

So `STOPPED` is published only once the epilogue has finished. An observer
seeing it knows the machine really is down, rather than merely committed to
coming down.

**The exception is an engine with nothing servicing it.** Nothing is coming to
call `stopped()` on behalf of an engine that was constructed and never run, so
`stop()` completes the cycle itself. That is what makes
`BlitsPerSecond().stop()` terminal. `_servicing` is what tells the two apart,
because as established the state cannot.

`stopped()` also routes through `STOPPING` when the loop left without anyone
having requested it — a surface that closed underneath us — so teardown still
runs exactly once and no observer sees a state skipped.

## Requests are deferred; stopping is not

`suspend()` from inside a game tick must not freeze the machine half-way
through that tick. So it sets an intent, and `settle()` acts on it at **one
boundary per loop iteration**, between event dispatch and the swap — never
part-way through a coherent simulation step.

`stop()` is the deliberate exception: shutdown is published immediately so
subsystems tear down at the moment of the request, and the loop then unwinds on
its own terms.

`settle()` returns the settled state it arrived at, or `None`, and the loop
needs that answer rather than merely being told. **Resuming invalidates its
timing baselines** — wall time passed that no tick accounted for. The pacer
re-anchors, metrics rebase, and input flushes what accumulated. A resume that
treated the gap as frames it owed would deliver a catch-up batch and a window
holding thirty seconds of input nobody aimed at the game, and would release no
key let go during the pause.

## Backgrounding is an axis, not a state

The single most important rule on this page, and the one a redesign will reach
for first.

Kris, 2026-07-11: ***"pause is game-domain, not an engine state."***

`backgrounded` is an observation about the window system. `state` is explicit
engine control. Folding the first into the second costs twice:

**It makes the policy the engine's.** Losing visibility does not mean the same
thing to every game — one pauses, one keeps simulating, one mutes the music and
carries on. An engine that suspends on occlusion has taken that decision away
from all three. Backgrounding therefore **never** calls `suspend()`
automatically; it publishes `on_background`, and a game that wants a pause loop
writes one.

**It multiplies every state by two.** Nine states become eighteen, and
seventeen of them are the same nine with a boolean stapled on.

The expected real usage needs no suspension at all: a game catches
`on_background` / `on_foreground` and switches itself to a pause loop. That is
already served — the axis is separate, already coalesced into single edges, and
carries no policy of its own.

### Two routes, one edge

The same condition arrives two ways. **Window events** say it directly:
`on_hide` backgrounds, and `on_show` / `on_expose` / `on_activate` foreground.
**The loop discovers it the hard way** when the compositor starts withholding
presentations — roughly 2s grants under Mutter — which [pacing.md](pacing.md)
detects as two consecutive swaps over 250ms.

Both funnel through one coalescing setter, which returns early if the value has
not changed. Observers see one edge, not two, and a game does not have to
de-duplicate.

**A backgrounded engine keeps ticking and stops presenting**, because hiding a
window changes what is displayed and not how fast the game runs.

## What it is not

**Not a pause.** Game-level pause is a mode the game runs while the engine is
`RUNNING`. The engine has no opinion about menus.

**Not where a game's state machines live.** This is the engine's own lifecycle
and the only state machine the engine has. A game's scenes, its animation
states and its AI are game code — the engine deliberately ships no framework
for them.

**Not a `_running` flag.** `running` is derived from the state on every read.
Storing it beside the state is one fact in two places and no answer to which
one wins when they disagree.

**Not an auto-suspend.** Detecting occlusion and suspending on it is one line
away and is refused for the reason above: the pacer reports, `Lifecycle`
publishes, and neither chooses a pause policy.

## Still open

**The suspension arc may not earn its place.** Nothing asks for it —
`suspend()` and `resume()` are game-facing verbs no game has used, and
backgrounding deliberately does not imply them. It is kept because it is a
coherent lifecycle in its own right.

The trap is that it looks like a clean three-state deletion and is not. That
arc's single call site in `loop.py` is **the loop's only per-iteration
lifecycle boundary**. Dropping suspension drops step 2 of the loop's ordering
contract with it, and the loop stops consulting the lifecycle mid-iteration at
all — it would only bracket it. The real question is whether anything else will
ever want to land at that boundary, and backgrounding is the obvious candidate:
it currently sits *outside* it, hand-latched as `was_backgrounded` in
`Loop.run`. The board card that held this question is declined; the decision
belongs with *Settle and enforce the lifecycle transition table*.

**OS sleep is a smaller hazard than the pacing code braces for, on Linux at
least.** `perf_counter()` is `CLOCK_MONOTONIC`, which does not count time spent
asleep — measured on the reference box as 70,801s (~19.7h) of sleep the
monotonic clock never saw. The process freezes, the pacing clock freezes with
it, and the loop continues on resume. Windows is the unknown: `perf_counter()`
is `QueryPerformanceCounter` there, and nothing has logged it either side of a
real suspend — nor will it here, because the Windows test machine has sleep
disabled by design so that a shared NTFS volume can be handed over safely. The
reading that would answer it without an instrumented suspend is preserved on
the declined card: `QueryInterruptTime` is biased and
`QueryUnbiasedInterruptTime` is not, so whichever of them `perf_counter()`
tracks after any sleep is the answer. What remains after that is display-side — surface validity, and a
refresh rate that changed while outputs were re-enumerated — which lands on
`Display`.

**The transition table is provisional and has no tests.** There is no test file
for `Lifecycle` at all: not the legal moves, not the deferred-request timing,
not the exit statuses, not the coalescing setter. The table describes the
machine rather than constraining it, and working the transitions through
properly is its own pass. Until then it warns rather than raises, which is the
right default for a rule nobody has signed off on — but it means the machine's
shape is documented here and enforced nowhere.
