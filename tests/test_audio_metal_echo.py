"""`Metal`, `Delay`, `Echo`, and the eight instrument presets.

**`Metal`'s asymmetry is the subject, not its waveform.** Its two levels are
`1 - duty` and `-duty` so the mean is exactly zero at any duty cycle; a naive
square is only DC-free at 50%, and six of them stacked would thump on every
strike. That property is tested across the whole duty range.

**The presets are tested as instruments, not as Programs.** Asserting which
operators a factory assembled would just restate the source. What is worth
checking is that each one makes a sound when struck, stops afterwards, and is
distinguishable from its siblings -- which is what "eight presets" claims.
"""

import numpy as np
import pytest

from blitspersecond.audio import AudioEngine
from blitspersecond.audio.transport import render
from blitspersecond.audio.synth.block import BlockPool
from blitspersecond.audio.synth.bus import Bus
from blitspersecond.audio.constants import BLOCK_SIZE, SAMPLE_RATE
from blitspersecond.audio.synth.instruments import (
    METAL_BANK, clap, cowbell, cymbal, drum, hat, kick, metal_drum,
    noise_drum, shaker, snare,
)
from blitspersecond.audio.synth.operators import Delay, Echo, Metal
from blitspersecond.audio.synth.registers import RegisterWrite

PRESETS = (kick, snare, clap, shaker, cowbell, cymbal, hat, drum,
           noise_drum, metal_drum)


def _run(operator, blocks=1, carrier=None, writes=(), **overrides):
    bus = Bus(BlockPool())
    registers = {n: s.default for n, s in operator.registers.items()}
    registers.update(overrides)
    out = []
    for index in range(blocks):
        if carrier is not None:
            bus.current.data[:] = carrier
        operator.control(index * BLOCK_SIZE, dict(registers), tuple(writes))
        for write in writes:
            for name, value in write.values:
                registers[name] = value
        writes = ()
        operator.process(bus)
        out.append(np.array(bus.current.data))
    return np.concatenate(out)


# -- Metal ---------------------------------------------------------------


@pytest.mark.parametrize("duty", [0.1, 0.3, 0.48, 0.5, 0.7, 0.9])
def test_metal_carries_no_dc_at_any_duty(duty):
    """The asymmetric rectangle, measured against the naive alternative.

    A plain +/-1 square at duty d sits at a mean of `2d - 1` -- at duty 0.1
    that is -0.8, and six of them stacked is an audible thump on every
    strike. The levels here are `1 - duty` and `-duty`, whose mean is zero by
    construction whatever the duty.

    Compared against that naive figure rather than against zero, because a
    finite measurement never contains a whole number of cycles of six
    inharmonic partials: the residual is about 0.003 at EVERY duty, 0.5
    included, which is the window and not the waveform.
    """
    signal = _run(Metal(METAL_BANK, duty=duty), blocks=40)
    residual = abs(float(np.mean(signal)))
    naive = abs(2.0 * duty - 1.0)

    assert residual < 0.01
    # The ratio only says anything where the naive offset is well clear of
    # that ~0.003 floor. Near 50% duty a plain square is almost DC-free too,
    # so there is nothing there to be twenty times better than.
    if naive > 0.1:
        assert residual < naive / 20


def test_a_bank_is_normalised_by_its_size():
    """Two partials and six arrive at comparable levels."""
    one = float(np.max(np.abs(_run(Metal((400.0,)), blocks=20))))
    six = float(np.max(np.abs(_run(Metal(METAL_BANK), blocks=20))))
    # A single partial peaks at 2 * (1 - duty) = 1.04 at the default duty.
    assert one == pytest.approx(1.04, abs=0.01)
    assert six < one * 2.0


def test_a_bank_has_no_common_fundamental():
    """Why it reads as metal rather than as a chord."""
    signal = _run(Metal(METAL_BANK), blocks=40)
    spectrum = np.abs(np.fft.rfft(signal * np.hanning(len(signal))))
    resolution = SAMPLE_RATE / len(signal)
    for partial in METAL_BANK:
        assert spectrum[int(round(partial / resolution))] > np.mean(spectrum) * 5


def test_tune_cannot_be_pushed_into_aliasing():
    spec = Metal(METAL_BANK).registers["tune"]
    # `RegisterSpec` bounds are typed `object` so a register can hold a
    # structured value; narrowed here rather than cast.
    ceiling = spec.maximum
    assert isinstance(ceiling, float)
    assert ceiling == pytest.approx((SAMPLE_RATE / 2) / max(METAL_BANK))
    assert max(METAL_BANK) * ceiling <= SAMPLE_RATE / 2


def test_a_bank_must_be_non_empty_and_positive():
    with pytest.raises(ValueError):
        Metal(())
    with pytest.raises(ValueError):
        Metal((0.0,))
    with pytest.raises(ValueError):
        Metal((float("inf"),))


def test_a_bank_cannot_be_retuned_after_construction():
    metal = Metal(METAL_BANK)
    with pytest.raises(ValueError):
        metal.frequencies[0] = 1.0


