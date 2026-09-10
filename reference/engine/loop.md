# The machine loop

Everything that happens every frame happens here, in one order, decided in one
place. Graphics, input, metrics, lifecycle and presentation are serviced from
this file — every subsystem the engine currently has. It is the clearing
house, and its job is sequencing rather than doing.

One iteration, top to bottom. **This is one tick**, not one presentation slot:
at 60fps on a 144Hz panel the loop wakes 60 times a second, not 144, because
the slot a frame belongs on is computed rather than polled for.

    wait_for(frame)             sleep to the slot this frame belongs on
    ──────────────────────────────────────────────────────────────────────
    dispatch_events()           pump the window queue
    settle()                    apply deferred lifecycle requests
    present(frame)              the frame goes up
    ──────────────────────────────────────────────────────────────────────
    wait_before_tick(frame+1)   sleep until the engine has to start
    ──────────────────────────────────────────────────────────────────────
    metrics.advance()     ┐
    inputs.cut(frame)     │     close the interval the frame just showed
    clock.tick()          ┘
    layers.prepare()            BEGIN  — bring the world current
    game_loop()                 ─────    the game's tick mutates it
    layers.commit(pending)      COMMIT — draw what that state implies

The two sleeps are the pacer's and are the only timing the loop contains;
every other use of a wall clock in it is measurement. [pacing.md](pacing.md)
owns both. [lifecycle.md](lifecycle.md) owns what `settle()` applies.
[clock.md](clock.md) owns what the tick is worth.

**(Alpha) — the shape is settled, the vocabulary and the phase list are not.**
The loop is deliberately being left alone while the subsystems it services come
online, because it is the one place they all meet and every one of them still
has an opinion coming. Kris, 2026-09-10: *"it's serviceable for now; when
there's enough for the engine to support then the loop can be cleaner."* So
the hold is not indecision, and tidying this file ahead of the subsystems is
work that will be undone — you cannot design a clearing house before you know
what is being cleared through it. Two phases are designed and unbuilt (SETTLE
and RESET), one phase placement is contested three ways, and the vocabulary is
borrowed from databases and does not fit in one specific respect. All of that
is in *Still open*, and it is a live list.

---

## Limits and costs

### What is serviced, and in what order

| | | why here |
|---|---|---|
| the slot wait | pacer | the frame cannot go up before its window opens |
| `dispatch_events` | window | events must land before the lifecycle reads them |
| `settle` | lifecycle | one boundary per iteration, never mid-tick |
| `present` | pacer | the frame goes up as early in the iteration as it can |
| the start wait | pacer | so input is sampled close to the moment the frame is shown |
| `metrics.advance` | metrics | closes the interval's measurements |
| `inputs.cut` | input | closes the interval's events |
| `clock.tick` | clock | turns the frame index |
| `prepare` | compositor | the game must read a world already current |
| the game's tick | the game | between the two compositor calls, and nowhere else |
| `commit` | compositor | draws what the tick produced |

### What is NOT serviced here

**Anything per-layer.** The loop names two compositor calls and no per-layer
step. Which layers are prepared, which are drawn, and what either means belongs
to `Layers` — see [layers.md](../graphics/layers.md).

**The state machine.** The loop drives the lifecycle at the two places a loop
legitimately can — the brackets around servicing, and one boundary per
iteration — and never mutates it.

**Audio, which is not in the engine at all yet.** Worth stating because
"everything that happens every frame" invites the assumption that it will
arrive here, and the intent is that it does not: a blocking worker paced by
output-device backpressure is a third clock domain, referencing neither the
frame count nor the slot grid, and a tick issues audio *events* rather than
work for the loop to pump. That is a design position taken in advance, not an
arrangement this file can be read to confirm. `docs/bps/audio.md` holds it
until the subsystem lands.

### Where time goes

