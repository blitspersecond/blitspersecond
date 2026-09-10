# Frame pacing

The pacer answers one question, once per presentation slot: **is this the slot
the pending frame belongs on?** Yes, and it goes up and the engine is called
for the next one. No, and the frame is held and the slot passes.

That is the whole algorithm. There is no cadence table, no rational, and
nothing computed ahead of time. The familiar 3, 2, 3, 2, 2 at 60fps on a 144Hz
panel is what the question **answers**, not what it consults.

    window           ├──F1────┼───F2────┼──F3────┼───F4────┼───F5────┤
                     v        v         v        v         v         v
    slot   ┼───┼───┼───┼───┼───┼───┼───┼───┼───┼───┼───┼───┼───┼───┼───┼
           0   1   2   3   4   5   6   7   8   9  10  11  12  13  14  15
    shown              ●       ●           ●       ●       ●           ●
                     20.83   34.72       55.56   69.44   83.33       104.17
    gap    └─────3─────┴───2───┴─────3─────┴───2───┴───2───┴─────3─────┘

Frame `n` owns the window `[origin + n·step, origin + (n+1)·step)`. It is shown
on the first slot at or after that window opens, so the distance from each `v`
to the `●` on its right is time the finished frame spends waiting. Twelve slots
is exactly five frames, which is why the pattern repeats without being stored.

Two properties hold, and the engine depends on both:

**Never early.** A frame cannot be shown before its window opens, so the game
can never run fast, and a frame the engine finished early is held rather than
shown into the previous frame's slot.

**The engine is pumped at the authored period.** Once the frame is up, the
pacer works out when the next one has to start — its deadline, less what the
engine needs to build it — and sleeps until then. That target is derived from
the even window grid and not from the uneven slot grid, so the game clock keeps
the authored period whatever the panel is doing. The unevenness lives in the
gap row, where it is harmless.

**Nothing schedules the tick.** The start target is computed afresh each time
round and the tick begins wherever `sleep` returns. What makes the result even
is where that target is derived from, plus a sleep accurate enough that the
difference does not matter — not a grid the ticks are pinned to.

[clock.md](clock.md) owns what a frame is worth and why the engine measures in
frames at all; this page assumes it. [display.md](display.md) owns the buffer
pair that a present promotes. [lifecycle.md](lifecycle.md) owns the
backgrounding axis this page reports into.
Taking and reading a capture is a runbook rather than a chapter, and lives in
the tools: `util/capture_frame_pacing.py` takes one, `util/frame_pacing.py`
reads one, and `blitspersecond/system/frame_pacing/recorder.py` owns the
format. This page says what the numbers have to be; those say how to get
them.

---

## Limits and costs

### The grids

| | |
|---|---|
| authored rate | 15, 20, 30, 40 or 60. An integer, fixed at construction, and never adjusted to anything |
| paced rate | the authored rate re-timed to divide the panel — 59.94 for a 60fps game on a 59.94Hz display. Within **1%** of the authored rate or it is refused |
| the window grid | `origin + frame · step`, counted in frames by the engine and stepped at the **paced** rate. Evenly spaced always |
| the slot grid | period from the panel, phase from wherever `perf_counter` zero fell |
| slot period | the panel's refresh period — 6.944ms at 144Hz |
| assumed refresh | **60Hz** when the platform will not say. Only the slot grid depends on it |
| refresh source | the most exact rate the platform will give. Pyglet reports an integer on both platforms that matter, so Win32 reads a rational rate from the active display path and X11 divides the CRTC's own mode line. See *Still open* |
| tick-to-slot ratio | `desired / round(refresh)` — exact integers, no approximation and no bound. Used once, to pick the step |
| pattern length | how long the tick-to-slot pattern takes to repeat. **333ms** at worst across every rate and panel the engine knows of |
| Win32 phase | DWM's absolute QPC vblank grid, adopted only when its rate agrees with the window's configured mode to within **1%** |
| DWM submit lead | **1.25ms**, folded into the adopted phase. No other path applies a lead |
| re-pacing | on Win32 only, when the window moves to another output. It corrects the slot grid and nothing else |

### The reserve

The engine is not started when the previous frame goes up; it is started late,
so input is sampled close to the moment the frame is shown.

