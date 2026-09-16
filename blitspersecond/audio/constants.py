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
#: **400 is the knee of the cost curve, and that is the whole reason for it.**
#: Block size trades CPU against responsiveness: fixed per-block overhead is
#: amortised over a longer block while the working set stays resident, so cost
#: falls as the block grows and every wait gets longer. Swept offline on a
#: Ryzen 7 5700X3D with 32 voices across 128 stages, the curve is flat above
#: 400 and steep below it -- 800 saves 5 points of CPU and doubles every wait,
#: while 200 costs 10 points more to save four milliseconds. 400 is where the
#: marginal CPU per millisecond of latency turns over. The numbers, and the
#: method: `docs/bps/synth-findings.md`.
#:
#: It is also exactly half a 60Hz simulation step (48000/60 == 800), and that
#: is a coincidence with no authority in it. It is convenient -- one tick is
#: two blocks with no remainder, which is what would let a game-facing "in N
#: frames" become samples without a rounding rule -- but it is not why the
#: number is 400, and it must not be defended as though it were. **If a knee
#: and a frame relationship ever disagree, the knee wins.** The knee is a fact
#: about one CPU's cache and context-switch costs rather than about audio, so
#: another machine gets the sweep re-run, not this number copied.
#:
#: Deliberately independent of the host's period. The output stream is opened
#: with `blocksize=0`, leaving that to whichever of PipeWire, ALSA, WASAPI,
#: USB or Bluetooth is on the other end; one blocking write carries one whole
#: block without forcing the device to adopt this size.
BLOCK_SIZE = 400

#: `[0, 1, 2, ... BLOCK_SIZE-1]`, for oscillators turning a per-sample step
#: into one block of phase. Read-only and shared: every oscillator on every
#: voice wants the same ramp, and handing out one frozen array means they read
#: it instead of each building their own a hundred and twenty times a second.
_SAMPLE_OFFSETS = arange(BLOCK_SIZE, dtype=float32)
_SAMPLE_OFFSETS.setflags(write=False)