| metric | spans |
|---|---|
| `engine.frame.ms` | tick start to tick start. Flat at the paced period by construction |
| `gameloop.ms` | the game's tick alone |
| `compose.ms` | `commit` alone — the clear, the walk, and every layer's settle half |
| `swap.ms` | the present |
| `display.frame.ms` | screen to screen. Bimodal by design |

**`prepare` is in no published metric.** `gameloop_ms` starts after it,
`compose_ms` starts after that, and `frame_ms` is a paced interval rather than
a cost. The pacer's `observe_work` spans it, so a slow prepare is felt as a
missed reserve rather than seen as a number. It is the one phase with no
instrument — see *Still open*.

### Costs

| | |
|---|---|
| an empty draw order | one clear and a swap, and that is the figure every pacing measurement was taken against |
| a layer with nothing to do | one `is None` test in `prepare` |
| the game's callback | a bare call. An unset loop is a do-nothing lambda, never a test |
| the scene latch | one attribute read per tick |

## The frame is a transaction

The organising idea, and Kris's, 2026-09-06: *"a very much classic BEGIN →
COMMIT transaction pattern."*

    BEGIN     the engine brings the world to a coherent state for this tick
    ---       the game's tick runs and mutates it
    COMMIT    the engine writes the frame that state implies

**The invariant is what earns the name:** the game sees a coherent world and
can never observe a half-written frame. Everything the engine does either
happens before the game looks, or after it has finished looking. There is no
third category, and that is a rule rather than a description — a new
per-frame job belongs in one of the two, and deciding which is the real work
of adding it.

`prepare` is BEGIN's verb, and the compositor owns both ends. The engine
sequences them because the engine holds the game's tick:

    compositor.prepare()      # bring every attached layer current
    game_loop()               # the game mutates
    compositor.commit(fbo)    # draw, into the buffer display handed over

**The shape this rules out** is `compositor.frame(game_loop)` — handing the
game's callback in so the compositor can sandwich it. That inverts ownership:
the thing that draws would decide when the game runs. The engine owns the game
loop, so the engine sequences.

### The vocabulary is borrowed, and one part of it does not fit

BEGIN and COMMIT come from databases, and the fit is good enough to be worth
keeping: a bracketed region, a coherent view inside it, and a single point
where the result becomes visible.

**There is no rollback, and COMMIT never fails.** Say that plainly or the
metaphor invites someone to go looking for the abort path. A dropped frame is
not a rollback — the simulation advanced, and a player killed on an unpresented
tick stays killed. Nothing is undone, ever; the only thing a frame can fail to
do is be *shown*, which happens after COMMIT is over and is the pacer's
business.

## Three things close the interval, and the clock turns last

    metrics.advance()      the interval's measurements
    inputs.cut(frame)      the interval's events
    clock.tick()           the frame index

They are one operation in three parts, and the order inside them matters.

**The index turns last** so that `metrics` and `input` both stamp the frame now
*closing*. Everything staged since the last tick belongs to it — including the
swaps that showed it — so a tick and its presentations share one number, and
the cut stamps that number directly rather than computing it.

**The input cut is unconditional.** A frame where nothing arrived still cuts
an empty window. Skipping empty ones would quietly turn "the last fifteen
frames" into "the last fifteen that happened to have input" — which reads
correctly in any test that presses a key, and is wrong exactly when the player
pauses mid-motion.

The cut is also the clearest case of BEGIN doing what BEGIN is for: the tick
reads **one frozen input world for the whole of its duration**.

## Composition bills at tick rate

`commit` runs once per tick and never once per slot. On a 144Hz panel a 60fps
game composes 60 times a second, not 144 — the extra slots re-present what is
already there, and the compositor never learns of them.

That is what makes `compose.ms` comparable across panels, and it is why the
loop is where the decision lives: a compositor asked to draw per slot would
bill at panel rate, and a game's frame cost would depend on the monitor it was
run on.

## The tick runs after the present, which is what makes `pending` free

The pacer swaps and *then* issues the tick. So by the time COMMIT resolves
`display.pending`, it is the buffer that just stopped being shown.

