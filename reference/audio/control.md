# Registers, the timeline, and signals

An Operator has no setters and a game never calls one. It declares registers
— `frequency`, `gate`, `level` — and everything that changes a sound is a
**timestamped write** to one of them, applied at a sample offset the caller
chose.

```python
engine.schedule(
    [
        (0,      voice, {"frequency": 440, "gate": True}),
        (24_000, voice, {"gate": False}),
        (0,      desk, voice, {"level": 0.8, "pan": -0.25}),
    ]
)
```

Three-item events address a Stage; four-item events name the connected Stage
as well and address that input's lane. **Those are the only two addresses in
the engine**, which matters later: they are also exactly what `cancel()` takes.

## A write lands at a sample, not at a block boundary

A block is 400 samples, and an Operator cuts its block at each write
timestamp, rendering each piece under the value in force across it. Measured
against an unmodified reference render of the same graph:

| samples | gain applied |
|---:|---:|
| 130–136 | 1.0000 |
| 137–143 | 0.2500 |

The write was scheduled at sample 137 and the first sample at the new level is
137. `Gain` is the smallest place to see the whole pattern; every Operator
that reads a register does this.

## Order at one timestamp is the order you wrote it

Two writes at the same sample apply in the order they were submitted, and that
is observable rather than incidental. A `Pulse` whose burst length and gate are
written at the same instant:

| written | burst |
|---|---:|
| `pulse_ticks` then `gate` | 200 samples |
| `gate` then `pulse_ticks` | 48 samples |

The rising edge fires with whatever was in force *at that point in the
sequence*. **This is what makes a retrigger expressible**: `gate=False` then
`gate=True` at one timestamp is two edges at one instant, and a `Pulse` gated
at 0, then retriggered at 200, produces bursts beginning at 0 and 200.

Writing a value that is already in force is not an edge and retriggers
nothing. Gate is level-triggered, so holding it high is legato.

## Validation is the caller's, and all-or-nothing

Everything is checked on the calling thread, before anything is queued:

```
register 'frequency' must be at most 24000.0
register 'frequency' expects float, got bool
Stage has no register 'gate'
```

An out-of-range value or an unknown name is a caller's error with a caller's
traceback — not a failure inside the audio worker, where the only symptom
would be silence. A batch is admitted whole or not at all, so a bad event late
in a score cannot leave half of it playing.

**`bool` is tested before `int`**, because in Python `bool` *is* an `int`.
Without that order `True` would be an acceptable write to a frequency and
would set it to 1 Hz. Numbers are converted to the declared type, so writing
`600` to a float register stores `600.0` and an Operator never sees a type it
did not declare.

**NaN is rejected by the shape of the bounds check.** It is spelled
`not value >= minimum` rather than `value < minimum`, because every comparison
against NaN is False and the readable spelling would admit it. That guards
every register that *has* a bound; the unbounded ones are listed below.

## The timeline is relative, and the worker picks the origin

`dt` is a non-negative sample offset from a common origin, and the audio
worker chooses that origin when it drains the whole batch at the first
unresolved block. A caller never reads the moving clock.

That is what makes a score's internal timing exact: a late first event delays
the piece, it cannot smear it. A one-event batch travels the same road as a
complete piece of music.

Once admitted, writes live in a min-heap ordered by absolute timestamp and
insertion order — the second is the tiebreak that makes the table above true.
Before settling a block the engine pops that block's writes and caches them,
and **that cache is the resolution boundary**: a batch arriving afterwards
waits for the following block rather than editing one already being produced.

After rendering, every write is committed to its target's register bank. So a
Stage that was never pulled still has an authoritative starting value when it
next runs, and a bank always describes the value at the latest settled block.

## Withdrawing a future

`cancel()` drops the unresolved writes aimed at a target, and
`schedule(..., replacing=...)` does both as one command so a replacement
cannot be torn in half or arrive out of order.

```python
engine.cancel([stem])                                   # the voice's future
engine.cancel([(desk, stem)])                           # that strip's future
engine.schedule(events, replacing=[stem, (desk, stem)]) # an atomic swap
```

**The graph is the identity.** A queued write already names the Stage it is
for, so there is no handle to keep and no group name to invent, and a target
cannot go stale while the node it names exists.

