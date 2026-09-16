"""`Pluck`: Karplus-Strong, a string rather than a struck body."""

import math
from collections.abc import Mapping
from functools import lru_cache
from numbers import Integral
from types import MappingProxyType

from numba import njit
from numpy import ceil, float32, random, zeros

from ..bus import Bus
from ...constants import BLOCK_SIZE, SAMPLE_RATE
from ..registers import RegisterSpec, RegisterWrite
from ._oscillator import C_SHARP_5
from .delay import MAX_DELAY_TICKS
from .operator import _QUIET_BLOCKS, _SILENCE_FLOOR, Operator

#: Where the loop filter's cutoff sits above the fundamental, in semitones,
#: at full damping and at none. One octave up leaves a dull thud; six octaves
#: up is a bright steel string. The square in between spends most of the
#: register's travel in the range where the change is audible, rather than
#: crowding it all into the last tenth.
_LOOP_FILTER_FLOOR = 12.0
_LOOP_FILTER_RANGE = 60.0

#: A loop gain of exactly one never decays. This is as close as the feedback
#: is allowed to get, so even a string_decay of a century still ends.
_MAX_FEEDBACK = 0.9999

#: Plucking very near the end notches out almost every harmonic, and the
#: normalisation that compensates would otherwise ask for unbounded gain.
_MAX_COMB_GAIN = 4.0


@njit(cache=True)
def _pluck_segment(
    output,
    start,
    stop,
    buffer,
    comb,
    position,
    comb_at,
    filtered,
    delay,
    smoothing,
    feedback,
    comb_delay,
    comb_gain,
):
    """One segment of the string, sample by sample.

    A kernel because it genuinely cannot be vectorised: every sample is read
    out of a buffer that the previous sample wrote, through a filter whose
    state the previous sample set. The recurrence IS the instrument -- it is
    what makes the upper partials die before the lower ones -- so there is no
    block form of it to find.
    """
    capacity = len(buffer)
    peak = 0.0
    for index in range(start, stop):
        # Read the string one period back, interpolating between the two
        # samples either side: a period is almost never a whole number.
        read = position - delay
        if read < 0.0:
            read += capacity
        lower = int(read)
        fraction = read - lower
        upper = lower + 1
        if upper == capacity:
            upper = 0
        delayed = buffer[lower] * (1.0 - fraction) + buffer[upper] * fraction
        peak = max(peak, abs(delayed))

        # One pole of lowpass inside the loop, which is the whole trick: each
        # trip round the string loses more of the top than the bottom.
        filtered += smoothing * (delayed - filtered)
        buffer[position] = feedback * filtered
        position += 1
        if position == capacity:
            position = 0

        if comb_gain == 0.0:
            output[index] = delayed
            continue

        # Plucking away from the end cancels the harmonics with a node there,
        # which is a comb: the string against itself, delayed by the distance
        # to the pluck.
        comb_read = comb_at - comb_delay
        if comb_read < 0.0:
            comb_read += capacity
        lower = int(comb_read)
        fraction = comb_read - lower
        upper = lower + 1
        if upper == capacity:
            upper = 0
        echo = comb[lower] * (1.0 - fraction) + comb[upper] * fraction
        comb[comb_at] = delayed
        comb_at += 1
        if comb_at == capacity:
            comb_at = 0
        output[index] = (delayed - echo) * comb_gain
    return position, comb_at, filtered, peak


@lru_cache(maxsize=1)
def _warm_pluck_kernel():
    """Compile the loop before it is ever needed, once per process.

    Called from `__init__`, so the cost lands while a game is building its
    graph rather than on the first note, where it would be a gap in the
    audio. Cold that is most of a second; from numba's on-disk cache it is
    about a sixth of one.
    """
    scratch = zeros(4, dtype=float32)
    _pluck_segment(
        scratch, 0, 0, scratch.copy(), scratch.copy(),
        0, 0, 0.0, 2.0, 0.5, 0.5, 0.0, 0.0,
    )


@lru_cache(maxsize=256)
def _comb_gain(place: float, harmonics: int = 16) -> float:
    """What to multiply by so moving the pluck does not change the loudness.

    The comb removes energy, and how much depends on where it is. Summing the
    first sixteen harmonics' responses gives the loss, and its reciprocal
    puts the level back, so `position` is a timbre control and not a second
    volume control.
    """
    energy = 0.0
    for harmonic in range(1, harmonics + 1):
        response = 2.0 * math.sin(harmonic * math.pi * place)
        energy += response * response
    rms = math.sqrt(energy / harmonics)
    return 1.0 / max(rms, 1e-6)


