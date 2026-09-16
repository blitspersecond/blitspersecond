# A recording, and playing one

Two things, one road. `Sample` is a decoded recording that exists once per
file however many voices play it; `PCM` is the operator that plays one, gated
and resampled like any other source.

```python
shout = Sample("yeah1.wav")                  # decoded from disk
bleep = Sample("bleep", data, 8_000)         # from an array in hand

voice = engine.source(Program(lambda: PCM(shout)))
engine.schedule([(0, voice, {"gate": True})])
```

## The accepted file

**16-bit WAV, and nothing else.** Anything other than 16-bit is refused by
width, with the width it found:

```
yeah1.wav: only 16-bit WAV is supported, this is 8-bit
```

**Stereo is folded to mono by averaging the channels** on the way in. The
graph is mono until the mixing desk, where position is a `pan` on a strip
rather than something baked into a recording — a recording carrying its own
stereo image could not be placed. The fold is a mean, so a file whose channels
are opposite arrives as silence, correctly.

**Samples are scaled by 32,767, not 32,768.** That puts full-scale positive at
exactly 1.0 and lets the bottom code land a hair past -1.0. The asymmetry is
in two's complement and cannot be removed, only placed: dividing by 32,768
would put it on the loudest positive sample instead, a step short of unity.

The result is mono `float32`, one dimension, marked read-only at load.

## The recording keeps its own rate

A 22 kHz file is stored at 22 kHz and resampled by `PCM` as it plays.
Resampling at load would fix the pitch at unity forever and cost memory to do
it, and the speed register is downstream anyway.

**`Sample.rate` is not `SAMPLE_RATE`.** They are the two numbers this design
works hardest to keep apart: the first is what the file was recorded at, the
second is the engine's 48 kHz. `PCM` is the thing that converts between them,
and it is the only thing that should.

| | |
|---|---|
| `sample.data` | the samples, mono float32, read-only and shared |
| `sample.rate` | the recording's own rate in Hz |
| `sample.duration` | seconds at unity speed |
| `sample.filename` | absolute, or the name it was minted under |
| `len(sample)` | samples |

## Interning, and why there is no `copy()`

Naming the same file twice gives the **same object**, so a path named in ten
places is decoded once. Samples handed in as an array are never interned —
a file has identity and an array you built does not, so two callers naming one
path mean one recording while two callers passing samples mean two, however
alike.

**Immutability is what makes that safe.** The array is read-only from load and
nothing can change it, so every Stage playing one file reads the same memory
and none of them has to know they are sharing. `PCM` indexes straight into it
rather than taking a copy per voice.

It is also why this interns without offering the `copy()` that
`CacheableResource` otherwise expects. A `PixelBuffer` needs one because a
caller may want to draw into their own pixels, and interning without an escape
would make that impossible. **There is nothing here to escape from** — a
second identical `Sample` would differ from the first in no way a program
could observe.

**A cache hit skips the constructor's checks.** `Sample(path, None, 44_100)`
is a `TypeError` on a path that has not been loaded, and silently returns the
cached recording on one that has, because the cache key is the path alone.
The error is therefore order-dependent, which is on the board; do not rely on
the refusal to catch the mistake.

## `PCM`

A sampler voice and deliberately no more: a source like `Sine` or `Noise`, at
the head of a Program, with everything downstream — envelope, filter, pan —
the same vocabulary anything else gets. A drum machine is `PCM` into `Gain`;
a voice line is `PCM` on its own.

### The gate edge starts it, not the write

Raising `gate` rewinds to the cue point and plays; lowering it stops **dead**,
at that sample. Holding it high neither loops nor retriggers, so a recording
that has run out stays finished until the gate falls and rises again — which
makes a rapid retrigger a deliberate pair of writes rather than something that
can happen by accident.

```
never gated        peak 0.0000
gate at 0          plays from the start
gate off at 200    sample 199 = 0.00415, sample 200 = 0.0
```

### `playhead` is the cue point *and* the seek

Which of the two it is depends on whether it is **written**, not on what it
holds. In seconds, so it survives the `sample` register being written and
means the same thing at every recorded rate.

| | |
|---|---|
| holding a value | where the next gate edge starts from |
| written under a running sound | a jump cut, at that sample |
| written under a stopped one | parks the head and waits there |

```
cue 0.5 then gate       first sample 0.5000     (half way into a 1s ramp)
gate, then seek at 400  sample 399 = 0.00831 -> sample 400 = 0.2500
```

So scrubbing is a stream of writes, seeking is one, and playing a region is a
write and a gate with a second gate write timed at the out point. A sampler
never touches it: the default is zero, so a gate edge rewinds to the start
exactly as it would if the register did not exist, which is why a drum hit is
still one write and not two.

### `speed` is a resampling ratio, and so it is a pitch

The recording's own rate is the other half. A 22 kHz sample at speed 1.0 plays
at its recorded pitch by reading 0.459 of a stored sample per output sample,
interpolating between neighbours. Doubling the speed raises it an octave and
halves its duration, exactly as a tape or a tracker does — there is no
time-stretching here, and pretending otherwise would be a different and much
larger instrument.

```
speed 0.5   after 400 output samples, 0.5x through the recording
speed 1.0                             1.0x
speed 2.0                             2.0x
```

**Speed zero freezes rather than stops.** It holds whichever sample it had
reached — a click or a drone, not silence — and `gate` is what silences a
voice. A speed register that stopped the sound would be a second gate with
different manners.

```
frozen at 200   samples 200..204 all 0.00417, still 0.00417 at 399
```

### Writing a different recording restarts it

A position measured in the old recording means nothing in the new one, so the
replacement starts at the cue point, at the exact write timestamp, inheriting
whatever the gate is doing — swapped in under a held gate, it plays.

```
swapped at 200   sample 199 = 0.00415 -> 200 = 0.7500, the new one from 0
```

### Costs and scratch

23.6 µs per block for a sounding voice, 0.28% of the budget. A voice that has
run out or is gated off is quiescent and costs nothing.

Resampling is five array operations over the block, done into four scratch
arrays owned by the operator — into fresh ones it would be five allocations a
hundred and twenty times a second per sounding voice. The value scratch is
`float32` to match the recording, because `take` into an array of another
dtype quietly buffers: it reads the destination it is about to overwrite.

**Positions are float64 here**, which is the one place in the engine that
departs from the shared float32 ramp. Everywhere else an offset is a phase
within one block; this one is an absolute position inside a recording, and
float32 stops being able to tell neighbouring samples apart about sixteen
million in — around six minutes of 44.1 kHz audio.

## Limits

| | |
|---|---|
| format | 16-bit WAV only; any other width is refused by name |
| channels | mono, or stereo folded by averaging |
| stored as | mono float32, read-only, at the file's own rate |
| identity | interned by absolute path; arrays are never interned |
| `copy()` | none, and none is needed — the data cannot be changed |
| `Sample(path)` with a rate | `TypeError` — **unless the path is already interned** |
| `Sample(name, data)` without a rate | `TypeError` |
| an empty or multi-dimensional array | `ValueError` |
| a non-finite value anywhere in it | `ValueError` |
| a rate of zero or less | `ValueError` |
| `speed` | `0.0` upward; zero freezes |
| `playhead` | seconds, `0.0` upward |
| looping | none. A gate edge is the only thing that restarts a recording |
| time-stretching | none. `speed` is a pitch |