A target is a Stage or a `(stage, source)` pair — **the same two addresses
`schedule()` accepts**, so what can be written can be withdrawn in the words
it was written in. The two are distinct, and that distinction holds:

| queued | cancelled with | survives |
|---|---|---|
| a lane write | that lane | no |
| a Stage write | that Stage | no |
| a lane write | the Stage | **yes** |

**A node's future is one future.** Two timelines writing the same Stage die
together; where they must die separately they are two voices, which the graph
makes cheap.

**Never retroactive.** A target's unresolved writes end at the first
unresolved block — the same boundary `dt=0` means — because a block that has
been rendered is a fact. And it is purely a control operation: withdrawing a
source's writes stops it being told to do anything new, while the echo and
reverb downstream go on ringing until they drain.

Naming a target with nothing queued is not an error; a transport that stops
twice is ordinary. A rejected batch changes nothing — validation runs before
the cancellation is queued, so a replacement that fails leaves what it was
going to replace exactly as it was.

## Stage scope, lane scope, and shadowing

A Stage-scoped register is one value shared by everything the Stage does. A
lane-scoped one exists once per connected input, which is what lets a mixing
desk give every strip its own level without the connected Stages knowing they
are on a desk.

**A Stage write broadcasts to every lane that has not claimed that name** —
and the claim is resolved in timestamp order. For a Composite whose Program
holds an `Envelope`, with two lanes:

```
  50 : Stage  attack = 0.10      reaches both lanes
 100 : lane a attack = 0.80      lane a claims the name here
 200 : Stage  attack = 0.30      reaches lane b only
```

settles at lane a `0.80`, lane b `0.30`. A lane write at 100 shadows a Stage
write at 200 and does not shadow one at 50. **That ordering is the whole
subtlety**: collecting the lane's names up front would make the earlier Stage
write vanish, and collecting them afterwards would let it win.

**A Stage's register bank is whatever its Program declares.** A Composite with
an empty Program has none at all, and `schedule([(0, comp, {"level": ...})])`
raises `Stage has no register 'level'` — because the structural lane gain is
*lane*-scoped, so `level` exists only on the lane and must be addressed there.
Reading is the mirror: `stage.registers_for(source)` overlays the lane's own
values on the Stage's.

## Driving a register from audio

A register signal replaces the stored value with a whole block pulled from
another Stage, so it can change every sample. A tremolo is an oscillator
connected to a `level`; a vibrato is one connected to a `frequency`.

```python
lfo = engine.source(Program(Sine))
instrument.connect_signal(lfo, to="level")
desk.connect_signal(lfo, to="pan", lane=instrument)
```

**There is no depth, bias or amount.** The control Stage produces exactly the
values the target register is to observe, which keeps modulation an ordinary
graph rather than a set of hidden modes.

It is a **separate kind of edge** from an audio lane: the source is not mixed
into anything, it settles once per block like any other Stage, and it can fan
out to several sockets while processing once — measured at four targets over
ten blocks, **ten renders**.

**While connected, the signal overrides the stored scalar completely**, and
scheduled writes keep updating that scalar underneath. Disconnecting restores
it rather than freezing whatever the signal last said. An Operator is handed
either a scalar or a `(400,)` array and uses the same vector operation for
both, because NumPy broadcasts a scalar rather than building 400 copies of it.

**`accepts_signal` is a claim the Operator implemented the array-valued
meaning**, not a property of the value's type. A signal-rate frequency has to
integrate phase rather than multiply, so opting in is per register and only
three do:

    Sine.frequency        Gain.level        _Pan.pan

Everything else refuses: `register 'sustain' does not accept a signal`.

## The registers

46 declarations across the vocabulary. Bounds are inclusive; **sig** marks a
register that accepts a signal.

