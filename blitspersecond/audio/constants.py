"""The audio grid: how fast, and how much of it is settled at a time."""

from numpy import arange, float32

#: Samples per second. 48 kHz because it is what the hosts this engine talks
#: to run at natively -- PipeWire and WASAPI shared mode both default to it --
#: so nothing in the chain resamples on the way out.
SAMPLE_RATE = 48_000

#: Samples settled per engine advance. THE unit of audio work: every Operator
#: writes exactly this many samples, every Block is this long, and the clock
#: moves by this much or not at all.
#:
#: **400 is the knee of a cost curve, and nothing else.** Two things follow,
#: and both are easy to get wrong:
#:
#: - It is also exactly half a 60Hz step (48000/60 == 800), and that is a
#:   COINCIDENCE with no authority in it. Convenient, but not why the number
#:   is 400, and it must not be defended as though it were. **If a knee and a
#:   frame relationship ever disagree, the knee wins.**
#: - The knee is a fact about one CPU's cache and context-switch costs rather
#:   than about audio, so another machine gets the sweep RE-RUN, not this
#:   number copied.
#:
#: Deliberately independent of the host's period: the output stream opens with
#: `blocksize=0`, so one blocking write carries one whole block without
#: forcing the device to adopt this size.
#:
#: The sweep, the figures and the method: `bps/reference/audio/audio.md`.
BLOCK_SIZE = 400

#: `[0, 1, 2, ... BLOCK_SIZE-1]`, for oscillators turning a per-sample step
#: into one block of phase. Read-only and shared: every oscillator on every
#: voice wants the same ramp, and handing out one frozen array means they read
#: it instead of each building their own a hundred and twenty times a second.
_SAMPLE_OFFSETS = arange(BLOCK_SIZE, dtype=float32)
_SAMPLE_OFFSETS.setflags(write=False)