| | |
|---|---|
| when the engine starts | `deadline(frame) − reserve` |
| reserve | the worst frame in the recent window, doubled |
| window remembered | **90 frames**, a windowed maximum rather than a decay |
| floor | **1ms** — below that the wake costs more than the wait is worth |
| ceiling | **half a step**, so the reserve can never swallow the interval |
| overrunning the reserve | the frame slips a slot; ordinary recovery handles it |
| `late` | the moment to start had already passed when the pacer checked, so there was no sleep to take. This is the bet being wrong |

### Costs

Measured on XWayland/GNOME, 144Hz panel, 60fps authored, 300 frames.

| | |
|---|---|
| CPU, whole process | **2.0% of one core** |
| wake accuracy | within **0.080ms** of target, worst case (Linux/CPython 3.14) |
| wakes per second | **60**, not 144 — the frame rate, not the panel rate |
| `engine.frame.ms` | avg 16.666, p50 16.667, p90 16.669, max 16.719 |
| `display.frame.ms` | avg 16.675, p50 13.909, p90 20.847, max 20.904 |
| tick to photon | p50 4.433, p90 7.220, max 7.278ms — about one slot period |
| pace | 100.00% |
| the compose target | one buffer, about 1MB at 640x360 |

A figure near 13% of a core means something is spinning; nothing here should.
The Windows equivalents are not measured — see *Still open*.

### What the counters mean when they rise

| | |
|---|---|
| `display.frames.overran` | the engine failed to get the frame up inside its budgeted window, so the previous frame stayed on screen an extra window. **The fault a player can see** |
| `display.starts.late` | the engine was called with no reserve left before its slot: the pacer's estimate of frame cost was short |
| `display.phase.shifts` | the slot grid was nudged a quarter period off a suspected vblank boundary. A handful over a long session is the mechanism working; a **rising** count means the refresh rate we were told is not the rate this panel runs at |
| `engine.frame.ms` spread | several ms means ticks have picked up the slot grid's quantisation. That is a defect, and `display.frame.ms` is the quantity that is supposed to be uneven |

**`late` and `overran` are prediction and outcome, and are independent.**
`late` is counted before a frame is built, when the moment to start had
already passed; `overran` after it is shown. Late without overran is a
pessimistic reserve — tight, but sufficient. Overran without late is a spike
the reserve cannot predict, because it is built from recent work. Read
together they say which of the two you have, which is why the instruments
list them adjacent.

`overran` records a failure and `phase.shifts` records a deliberate
manoeuvre. They are counted separately and a phase shift explicitly excludes
an overrun frame, because a gap caused by the engine taking too long is not
evidence about where the vblank is.

### What a healthy run reads

Thresholds rather than the measured figures above — what to check when the
hardware is not the reference machine. `util/frame_pacing.py` reads a capture
and `util/capture_frame_pacing.py` takes one; both carry their own procedure.

| measure | source | passes when |
|---|---|---|
| mean simulation rate | ticks / elapsed | within 0.5% of the **paced** rate. Below 99% is the hard failure and wants investigating before anything else here |
| simulation rate, worst 10s window | ticks / elapsed | within 2% of the paced rate |
| `engine.frame.ms` mean | the clock | within 0.1ms of the paced period |
| `engine.frame.ms` spread | the clock | under 1ms. Several ms means ticks have picked up the slot grid's quantisation, which is a defect |
| `display.frame.ms` | the cadence | bimodal at the slot gaps with the mean on the paced period. Uneven here is correct |
| tick interval max | `frame_interval_ms` | no sample above twice nominal, outside a deliberately overloaded run |
| deadline error p99 | `deadline_error_ms` | below one slot period plus wake jitter. It includes the intended snap from the deadline to a later slot |
| present intervals | `present_interval_ms` | on the expected slot gaps, holding the paced long-term mean |
| CPU, whole process | MangoHud, Task Manager | near 2% of a core. Near 13% means something is spinning |
| `overran`, `late` | counters | zero. A clean run pushes nothing, so absent is the healthy reading |

**75% of target is the signature to know.** It is what presentation timing
leaking into simulation timing looks like, and it is the one number that means
the design has been broken rather than tuned badly.

### What to exercise

| panel | why it is in the matrix |
|---|---|
| 60Hz nominal, 59.94 or 59.92 actual | the re-timing case. `engine` and `display` must read the same rate |
| 120Hz | exact 2x for 60fps — the well-behaved control |
| 144 / 165 / 175Hz | non-integer relationships, and 175 is the stock factory overclock on a 165Hz part |
| 40Hz cap, as a Steam Deck sets | below the crossover: presentation thins out and the simulation rate holds |
| two unlike outputs | drag the window between them. The simulation rate must not change — that is the decoupling proof, and on Linux the *presentation* rate wrongly will not either |

