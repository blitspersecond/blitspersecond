# Stages, lanes and the pull

A graph of `Stage`s, pulled once per block from whichever one is terminal.
Demand walks depth-first from there to the sources, and a block exists because
something asked for it.

    engine.output                  the terminal Stage, pulled by the transport
      └── desk lane ── copy → Gain → Pan ──┐
            └── CompositeStage             │  a submix
                  └── lane ── copy → Gain → Program ──┐
                        └── SourceStage               │  a voice
                              └── Program: Sine → Envelope

**A Stage is a place in the graph; a Program is what it does there.** Those
are the two nouns, and almost everything else on this page follows from
keeping them apart.

## A Stage is the voice

There is no voice object. A `SourceStage` running `Program(Sine, Envelope)`
*is* one note's worth of machine, and calling the same factory again builds a
second that shares nothing with the first. Which of them receives a note is
the caller's policy and never the engine's — there is no channel number, no
slot table, no stealing rule and no polyphony cap anywhere in here.

That is why voices are cheap enough to spend. Where two things must be
controlled separately, they are two Stages rather than one Stage with a mode.

## A Program is a recipe, not a chain

`Program` holds **zero-argument factories**. It has no DSP state and belongs
to nobody, so one blueprint builds a hundred voices that share nothing:

```python
voice = Program(Sine, lambda: Envelope(0.01, 0.0, 0.2, 0.7, 0.3))
```

An Operator needing construction arguments is wrapped, because what is stored
is the factory. `Sine` is spellable bare only because it needs nothing; eight
of the 24 operators require the `lambda`.

Wiring that blueprint to a Stage produces a `_BoundProgram` — real Operators,
with real state, belonging to exactly one lane. **Handing out one Operator to
several lanes would have them share a filter's history and an oscillator's
phase**, and two notes would fight over one voice. The factory is what makes
that impossible rather than merely discouraged.

## The four forms

Distinguished only by the relationship between inputs and Program.

| | inputs | Program | output |
|---|---|---|---|
| `SourceStage` | none | one, local | mono |
| `CompositeStage` | any | one instance **per lane** | mono |
| `ApplyStage` | any | one **shared**, lanes meet at its head | mono |
| `MixingDesk` | any | none | stereo |

**`SourceStage` has no `connect()`.** A source Program cannot silently become
an effect Program because somebody wired an input into it — wanting inputs
means wanting a different Stage. The absence is the contract.

**Composite against Apply is parallel against junction.** A Composite gives
each lane its own chain and sums the results, which is what an instrument
wants: three notes through three envelopes into one output. An Apply hands
every lane at once to one chain's head Operator, which is what a cross-input
effect needs — `RingMod` multiplying two signals, `Duck` using one to push
down another.

Verified on a two-lane Composite holding one `Envelope` in its Program: two
lanes, **two distinct Envelope instances**, and `gate` declared on the Stage
rather than owned by either lane. That is the coherent instrument default —
independent DSP state, one shared control.

**An empty Program is a wire**, so an empty Composite is a plain `Gain` into
the mix, which is how a submix bus is built.

**A `MixingDesk` has no Program at all.** A desk is routing rather than
processing; anything that wants to change the sound is a Stage upstream of it.
It is the only Stage with two channels, and its output cannot feed a mono one.

## A lane

Every input gets a `_Connection` — its own Bus, its own level, and on a
Composite its own instance of the Program:

    connected Stage → copy → structural Gain → [Program] → Mix

**The copy is not avoidable, even at unity gain.** The upstream Stage's
settled block is cached and may be feeding several lanes this block, so a lane
working in place would be editing a block its siblings are about to read.

The structural `Gain` is lane-scoped, which is what gives every connection its
own `level` without the connected Stage knowing it is on a desk. Disconnecting
releases the lane's rows, and automation aimed at a lane that has gone does
not resurrect if that source is reconnected — a new connection builds fresh
defaults. [control.md](control.md) owns the register half of this.

## The settled cache

**A Stage renders at most once per block**, and every later pull in the same
block hands back the identical cached Bus.

That makes fan-out correct before it makes it cheap. One oscillator feeding
six consumers must advance its phase once, not six times, or the six hear
different signals and the shared source is not shared at all. Measured with a
source feeding six Composites over ten blocks: **ten renders**, not sixty.

## Quiescence

A Stage that is silent *and* has nothing left to produce is skipped entirely.
It is not a claim that the current block is zero — it is a claim about the
future, that with no new stimulus this Stage has no retained output left. Only
the Stage's operators know that, so the answer is propagated along the bus by
each one in turn.

**Every uncertainty resolves to staying awake.** Being wrong in that direction
costs CPU; being wrong in the other drops a note. An Operator that has not
declared a rule stays active, a cycle asking whether it is asleep answers no,
and a Stage that has never rendered has proved nothing.

What it is worth, measured on a Ryzen 7 5700X3D — 32 voices of
`Sine → Envelope` into one desk, gated on against never gated, both arms in
one process:

| 32 voices | per block | of the 8,333 µs budget |
|---|---:|---:|
| sounding | 812.8 µs | 9.8% |
| idle | 36.1 µs | 0.4% |

**A sleeping source freezes rather than free-running.** Its phase is left
where it was, and on wake it resumes there — it does not race forward through
samples nobody heard. A Composite and a desk leave quiescent lanes out of the
mix entirely, so a block costs the voices actually sounding or draining a
tail.

**An `ApplyStage` still passes its sleeping lanes**, because there lane order
is meaning: `Duck`'s first input is the signal and its second is the control,
so dropping a quiet lane would promote the one after it.

A write addressed to a Stage or one of its lanes wakes it before that block is
processed, and so do topology changes and an active upstream lane. **Asking
whether a Stage is asleep never admits a pending batch** — only ordinary
resolution does that, so inspection cannot pull a scheduled write into the
current block.

Quiescence observes DSP state and nothing else. It never invents a missing
gate edge, ends a note, reclaims a voice or takes part in allocation.

## The feedback boundary

A pull that arrives while a Stage is already rendering gets that Stage's
**previous** settled output instead of recursing. The consumer copies it
immediately, so the Stage may replace its output at the end of the current
render without aliasing what the cycle read; the first pull of all sees the
zero-filled initial output.

So a cycle in the graph consumes prior settled data and reads as one block of
delay, rather than as a stack overflow. Verified on a two-Stage loop: the
graph renders, terminates, and sustains.

## Topology is construction-time state

Building a Stage, connecting one, attaching a register signal and replacing
the output all raise `RuntimeError` once a driver owns the engine:

    audio topology cannot change while AudioEngine is driven

The graph is frozen for the whole run, which is what lets the settled cache
never reconcile a concurrent change. The runtime surface is `schedule()` and
`cancel()` and nothing else — live rewiring means mutating the worker's
objects underneath it, which needs a command contract rather than an
unsynchronised write.

The practical consequence is an ordering one: a game builds its graph between
`BlitsPerSecond()` and `bps.start()`. [audio.md](audio.md) has the sequence.

## Limits

| | |
|---|---|
| inputs per Stage | no cap; connections are held in connection order |
| connecting the same Stage twice | idempotent, not an error |
| disconnecting an unknown Stage | ignored, not an error |
| connecting across engines | `ValueError` |
| connecting a stereo Stage to a mono input | `ValueError` |
| a Lane-scoped Operator in a Source or Apply Program | `ValueError` — no single input identity for it to belong to |
| an `ApplyStage` with an empty Program | silence, whatever is connected — with nothing at the head to reduce the lanes, there is no single answer |
| changing topology while driven | `RuntimeError` |
