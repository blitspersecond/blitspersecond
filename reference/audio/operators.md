# The vocabulary

An Operator is one step of DSP. It takes a lane's `Bus`, does its work **in
place** on that bus's current block, and returns the bus. Nothing is
allocated, and the block it was handed is the block it hands on.

Twenty-four of them, assembled into a [`Program`](graph.md) and bound to a
Stage. None knows where in a graph it sits.

## Arity is declared by inheritance

    Operator      process(bus, /) -> Bus          unary, positional-only
    Compositor    process(*inputs) -> Bus         N-ary, and only ever a head

Every operator in a chain after the first takes exactly one bus, and the unary
signature is what says so. A `Compositor` widens it deliberately: the chain
hands the **first** operator every prepared lane and every operator after it
the single bus produced so far, so a compositor reduces N lanes to one and
everything downstream is ordinary unary work.

Inheriting from the right one of the two *is* the declaration. There is no
`*inputs` on a unary operator to make the two look alike, and a `Compositor`
that forgets to override is a passthrough rather than a silent reducer.

Four are N-ary: `Mix`, `RingMod`, `Duck`, and the desk's private `_Pan`.

## What it costs

Median microseconds per block on a Ryzen 7 5700X3D, one session, 200 blocks of
warm-up off the metric and 600 measured. Each figure is the operator's cost
above the same graph without it, against the **8,333 µs** block budget.

| operator | µs/block | % budget |
|---|---:|---:|
| `Duck` | 189.72 | 2.28% |
| `Echo` | 173.59 | 2.08% |
| `Metal`, 6 partials | 64.74 | 0.78% |
| `Reverb` | 28.34 | 0.34% |
| `Metal`, 2 partials | 27.89 | 0.33% |
| `Crush` | 22.66 | 0.27% |
| `PCM` | 23.63 | 0.28% |
| `Resonator` | 8.40 | 0.10% |
| `LowPass` / `HighPass` / `BandPass` | ~6.1 | 0.07% |
| `Triangle` / `Square` / `Saw` | ~12.5 | 0.15% |
| `Pulse` | 11.93 | 0.14% |
| `Pluck` | 10.40 | 0.12% |
| a desk strip (`Gain` + `Pan`) | 8.25 | 0.10% |
| `Sine` | 7.78 | 0.09% |
| `Noise` | 6.72 | 0.08% |
| `Clip` | 6.08 | 0.07% |
| `Delay`, block-aligned | 5.53 | 0.07% |
| `Tanh` | 5.20 | 0.06% |
| `Gain` | 4.01 | 0.05% |
| `Envelope` | 3.72 | 0.04% |
| `RingMod` | 2.73 | 0.03% |

An empty source is 1.58 µs, which is the floor a Stage costs to be pulled at
all. **A sleeping voice costs nothing measurable** — a `Pulse` or `Pluck` that
has never been gated is quiescent, and its Stage is skipped before any of this
is reached.

**Two operators dominate, and for one reason.** `Duck` and `Echo` are an order
of magnitude above everything else because each sample they write depends on
one read back from what they just wrote:

```python
buffer[position] = dry + feedback * delayed
```

That recurrence has no vectorised form, so both run a Python loop per sample.
About 44 `Duck`s or 48 `Echo`s would consume the whole budget. It is not
hypothetical cost — `clap` contains an `Echo`, so an ordinary drum pattern
pays it.

**`Reverb` proves the bulk path rather than arguing for it.** It runs *four*
feedback combs for an eighth of `Echo`'s cost, because every comb is longer
than a block: the samples a block reads back were written by an earlier block,
so the recurrence never closes inside one and whole slices move instead. An
`Echo` with a delay longer than a block is the same shape as one of those
combs. `Duck` is the one that genuinely cannot vectorise — its envelope
depends on the previous sample *and* on a comparison choosing which
coefficient to use, so there is no window in which it unrolls.

Both are on the board, and neither figure is settled work.

## Quiescence, per operator

An operator is asked whether **its** output has settled, given what it was
handed, and the answer rides the bus into the next one. The default is to stay
awake: an operator that has not thought about the question must not be
skipped.

| rule | operators |
|---|---|
| passes its input's answer through | `Gain`, `Clip`, `Crush`, `Tanh` |
| a tail: 3 blocks under `1e-4`, then state zeroed | `Echo`, `Reverb`, the three biquads, `Resonator` |
| its own explicit lifetime | `Envelope`, `Pulse`, `PCM`, `Pluck`, `Delay` |
| always awake | `Sine`, `Saw`, `Square`, `Triangle`, `Noise`, `Metal` |
| always awake (N-ary) | `Mix`, `RingMod`, `Duck` |

**Stateless operators preserve quiet input** — that is the first row, and it is
what lets a chain of them settle behind a source that has stopped.