Beyond steady state, five runs are worth taking: **15 minutes or more**, which
is what exposes a slow beat; **occlusion**, where stall detection must fire and
recover; **suspend and resume**, where no catch-up burst may appear;
**fullscreen toggle**, after which the rate must not have changed permanently;
and **a deliberately slow tick** longer than one frame interval, where
degradation must be proportional to the overrun rather than quantised to a
whole refresh.

### Environment overrides

| | |
|---|---|
| `BPS_VSYNC=1` | requests client vsync. An escape hatch for an explicitly measured path where the request actually blocks; every ordinary path runs with it off |
| `BPS_GAMESCOPE_BACKEND` | records the outer backend a Gamescope child cannot otherwise discover. Description and capture metadata only — it steers nothing |
| `BPS_FRAMEPACE_CSV` | switches the capture sink on. Absent is the normal case; `BPS_FRAMEPACE_CAPACITY` and `BPS_FRAMEPACE_DURABLE` tune it, and `system/frame_pacing/recorder.py` documents all three |

### What refuses

| | |
|---|---|
| a refresh rate that is not finite, or below 1Hz | logged as an error; paces against 60Hz |
| no refresh rate at all | logged; paces against 60Hz, and the capture header records the rate as unavailable |
| a DWM clock disagreeing with the window's mode by more than 1% | logged as a warning once per distinct mismatch; falls back to the notional grid |
| a DWM query that fails, or a secondary output | falls back to the notional grid, silently |
| no rate within 1% of the authored one that divides the panel | logged as a warning; paces at the authored rate and accepts the periodic repeat. Needs a reported rate far from any integer, so a low refresh — 30.4Hz gives 60.8fps, +1.33% |
| a frame-pacing sink that cannot save | logged as an error at shutdown; the run is unaffected |

In every one of these the simulation is untouched. A wrong answer about the
panel costs an uneven present pattern, never a game running at the wrong speed.

## Two rates, and only one of them is discovered

Everything below turns on keeping these apart.

| | authored rate | panel rate |
|---|---|---|
| what it is | how often the game ticks | how often the display can show something |
| where it comes from | the `fps` enum — 15, 20, 30, 40 or 60 | the platform, asked at startup and again when the window changes output |
| how exact | exact. `step` is `1/rate` and `clock.time` is `frame · step` | as exact as the platform will say |
| what it feeds | the window grid, and nothing else | the slot grid, and nothing else |
| what a wrong one costs | a game running at the wrong speed | an uneven present pattern |

**The panel rate is discovered, never assumed**, and the engine goes to some
trouble to get the real one rather than the nominal one. On Win32 the rational
rate is read from the active display path, because the reported mode rate is an
integer and truncates 120000/1001 to `119`; the DWM composition clock reports a
live 143.998848Hz where the mode advertises 144/1. A 59.94Hz panel is paced as
59.94Hz, not as 60.

**Where the two do not divide evenly, the arithmetic has to give somewhere.**
Sixty ticks a second cannot all be shown on a panel offering 59.94 slots a
second: six hundredths of a frame each second have nowhere to go. Left alone
that surfaces as one repeated frame roughly every 17 seconds, forever — a
monotonic beat, which is exactly the interval the eye is good at catching.

**The pacer re-times its second rather than dropping the frame.** It takes the
tick-to-slot ratio the panel's NOMINAL rate implies and applies it to the rate
the panel actually reports, so the two grids are commensurate by construction:

    ratio = desired / nominal        60 on 144 -> 5/12, exact integers
    step  = ratio over measured      60 on 59.94 -> one tick per slot

A 60fps game on a 59.94Hz panel is therefore paced at 59.94, and no frame is
ever left without a slot.

**The simulation does not move.** `Clock.step` stays `1/60` exactly and
`clock.time` stays `frame · step`, identical on every machine. What changes is
how long the pacer makes one second last — a game-second is still sixty ticks;
on that panel it takes 1001ms of wall time instead of 1000. `pace` reads 99.90%
and is telling the truth. Kris, 2026-09-10: *"the simulation is 1/60 — that
doesn't change ever. The pacer is adjusting how long the 1 second is."*

