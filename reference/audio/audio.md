# Audio

Making a sound, from a wave in an array to a block the sound card has taken.

    bps.audio                       the graph, already being driven
    engine.source(Program(Sine))    a voice IS a Stage
    engine.output.connect(voice)    a strip on the terminal desk
    engine.schedule([...])          writes, each at an exact sample

**(Alpha) — the machinery is settled and the layer a game reaches for is
not built.** Everything below this line is a faithful account of what runs:
24 operators, four Stage forms, two transports, 706 tests. What does not
exist is the surface above them. There is no `play("door.wav")`, no voice
allocation, no polyphony policy and no velocity mapping, so a game today
constructs voices and schedules register writes at sample offsets — which is
the right level for building an instrument and the wrong level for closing a
door. *[The missing layer](#the-missing-layer)* says exactly what is absent
and why it is being waited for rather than guessed at. Nothing else on this
page is provisional.

## The shape

    ┌──────────────────────────────────────────────────────────────┐
    │  bps.audio          one AudioEngine, for the life of the run  │
    │                     the sample clock, and the write timeline  │
    └──────────────────────────────────────────────────────────────┘
                                   ▲  advance() — one block, pulled
    ┌──────────────────────────────┴───────────────────────────────┐
    │  MixingDesk         the terminal Stage: mono strips → stereo  │
    └────────┬─────────────────────────┬───────────────────────────┘
             │ a lane: copy → Gain →   │ Pan
    ┌────────┴────────┐       ┌────────┴────────┐
    │  Stage          │       │  Stage          │   a Stage IS a voice
    │  ┌───────────┐  │       │  ┌───────────┐  │
    │  │ Program   │  │       │  │ Program   │  │   a chain of Operators,
    │  │ Sine→Env  │  │       │  │ PCM→Gain  │  │   in place, on one row
    │  └───────────┘  │       │  └───────────┘  │
    └─────────────────┘       └─────────────────┘

             every mono signal is one row of ONE float32 slab

    ┌──────────────────────────────────────────────────────────────┐
    │  Driver             one thread, one stream, blocking writes   │
    │                     — or render(), the same boundary, unpaced │
    └──────────────────────────────────────────────────────────────┘

**48 kHz, in blocks of 400 samples.** That is the unit of everything: every
Operator writes exactly 400 samples, every `Block` is 400 long, and the clock
moves by 400 or not at all. One second is exactly 120 advances, and one block
is 8,333 µs — the budget every cost on these pages is quoted against.

**400 is the knee of a cost curve, and nothing else.** Block size trades CPU
against responsiveness: fixed per-block overhead is amortised over a longer
block while the working set stays resident, so cost falls as the block grows
and every wait gets longer. Swept offline on a Ryzen 7 5700X3D with 32
continuously active voices across 128 stages:

| block | max wait | median render | CPU per second of audio | realtime |
|---:|---:|---:|---:|---:|
| 2400 | 50.000 ms | 3.455 ms | 6.93% | 14.44x |
| 1600 | 33.333 ms | 2.607 ms | 7.86% | 12.73x |
| 800 | 16.667 ms | 1.766 ms | 10.76% | 9.29x |
| **400** | **8.333 ms** | **1.291 ms** | **15.80%** | **6.33x** |
| 256 | 5.333 ms | 1.126 ms | 21.48% | 4.65x |
| 200 | 4.167 ms | 1.060 ms | 25.86% | 3.87x |
| 128 | 2.667 ms | 0.964 ms | 36.78% | 2.72x |

**Flat above, steep below.** 800 costs 5.0 points less than 400 and doubles
every wait; 256 costs 5.7 points *more* to save 3 ms; 128 costs 21 points.
Four hundred is where the marginal CPU per millisecond of latency turns over.

Two things follow, and both are easy to get wrong. It is also exactly half a
60 Hz simulation step, and **that is a coincidence with no authority in it** —
convenient, but not why the number is 400, and it must not be defended as
though it were. **If a knee and a frame relationship ever disagree, the knee
wins.** And the knee is a fact about one CPU's cache and context-switch costs
rather than about audio, so another machine gets the sweep **re-run**, not this
number copied.

Only one machine has ever been swept, so the *shape* is an argument rather
than an observation: fixed per-block overhead amortised against a resident
working set means a turnover should exist somewhere on any machine. Expect a
knee; do not assume this one's position, or that the curve either side of it
is this steep. The method transfers even where the number does not — sweep
offline, plot cost against maximum wait, take the smallest block still on the
flat.

**Pulled, never pushed.** A block exists because something asked for it. The
transport demands the terminal Stage, that Stage demands whatever feeds it,
and the demand walks depth-first to the sources. A Stage nothing listens to
costs nothing; a Stage several things listen to renders once and is cached.

**Control is timestamped writes, and there are no setters anywhere.** An
Operator declares registers — `frequency`, `gate`, `level` — and everything
that changes a sound is a write to one, applied at a sample offset the caller
chose. That is why a note can start between two samples instead of on the
nearest block boundary, and why nothing in a game holds an audio object it has
to poke.

**Mono until the mixing desk.** Position is a `pan` on a desk strip rather
than something an operator carries, so one vocabulary serves every voice, a
recording cannot arrive with an image baked into it, and every Bus, delay ring
and filter in the engine is half the size it would otherwise be.

## Two clocks, and nothing converts between them

| | counts | paced by |
|---|---|---|
| simulation | frames, as an integer | nothing. `time = frame · step`, exact |
| audio | samples, as an integer | the output device's backpressure |

**The sample clock counts what the device has actually consumed.** It is the
one clock in the engine the game does not tell the time by: nothing in a game
reads it, and anything needing wall time asks the engine. A score's internal
timing is relative — `schedule()` takes offsets from an origin the audio
worker picks — so a caller never reads the moving clock and a late first event
cannot smear the piece behind it.

**Audio is the single exception to the engine's poll-at-the-cut rule**, and
the blocking write is the reason. PortAudio's `write` returns when the device
has room for more, so `advance → write → repeat` is paced by the hardware
itself with no timer, no sleep and no deadline arithmetic. Putting that on the
game's thread would mean blocking the simulation on a sound card. So audio
gets the only thread in the engine, and it stays off the sixteen milliseconds
the simulation and the picture share.

## Where the engine touches it

One `AudioEngine` and one `Driver` are built in `BlitsPerSecond.__init__` and
last the life of the process. Neither touches a device there — an engine is a
graph and a driver is a thread that has not started.

**The device opens at the top of `Loop.run`, and the ordering is load-bearing
in both directions.** Starting the driver freezes the audio topology, so it
must happen *after* the game has built its voices; it blocks until the stream
has accepted a block, so it must happen *before* the presentation baselines
exist, or opening a device would read as a stalled first frame.

Audio comes down first in teardown, before the window, so nothing is still
pulling a graph while the rest is dismantled. In between it runs continuously
— **there is no pause or resume**, through suspension, backgrounding and
everything else. A game being paused is not a reason to tear down an audio
device, and reopening one is where the clicks live.

## What a game can do today

Build a graph before `bps.start()`, then write to it:

```python
bps = BlitsPerSecond()
engine = bps.audio

voice = engine.source(Program(Sine, lambda: Envelope(0.01, 0.0, 0.2, 0.7, 0.3)))
engine.output.connect(voice)
engine.schedule(
    [
        (0, engine.output, voice, {"level": 0.8, "pan": -0.25}),
        (0, voice, {"frequency": 440, "gate": True}),
        (24_000, voice, {"gate": False}),
    ]
)
```

**A `Program` holds zero-argument factories, not Operators**, which is what
lets one blueprint build a hundred voices that share no state. An Operator
taking construction arguments is therefore wrapped — `lambda: Envelope(...)`
above, and the same for the eight of the 24 that require one. Bare `Sine`
works because it needs nothing.

**`engine.output` is already a `MixingDesk`**, empty and settling stereo
silence, which is what lets the driver open and hold the device before a game
has built anything at all. Connecting to it is the shortest road to being
heard; `engine.mixing_desk()` builds another where a game wants its own.

**Topology is construction-time state.** Building Stages, connecting them,
attaching register signals and replacing the output all raise `RuntimeError`
once a driver owns the engine. The runtime surface is exactly two calls —
`schedule()` and `cancel()` — because rewiring a live graph means mutating the
worker's objects underneath it, which needs a command contract that does not
exist rather than an unsynchronised write.

**A scheduled future can be withdrawn by naming the node it addresses.**
`engine.cancel([voice])` drops that voice's unresolved writes;
`schedule(events, replacing=[voice])` does both as one command so a
replacement cannot be torn in half. Identity is the graph's own — a write
already names the Stage it is for — so there is no handle to keep or group
name to mint. The boundary is the same one `dt=0` means: a target's unresolved
writes end at the first unresolved block, never retroactively.

## The missing layer

The four things a game-facing API has to decide, none of which are decided:

- **Playing a `Sample` is not a one-liner.** It means building a `SourceStage`
  with a `PCM` Program, connecting a desk strip, and scheduling a gate.
- **Voice allocation is the caller's, every time.** Four callers have written
  three strategies by hand: a live pool with LRU stealing, a scored pass that
  measures peak polyphony up front and steals nothing, and a transport over
  stems that is not notes at all. The scored pattern has now been written
  independently twice, which is the threshold that promoted this from a
  conversation to a card.
- **Velocity has no shared spelling**, and the obvious one is a trap: writing
  `{"level": velocity}` silently discards the instrument's own level. Three
  callers have written some version of the multiply instead.
- **Polyphony policy is stated as absent, and an API that plays a note IS
  one.** Something has to decide what happens when a fifth note arrives at a
  four-voice instrument. The presumed resolution is an allocator a game opts
  into rather than a cap the engine imposes, but that is a decision to take
  rather than assume.

It is deliberately last. Integrators settle after the leaves they service, and
the machinery underneath is now settled enough that this can be designed
against a real consumer instead of an imagined one. See *Every audio caller
writes its own voice allocator* on the board.

## The chapters

### The machine

| | |
|---|---|
| [graph.md](graph.md) | stages, lanes and the pull: the four forms, the settled cache, the feedback boundary, and how a silent voice costs nothing |
| [control.md](control.md) | registers and the timeline: what a write is, when it lands, how a lane shadows a Stage, and driving a register from audio |
| [blocks.md](blocks.md) | the memory underneath: one slab, stable row IDs, the delay ring, and why nothing is allocated per block |

### The vocabulary

| | |
|---|---|
| [operators.md](operators.md) | the 24 operators, what each costs, and the arity split that is declared by inheritance |
| [sample.md](sample.md) | a recording and playing one: the accepted WAV profile, and `PCM`'s gate, playhead and speed |
| [instruments.md](instruments.md) | the percussion presets: three roots, eight names, and the level they are not levelled against |

### Out

| | |
|---|---|
| [transport.md](transport.md) | the device thread and the offline renderer: the blocking write as a clock, the readiness handshake, and which device opens |

## Limits

| | |
|---|---|
| sample rate | 48 kHz, fixed. Nothing in the chain resamples on the way out |
| block | 400 samples, fixed: 8,333 µs, 120 a second |
| channels | mono everywhere except a `MixingDesk`'s output, which is `(400, 2)` |
| voices | no cap. A Stage is a voice and the graph has as many as it needs |
| delay depth | 4 seconds per operator that keeps history |
| recordings | 16-bit WAV only, folded to mono, kept at their own rate |
| threads | one, and it is the driver's |
| allocation per block | none, with stable topology and settled delay depth |
| platforms | Linux and macOS open PortAudio's default; Windows opens WASAPI's default output |

**Windows refuses rather than guessing.** PortAudio's overall default there
can be a legacy MME endpoint even when the useful one is available through
WASAPI, and a wrong-but-working device reaches a player as bad latency rather
than as an error. [transport.md](transport.md) owns the device policy.
