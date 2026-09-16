"""`Clip`, `Crush`, `RingMod`: three ways to damage a signal deliberately.

**Neither is graded against a clean signal.** Both exist to introduce error,
so "how close is the output to the input" is the wrong question for all but
one of these tests. What is asserted instead is that the damage has the shape
it claims: that `Clip` is bit-exact below its ceiling and flat on it, and that
`Crush` lands on a real quantisation lattice and holds for a real period.

**`RingMod` is asserted on what LEAVES, not on what arrives.** Multiplying
two tones is easy to check by multiplying two arrays, which would only prove
the multiply. The claim worth pinning is the spectral one -- that neither
input frequency is in the output and both sidebands are -- because that is
what a caller is reaching for it to do.

**`Crush`'s hold is checked against an oracle.** The block form solves for
every hold point at once; the obvious form walks a phase accumulator one
sample at a time. The second is written out here and the two are required to
agree sample-for-sample, including across block boundaries and at rates that
do not divide 48,000 -- which is the only way to be sure the closed form did
not quietly round the period onto the block grid.
"""

import numpy as np
import pytest

from blitspersecond.audio.synth.block import BlockPool
from blitspersecond.audio.synth.bus import Bus
from blitspersecond.audio.constants import BLOCK_SIZE, SAMPLE_RATE
from blitspersecond.audio.synth.operators import Clip, Crush, RingMod
from blitspersecond.audio.synth.registers import RegisterWrite


def _defaults(operator):
    return {name: spec.default for name, spec in operator.registers.items()}


