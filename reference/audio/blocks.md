# The memory underneath

Audio allocates nothing per block. At 120 advances a second across however
many voices are sounding, an array built per operator per block is the one
shape that turns synthesis into a garbage collection problem — so **every mono
signal in the engine is a row of one float32 slab.**

    BlockPool     one growing (rows, 400) float32 slab
      │
      ├─ Block    a stable integer row ID, resolved on every access
      │
      └─ Bus      one current Block, plus rings for operators that asked

One row is 400 float32: **1,600 bytes**.

## A row ID, never a view

`Block` hands out an integer and resolves the array on every access. Caching
that array is the bug the class exists to prevent: **claiming past the end of
the slab reallocates it**, which can happen on another voice, in another part
of the graph, between two lines of the operator holding it. A view into the
old slab keeps working, silently, against memory nothing plays.

Using a released `Block` raises rather than returning the row, which by then
may belong to another voice — otherwise it would be corruption with no
exception attached.

## Grow, never shrink

A game's voice count settles in the first few seconds and stays there, so
returning rows to the operating system means giving back memory that is about
to be asked for again. Released rows go on a **stack**, newest first, so a
claim/release pair reissues the same row and the sequence of IDs a given graph
produces is reproducible between runs:

```
claimed 4 rows        -> count 4
released row 2        -> count 4, free [2]
claimed again         -> got row 2, count still 4
```

The `claimed` flags are not how a free row is found — the stack does that.
They exist to catch a row being released twice, which would hand the same
memory to two owners and mix two voices into one buffer with nothing raised.

## Steady state claims nothing

With stable topology and a settled delay depth, rendering performs no claim,
no release and no growth. A lane's Bus claims its row once when the graph is
wired, and every block overwrites it in place.

Measured on a graph with a delay, rendering 120 further blocks:

```
before   count=28  capacity=36  claimed=28  free=0
after    count=28  capacity=36  claimed=28  free=0
```

## A Bus is one block, not a queue

The graph is pulled, so there is never a second block in flight waiting for
somebody to take it. A `Bus` holds the one block currently being settled, plus
whatever history an operator on that lane has asked for.

`ingest` takes a **copy** of another bus's current block rather than a
reference or a swap, because the source is some upstream Stage's settled
output and may be feeding several lanes this block. Whoever ingests it is
about to run operators that write in place.

**Mono and stereo share no base class.** `StereoBus` has no history and cannot
be delayed: everything that looks backwards is an operator, and operators live
upstream where the signal is still mono. By the time audio is stereo it is on
its way to the device. A `StereoBlock` also owns its array outright rather
than renting a pool row — there is one per desk strip plus one per desk, a
handful for the whole engine, and a pool is pure indirection at that scale.

## Mix moves no samples

Every Stage ends in a `Mix`, and it is structural rather than a choice — there
is no configuring it away.

**Lane zero is the accumulator.** The other lanes are added into its block,
and then that block is *swapped* into the Stage output rather than copied: the
summed row becomes the output's row, and the output's previous row goes back
to lane zero to be overwritten next block.

```
ids (0, 1) -> (1, 0);  a now reads 2.0, b reads 1.0
both handles still own a row
```

So mixing N lanes costs N-1 additions and no allocation at all.

**Nothing may read a lane's block after passing it to Mix.** The lane no
longer holds what it held — it holds either the sum or the output's old
contents. That is safe because every lane re-ingests from its source before
its next use, and a source clears its working bus, so no stale block outlives
the mix that consumed it. It is the sharpest invariant on this page and the
one most worth not breaking.

## The delay ring

An operator that needs the signal as it was N samples ago keeps the blocks
itself. **The ring stores row IDs, not arrays**: a block rotates into history
by handing over its pool row, and the row it gets back is the one that fell
off the far end. For a delay landing on a block boundary **no samples are
copied at all** — the ring is a permutation of who owns which row.

A delay that does *not* land on a boundary straddles two stored blocks, so the
output is assembled in **one scratch row**: the tail of the older followed by
the head of the newer. The scratch is claimed only while the delay is
unaligned and released the moment it becomes aligned again.

One ring per operator per lane. Two delays in one chain see different signals
— the second sees what the first did — so they cannot share.

### One reallocation, and it lands on the audio worker

The first history claim grows the slab to the **next power of two at or above
the ring depth**, so a ring costs one reallocation rather than one per row —
measured at one for an entire render, and none at all once the ring is full.

**That one reallocation happens while rendering**, because a ring is built the
first time its delay is used and grown again if `delay_ticks` lengthens. So it
lands on the audio worker rather than during graph construction, and it copies
the whole slab forward. A 4-second ring is 806 KB of copy at the moment a game
first plays through a long delay, or first automates a short one longer.

Not observed as a fault, and the common case escapes it entirely: a delay
built before `bps.start()` and left at a fixed depth builds its ring on the
engine's **first** block, which the driver renders before it starts the
stream. What is exposed is a delay first reached later in a run, or lengthened
during one. It is the one place where per-block work is not bounded by the
graph's shape, which is what makes it worth knowing.

### Footprint

What a delay costs, measured with its ring full. The four baseline rows are
the source's working bus, the lane's bus and the two Stage outputs.

| delay | ring | slab |
|---:|---:|---:|
| 0 | 0 | 6.2 KB |
| 400 | 1 | 9.4 KB |
| 9,600 | 24 | 56.2 KB |
| 96,000 | 240 | 406.2 KB |
| 192,000 | 480 | 806.2 KB |

**The 4-second ceiling is 806 KB per delay**, and it belongs to the register
rather than to the pool — the pool itself has no cap. An unaligned delay adds
its one scratch row and usually fits inside the spare capacity. For scale, 32
plain voices into one desk hold 96 rows, 150 KB, plus 33 stereo blocks at
103 KB.

### Depth is a register, so the ring re-reads it every block

**Shrinking is exact.** The ring keeps its newest blocks and a shorter delay
needs nothing older, so every step lands on real audio, fractional offsets
included. Verified against the dry signal: a delay dropped from 2,000 to 400
ticks reads the correct earlier block immediately.

**Growing fills from silence.** While the delay was short the ring was short,
so blocks scrolling into range as it lengthens were never stored. They come
back as silence — the signal genuinely was silent that long ago, as far as
this lane knows. A delay grown from 400 to 2,000 ticks renders exact zeros
across the newly-reachable span.

That is audible as a gap when a game automates a delay time upwards, and it is
a property of buffering only what is needed rather than a fault. It is worth
knowing before reaching for the automation: a fixed delay is exact from the
first block it can reach, and only a *lengthening* one has this shape. The
analogue answer is a third thing again — a tape or BBD delay moves its read
head and sweeps pitch rather than gapping — and whether that is the behaviour
BPS wants is on the board.

Pinned by `test_growing_a_delay_fills_from_silence_not_from_the_past`, so a
ring that later retains the deeper past has to fail that test and say so
rather than changing the sound quietly.

## Limits

| | |
|---|---|
| one row | 400 float32, 1,600 bytes |
| pool ceiling | none. The 4-second bound belongs to `delay_ticks` |
| slab growth | next power of two at or above a new ring's depth, in one reallocation |
| ring maturity | one row per block; a 4-second ring is full after 4 seconds |
| per-block allocation | none, with stable topology and settled depth |
| a released `Block` | raises on read, write or a second release |
| swapping across pools | `ValueError` |
| a negative or zero growth claim | `ValueError` |
| releasing an unknown or freed row | `IndexError` / `ValueError` |
