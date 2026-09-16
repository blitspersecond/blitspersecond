"""`LowPass`, `HighPass`, `BandPass`: one biquad, three numerators.

**Tested by measuring the response, not by checking coefficients.** Feeding
sine waves at chosen frequencies and reading the output amplitude asks the
question a listener would ask -- does it pass this and stop that -- and
cannot pass by sharing an arithmetic slip with the implementation.

**A filter is state, and the state is the point.** It rings after its input
stops, and it carries that ringing across block boundaries and across
coefficient changes. Those are tested directly, because they are what
separates a filter from a per-block gain curve.
"""

import numpy as np
import pytest

from blitspersecond.audio.synth.block import BlockPool
from blitspersecond.audio.synth.bus import Bus
from blitspersecond.audio.constants import BLOCK_SIZE, SAMPLE_RATE
from blitspersecond.audio.synth.operators import BandPass, HighPass, LowPass
from blitspersecond.audio.synth.operators._biquad import (
    MAXIMUM_FILTER_FREQUENCY,
    MINIMUM_FILTER_FREQUENCY,
)
from blitspersecond.audio.synth.registers import RegisterWrite

FILTERS = (LowPass, HighPass, BandPass)


def _through(operator, signal, writes=(), clock=0):
    """Push a long signal through in blocks, as the engine would."""
    bus = Bus(BlockPool())
    out = []
    blocks = len(signal) // BLOCK_SIZE
    registers = {
        name: spec.default for name, spec in operator.registers.items()
    }
    for index in range(blocks):
        chunk = signal[index * BLOCK_SIZE : (index + 1) * BLOCK_SIZE]
        bus.current.data[:] = chunk
        operator.control(
            clock + index * BLOCK_SIZE, dict(registers), tuple(writes)
        )
        for write in writes:
            for name, value in write.values:
                registers[name] = value
        writes = ()
        operator.process(bus)
        out.append(np.array(bus.current.data))
    return np.concatenate(out)


def _tone(hz, blocks=40):
    n = blocks * BLOCK_SIZE
    return np.sin(2 * np.pi * hz * np.arange(n) / SAMPLE_RATE).astype(np.float32)