**A ringing operator cannot answer from its output alone.** A filter whose
history is still decaying may be outputting near-silence, so the shared
`_tail_quiescent` checks the output *and* every state array, requires three
consecutive blocks under the floor, and then **zeroes the state** — so the next
wake starts from silence rather than resuming a tail nobody heard. Three
blocks is 25 ms, because one quiet block is not evidence of a finished tail: a
signal can pass through near-zero on its way somewhere.

**The free-running sources never sleep on their own.** An oscillator has no
idea whether anything is listening, so what silences a voice is the `Envelope`
after it — and a Stage that does sleep freezes the oscillator's phase rather
than letting it run on.

## The sources

`Sine`, `Saw`, `Square` and `Triangle` are free-running and share one
`frequency` contract: the phase ramp is cut at every write, so a frequency
change takes effect at the exact sample **without resetting phase**.

`Noise` has no live register at all — an explicit construction-time seed is
what makes a graph deterministic, and it is why two presets built on noise can
be told apart by ear.

`Metal` sums several rectangular oscillators at unrelated frequencies. Its
asymmetric levels remove the DC that a non-50% duty cycle would otherwise
introduce, and its cost scales with the bank: six partials is 64.7 µs, two is
27.9 µs. [instruments.md](instruments.md) covers what the banks are and why
they are primes.

`Pulse` is an excitation rather than a tone: a fixed-length burst of 1.0 on
each rising gate, which a `Resonator` downstream turns into a struck body.

`Pluck` is a plucked string — noise in a filtered loop one period long. A
rising gate excites it deterministically; a falling gate does **not** erase the
decay, because a string does not stop when you let go. `string_decay` is named
apart from an `Envelope`'s `decay` so both can inhabit one Program and share
`gate` on purpose.

[sample.md](sample.md) owns `PCM`.

## The compiled kernels

Two operators own a recurrence where each sample needs the answer to the one
before it, so neither has a bulk form and both are `@njit(cache=True)`:

- **`Pluck`** — the Karplus-Strong loop. Every sample reads a buffer the
  previous sample wrote, through a filter whose state the previous sample set.
- **`_Biquad`** — the second-order IIR section under the three filters and,
  through `BandPass`, under `Resonator` as well. Transposed direct form II,
  with the state kept in float64 while the samples are float32, so the
  recursion has the wider type's headroom and only the stored result rounds.

**Both compile from their operator's `__init__`**, through a warm function
held behind an `lru_cache(maxsize=1)`. That matters more than it looks:
constructing an operator does not compile its kernel, *calling* it does — and
the first call would otherwise be the first block the operator renders, which
is on the audio worker. Warming from the constructor moves it to graph
construction, which cannot happen while a driver is running.

**A cached kernel is not necessarily a cheap one.** Measured across fresh
processes with numba's on-disk cache present:

| first construction | | |
|---|---:|---|
| `Pluck()` | 133–152 ms | expensive **every run** |
| `LowPass()` / `HighPass()` / `BandPass()` | ~1.5 ms | the three share one kernel |
| any second construction | < 0.05 ms | |

The biquad cost 223 ms on the single run that compiled it and wrote the cache,
and 1.5 ms on every run since. `Pluck` never gets cheap. So `cache=True` is
not a uniform discount, and an engine-wide prewarm budget has to be measured
per kernel rather than estimated by counting them. Where that prewarm belongs
— behind one splash screen with the engine's graphics kernels — is on the
board.

`Program.declare()` also builds Operators and throws them away, so a `Pluck`
in a Composite's Program is constructed once more than it looks. That is paid
during construction too.

## Filters

`LowPass`, `HighPass` and `BandPass` are thin subclasses of a shared
second-order IIR section and contain no filtering of their own — each is the
three numerator coefficients that make it what it is. `Resonator` wraps a
`BandPass` at a very narrow bandwidth, which is a bell: it rings at a pitch
for a decay time when excited.

**The engine has no third-party DSP dependency.** The section is the
difference equation, compiled. At 400 samples, 120 times a second per filter
instance, a per-call library boundary is the same order as the arithmetic
inside it, so owning it is worth about 2.7x on the whole operator. It also
puts the filter state in our hands at block granularity, which is what any
future smoothing of a live cutoff change needs.

**Cutoffs span 20 Hz to 21,600 Hz.** That is a stability rule rather than
taste: the biquad form places a double pole on the unit circle at DC and
Nyquist, where retained state can grow without bound.
[control.md](control.md) has the full register table.

## Limits

| | |
|---|---|
| block | every operator writes exactly 400 samples |
| allocation | none per block; operators work in place on the bus handed in |
| arity | unary, or N-ary as the head of a chain — never both |
| a `Compositor` given the wrong lane count | `ValueError`, naming the operator |
| an operator with required arguments | wrapped in a `lambda` inside a `Program` |
| `numba` | required, for `Pluck` and the biquad |
| third-party DSP | none |
