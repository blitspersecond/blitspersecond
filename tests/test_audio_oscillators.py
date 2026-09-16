"""`Sine`, `Square`, `Saw`, `Triangle`: the carriers.

**Waveforms are checked by their defining property, not by their samples.**
Comparing against a hand-written expected array only proves the test and the
implementation were typed by the same person. So a square is asserted to take
exactly two values, a saw to rise monotonically then jump once per cycle, a
triangle to be symmetric about its peak, and a sine to match `numpy.sin` at
the phase it claims to be at.

**Phase continuity is the thing most likely to break and least likely to be
noticed.** A carrier that restarts its cycle every block clicks 120 times a
second; one that drifts goes slowly out of tune against everything else. Both
are tested by rendering several blocks and checking the joins, never by
looking at one block in isolation.
"""

import numpy as np
import pytest

from blitspersecond.audio.synth.block import BlockPool
from blitspersecond.audio.synth.bus import Bus
from blitspersecond.audio.constants import BLOCK_SIZE, SAMPLE_RATE
from blitspersecond.audio.synth.operators import Saw, Sine, Square, Triangle
from blitspersecond.audio.synth.operators._oscillator import C_SHARP_5
from blitspersecond.audio.synth.registers import RegisterWrite

CARRIERS = (Sine, Square, Saw, Triangle)


@pytest.fixture
def bus() -> Bus:
    return Bus(BlockPool())


def _run(operator, blocks=1, frequency=None, writes=(), clock=0):
    """Render consecutive blocks, returning one flat signal."""
    pool = BlockPool()
    bus = Bus(pool)
    out = []
    for index in range(blocks):
        registers = {
            "frequency": operator.registers["frequency"].default
            if frequency is None
            else frequency
        }
        operator.control(clock + index * BLOCK_SIZE, registers, tuple(writes))
        writes = ()
        operator.process(bus)
        out.append(np.array(bus.current.data))
    return np.concatenate(out)


# -- what every carrier promises ---------------------------------------


@pytest.mark.parametrize("carrier", CARRIERS)
def test_a_carrier_declares_one_frequency_register(carrier):
    spec = carrier.registers["frequency"]
    assert spec.default == C_SHARP_5
    assert (spec.minimum, spec.maximum) == (0.0, SAMPLE_RATE / 2)
    assert spec.unit == "Hz"


@pytest.mark.parametrize("carrier", CARRIERS)
def test_a_carrier_fills_the_whole_block(carrier):
    signal = _run(carrier(), frequency=440.0)
    assert signal.shape == (BLOCK_SIZE,)
    assert np.all(np.isfinite(signal))


@pytest.mark.parametrize("carrier", CARRIERS)
def test_a_carrier_stays_inside_unity(carrier):
    signal = _run(carrier(), blocks=8, frequency=440.0)
    assert signal.max() <= 1.0 + 1e-6
    assert signal.min() >= -1.0 - 1e-6


@pytest.mark.parametrize("carrier", CARRIERS)
def test_a_carrier_is_never_quiescent(carrier):
    """A carrier is not finished; something downstream decides if it is heard."""
    assert carrier()._output_quiescent(Bus(BlockPool()), True) is False


@pytest.mark.parametrize("carrier", CARRIERS)
def test_two_carriers_of_one_kind_are_independent(carrier):
    """Each voice owns its phase, or two notes would fight over one cycle.

    441 Hz, not 440: at 440 Hz three blocks is exactly 11 cycles, so phase
    returns to zero and a quantised waveform repeats byte for byte. The test
    would pass for the wrong reason on three carriers and fail on Square.
    """
    first, second = carrier(), carrier()
    _run(first, blocks=3, frequency=441.0)
    assert not np.array_equal(
        _run(first, frequency=441.0), _run(second, frequency=441.0)
    )


@pytest.mark.parametrize("carrier", CARRIERS)
def test_a_carrier_is_deterministic(carrier):
    """Same construction, same writes, same samples -- offline renders diff."""
    assert np.array_equal(
        _run(carrier(), blocks=4, frequency=440.0),
        _run(carrier(), blocks=4, frequency=440.0),
    )


# -- phase continuity ---------------------------------------------------


@pytest.mark.parametrize("carrier", CARRIERS)
def test_a_carrier_does_not_restart_each_block(carrier):
    """A cycle restarting on every boundary is a click 120 times a second.
    Checked by comparing whole blocks rather than the samples either side of
    a join: a square is flat almost everywhere, so adjacent samples being
    equal is its normal condition and says nothing. What a restart WOULD do
    is make every block identical to the first.

    441 Hz because 440 is exactly 11 cycles per three blocks, which makes a
    restarted carrier and a continuous one produce the same bytes.
    """
    signal = _run(carrier(), blocks=4, frequency=441.0)
    blocks = signal.reshape(4, BLOCK_SIZE)
    for index in range(1, 4):
        assert not np.array_equal(blocks[index], blocks[index - 1])


def test_a_sine_holds_its_pitch_across_many_blocks():
    """Drift would put a long note slowly out of tune with everything else."""
    hz = 480.0  # exactly 4 cycles per block, so phase should return to 0
    signal = _run(Sine(), blocks=120, frequency=hz)
    expected = np.sin(2 * np.pi * hz * np.arange(len(signal)) / SAMPLE_RATE)
    assert np.allclose(signal, expected, atol=2e-6)