| register | operator | default | min | max | unit | sig | scope |
|---|---|---:|---:|---:|---|---|---|
| `amount` | Reverb | 0.3 | 0 | 1 | — | — | stage |
| `attack` | Duck | 0.005 | 0 | — | seconds | — | stage |
| `attack` | Envelope | 0.01 | 0 | — | seconds | — | stage |
| `bits` | Crush | 8 | 1 | 24 | bits | — | stage |
| `clip_level` | Clip | 1.0 | 0 | — | linear | — | stage |
| `cutoff` | BandPass, HighPass, LowPass | 1000 | 20 | 21600 | Hz | — | stage |
| `damping` | Pluck | 0.5 | 0 | 1 | ratio | — | stage |
| `decay` | Envelope | 0.1 | 0 | — | seconds | — | stage |
| `delay_ticks` | Delay | 1 | 0 | 192000 | ticks | — | stage |
| `delay_ticks` | Echo | 4800 | 1 | 4800 | ticks | — | stage |
| `depth` | Duck | 1.0 | 0 | — | — | — | stage |
| `drive` | Tanh | 2.0 | 1e-06 | — | linear | — | stage |
| `duty` | Metal | 0.48 | 0 | 1 | ratio | — | stage |
| `feedback` | Echo | 0.0 | -0.999 | 0.999 | — | — | stage |
| `frequency` | Pluck | 554.365 | 20 | 24000 | Hz | — | stage |
| `frequency` | Resonator | 120 | 20 | 21600 | Hz | — | stage |
| `frequency` | Sine | 554.365 | 0 | 24000 | Hz | **yes** | stage |
| `frequency` | Saw, Square, Triangle | 554.365 | 0 | 24000 | Hz | — | stage |
| `gate` | Envelope, PCM, Pluck, Pulse | False | — | — | — | — | stage |
| `hold` | Envelope | 0.0 | 0 | — | seconds | — | stage |
| `level` | Gain | 1.0 | — | — | linear | **yes** | stage |
| `level` | the structural lane gain | 1.0 | — | — | linear | **yes** | **lane** |
| `mix` | Echo | 0.5 | 0 | 1 | — | — | stage |
| `pan` | a desk strip | 0.0 | -1 | 1 | position | **yes** | **lane** |
| `playhead` | PCM | 0.0 | 0 | — | seconds | — | stage |
| `position` | Pluck | 0.0 | 0 | 0.5 | ratio | — | stage |
| `pulse_ticks` | Pulse | 48 | 1 | 48000 | ticks | — | stage |
| `q` | BandPass | 1.0 | 1e-06 | — | — | — | stage |
| `q` | HighPass, LowPass | 0.7071 | 1e-06 | — | — | — | stage |
| `rate` | Crush | 48000 | 0 | 48000 | Hz | — | stage |
| `release` | Duck | 0.08 | 0 | — | seconds | — | stage |
| `release` | Envelope | 0.2 | 0 | — | seconds | — | stage |
| `resonance_decay` | Resonator | 0.035 | 2.083e-05 | — | seconds | — | stage |
| `sample` | PCM | the `Sample` given | — | — | — | — | stage |
| `speed` | PCM | 1.0 | 0 | — | ratio | — | stage |
| `string_decay` | Pluck | 1.0 | 1e-06 | — | seconds | — | stage |
| `sustain` | Envelope | 0.5 | 0 | 1 | linear | — | stage |
| `tune` | Metal | 1.0 | 0 | 78.176 | ratio | — | stage |

**Units are descriptive and convert nothing.** `seconds`, `Hz`, `ticks`,
`linear`, `ratio`, `position`, `bits` — they say what the number means at the
point it is declared, for a reader and for a graph inspector.

**Ticks are samples**, 48,000 to the second, which is the same clock
timestamps are on.

**Filter cutoffs stop at 20 Hz and 21,600 Hz** because the biquad form places
a double pole on the unit circle at DC and Nyquist, where retained state can
grow without bound. That is a stability rule, not a taste one.

**Two registers are open at both ends**, both of them a `level`, and negative
is deliberate there — it inverts phase, which is a real thing to ask for. A
further 16 are bounded below only. `gate` and `sample` are unbounded because
neither a boolean nor a `Sample` can be out of range.

## Limits

| | |
|---|---|
| `dt` | samples. Fractional is a `TypeError`, negative a `ValueError` |
| an event that is not 3 or 4 items | `ValueError` |
| an empty batch, or an empty value mapping | `ValueError` |
| an unknown register | `ValueError`, naming it |
| a Stage from another engine | `ValueError`, on `schedule()` and `cancel()` alike |
| `cancel()` with no targets | `ValueError` |
| NaN or infinity | refused on any register with a bound; **admitted on the two `level`s** |
| writing while driven | fine — `schedule()` and `cancel()` are the runtime surface |