**The ratio is used once, to choose a step.** It is not consulted per slot and
it does not distribute ticks over presentations — see *Not a cadence table*,
which is a different mechanism and still refused.

**Both halves of the ratio matter.** It comes from the *nominal* rate because
nominal rates are integers, so the ratio is exact and there is no bound to
tune. The step comes from the *measured* rate because that is the grid frames
land on. Fitting the ratio to the measured rate instead looks more precise and
rebuilds the fault: 1000/999 on a 59.94Hz panel delivers exactly 60.000fps and
realigns the grids once every 16.667 seconds, which is the beat, restored. **A
ratio that is nearly but not quite a simpler one is the worst case there is**,
because it concentrates all of its irregularity into one rare event.

**An accepted rate outside 1% is refused**, and the authored step goes back —
the behaviour without any of this. That needs a reported rate far from any
integer, which needs a low refresh: at 30.4Hz the nearest commensurate rate is
60.8fps, +1.33%, and the panel keeps its periodic repeat instead.

### Measured on a scrolling playfield

A 59.918Hz panel, XWayland/GNOME, Green Hill Zone scrolling for 2:56 —
about 10,600 frames:

    engine  59.92 fps      display  59.92 fps      pace  99.86%

`display.frame.ms` is measured present to present, so the two rates agreeing
is the result: the engine is ticking at the panel's rate rather than near it,
and paced flat at 60 the `engine` line would read 60.00 against a `display`
line that could not follow it. Kris, 2026-09-10: *"as smooth as you can expect
on Wayland; the pacing number settled to desired fairly quickly."*

**`dropped` and `repeated` read zero and prove nothing** — they read zero on
every run ever taken. See *Still open*; do not cite them as evidence here or
anywhere.

**The two rates agreeing is the diagnostic.** They converge when the pacer has
the right panel and diverge when it does not, which makes the readout the
cheapest test for the re-pacing gap in *Still open*. The Wayland caveat is
real and not something this fixes: compositor-side jitter is invisible from
here, and `display.frame.ms` measures our submissions rather than the panel.

## The pacer pumps the clock

The loop contains no timing of its own. Its only two waits are the pacer's, and
every other use of a wall clock in it is measurement. One pass, in order:

    1.  sleep until the pending frame's slot
    2.  swap — the frame is now on screen
    3.  work out when the next frame must start, and sleep until then
    4.  tick, and compose the next frame

So the pacer is not only the presentation scheduler; **it is what drives the
simulation forward.** Steps 1 and 3 are both `sleep` calls, and between them
they are the entire clock of the engine.

That sounds like the coupling this design exists to refuse, and the distinction
is exact:

| | |
|---|---|
| the pacer decides | **when** a tick happens |
| the pacer never decides | **how many** ticks happen |

Presentation granting ticks is what makes a withheld present a withheld
simulation. Nothing here can do that. `clock.frame` is advanced once per pass
and never by an amount, and the wait that releases each tick is computed from
`origin + frame · step` — a quantity with no panel term in it at all. The pacer
pumps the clock **on the clock's own grid**.

The direction of derivation is the whole of it. A pump takes its cadence from
what it is pumping; a governor imposes one. This is a pump.

### "Evenly" means to within scheduler resolution

An OS sleep is not a precise instrument and the pump does not pretend
otherwise. Measured tick to tick, the engine clock reads mean 16.667ms with a
min of 16.647 and a max of 16.729 — a spread of 0.08ms, which is scheduler
resolution and nothing structural. It is never exactly the authored period, and
two things make that close enough.

**The error cannot accumulate.** Each deadline is `origin + frame · step`,
recomputed from the frame count rather than advanced by addition, so a tick
that woke 0.06ms late does not push the next one late. The error is bounded per
frame and then forgotten. Accumulating instead would let scheduler noise walk
the whole schedule off the panel over a long run, which is the failure this
shape exists to make impossible.

**The wall-clock interval is the delivery, not the value.** What a tick is
worth is `step`, exactly, and `clock.time` is `frame · step` on every platform
and in every run. A tick delivered 0.06ms late is still worth exactly one
sixtieth of a second to everything that reads the clock, so the jitter has
nowhere to enter the simulation. It is visible only in `engine.frame.ms`, which
is the instrument for exactly this, and see [clock.md](clock.md) for why the
count is authoritative rather than the wall time.