def test_a_carrier_resumes_where_it_stopped_when_a_stage_sleeps():
    """A skipped block must not be raced through on waking."""
    operator = Sine()
    _run(operator, blocks=2, frequency=440.0)
    paused = operator.phase
    assert _run(operator, frequency=440.0)[0] == pytest.approx(
        float(np.sin(paused)), abs=1e-6
    )


# -- each waveform, by its defining property ---------------------------


def test_a_square_takes_exactly_two_values():
    signal = _run(Square(), blocks=4, frequency=440.0)
    assert set(np.unique(signal)) == {-1.0, 1.0}


def test_a_square_is_half_high_and_half_low():
    signal = _run(Square(), blocks=120, frequency=480.0)
    assert np.mean(signal) == pytest.approx(0.0, abs=1e-3)


def test_a_saw_rises_and_falls_once_per_cycle():
    hz = 120.0
    signal = _run(Saw(), blocks=1, frequency=hz)
    drops = np.flatnonzero(np.diff(signal) < 0)
    assert len(drops) == pytest.approx(hz * BLOCK_SIZE / SAMPLE_RATE, abs=1)
    assert np.all(np.diff(np.delete(signal, drops)) > 0)


def test_a_triangle_is_symmetric_about_its_peak():
    signal = _run(Triangle(), blocks=1, frequency=120.0)
    peak = int(np.argmax(signal))
    span = min(peak, len(signal) - peak - 1, 40)
    assert np.allclose(
        signal[peak - span : peak], signal[peak + span : peak : -1], atol=1e-5
    )


def test_a_sine_matches_the_real_thing():
    signal = _run(Sine(), frequency=440.0)
    expected = np.sin(2 * np.pi * 440.0 * np.arange(BLOCK_SIZE) / SAMPLE_RATE)
    # 1e-5, not 1e-6: the block is float32 (eps ~1.2e-7) and phase is built by
    # accumulation, so agreement with a float64 reference to within a few
    # units in the last place is the correct answer, not a near miss.
    assert np.allclose(signal, expected, atol=1e-5)


def test_the_waveforms_are_actually_different():
    rendered = [_run(c(), frequency=440.0) for c in CARRIERS]
    for index, first in enumerate(rendered):
        for second in rendered[index + 1 :]:
            assert not np.allclose(first, second)


# -- frequency writes ---------------------------------------------------


@pytest.mark.parametrize("carrier", CARRIERS)
def test_a_frequency_write_takes_effect_at_its_sample(carrier):
    cut = 150
    low = _run(carrier(), frequency=110.0)
    switched = _run(
        carrier(),
        frequency=110.0,
        writes=[RegisterWrite(cut, None, (("frequency", 880.0),))],
    )
    assert np.allclose(switched[:cut], low[:cut])
    assert not np.allclose(switched[cut:], low[cut:])


@pytest.mark.parametrize("carrier", CARRIERS)
def test_retuning_bends_a_note_rather_than_restarting_it(carrier):
    """Phase carries across a write, so a held note glides."""
    cut = 200
    signal = _run(
        carrier(),
        frequency=220.0,
        writes=[RegisterWrite(cut, None, (("frequency", 330.0),))],
    )
    if carrier is Square:
        return  # a square is two values; continuity is not visible in one sample
    assert abs(signal[cut] - signal[cut - 1]) < 0.2


@pytest.mark.parametrize("carrier", CARRIERS)
def test_writes_are_offset_from_the_engine_clock(carrier):
    clock = 96_000
    signal = _run(
        carrier(),
        frequency=110.0,
        clock=clock,
        writes=[RegisterWrite(clock + 100, None, (("frequency", 880.0),))],
    )
    reference = _run(carrier(), frequency=110.0)
    assert np.allclose(signal[:100], reference[:100])


@pytest.mark.parametrize("carrier", CARRIERS)
def test_a_silent_frequency_is_allowed_and_holds_still(carrier):
    """0 Hz is in range: a stopped carrier, not an error."""
    signal = _run(carrier(), blocks=2, frequency=0.0)
    assert np.all(np.isfinite(signal))
    assert len(np.unique(signal)) <= 2


# -- the signal path, which only Sine has -------------------------------


def test_only_sine_accepts_a_frequency_signal():
    assert Sine.registers["frequency"].accepts_signal is True
    for carrier in (Square, Saw, Triangle):
        assert carrier.registers["frequency"].accepts_signal is False


def test_a_constant_signal_matches_the_scalar_path():
    """The two calculations must agree where they overlap."""
    steady = np.full(BLOCK_SIZE, 440.0, dtype=np.float32)
    assert np.allclose(
        _run(Sine(), frequency=steady), _run(Sine(), frequency=440.0), atol=1e-5
    )


def test_a_moving_signal_integrates_rather_than_multiplies():
    """Phase is the integral of frequency: a sweep must not jump."""
    sweep = np.linspace(200.0, 800.0, BLOCK_SIZE, dtype=np.float32)
    signal = _run(Sine(), frequency=sweep)

    assert np.all(np.abs(np.diff(signal)) < 0.2)
    assert signal.max() > 0.9 and signal.min() < -0.9


def test_a_signal_driven_sine_stays_continuous_across_blocks():
    operator = Sine()
    sweep = np.full(BLOCK_SIZE, 440.0, dtype=np.float32)
    first = _run(operator, frequency=sweep)
    second = _run(operator, frequency=sweep)
    assert abs(second[0] - first[-1]) < 0.1