Nobody reasons about which of the two buffers they hold, because nobody holds
one across a present that could exchange them. That is a structural property
rather than a discipline — there is no window in which the wrong answer is
reachable.

It also gives the engine the whole interval to the next slot it must make,
rather than the sliver of slack before an ideal instant. Everything the engine
does happens in the pacer's "yes" branch, immediately after the swap.

## Presentation does not grant ticks

The rule the whole arrangement exists to keep, and the one a redesign will
break first.

`clock.frame` advances **once per iteration and never by an amount**. There is
no `ticks_due`, nothing asks the display how many simulation steps it is
allowed, and no code path can make a late present into a late simulation.

The failure it prevents is measured: while a blocking `flip()` paced the loop,
a compositor charging a whole extra refresh for a narrowly missed vblank ran
the game at 75% of target. Presentation jitter *was* simulation jitter, and
the loop was where the two were welded together.

## What it is not

**Not an event loop.** The window's queue is pumped once per iteration at a
place the loop chooses. Handing control to a window system's own loop would
put the frame's shape in the window system's hands, and pyglet's default
`on_close` is wired to a global event loop this engine does not run — which is
why the close button is observed explicitly rather than inherited.

**Not a scheduler.** There is no queue of jobs and no priority. The order is
written down, in one function, and changing it is an edit rather than a
configuration.

**Not re-entrant.** The scene callback is latched before it is called, so
rebinding `bps.loop` during a tick takes effect on the next complete tick
rather than part-way through this one.

**Not a place for per-subsystem policy.** The loop reports and sequences.
Whether a withheld present means a pause is the lifecycle's; whether a hidden
layer still advances is the compositor's; what a tick is worth is the clock's.

## Still open

**SETTLE is designed and unbuilt.** The world-side work a frame implies —
collision planes, chiefly — is built inside COMMIT today, in the same breath as
painting the frame it describes. Moving it to the head of the *next* iteration
would state literally what every pillar's docstring already claims, shorten the
deadline-critical `tick → commit → swap` segment, and let the engine tell a
game how much of the period is left before its tick decides how much work to
do. Kris, 2026-09-08: *"the biggest benefit is the gameloop has better
visibility into what the budget is this frame."*

Two things gate it. **The honest scope is two pillars, not three** — the
sprite's plane compose and the console's collision refresh can move; the
tilemap's cannot, because its refill produces the placements the draw uses, and
rebuilding its plane after the fact costs 6.6ms against 3.7ms for stamping the
bits with the quads. And **it only pays if it sits before the start wait**,
where the hoisted work overlaps a sleep. After it, the same work has merely
moved. On the board as *Settle at the head of the frame, not the tail of the
commit*.

**RESET has no name in the code.** Returning each layer to a known state for
next frame already happens — a sprite restamping fresh, a dirty band cleared,
an upload flag going down — but scattered through each pillar wherever was
convenient. COMMIT and RESET are sequential with nothing between them, so
whether they are two named slots or one bundled call is a real choice: bundled
is one dispatch with no way to skip the reset, split is a phase boundary a
reader can see.

**Where playback advances is contested three ways** — BEGIN, where it is now
(between the tick and the compose), or inside COMMIT. It is not resolvable from
the armchair, because the two live positions protect different things: advancing
in BEGIN means a game reading `sprite.frame` during its tick gets this tick's
pose; advancing after the tick means a `play()` issued during this tick starts
on this tick's frame. It wants a forcing scene rather than an argument.

**`prepare` has no instrument.** It sits between two measured spans and is in
neither. A prepare that grew expensive would surface as missed reserves and
`late` counts rather than as a cost anyone could attribute, which is the
hardest kind of regression to find. Cheap to add; nothing has needed it yet.

**The frame body's name means its middle third.** `_frame` does BEGIN, the
game, and COMMIT in one function. The model wants the phases named in the code,
and naming them is most of what makes SETTLE and RESET buildable rather than
merely designed.