## The window is the pacer's object

`deadline(frame)` is `origin + frame · step`, computed from the engine's frame
count rather than accumulated. Adding `step` sixty times a second would throw
the grid position away and let it walk against the panel's, so ordinary jitter
would move the schedule permanently; this cannot.

A deadline is not itself a schedule. It is the quantity four separate
decisions are computed from:

| | |
|---|---|
| the slot the frame goes up on | the first one at or after its deadline |
| when the engine starts building it | its deadline, less the reserve at that moment |
| whether the grid re-anchors | how far past its deadline the frame actually landed |
| `deadline_error` in a capture | that same distance, recorded |

**Frame one is due one full step in.** No frame should be asked to be produced
in less than the budget it was promised, and the arithmetic delivers it without
anything arranging it: on a 144Hz panel the first slot at or after 16.667ms is
slot 3, so frame one gets 20.83ms on screen rather than the 6.94 or 13.89ms
that one or two slots would give.

**There is no catch-up.** A tick that overruns costs exactly what it overran
by, and the frame after it still gets a whole step, because the origin moves to
where the late frame actually landed. Letting the grid stand instead would fire
the next tick the instant it was owed, squeezing it into whatever was left of
its interval — the same defect the first-frame rule prevents, reintroduced one
frame later. Time lost stays lost. The engine measures in frames, and a
slowdown is affordable where a compressed frame is not.

The price is that the frame grid slides against the wall clock by every
overshoot. Nothing authoritative is measured in wall time, and `pace` reports
the slide honestly.

## The slot grid is notional, and that costs nothing

This is the part most likely to be read as a compromise, so the order of the
argument matters.

**The target is a region, not an instant.** What decides which vblank shows a
frame is which inter-vblank interval the submit falls in. Every instant inside
that interval produces the same picture at the same time, so accuracy beyond
*the right interval* buys nothing — and a window manager's latch timing is not
stable at that resolution anyway, so it could not be spent even if it were
free.

**The real intervals cannot be located on this path.** `glXGetSyncValuesOML`
looks like a vblank clock and is not: polled 460 times across 0.5s *without
swapping*, UST and MSC each returned a single distinct value (Mesa/radeonsi,
XWayland). Both are tied to swaps, so neither locates a retrace.

The X Present extension is the other candidate and is not one portable clock
either. Under direct Mutter XWayland it produced 123 completion events but only
**two distinct UST/MSC pairs** in 1.035s — stale, and unusable for grading
delivery. Under Gamescope's private XWayland stream the same probe produced 176
distinct pairs for 176 events in 2.929s, a 16.652ms mean over 175 flip
completions. So the one path that does report a usable stream reports the
*inner* compositor's delivery, which is not what reaches the panel.

**Equal spacing is therefore sufficient.** The period comes from the panel; the
phase is wherever `perf_counter` zero happened to fall. Two submits two
notional slots apart are two real periods apart, so they land in successive
real intervals whatever the offset between the grids happens to be. An offset
sweep of 0.0 / 1.0 / 2.0 / 3.5 / 5.0ms, two runs each, 1800 frames, gives a
mean tick interval of 16.667ms in every case and one outlier in total.

Note the shape of that: a perfect vblank clock would still aim at the interval.
The first point is what makes the second affordable, rather than the second
forcing a guess.

**This is also what pays for the sleep.** Aiming at an instant is what forces a
spin loop — no ordinary OS wait is precise enough to hit a microsecond, so the
only way to get there is to burn a core asking what time it is. Aiming at an
interval asks for nothing of the sort: an ordinary `sleep` lands somewhere
inside a 6.944ms region, which is all the accuracy the target has. The CPU cost
of this design is low by construction rather than by tuning.

**Win32 is the one path with a phase reference**, and it is used for the same
purpose. The configured mode can advertise 144/1Hz while DWM's live QPC period
is 69,445 ticks at 10MHz — 143.998848Hz — which is enough for an exact private
grid to drift about **1.44ms over a three-minute run**, walking across a vblank
boundary. Adopting DWM's absolute period and phase removes that drift. The 1.25ms lead places an ordinary WGL
submission inside DWM's interval ahead of its vblank; it is not an attempt to
hit the boundary, and it does not make DWM's downstream queue deterministic.

The one way an arbitrary phase can still be wrong is *straddling*, and that has
its own section.