def _gain_at(operator, hz):
    """Steady-state amplitude ratio, measured past the settling transient."""
    out = _through(operator, _tone(hz))
    settled = out[len(out) // 2 :]
    return float(np.max(np.abs(settled)))


# -- the response each filter is named for ------------------------------


def test_a_low_pass_keeps_the_low_and_removes_the_high():
    low_pass = LowPass(1_000.0)
    assert _gain_at(low_pass, 100.0) == pytest.approx(1.0, abs=0.05)
    assert _gain_at(LowPass(1_000.0), 8_000.0) < 0.05


def test_a_high_pass_does_the_opposite():
    """Its passband approaches unity toward Nyquist, not immediately.

    This is a DIGITAL filter: the bilinear transform warps the frequency
    axis, so at 8 kHz -- a sixth of the sample rate -- a 1 kHz high-pass is
    still climbing (about 0.94) and only reaches unity much higher up.
    Expecting the analogue answer here is the mistake, not the filter.
    """
    assert _gain_at(HighPass(1_000.0), 100.0) < 0.05
    assert _gain_at(HighPass(1_000.0), 8_000.0) > 0.9
    assert _gain_at(HighPass(1_000.0), 20_000.0) == pytest.approx(1.0, abs=0.01)


def test_a_band_pass_keeps_only_its_middle():
    assert _gain_at(BandPass(1_000.0), 1_000.0) == pytest.approx(1.0, abs=0.1)
    assert _gain_at(BandPass(1_000.0), 100.0) < 0.2
    assert _gain_at(BandPass(1_000.0), 10_000.0) < 0.2


@pytest.mark.parametrize("kind", (LowPass, HighPass))
def test_the_corner_is_three_decibels_down(kind):
    """Butterworth at the default Q: the cutoff is the half-power point."""
    assert _gain_at(kind(1_000.0), 1_000.0) == pytest.approx(
        1 / np.sqrt(2), abs=0.05
    )


def test_a_low_pass_falls_at_twelve_decibels_an_octave():
    """Second order: one octave up costs a factor of four.

    Measured low in the band, where the analogue answer still holds. The same
    ratio climbs to about 5 at 4-8 kHz and about 9 at 8-16 kHz, because the
    bilinear transform compresses the frequency axis as it approaches
    Nyquist. That steepening is correct behaviour for a digital biquad; it
    just makes the octave measured up there the wrong place to check the
    slope.
    """
    one = _gain_at(LowPass(500.0), 1_000.0)
    two = _gain_at(LowPass(500.0), 2_000.0)
    assert one / two == pytest.approx(4.0, rel=0.1)


def test_raising_q_peaks_the_corner():
    flat = _gain_at(LowPass(1_000.0, q=0.7071), 1_000.0)
    resonant = _gain_at(LowPass(1_000.0, q=6.0), 1_000.0)
    assert resonant > flat * 3


# -- it is a filter, not a gain curve -----------------------------------


def test_a_filter_rings_after_its_input_stops():
    """The retained state is what makes this a filter at all."""
    low_pass = LowPass(400.0, q=8.0)
    signal = np.concatenate(
        [_tone(400.0, blocks=4), np.zeros(4 * BLOCK_SIZE, dtype=np.float32)]
    )
    out = _through(low_pass, signal)
    tail = out[4 * BLOCK_SIZE :]
    assert np.max(np.abs(tail)) > 0.01
    assert np.max(np.abs(tail[-BLOCK_SIZE:])) < np.max(np.abs(tail[:BLOCK_SIZE]))


def test_state_carries_across_block_boundaries():
    """Filtered in blocks must equal filtered all at once."""
    signal = _tone(300.0, blocks=8)
    in_blocks = _through(LowPass(1_000.0), signal)

    one_shot = LowPass(1_000.0)
    bus = Bus(BlockPool())
    whole = []
    for index in range(8):
        bus.current.data[:] = signal[index * BLOCK_SIZE : (index + 1) * BLOCK_SIZE]
        one_shot.control(index * BLOCK_SIZE, {"cutoff": 1_000.0, "q": 0.7071}, ())
        one_shot.process(bus)
        whole.append(np.array(bus.current.data))
    assert np.allclose(in_blocks, np.concatenate(whole))


def test_two_filters_do_not_share_state():
    signal = _tone(300.0, blocks=4)
    warmed = LowPass(1_000.0)
    _through(warmed, signal)
    assert not np.array_equal(
        _through(warmed, signal), _through(LowPass(1_000.0), signal)
    )


# -- live control --------------------------------------------------------


def test_a_cutoff_write_takes_effect_at_its_sample():
    signal = _tone(6_000.0, blocks=4)
    cut = 150
    out = _through(
        LowPass(20_000.0),
        signal,
        writes=[RegisterWrite(cut, None, (("cutoff", 200.0),))],
    )
    assert np.max(np.abs(out[:cut])) > 0.5
    assert np.max(np.abs(out[3 * BLOCK_SIZE :])) < 0.1


def test_coefficients_are_computed_once_when_nothing_moves():
    low_pass = LowPass(1_000.0)
    _through(low_pass, _tone(300.0, blocks=8))
    first = low_pass._coefficients_cache
    _through(low_pass, _tone(300.0, blocks=4))
    assert low_pass._coefficients_cache is first


def test_a_write_recomputes_them():
    low_pass = LowPass(1_000.0)
    _through(low_pass, _tone(300.0, blocks=2))
    before = low_pass._coefficients_cache
    _through(
        low_pass,
        _tone(300.0, blocks=2),
        writes=[RegisterWrite(0, None, (("cutoff", 4_000.0),))],
    )
    assert low_pass._coefficients_cache is not before


def test_writes_are_offset_from_the_engine_clock():
    clock = 48_000
    signal = _tone(6_000.0, blocks=4)
    out = _through(
        LowPass(20_000.0),
        signal,
        clock=clock,
        writes=[RegisterWrite(clock + 100, None, (("cutoff", 200.0),))],
    )
    assert np.max(np.abs(out[:100])) > 0.5


# -- the declared range --------------------------------------------------


@pytest.mark.parametrize("kind", FILTERS)
def test_the_cutoff_range_stops_short_of_dc_and_nyquist(kind):
    """A stability rule: both endpoints put a double pole on the unit circle."""
    spec = kind(1_000.0).registers["cutoff"]
    assert spec.minimum == MINIMUM_FILTER_FREQUENCY == 20.0
    assert spec.maximum == MAXIMUM_FILTER_FREQUENCY == 21_600.0
    assert spec.maximum < SAMPLE_RATE / 2


@pytest.mark.parametrize("kind", FILTERS)
def test_both_ends_of_the_range_are_stable(kind):
    """What the range is for: neither endpoint may run away."""
    for cutoff in (MINIMUM_FILTER_FREQUENCY, MAXIMUM_FILTER_FREQUENCY):
        out = _through(kind(cutoff), _tone(1_000.0, blocks=40))
        assert np.all(np.isfinite(out))
        assert np.max(np.abs(out)) < 10.0


@pytest.mark.parametrize("kind", FILTERS)
def test_q_has_no_ceiling_yet(kind):
    """Pinned: the register-range card flags this as probably an oversight."""
    assert kind(1_000.0).registers["q"].maximum is None


# -- settling ------------------------------------------------------------


def test_a_filter_settles_once_its_tail_has_gone():
    low_pass = LowPass(1_000.0)
    bus = Bus(BlockPool())
    _through(low_pass, _tone(1_000.0, blocks=4))

    settled = False
    for _ in range(12):
        # Re-zeroed EVERY block. `process` writes its result back into the
        # bus, so filling once and looping feeds the filter its own decaying
        # tail -- which never settles, and is a property of the test rather
        # than of the filter.
        bus.current.data.fill(0.0)
        low_pass.control(0, {"cutoff": 1_000.0, "q": 0.7071}, ())
        low_pass.process(bus)
        settled = low_pass._output_quiescent(bus, True)
    assert settled is True


def test_a_filter_stays_awake_while_its_input_does():
    low_pass = LowPass(1_000.0)
    bus = Bus(BlockPool())
    bus.current.data.fill(0.0)
    assert low_pass._output_quiescent(bus, False) is False