def test_each_partial_keeps_its_own_phase():
    metal = Metal(METAL_BANK)
    _run(metal, blocks=3)
    assert len(set(np.round(metal.phase, 6))) > 1


# -- Delay ----------------------------------------------------------------


def _delayed(delay, carriers, **overrides):
    """Feed successive blocks through ONE bus.

    A delay ring belongs to the Bus, not to the operator, so a fresh Bus per
    block would start a fresh ring every time and the delay would never
    reach back at all.
    """
    bus = Bus(BlockPool())
    registers = {n: s.default for n, s in delay.registers.items()}
    registers.update(overrides)
    out = []
    for index, carrier in enumerate(carriers):
        bus.current.data[:] = carrier
        delay.control(index * BLOCK_SIZE, dict(registers), ())
        delay.process(bus)
        out.append(np.array(bus.current.data))
    return out


def test_a_delay_moves_the_lane_back():
    blocks = _delayed(
        Delay(BLOCK_SIZE),
        [
            np.full(BLOCK_SIZE, 1.0, dtype=np.float32),
            np.full(BLOCK_SIZE, 2.0, dtype=np.float32),
            np.full(BLOCK_SIZE, 3.0, dtype=np.float32),
        ],
    )
    assert np.all(blocks[0] == 0.0)   # nothing has been recorded yet
    assert np.all(blocks[1] == 1.0)   # one block ago
    assert np.all(blocks[2] == 2.0)


def test_a_zero_delay_is_a_wire():
    signal = _run(
        Delay(0), carrier=np.full(BLOCK_SIZE, 1.0, dtype=np.float32)
    )
    assert np.all(signal == 1.0)


def test_a_delay_write_applies_from_the_next_block():
    """Moving the read head mid-block has no coherent meaning.

    The samples before the write came from one distance and the ones after
    would come from another, with a step between them. So an intra-block
    write settles and applies from the next block instead.
    """
    delay = Delay(BLOCK_SIZE)
    bus = Bus(BlockPool())
    carrier = np.full(BLOCK_SIZE, 1.0, dtype=np.float32)

    bus.current.data[:] = carrier
    delay.control(0, {"delay_ticks": BLOCK_SIZE}, ())
    delay.process(bus)

    bus.current.data[:] = np.full(BLOCK_SIZE, 2.0, dtype=np.float32)
    delay.control(
        BLOCK_SIZE,
        {"delay_ticks": BLOCK_SIZE},
        (RegisterWrite(BLOCK_SIZE + 200, None, (("delay_ticks", 0),)),),
    )
    delay.process(bus)

    # Still the full block's delay: the mid-block write did not take hold.
    assert np.all(bus.current.data == 1.0)


def test_the_delay_ceiling_is_four_seconds():
    spec = Delay().registers["delay_ticks"]
    assert spec.maximum == 4 * SAMPLE_RATE


def test_a_delay_must_be_whole_ticks():
    with pytest.raises(TypeError):
        Delay(1.5)  # type: ignore[arg-type]


# -- Echo ------------------------------------------------------------------


def test_an_echo_repeats_what_it_was_given():
    strike = np.zeros(BLOCK_SIZE, dtype=np.float32)
    strike[0] = 1.0
    echo = Echo(200, feedback=0.5, mix=1.0)
    bus = Bus(BlockPool())
    bus.current.data[:] = strike
    echo.control(0, {"delay_ticks": 200, "feedback": 0.5, "mix": 1.0}, ())
    echo.process(bus)
    out = np.array(bus.current.data)

    assert out[0] == pytest.approx(1.0)
    assert out[200] == pytest.approx(1.0, abs=1e-5)


def test_feedback_makes_each_repeat_quieter():
    strike = np.zeros(BLOCK_SIZE, dtype=np.float32)
    strike[0] = 1.0
    echo = Echo(100, feedback=0.5, mix=1.0)
    bus = Bus(BlockPool())
    bus.current.data[:] = strike
    echo.control(0, {"delay_ticks": 100, "feedback": 0.5, "mix": 1.0}, ())
    echo.process(bus)
    out = np.array(bus.current.data)

    assert out[100] > out[200] > out[300] > 0.0


def test_the_buffer_is_sized_once_at_construction():
    """So automating the delay can never allocate on the audio thread."""
    echo = Echo(100, max_delay_ticks=4_800)
    before = echo._buffer
    _run(echo, blocks=4, carrier=np.ones(BLOCK_SIZE, dtype=np.float32))
    assert echo._buffer is before
    assert echo.registers["delay_ticks"].maximum == 4_800


def test_feedback_stops_short_of_one():
    """At 1.0 a repeat never decays and the buffer fills without bound."""
    spec = Echo(100).registers["feedback"]
    assert spec.maximum == 0.999 and spec.minimum == -0.999