## Waiting for the slot

`wait_for` sleeps straight to the slot the pending frame belongs on.

**No spin**, and that follows from the target being a region. The wait has to
land inside a 6.944ms interval; an ordinary `sleep` lands within 0.080ms of
where it was asked to, which is two orders of magnitude better than it needs to
be. Sharpening it with a spin buys accuracy against a requirement that does not
exist, and costs 13.4% of a core against 2.1%.

Knowing when the frame is due also means never waking to ask. The wake rate is
the **frame** rate, not the panel rate — 60 a second rather than 144 at 60fps
on a 144Hz panel — because the slot is computed rather than polled for.

Returning immediately means the slot has already passed, which is the overrun
path.

## The swap

Present promotes the newest finished frame, draws it, and swaps, in one call.

**Promotion is deliberately late** — immediately before the draw, never at the
moment the engine flags a frame complete. With one compose target and this
call order it costs nothing either way: present precedes compose within an
iteration, so a frame flagged at the end of one is shown on the next whichever
arrangement is used. The rule stands because it is free and because a loop
shaped differently would need it, not because it buys anything today.
[display.md](display.md) traces it.

**The loop presents once per tick, not once per slot.** That is what keeps the
wake rate at the frame rate, and it decides both sides of the crossover:

| | |
|---|---|
| panel faster than engine | the extra slots are the panel re-scanning what it already holds. The engine never sees them and never presents into them |
| engine faster than panel | presents arrive faster than the panel can show them and the compositor discards the surplus. A producer running at the authored rate never batches, so every tick still gets freshly pumped input |

**Neither of those is the handoff holding or dropping a frame**, and its
`promote`/`complete` alternate strictly because of this call order — which is
why its two counters read zero. See *Still open*.

There is one compose target, not a pair, and this call order is what makes
one enough: the pacer presents and only then issues the tick, so a compose
always follows the present that read the buffer it is about to write.
[display.md](display.md) owns the handoff, and states the edit that would
need a second buffer.

The swap is measured on its own, excluding compose and the post-process draw,
so composition cost cannot masquerade as a presentation stall. With client
vsync off on every path, nothing legitimately blocks in it.

## Overrun needs no rule

If the engine runs past the slot its frame was due on, that slot's answer was
simply *not ready*. The next slot answers yes, and the origin re-anchors to
where the frame actually landed.

The cycle restarts from there, which hands the recovering frame the **front
gap** — `ceil(refresh / rate)`, the largest gap in the schedule, verified
across all five legal rates against 60, 120, 144, 165, 175 and 240Hz. The frame
most likely to be in trouble gets the most room, and nothing arranges it.

A 30ms overrun costs one frame and does not cascade:

    frame 120:  16.664 ms
    frame 121:  38.671 ms   <- the overrun
    frame 122:  16.668 ms   <- back on the 16.667 basis
    next 38 frames: mean 16.667 ms

Under sustained overload the engine settles rather than thrashing. Every frame
doing 25ms of work against a 16.667ms budget gives a tick interval of mean
33.652ms, min 33.621, max 33.679 — a spread of six hundredths of a millisecond
at an effective 29.72fps. The game runs at roughly half speed, smoothly, and
`pace` says so.

This is the direct regression test for presentation timing leaking into
simulation timing: degradation must be proportional to the overrun, not
quantised to a whole refresh.

## Starting the engine late

The engine could be called the instant the previous frame goes up. It would
then finish in a fraction of its budget and leave the composed frame sitting in
the back buffer for the rest of the interval — p50 13.9ms and p90 20.8ms on a
144Hz panel, essentially a whole frame of input-to-photon latency spent
waiting.

`wait_before_tick` sleeps until the engine actually needs to start. Input is
then sampled close to the moment the frame is shown, and tick-to-photon sits at
p50 4.4 / p90 7.2ms — about one slot period.

**This is a real trade.** The engine does not get the interval to the next
slot; it gets the reserve, and a frame that overruns the reserve slips a slot.
`late` counts how often the bet was wrong.

**The target comes from the next frame's deadline, not from its slot.** This is
the distinction the whole arrangement rests on. Taking the slot instead makes
the pump inherit the grid's 2-and-3 alternation, so the game clock itself runs
unevenly — measured at a tick interval swinging between 13.838 and 20.887ms.
Deadlines are evenly spaced, so deriving the target from one gives a 16.667ms
pump with only scheduler resolution around it.