class Pluck(Operator):
    """A plucked string: noise in a filtered loop one period long.

    Karplus-Strong. A gate edge fills a buffer a wavelength long with noise
    and then reads it round and round, lowpassing a little on each trip. The
    noise is every frequency at once; the loop keeps only the ones that fit a
    whole number of times into its length, which is the harmonic series of
    the note; and the filter takes the top off a bit more each lap, so the
    bright attack decays into a soft tone the way a real string does.

    **This is not `Pulse` into `Resonator`, and the difference is the point.**
    A resonator is one resonant mode, so striking it gives a body with a
    single pitch and a single decay -- a drum, a bell, a block. A string is a
    whole series of partials whose upper members die first, and that comes
    from the filter being *inside* the feedback path. Nothing else in the
    vocabulary can put it there: `Echo` owns its feedback and will not take a
    filter into it, and a Stage feedback boundary resolves one block at a
    time, which is 400 samples where a 440 Hz string needs 109.

    **`position` is where the string is plucked**, from 0 (at the bridge, all
    harmonics) to 0.5 (the middle, which cancels every even one). Plucking at
    a node silences that harmonic exactly as it does on a guitar, and picking
    near the bridge is bright for the same reason it is on a real one.

    **`damping` is how dull the string is**, and `string_decay` is how long
    the FUNDAMENTAL rings for, in seconds to -60 dB -- the partials above it
    decay faster by design, which is the whole point of the loop filter. The
    two registers are separate because a real string has both: a wound bass
    string is dull AND long, a muted one is bright and short.

    **`string_decay` has a ceiling it does not declare.** Loop gain is capped
    just under unity, so past a certain point asking for longer changes
    nothing: at 220 Hz with damping 0.5 the ring saturates at about 1.4
    seconds, and 2.0 and 4.0 sound identical. Where that ceiling falls
    depends on both frequency and damping -- a fully damped string cannot be
    made to ring past about a quarter second at any setting -- which is why
    it is not written into the register's bounds. Pinned by
    `test_a_string_cannot_be_asked_to_ring_for_ever`.

    Deterministic. The excitation noise comes from a seeded generator, so the
    same note plucked twice is the same note, and an offline render can be
    diffed against the last one.
    """

    def __init__(
        self,
        frequency: float = C_SHARP_5,
        string_decay: float = 1.0,
        damping: float = 0.5,
        position: float = 0.0,
        min_frequency: float = 20.0,
        seed: int = 0,
    ):
        _warm_pluck_kernel()
        min_frequency = float(min_frequency)
        if not math.isfinite(min_frequency) or min_frequency <= 0.0:
            raise ValueError("pluck min_frequency must be finite and positive")
        if SAMPLE_RATE / min_frequency > MAX_DELAY_TICKS:
            raise ValueError(
                f"pluck min_frequency must be at least "
                f"{SAMPLE_RATE / MAX_DELAY_TICKS}"
            )
        if not isinstance(seed, Integral) or isinstance(seed, bool):
            raise TypeError("pluck seed must be an integer")
        if seed < 0:
            raise ValueError("pluck seed cannot be negative")

        self.registers = MappingProxyType(
            {
                # `min_frequency` sizes the buffer, so it is a construction
                # argument and not a register: the lowest note a string can
                # play is a property of the string, and lowering it later
                # would mean reallocating mid-note.
                "frequency": RegisterSpec(
                    float(frequency),
                    minimum=min_frequency,
                    maximum=SAMPLE_RATE / 2,
                    unit="Hz",
                ),
                "string_decay": RegisterSpec(
                    float(string_decay), minimum=1e-6, unit="seconds"
                ),
                "damping": RegisterSpec(
                    float(damping), minimum=0.0, maximum=1.0, unit="ratio"
                ),
                "position": RegisterSpec(
                    float(position), minimum=0.0, maximum=0.5, unit="ratio"
                ),
                "gate": RegisterSpec(False),
            }
        )

        # Two samples of headroom past the longest period, because the read
        # head interpolates between a sample and the one after it.
        capacity = int(ceil(SAMPLE_RATE / min_frequency)) + 2
        self._buffer = zeros(capacity, dtype=float32)
        self._comb = zeros(capacity, dtype=float32)
        self._rng = random.default_rng(int(seed))
        self._position = 0
        self._comb_at = 0
        self._filtered = 0.0
        self._gate = False
        self._ringing = False
        self._block_peak = 0.0
        self._quiet_blocks = 0
        self._sounded_this_block = False
        self._clock = 0
        self._registers: Mapping[str, object] = MappingProxyType({})
        self._writes: tuple[RegisterWrite, ...] = ()

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        self._clock = clock
        self._registers = registers
        self._writes = writes

    def process(self, bus: Bus) -> Bus:
        # Copied only when there are writes to fold into it; with none, the
        # settled view is already the answer for the whole block.
        registers = dict(self._registers) if self._writes else self._registers
        self._sounded_this_block = self._ringing
        self._strike(bool(registers["gate"]))
        self._sounded_this_block |= self._ringing

        output = bus.current.data
        self._block_peak = 0.0
        start = 0
        for write in self._writes:
            stop = write.timestamp - self._clock
            self._generate(output, start, stop, registers)
            for name, value in write.values:
                registers[name] = value  # type: ignore[index]
            self._strike(bool(registers["gate"]))
            self._sounded_this_block |= self._ringing
            start = stop
        self._generate(output, start, BLOCK_SIZE, registers)

        # Retired on the loop's own peak rather than on the output's, because
        # `position` can notch the output to nothing while the string is
        # still very much ringing behind the comb.
        if self._ringing:
            if self._block_peak < _SILENCE_FLOOR:
                self._quiet_blocks += 1
                if self._quiet_blocks >= _QUIET_BLOCKS:
                    self._ringing = False
            else:
                self._quiet_blocks = 0
        return bus

    def _output_quiescent(self, output: Bus, input_quiescent: bool) -> bool:
        return not self._sounded_this_block and not self._ringing

    def _strike(self, gate: bool):
        """A rising edge refills the string with noise; nothing else does."""
        if gate == self._gate:
            return
        self._gate = gate
        if not gate:
            # Releasing does NOT damp it. A string let go carries on sounding,
            # and stopping one is a separate physical act -- an Envelope, or
            # a `string_decay` write, not the gate falling.
            return

        self._rng.random(dtype=float32, out=self._buffer)
        self._buffer *= 2.0
        self._buffer -= 1.0
        self._comb.fill(0.0)
        self._position = 0
        self._comb_at = 0
        self._filtered = 0.0
        self._ringing = True
        self._quiet_blocks = 0

    def _generate(self, output, start: int, stop: int, registers):
        if start == stop:
            return
        if not self._ringing:
            output[start:stop].fill(0.0)
            return

        frequency = float(registers["frequency"])
        # Held just under one, because a damping of exactly 1.0 would put the
        # loop filter's cutoff at the fundamental and square the zero below.
        damping = min(float(registers["damping"]), 1.0 - 1e-9)
        semitones = _LOOP_FILTER_FLOOR + (1.0 - damping) ** 2 * _LOOP_FILTER_RANGE
        cutoff = min(frequency * 2.0 ** (semitones / 12.0), 0.45 * SAMPLE_RATE)
        smoothing = 1.0 - math.exp(-2.0 * math.pi * cutoff / SAMPLE_RATE)

        # The loop filter is itself a delay, and a fraction of a sample of it
        # is enough to put the string out of tune. Measure the filter's own
        # group delay at the fundamental and take it off the buffer length.
        pole = 1.0 - smoothing
        omega = 2.0 * math.pi * frequency / SAMPLE_RATE
        real = 1.0 - pole * math.cos(omega)
        imaginary = pole * math.sin(omega)
        filter_delay = math.atan2(imaginary, real) / omega
        capacity = len(self._buffer)
        delay = min(max(SAMPLE_RATE / frequency - filter_delay, 2.0), capacity - 2.0)

        # `string_decay` is seconds to -60 dB, so the per-lap loss is set from
        # how many laps fit in that time -- and divided by what the loop
        # filter already takes, so the two registers stay independent.
        response = smoothing / math.hypot(real, imaginary)
        target = 0.001 ** (delay / (float(registers["string_decay"]) * SAMPLE_RATE))
        feedback = min(target / max(response, 1e-9), _MAX_FEEDBACK)

        place = float(registers["position"])
        if place == 0.0:
            comb_delay = comb_gain = 0.0
        else:
            comb_delay = min(
                max(place * SAMPLE_RATE / frequency, 1.0), capacity - 2.0
            )
            comb_gain = min(_comb_gain(place), _MAX_COMB_GAIN)

        position, comb_at, filtered, peak = _pluck_segment(
            output,
            start,
            stop,
            self._buffer,
            self._comb,
            self._position,
            self._comb_at,
            self._filtered,
            delay,
            smoothing,
            feedback,
            comb_delay,
            comb_gain,
        )
        self._position = position
        self._comb_at = comb_at
        self._filtered = filtered
        self._block_peak = max(self._block_peak, peak)