def test_an_echo_settles_once_its_repeats_have_gone():
    echo = Echo(200, feedback=0.3, mix=1.0)
    bus = Bus(BlockPool())
    bus.current.data.fill(1.0)
    echo.control(0, {"delay_ticks": 200, "feedback": 0.3, "mix": 1.0}, ())
    echo.process(bus)

    settled = False
    for index in range(40):
        bus.current.data.fill(0.0)
        echo.control(
            (index + 1) * BLOCK_SIZE,
            {"delay_ticks": 200, "feedback": 0.3, "mix": 1.0},
            (),
        )
        echo.process(bus)
        settled = echo._output_quiescent(bus, True)
    assert settled is True


def test_an_echo_delay_must_be_whole_ticks_and_in_range():
    with pytest.raises(TypeError):
        Echo(1.5)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Echo(100, max_delay_ticks=0)
    with pytest.raises(ValueError):
        Echo(100, max_delay_ticks=5 * SAMPLE_RATE)


# -- the presets ------------------------------------------------------------


def _strike(factory, seconds=1.0, **kwargs):
    engine = AudioEngine()
    voice = factory(engine, **kwargs)
    engine.output = voice
    engine.schedule([(0, voice, {"gate": False}), (0, voice, {"gate": True})])
    return render(engine, seconds)


@pytest.mark.parametrize("factory", PRESETS)
def test_every_preset_makes_a_sound_when_struck(factory):
    signal = _strike(factory)
    assert np.max(np.abs(signal)) > 0.01
    assert np.all(np.isfinite(signal))


@pytest.mark.parametrize("factory", PRESETS)
def test_every_preset_stops_by_itself(factory):
    """Percussion has no note-off; the envelope ends it."""
    signal = _strike(factory, seconds=2.0)
    tail = signal[-BLOCK_SIZE * 4 :]
    assert np.max(np.abs(tail)) < np.max(np.abs(signal)) * 0.05


@pytest.mark.parametrize("factory", PRESETS)
def test_a_preset_is_a_stage_so_two_are_independent(factory):
    """The engine owns no allocator: calling it twice gives two voices."""
    engine = AudioEngine()
    assert factory(engine) is not factory(engine)


def test_the_kit_members_sound_different_from_each_other():
    """Eight presets, not one preset eight times."""
    centroids = {}
    for factory in (kick, snare, clap, shaker, cowbell, cymbal):
        signal = _strike(factory, seconds=0.5)
        spectrum = np.abs(np.fft.rfft(signal))
        freqs = np.fft.rfftfreq(len(signal), 1 / SAMPLE_RATE)
        centroids[factory.__name__] = float(
            np.sum(spectrum * freqs) / np.sum(spectrum)
        )
    assert centroids["kick"] < centroids["snare"] < centroids["shaker"]
    assert len(set(round(c, -1) for c in centroids.values())) == 6


def test_openness_lengthens_a_hat():
    closed = _strike(hat, seconds=1.0, openness=0.0)
    open_hat = _strike(hat, seconds=1.0, openness=1.0)

    def length(signal):
        loud = np.flatnonzero(np.abs(signal) > np.max(np.abs(signal)) * 0.05)
        return int(loud[-1]) if len(loud) else 0

    assert length(open_hat) > length(closed) * 5


def test_openness_is_bounded():
    engine = AudioEngine()
    with pytest.raises(ValueError):
        hat(engine, -0.1)
    with pytest.raises(ValueError):
        hat(engine, 1.1)


def test_a_cowbell_has_a_pitch_where_a_cymbal_does_not():
    """Two partials read as an interval; six read as noise."""

    def peakiness(signal):
        spectrum = np.abs(np.fft.rfft(signal * np.hanning(len(signal))))
        return float(np.max(spectrum) / np.mean(spectrum))

    assert peakiness(_strike(cowbell, 0.5)) > peakiness(_strike(cymbal, 0.5))


def test_the_bank_is_coprime_so_its_period_is_a_whole_second():
    """Why the frequencies are primes, and what it buys.

    A bank of whole-number frequencies repeats every `1 / gcd` seconds. The
    TR-808's measured oscillators moved to nearby primes are pairwise
    coprime, so the gcd is 1 and the composite period is one second -- the
    longest whole numbers permit. Every preset here rings for less than that,
    so no strike contains a repeat and the bank reads as noise.
    """
    from functools import reduce
    from math import gcd

    whole = [int(f) for f in METAL_BANK]
    assert whole == list(METAL_BANK)
    assert reduce(gcd, whole) == 1
    for frequency in whole:
        assert all(frequency % d for d in range(2, int(frequency**0.5) + 1))

    metal = Metal(METAL_BANK)
    _run(metal, blocks=SAMPLE_RATE // BLOCK_SIZE)
    for phase in metal.phase:
        assert min(phase, 1.0 - phase) < 1e-9


def test_a_bank_sharing_a_factor_repeats_far_sooner():
    """The failure the primes avoid: gcd 100 means a buzz at 100 Hz."""
    signal = _run(Metal((200.0, 300.0, 400.0)), blocks=30)
    period = SAMPLE_RATE // 100
    assert np.allclose(signal[:period], signal[period : 2 * period], atol=1e-6)
