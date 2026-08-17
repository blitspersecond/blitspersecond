import math
from functools import lru_cache
from numbers import Integral
from types import MappingProxyType
from typing import cast

from numba import njit
from numpy import ceil, float32, random, zeros

from ..common import Bus, FRAME_SIZE, RegisterSpec, RegisterWrite, SAMPLE_RATE
from .delay import MAX_DELAY_TICKS
from .operator import Operator
from .sine import C_SHARP_5

_LOOP_FLOOR = 12.0
_LOOP_RANGE = 60.0
_MAX_FEEDBACK = 0.9999
_MAX_COMB_GAIN = 4.0
_MIN_DECAY = 1e-6
_SILENCE_FLOOR = 1e-4
_QUIET_FRAMES = 3


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
    capacity = len(buffer)
    peak = 0.0
    for index in range(start, stop):
        read = position - delay
        if read < 0.0:
            read += capacity
        lower = int(read)
        fraction = read - lower
        upper = lower + 1
        if upper == capacity:
            upper = 0
        delayed = (
            buffer[lower] * (1.0 - fraction)
            + buffer[upper] * fraction
        )
        peak = max(peak, abs(delayed))
        filtered += smoothing * (delayed - filtered)
        buffer[position] = feedback * filtered
        position += 1
        if position == capacity:
            position = 0

        if comb_gain == 0.0:
            output[index] = delayed
            continue
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
    """Compile the stateful loop before an AudioEngine can be started."""
    scratch = zeros(4, dtype=float32)
    _pluck_segment(
        scratch,
        0,
        0,
        scratch.copy(),
        scratch.copy(),
        0,
        0,
        0.0,
        2.0,
        0.5,
        0.5,
        0.0,
        0.0,
    )


@lru_cache(maxsize=256)
def _comb_gain(place: float, harmonics: int = 16) -> float:
    energy = 0.0
    for harmonic in range(1, harmonics + 1):
        response = 2.0 * math.sin(harmonic * math.pi * place)
        energy += response * response
    rms = math.sqrt(energy / harmonics)
    return 1.0 / max(rms, 1e-6)


class Pluck(Operator):
    """A provisional deterministic gate-triggered Karplus-Strong source.

    TODO: Reassess for deprecation once excitation and resonator operators
    have a composable contract; this currently bundles both policies.
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
        frequency = float(frequency)
        string_decay = float(string_decay)
        damping = float(damping)
        position = float(position)
        min_frequency = float(min_frequency)
        if not math.isfinite(min_frequency) or min_frequency <= 0.0:
            raise ValueError("pluck min_frequency must be finite and positive")
        if SAMPLE_RATE / min_frequency > MAX_DELAY_TICKS:
            minimum = SAMPLE_RATE / MAX_DELAY_TICKS
            raise ValueError(f"pluck min_frequency must be at least {minimum}")
        if not isinstance(seed, Integral) or isinstance(seed, bool):
            raise TypeError("pluck seed must be an integer")
        if seed < 0:
            raise ValueError("pluck seed cannot be negative")

        self.registers = MappingProxyType(
            {
                "frequency": RegisterSpec(
                    frequency,
                    minimum=min_frequency,
                    maximum=SAMPLE_RATE / 2,
                    unit="Hz",
                ),
                "string_decay": RegisterSpec(
                    string_decay, minimum=_MIN_DECAY, unit="seconds"
                ),
                "damping": RegisterSpec(
                    damping, minimum=0.0, maximum=1.0, unit="ratio"
                ),
                "position": RegisterSpec(
                    position, minimum=0.0, maximum=0.5, unit="ratio"
                ),
                "gate": RegisterSpec(False),
            }
        )

        capacity = int(ceil(SAMPLE_RATE / min_frequency)) + 2
        self._buffer = zeros(capacity, dtype=float32)
        self._comb = zeros(capacity, dtype=float32)
        self._rng = random.default_rng(int(seed))
        self._position = 0
        self._comb_at = 0
        self._filtered = 0.0
        self._gate = False
        self._excited = False
        self._frame_peak = 0.0
        self._quiet_frames = 0
        self._clock = 0
        self._registers = MappingProxyType({})
        self._writes = ()

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        self._clock = clock
        self._registers = registers
        self._writes = writes

    def process(self, bus: Bus) -> Bus:
        registers = dict(self._registers) if self._writes else self._registers
        self._active_this_frame = self._excited
        self._synchronize(registers["gate"])
        self._active_this_frame |= self._excited
        output = bus.current.data
        self._frame_peak = 0.0
        start = 0
        for write in self._writes:
            stop = write.timestamp - self._clock
            self._generate(output, start, stop, registers)
            for name, value in write.values:
                cast(dict, registers)[name] = value
            self._synchronize(registers["gate"])
            self._active_this_frame |= self._excited
            start = stop
        self._generate(output, start, FRAME_SIZE, registers)
        if self._excited:
            if self._frame_peak < _SILENCE_FLOOR:
                self._quiet_frames += 1
                if self._quiet_frames >= _QUIET_FRAMES:
                    self._excited = False
            else:
                self._quiet_frames = 0
        return bus

    def _output_quiescent(self, output: Bus, input_quiescent: bool) -> bool:
        return not self._active_this_frame and not self._excited

    def _synchronize(self, gate: bool):
        if gate == self._gate:
            return
        self._gate = gate
        if not gate:
            return

        self._rng.random(dtype=float32, out=self._buffer)
        self._buffer *= 2.0
        self._buffer -= 1.0
        self._comb.fill(0.0)
        self._position = 0
        self._comb_at = 0
        self._filtered = 0.0
        self._excited = True
        self._quiet_frames = 0

    def _generate(self, output, start: int, stop: int, registers):
        if start == stop:
            return
        if not self._excited:
            output[start:stop].fill(0.0)
            return

        frequency = registers["frequency"]
        damping = min(registers["damping"], 1.0 - 1e-9)
        harmonics = _LOOP_FLOOR + (1.0 - damping) ** 2 * _LOOP_RANGE
        cutoff = min(
            frequency * 2.0 ** (harmonics / 12.0),
            0.45 * SAMPLE_RATE,
        )
        smoothing = 1.0 - math.exp(-2.0 * math.pi * cutoff / SAMPLE_RATE)

        pole = 1.0 - smoothing
        omega = 2.0 * math.pi * frequency / SAMPLE_RATE
        real = 1.0 - pole * math.cos(omega)
        imaginary = pole * math.sin(omega)
        filter_delay = math.atan2(imaginary, real) / omega
        capacity = len(self._buffer)
        delay = min(
            max(SAMPLE_RATE / frequency - filter_delay, 2.0),
            capacity - 2.0,
        )

        response = smoothing / math.hypot(real, imaginary)
        target = 0.001 ** (
            delay / (registers["string_decay"] * SAMPLE_RATE)
        )
        feedback = min(target / max(response, 1e-9), _MAX_FEEDBACK)

        place = registers["position"]
        if place == 0.0:
            comb_delay = comb_gain = 0.0
        else:
            comb_delay = min(
                max(place * SAMPLE_RATE / frequency, 1.0),
                capacity - 2.0,
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
        self._frame_peak = max(self._frame_peak, peak)