def _through(operator, signal, writes=(), clock=0):
    """Push a long signal through in blocks, as the engine would."""
    bus = Bus(BlockPool())
    registers = _defaults(operator)
    out = []
    for index in range(len(signal) // BLOCK_SIZE):
        bus.current.data[:] = signal[index * BLOCK_SIZE : (index + 1) * BLOCK_SIZE]
        operator.control(clock + index * BLOCK_SIZE, dict(registers), tuple(writes))
        for write in writes:
            for name, value in write.values:
                registers[name] = value
        writes = ()
        operator.process(bus)
        out.append(np.array(bus.current.data))
    return np.concatenate(out)


def _tone(hz, blocks=8):
    n = blocks * BLOCK_SIZE
    return np.sin(2 * np.pi * hz * np.arange(n) / SAMPLE_RATE).astype(np.float32)


# --------------------------------------------------------------------------
# Clip


def test_a_signal_under_the_ceiling_is_untouched_bit_for_bit():
    # The property that separates a hard clip from every soft saturator: it
    # is not an approximation below the corner, it is the identity.
    signal = _tone(441) * 0.4
    assert np.array_equal(_through(Clip(1.0), signal), signal)


def test_clipping_is_symmetric_and_flat_at_the_ceiling():
    out = _through(Clip(0.25), _tone(441))
    assert out.max() == pytest.approx(0.25)
    assert out.min() == pytest.approx(-0.25)
    # Flat, not merely bounded: most of a hard-clipped sine sits exactly on
    # one rail or the other.
    assert np.mean(np.abs(np.abs(out) - 0.25) < 1e-7) > 0.7


def test_a_ceiling_of_zero_is_silence():
    assert not np.any(_through(Clip(0.0), _tone(441)))


def test_clipping_hard_drives_a_sine_toward_a_square():
    # A square's third harmonic is a third of its fundamental. Clip low
    # enough and that is what is left, whatever went in.
    out = _through(Clip(0.02), _tone(441, blocks=120))
    spectrum = np.abs(np.fft.rfft(out * np.hanning(len(out))))
    bins = np.fft.rfftfreq(len(out), 1 / SAMPLE_RATE)
    first = spectrum[np.argmin(np.abs(bins - 441))]
    third = spectrum[np.argmin(np.abs(bins - 1323))]
    assert third / first == pytest.approx(1 / 3, abs=0.02)


def test_a_clip_level_write_lands_on_its_sample():
    at = 137
    out = _through(
        Clip(1.0),
        np.full(BLOCK_SIZE, 0.8, dtype=np.float32),
        writes=(RegisterWrite(at, None, (("clip_level", 0.1),)),),
    )
    assert np.all(out[:at] == pytest.approx(0.8))
    assert np.all(out[at:] == pytest.approx(0.1))


def test_clip_passes_quiescence_through():
    clip = Clip()
    bus = Bus(BlockPool())
    assert clip._output_quiescent(bus, True) is True
    assert clip._output_quiescent(bus, False) is False


# --------------------------------------------------------------------------
# Crush: bit depth


@pytest.mark.parametrize("bits", (1, 2, 4, 8, 12, 16))
def test_quantised_output_lands_on_the_lattice_for_that_depth(bits):
    out = _through(Crush(bits=bits), _tone(441))
    steps = 2 ** (bits - 1)
    assert np.allclose(out * steps, np.rint(out * steps), atol=1e-4)


def test_one_bit_keeps_only_the_sign():
    out = _through(Crush(bits=1), _tone(441) * 0.9)
    assert set(np.unique(out)) <= {-1.0, 0.0, 1.0}


def test_the_error_is_never_worse_than_half_a_step():
    # The whole of the error, stated exactly rather than as a threshold
    # picked to pass: rounding to a lattice can be out by half its spacing
    # and no more, and a signal that crosses enough of the lattice reaches
    # that bound. Each extra bit halves both numbers.
    signal = _tone(441)
    for bits in (4, 8, 12, 16):
        step = 2.0 ** -(bits - 1)
        error = float(np.max(np.abs(_through(Crush(bits=bits), signal) - signal)))
        assert error <= step / 2 + 1e-9
        assert error > step / 4


def test_a_bit_depth_must_be_a_whole_number():
    with pytest.raises(TypeError):
        Crush(bits=8.0)
    with pytest.raises(TypeError):
        Crush(bits=True)


# --------------------------------------------------------------------------
# Crush: sample rate hold


def _held_by_hand(signal, rate, bits=24):
    """The obvious per-sample form, as an oracle for the block form."""
    steps = float(2 ** (bits - 1))
    samples = np.rint(signal.astype(np.float32) * steps) / steps
    out = np.empty_like(samples)
    period = SAMPLE_RATE / rate
    phase, held = 0.0, 0.0
    for index in range(len(samples)):
        if phase <= 0.0:
            held = float(samples[index])
            phase += period
        out[index] = held
        phase -= 1.0
    return out


@pytest.mark.parametrize("rate", (24000.0, 16000.0, 11025.0, 8000.0, 1000.0, 37.0))
def test_the_hold_agrees_with_the_obvious_per_sample_form(rate):
    signal = _tone(441, blocks=12)
    crush = Crush(bits=24, rate=rate)
    assert np.array_equal(_through(crush, signal), _held_by_hand(signal, rate))


def test_the_hold_carries_across_block_boundaries():
    # 11,025 Hz is 4.3537 samples per hold: no hold point lands on a block
    # edge twice running, so a phase that reset per block would drift away
    # from the oracle within a few blocks.
    signal = _tone(441, blocks=12)
    by_hand = _held_by_hand(signal, 11025.0)
    assert np.array_equal(_through(Crush(bits=24, rate=11025.0), signal), by_hand)
    # And the sound is genuinely stepped, not merely equal to the oracle.
    assert len(np.unique(np.diff(by_hand) != 0)) == 2


def test_the_full_sample_rate_holds_nothing():
    signal = _tone(441)
    assert np.array_equal(_through(Crush(bits=24), signal), _held_by_hand(signal, SAMPLE_RATE))


def test_a_rate_of_zero_freezes_the_last_sample_it_latched():
    signal = np.concatenate([_tone(441), _tone(441)])
    crush = Crush(bits=24, rate=12000.0)
    out = _through(
        crush,
        signal,
        writes=(RegisterWrite(BLOCK_SIZE, None, (("rate", 0.0),)),),
    )
    frozen = out[BLOCK_SIZE:]
    assert np.all(frozen == frozen[0])
    assert frozen[0] != 0.0


def test_a_rate_write_lands_on_its_sample():
    at = 200
    out = _through(
        Crush(bits=24, rate=SAMPLE_RATE),
        _tone(441),
        writes=(RegisterWrite(at, None, (("rate", 2000.0),)),),
    )
    # Before the write every sample is its own; after it they come in runs.
    assert len(np.unique(np.diff(out[:at]) != 0)) == 1
    assert np.any(np.diff(out[at : at + 100]) == 0)


def test_crush_settles_once_its_hold_has_emptied():
    crush = Crush(bits=8, rate=4000.0)
    bus = Bus(BlockPool())
    # A lane that stops mid-hold leaves a DC step behind it, and at 4 kHz it
    # would sit there for a good fraction of a second.
    bus.current.data[:] = 0.5
    crush.control(0, _defaults(crush), ())
    crush.process(bus)
    assert crush._output_quiescent(bus, True) is False

    bus.current.data[:] = 0.0
    crush.control(BLOCK_SIZE, _defaults(crush), ())
    crush.process(bus)
    assert crush._output_quiescent(bus, True) is True
    assert crush._output_quiescent(bus, False) is False


# --------------------------------------------------------------------------
# RingMod


def _two_lanes(first, second):
    pool = BlockPool()
    signal, carrier = Bus(pool), Bus(pool)
    signal.current.data[:] = first
    carrier.current.data[:] = second
    return signal, carrier


def test_ringing_two_tones_leaves_neither_of_them():
    # 200 against 300 gives 100 and 500 and nothing else: the sum and the
    # difference, with both originals gone.
    blocks = 120
    n = blocks * BLOCK_SIZE
    signal, carrier = _tone(200, blocks), _tone(300, blocks)
    out = []
    ring = RingMod()
    pool = BlockPool()
    left, right = Bus(pool), Bus(pool)
    for index in range(blocks):
        chunk = slice(index * BLOCK_SIZE, (index + 1) * BLOCK_SIZE)
        left.current.data[:] = signal[chunk]
        right.current.data[:] = carrier[chunk]
        out.append(np.array(ring.process(left, right).current.data))
    spectrum = np.abs(np.fft.rfft(np.concatenate(out) * np.hanning(n)))
    bins = np.fft.rfftfreq(n, 1 / SAMPLE_RATE)

    def at(hz):
        return spectrum[np.argmin(np.abs(bins - hz))]

    assert at(100) > 0.1 * n and at(500) > 0.1 * n
    assert at(200) < at(100) / 1000
    assert at(300) < at(100) / 1000


def test_the_result_is_written_into_the_signal_lane():
    signal, carrier = _two_lanes(0.5, 0.25)
    row = signal.current.id
    out = RingMod().process(signal, carrier)
    assert out is signal
    assert out.current.id == row
    assert np.all(out.current.data == pytest.approx(0.125))
    # The carrier is read and left as it was.
    assert np.all(carrier.current.data == pytest.approx(0.25))


def test_ring_modulation_needs_exactly_two_lanes():
    signal, carrier = _two_lanes(0.5, 0.25)
    with pytest.raises(ValueError, match="two inputs"):
        RingMod().process(signal)
    with pytest.raises(ValueError, match="two inputs"):
        RingMod().process(signal, carrier, carrier)


@pytest.mark.parametrize(
    "signal_quiet,carrier_quiet,expected",
    ((False, False, False), (True, False, True), (False, True, True), (True, True, True)),
)
def test_either_lane_stopping_ends_the_output(signal_quiet, carrier_quiet, expected):
    # The opposite of Mix, which needs every lane to have stopped. Anything
    # multiplied by silence is silence.
    signal, _ = _two_lanes(0.5, 0.25)
    assert RingMod()._output_quiescent(signal, signal_quiet, carrier_quiet) is expected
