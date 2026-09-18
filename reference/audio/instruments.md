# The instrument presets

Ready-made voices, built from the public operator vocabulary. **These are
presets, not implementations** — every one is an ordinary `SourceStage`
running a Program anybody could have assembled, and there is no instrument
runtime underneath them.

```python
from blitspersecond.audio.synth.instruments import kick, snare, hat

drum = kick(engine)
desk.connect(drum)
engine.schedule([(0, drum, {"gate": True})])
```

Percussion is what the package holds today. It is the place tuned families
land too — brass, strings, wind — which is why it is a package rather than a
section of the operator vocabulary.

## A Stage is the voice

Call a factory twice and you have two independent voices. **Which of them
receives a hit is the game's policy, never the engine's**: there is no channel
number, no voice stealing, no velocity and no polyphony cap. Voices are cheap,
so where two things must sound at once they are two Stages.

Every factory takes the owning `AudioEngine` and returns a `SourceStage`.

## Three roots and seven tunings

    drum        Pulse -> Resonator -> Tanh -> LowPass -> Gain
    noise_drum  Noise -> BandPass -> Envelope -> Tanh -> Gain
    metal_drum  Metal -> BandPass -> Tanh -> Envelope -> Gain

| root | tunings |
|---|---|
| `drum` | `kick` |
| `noise_drum` | `snare`, `clap`, `shaker` |
| `metal_drum` | `hat`, `cowbell`, `cymbal` |

**`drum` is a beater striking a tuned body.** `Pulse` is the beater and
`Resonator` is the skin; `Tanh` supplies the thickening a hard-struck drum
gets from its own non-linearity, and the `LowPass` takes off the click the
pulse leaves behind. `kick` is that tuned low and long.

**`noise_drum` is a burst of hiss with a band and an envelope.** White noise
has every frequency, so where the band sits *is* the instrument — that is what
`tone` moves — and the envelope decides whether it reads as a hit or a hiss.
Each preset takes its own `seed`, so two of them are audibly different
instruments rather than the same noise twice. `clap` adds a very short, very
fed-back `Echo` — a hand clap is several hands slightly out of time, and 800
ticks is about 17 ms, too short to hear as a repeat and just long enough to
smear one strike into a cluster.

**`metal_drum` is a bank of inharmonic squares, filtered and gated.** Note the
envelope comes *after* the saturation here, unlike `noise_drum`: driving a
steady bank and then gating it keeps every strike's attack identical, where
gating first and driving second would make a quiet hit a different shape from
a loud one.

`hat(openness=...)` moves six tunings together — longer decay, softer attack,
darker tone, less drive — because that is what opening a hi-hat does to the
sound, not just how long it lasts. It refuses an `openness` outside 0 to 1.

**`tone` is where the filter sits, on all three roots.** It is the `LowPass`
cutoff on `drum` and the `BandPass` centre on the other two — the same control
under one word, which is what a drum machine's panel calls it. The 808 cymbal
circuit this family descends from has a *tone control stage* by that name.

## The banks are primes

    METAL_BANK    211, 307, 373, 509, 557, 823 Hz
    COWBELL_BANK  557, 823 Hz

The TR-808's six square oscillators moved to nearby primes, so the bank is
pairwise coprime. A set of whole-number frequencies repeats every `1 / gcd`
seconds, so a common factor of 100 would buzz a hundred times a second; with a
gcd of 1 the period is exactly one second. The longest preset here rings for
0.8 s, so no strike ever contains a repeat.

That exact one-second period was chosen so the bank could be generated once
and read back rather than run as six oscillators per block. **The engine does
not take that benefit yet** — six partials cost 64.7 µs a block against 0.6 µs
for reading a table — and it is on the board.

`COWBELL_BANK` is the same idea with almost all of it removed. Two partials is
few enough that the ear finds an interval between them, which is why a cowbell
has a note where a cymbal does not.

## The register surface is the operators inside

There are no instrument knobs. A preset's registers are the raw vocabulary of
its Program — `gate`, `frequency`, `cutoff`, `level`, `decay` and so on — and
[control.md](control.md) has them all. Construction arguments tune a preset
once; registers change it while it sounds.

**A hit is two writes at one tick:**

```python
events.extend([
    (at, voice, {"gate": False, "level": accent}),
    (at, voice, {"gate": True}),
])
```

The falling edge first is what makes it a retrigger rather than legato on a
voice that is already sounding. On a voice that is silent the pair is
identical to a single `gate` write.

## Levels, and the one thing to know before building a kit

**`level=1.0` does not mean the same loudness across the family.** Peak of one
strike, measured through to the output:

| preset | peak at `level=1.0` | `level` to reach 0.8 |
|---|---:|---:|
| `kick` | 0.055 | **14.59** |
| `drum` at 75 Hz | 0.087 | 9.21 |
| `drum` (120 Hz) | 0.082 | 9.74 |
| `hat` closed | 0.412 | 1.94 |
| `cymbal` | 0.550 | 1.45 |
| `hat` open | 0.552 | 1.45 |
| `snare` | 0.891 | 0.90 |
| `shaker` | 1.005 | 0.80 |
| `cowbell` | 1.043 | 0.77 |
| `clap` | 1.252 | 0.64 |

**A 23x spread**, and the pulse-excited presets sit at the quiet end. A
`Resonator` is a very narrow band-pass — for a 56 Hz kick at 0.068 s decay its
q is about 12 — and a 48-sample pulse is broadband, so almost none of it
survives the body. That is what a narrow band-pass does rather than a fault in
either operator, but nothing puts the energy back, so **a kit assembled at
sensible-looking levels has no kick in it.**

Three presets also exceed unity in mono, and whether that reaches the device
as clipping depends on where they sit in the image. The desk's constant-power
pan puts each channel at 0.707 in the centre:

| `clap` at `level=1.0` | peak | past unity |
|---|---:|---:|
| centred | 0.885 | none |
| hard left | 1.252 | 12 samples |

So a centred `clap` survives and a panned one clips. Levelling the family
against each other is on the board.

**Scale a preset's level, never overwrite it.** The obvious spelling for
velocity —

```python
{"level": velocity}          # discards the tuning the preset chose
```

— throws away the balance the factory set, which is how a crash ends up louder
than intended while the kick gets quieter. Record each voice's built level and
multiply.

## Limits

| | |
|---|---|
| factories | take the `AudioEngine`, return a `SourceStage` |
| voices | one Stage per voice; two calls are two independent voices |
| allocation, stealing, polyphony | none — caller policy |
| velocity | not a concept here; scale `level` |
| `hat(openness=)` | 0.0 to 1.0 inclusive; `ValueError` outside |
| `level=1.0` | not comparable across presets — see the table |
| retrigger | `gate` false then true at one timestamp |