**What decides the start in the end is `sleep`.** The target is arithmetic; the
tick begins wherever the syscall returns. Linux/CPython 3.14 lands within
0.080ms of target worst case. Windows advertises a 1ms timer resolution and in
practice does considerably better — Python has used a high-resolution waitable
timer there since 3.11 — but no BPS measurement stands behind that. Whatever a
sleep overshoots by is taken out of the engine's working time, which is one
reason the reserve is a doubled worst case rather than a tight estimate.

The variation has to live somewhere. A frame can only be shown *on* a slot and
slots are not evenly spaced against the tick rate, so either the pump is uneven
or the frame's remaining wait is. Sampling input on an even cadence is worth
more than a constant wait, and the wait could not have been made constant
anyway.

**The reserve is a windowed maximum, not a decay.** A decay that forgets slowly
enough to respect a spike also takes hundreds of frames to give the latency
back: `max(work · 3, reserve · 0.99)` pinned above a whole frame interval after
a single injected 20ms stall, so the engine started immediately on 131 frames
out of 240 and the latency benefit was silently lost for the rest of the run —
while pace, presents and cadence all read clean. A ninety-frame maximum forgets
cleanly, and the same stalls give zero no-reserve starts.

## Straddling a vblank

If the notional grid happens to sit within timing jitter of the real one,
submits nominally aimed at the same slot land on either side of a real boundary
from frame to frame, and are shown on different real vblanks — a slot early or
late, intermittently. That is roughly **2.3% of the phase space** at the jitter
measured here, so it happens eventually.

It is detectable with no external reference. The intended gap between two
presents is known exactly — it is the difference between their slot indices —
so a measured interval most of a slot away from that is a frame that went
somewhere it was not sent. Six such frames in a window of 120 move the grid a
quarter period and restart the evidence. Slipped frames are excluded: that gap
is late because the engine overran, which is a different fault and already
counted.

**The correction moves a belief, not a frame.** Any offset is as valid as any
other, so relocating an arbitrary phase to another arbitrary phase costs
nothing but the one frame it lands on. This never runs on the DWM path, which
has a real phase reference and would be discarding it.

Measured on XWayland/GNOME at 144Hz with two consoles composited statically:

    2000 frames   1 phase shift   off-pattern 0.85% -> 0.28%
     900 frames   1 phase shift   off-pattern 1.06%
     900 frames   0 phase shifts  off-pattern 0.33%
     900 frames   0 phase shifts  off-pattern 0.45%

Every off-pattern interval measured +5 to +7ms against a 6.944ms period: one
whole slot, which is what a frame going to the next vblank looks like rather
than what timing noise looks like. The starting phase is a random draw, most
runs never need to move, and an unlucky one moves once and settles.

**What this cannot tell you** is whether the straddle was real. The evidence
and the correction are both computed from the same submission timeline, so a
phase error and a measurement artefact are indistinguishable here by
construction. The *move* is safe either way. The *counter* is not yet a graded
diagnostic — see *Still open*.

## Is the compositor still taking frames?

One direction only. A compositor may withhold presentations entirely from an
occluded or minimised window — roughly 2s grants observed under Mutter — and
feeding a stalled swap chain is wasted work. Two consecutive swaps over 250ms
report the condition.

The streak is there for a small reason: one slow flip is a hiccup, and treating
it as a background transition would make brief stutter into a state change.

The pacer reports and does not decide. `Lifecycle` publishes visibility, and no
pause policy is chosen here. A backgrounded engine stops presenting and keeps
ticking, because hiding a window changes what is displayed and not what the
simulation rate is.

## What it is not

### Not a cadence table

**A ratio is computed, and this is not that.** The tick-to-slot ratio picks a
step once and is never looked at again; the pacer free-runs on its own deadline
grid, and a withheld present cannot withhold a tick. What follows is the other
thing — a ratio consumed per presentation, returning how many ticks that
present has earned.

Dividing presentation slots into game ticks as a small rational is the shape a
reader assumes, and it is the shape that makes presentation **grant** ticks. A
withheld present then withholds simulation, so a compositor charging a whole
extra refresh for a narrowly missed vblank runs the game at 75% of target —
measured as roughly 45ms frame times on Mutter's XWayland GLX path. That
inheritance is the defect, and it is not specific to one compositor: while a
blocking `flip()` paces the loop, presentation jitter *is* simulation jitter,
because they are the same clock.

Running at roughly the target rate with an occasional skipped frame is far
preferable to running steadily at 75% of it.

### Not a measured delivered rate

Measuring the real presentation rate from present-to-present intervals is how
you would keep such a division honest as a window moves between outputs. It
cannot run here: on a non-blocking path a present interval measures our own
timer, not the panel. The rate the pacer needs is the panel's nominal one, and
being 0.1% wrong about presentation cadence is invisible.

### Not a blocking swap

Requesting vsync and letting the swap block supplies pacing and, on some paths,
a phase reference. On Win32/WGL under DWM it supplies neither: the present mode
is *Composed: Copy with GPU GDI*, `SwapBuffers` returns immediately, and swap
time is unchanged at 0.032ms whether vsync is requested or not.

`DwmFlush` is the other way to reach for the same thing, and it is worse:
flushing after every present serialises the client to roughly 48fps, and
flushing once retains 60fps but leaves 28.1% of display gaps off-pattern.

### Not a servant of every compositor slot

Servicing every DWM slot on the engine thread either inherits DWM's irregular
cadence or steals the uninterrupted work interval from the game. The engine
thread is woken once per frame, and the frames in between are the compositor's
business.

### Not on a thread of its own

Presentation on its own thread is the shape this problem seems to want: a
blocking swap could then coexist with a free-running simulation, and the pacer
would not have to be right about anything. **GL contexts are thread-affine**,
so the swap cannot move without the context moving, and the context is what the
compositor draws through. Everything on this page is single-threaded, and the
sleep costs 2.1% of one core — which is the budget a thread would be spending
to remove a problem that has not been demonstrated.

## Still open

**The phase counter is not yet graded.** `MsBetweenDisplayChange` can separate
a real straddle from a measurement artefact — if those +6ms intervals are real,
the panel changed a whole vblank later than intended, and if they are not, the
grid is being shifted when it was never in the wrong place. Cheap to do, and
the answer changes what `phase_shifts` means as a diagnostic.

**Windows has no wake-accuracy or CPU figure.** The advertised timer
resolution there is 1ms, and observed behaviour is considerably better than
that — Python has used a high-resolution waitable timer since 3.11 — so the
no-spin result should hold. Both halves of that are reasoning, not measurement,
and the reserve is sized against the advertised figure rather than the observed
one.

**The refresh rate is read through a local workaround on both platforms.**
Pyglet rounds it — Win32 from an integer `dmDisplayFrequency`, X11 from a
`round()` over the CRTC's own mode line — so a 59.918Hz panel reports as 60 and
the re-timing has nothing to act on. Both are fixed upstream (pyglet #1470 and
#1496) but neither is in a release, so `internal/pacing/refresh_rate/` reads
each platform directly. It is about 220 lines that collapse to
`screen.get_mode().rate` once 2.1.17 ships; on the board as *Delete the
refresh-rate workarounds when pyglet ships the fixes*.

**A window moving between outputs does not re-pace on Linux.**
`_tracks_window_output` is Win32 only, so the rate is read once at startup.
Where two outputs differ — 59.918 and 59.963 on the reference machine — a
dragged window is then paced for the wrong grid, which reintroduces the beat at
about one frame every 22 seconds. On the board as *Re-pace when a window moves
output on Linux*.

**Two figures disagree for sustained overload.** Every frame doing 25ms of
work against a 16.667ms step is recorded both as a 33.652ms tick interval at
29.72fps and as 34.722ms at 28.80fps. Only one can describe the tick interval,
and the difference is the gap between "two slots plus the overrun" and "exactly
two slots" — which is the whole question of whether degradation is proportional
or quantised. Re-measure rather than choose.

**The published figures assume a trivial workload.** They were taken with a
game loop of 0.014ms and a compose that is a buffer clear. Real graphics change
swap and compose cost; they do not change the pacing structure, but nothing
here has exercised a frame that uses its budget.

**Gamescope is validated only short-run**, and its outer Wayland surface
reaching the physical display is still unmeasured. Native Xorg is not a target
— path detection describes it, which is not a support promise.

**The capture format is still v1.** `pacing_mode` is always `"slot"` and
`cadence` always `"decoupled"`; both should go, and overrun, late and
phase-shift facts should arrive, as v2. Loaders must retain v1 support or every
existing capture becomes unreadable.
