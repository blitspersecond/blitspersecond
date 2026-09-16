"""`Pluck`: Karplus-Strong, measured as a string rather than as arithmetic.

**The sustain is the subject, not the attack.** Every pluck begins with the
same broadband noise burst whatever the registers say, so a spectrum taken
from the first moments measures the excitation and not the instrument. The
brightness tests here skip the first 100ms deliberately; taken over the whole
note they report `damping` as having no consistent effect at all, which is a
statement about the window and not about the operator.

**DC is measured separately everywhere.** A plucked note leaves a standing
offset behind it -- see `test_a_plucked_note_leaves_a_dc_offset_behind_it`,
which pins that as it stands -- so every test about pitch, brightness or
decay removes the mean first. Otherwise the loudest thing in the spectrum is
0 Hz and the measurements are of the defect.
"""

import numpy as np
import pytest

from blitspersecond.audio.synth.block import BlockPool
from blitspersecond.audio.synth.bus import Bus
from blitspersecond.audio.constants import BLOCK_SIZE, SAMPLE_RATE
from blitspersecond.audio.synth.operators import Pluck
from blitspersecond.audio.synth.registers import RegisterWrite


def _defaults(operator, **overrides):
    values = {name: spec.default for name, spec in operator.registers.items()}
    values.update(overrides)
    return values


def _blocks(pluck, count, registers, writes=()):
    bus = Bus(BlockPool())
    registers = dict(registers)
    out = []
    for index in range(count):
        clock = index * BLOCK_SIZE
        due = tuple(
            write for write in writes if clock <= write.timestamp < clock + BLOCK_SIZE
        )
        bus.current.data[:] = 0.0
        pluck.control(clock, dict(registers), due)
        for write in due:
            for name, value in write.values:
                registers[name] = value
        pluck.process(bus)
        out.append(np.array(bus.current.data))
    return np.concatenate(out)


def _pluck(**kwargs):
    gate = kwargs.pop("gate", True)
    operator = Pluck(**kwargs)
    return operator, _defaults(operator, gate=gate)


def _spectrum(signal):
    """Magnitudes and their frequencies, with everything sub-audio removed.

    Subtracting the mean is not enough: the standing offset DECAYS, slowly,
    so what is left after the mean goes is a drift that shows up as an
    enormous 1 Hz component. Nothing below 20 Hz is a note here -- that is
    the lowest `min_frequency` the operator will accept -- so the whole band
    is discarded rather than argued with.
    """
    signal = signal - signal.mean()
    windowed = signal * np.hanning(len(signal))
    magnitudes = np.abs(np.fft.rfft(windowed))
    bins = np.fft.rfftfreq(len(signal), 1 / SAMPLE_RATE)
    magnitudes[bins < 20.0] = 0.0
    return magnitudes, bins


def _envelope(signal, window):
    """Peak per window, each window measured about its own mean.

    Local rather than global, for the same reason: a slowly falling offset
    would otherwise be counted as part of the note in the windows it is
    largest in.
    """
    usable = signal[: len(signal) // window * window].reshape(-1, window)
    return np.abs(usable - usable.mean(axis=1, keepdims=True)).max(axis=1)


def _seconds_to_minus_sixty(signal, frequency, window=4800):
    """How long ONE frequency takes to fall 60dB, by fitting its decay.

    Projected onto the frequency of interest per window rather than taken
    from a peak envelope: the attack burst is louder than the note it starts,
    so an envelope reaches -60dB from a level the string never sustained, and
    reports a decay a third shorter than the one that was asked for.
    """
    usable = signal[: len(signal) // window * window].reshape(-1, window)
    reference = np.exp(-2j * np.pi * frequency * np.arange(window) / SAMPLE_RATE)
    decibels = 20 * np.log10(np.abs(usable @ reference) * 2 / window + 1e-20)
    seconds = np.arange(len(decibels)) * window / SAMPLE_RATE
    # Fit past the attack and above the noise floor, where it is a straight
    # line in dB -- which is what an exponential decay is.
    usable_range = (seconds >= 0.2) & (decibels > decibels[2] - 55)
    slope = np.polyfit(seconds[usable_range], decibels[usable_range], 1)[0]
    return -60.0 / slope


def _centroid(signal):
    magnitudes, bins = _spectrum(signal)
    return float(np.sum(magnitudes * bins) / np.sum(magnitudes))


# --------------------------------------------------------------------------
# What it sounds like


@pytest.mark.parametrize("frequency", (110.0, 220.0, 440.0, 880.0))
def test_a_plucked_string_rings_at_the_pitch_asked_for(frequency):
    pluck, registers = _pluck(frequency=frequency, string_decay=2.0)
    out = _blocks(pluck, 120, registers)
    magnitudes, bins = _spectrum(out[:48000])
    assert bins[np.argmax(magnitudes)] == pytest.approx(frequency, rel=0.01)


def test_the_note_decays_rather_than_sustaining():
    pluck, registers = _pluck(frequency=220.0, string_decay=0.5)
    envelope = _envelope(_blocks(pluck, 120, registers), 4800)
    assert envelope[0] > 0.5
    # Monotonic after the attack: a string only ever gets quieter.
    assert np.all(np.diff(envelope[1:]) <= 0)
    assert envelope[-1] < envelope[0] / 1000


@pytest.mark.parametrize("decay", (0.5, 1.0))
def test_string_decay_is_seconds_to_minus_sixty_decibels(decay):
    # Measured against the number asked for, not against another setting, so
    # a change that scaled every decay alike would still fail. The
    # FUNDAMENTAL is what the register is defined on, so that is what is
    # tracked -- the partials above it decay faster by design, and the
    # standing offset below it slower.
    pluck, registers = _pluck(frequency=220.0, string_decay=decay)
    out = _blocks(pluck, int(decay * 2 * 120) + 240, registers)
    assert _seconds_to_minus_sixty(out, 220.0) == pytest.approx(decay, rel=0.05)


def test_a_string_cannot_be_asked_to_ring_for_ever():
    # Feedback is capped just under unity, so past a certain point the ring
    # stops getting longer however large `string_decay` is. Two settings a
    # factor of two apart land on the same note, to the fit's precision.
    times = []
    for decay in (2.0, 4.0):
        pluck, registers = _pluck(frequency=220.0, string_decay=decay)
        out = _blocks(pluck, 600, registers)
        times.append(_seconds_to_minus_sixty(out, 220.0))
    assert times[0] == pytest.approx(times[1], rel=0.01)
    assert times[0] < 1.6


@pytest.mark.parametrize("damping", (0.0, 0.2, 0.4, 0.6, 0.8, 1.0))
def test_damping_dulls_the_sustain(damping):
    # Each step up must be duller than the one below it, so the register
    # travels in one direction over its whole range rather than only at the
    # ends. The window skips the attack; see the module docstring.
    pluck, registers = _pluck(frequency=220.0, damping=damping, string_decay=2.0)
    brighter = _centroid(_blocks(pluck, 40, registers)[4800:14400])
    if damping == 0.0:
        return
    duller_op, duller_registers = _pluck(
        frequency=220.0, damping=damping - 0.2, string_decay=2.0
    )
    assert brighter < _centroid(_blocks(duller_op, 40, duller_registers)[4800:14400])


def test_plucking_at_the_middle_cancels_the_even_harmonics():
    # The physical claim: a harmonic with a node where the string is plucked
    # cannot be excited. Halfway along is a node of every even one.
    at_the_bridge, bridge_registers = _pluck(
        frequency=220.0, position=0.0, string_decay=2.0
    )
    at_the_middle, middle_registers = _pluck(
        frequency=220.0, position=0.5, string_decay=2.0
    )

    def ratio(operator, registers):
        magnitudes, bins = _spectrum(_blocks(operator, 40, registers))
        return (
            magnitudes[np.argmin(np.abs(bins - 440))]
            / magnitudes[np.argmin(np.abs(bins - 220))]
        )

    assert ratio(at_the_middle, middle_registers) < 0.02
    assert ratio(at_the_bridge, bridge_registers) > 0.2


def test_moving_the_pluck_does_not_move_the_volume():
    # `position` is a timbre control, and the comb's normalisation is what
    # keeps it from being a second volume control as well.
    levels = []
    for place in (0.0, 0.1, 0.25, 0.4, 0.5):
        pluck, registers = _pluck(frequency=220.0, position=place, string_decay=2.0)
        out = _blocks(pluck, 40, registers)
        levels.append(float(np.sqrt(np.mean((out - out.mean()) ** 2))))
    assert max(levels) / min(levels) < 1.5


# --------------------------------------------------------------------------
# When it sounds


def test_an_ungated_string_is_silent():
    pluck, registers = _pluck(gate=False)
    assert not np.any(_blocks(pluck, 4, registers))


def test_a_rising_edge_excites_it_on_that_exact_sample():
    at = 137
    pluck, registers = _pluck(gate=False)
    out = _blocks(
        pluck, 2, registers, writes=(RegisterWrite(at, None, (("gate", True),)),)
    )
    assert not np.any(out[:at])
    assert np.any(out[at:])


def test_letting_go_of_the_gate_does_not_damp_the_string():
    # A real string carries on when you stop holding the fret. Stopping one
    # is a separate act, which is an Envelope's job and not the gate's.
    pluck, registers = _pluck(frequency=220.0, string_decay=2.0)
    out = _blocks(
        pluck,
        60,
        registers,
        writes=(RegisterWrite(BLOCK_SIZE, None, (("gate", False),)),),
    )
    tail = out[-4800:]
    assert np.abs(tail - tail.mean()).max() > 1e-3


def test_a_second_rising_edge_plucks_it_again():
    pluck, registers = _pluck(frequency=220.0, string_decay=0.1)
    out = _blocks(
        pluck,
        60,
        registers,
        writes=(
            RegisterWrite(BLOCK_SIZE * 20, None, (("gate", False),)),
            RegisterWrite(BLOCK_SIZE * 40, None, (("gate", True),)),
        ),
    )
    faded = np.abs(out[BLOCK_SIZE * 38 : BLOCK_SIZE * 40]).max()
    struck = np.abs(out[BLOCK_SIZE * 40 : BLOCK_SIZE * 42]).max()
    assert struck > faded * 10


def test_a_string_settles_once_its_ring_has_gone():
    pluck, registers = _pluck(frequency=220.0, string_decay=0.25)
    bus = Bus(BlockPool())
    for index in range(600):
        bus.current.data[:] = 0.0
        pluck.control(index * BLOCK_SIZE, dict(registers), ())
        pluck.process(bus)
    assert pluck._output_quiescent(bus, True) is True


# --------------------------------------------------------------------------
# Determinism and construction


def test_the_same_seed_plucks_the_same_string():
    first, first_registers = _pluck(frequency=220.0)
    second, second_registers = _pluck(frequency=220.0)
    assert np.array_equal(
        _blocks(first, 20, first_registers), _blocks(second, 20, second_registers)
    )


def test_a_different_seed_plucks_a_different_one():
    first, first_registers = _pluck(frequency=220.0)
    other, other_registers = _pluck(frequency=220.0, seed=7)
    assert not np.array_equal(
        _blocks(first, 20, first_registers), _blocks(other, 20, other_registers)
    )


def test_a_pluck_seed_must_be_a_non_negative_integer():
    with pytest.raises(TypeError):
        Pluck(seed=1.0)
    with pytest.raises(TypeError):
        Pluck(seed=True)
    with pytest.raises(ValueError):
        Pluck(seed=-1)


def test_the_lowest_note_must_fit_in_a_delay_line():
    with pytest.raises(ValueError, match="finite and positive"):
        Pluck(min_frequency=0.0)
    with pytest.raises(ValueError, match="finite and positive"):
        Pluck(min_frequency=float("inf"))
    # Below this the buffer would be longer than a Delay is allowed to be.
    with pytest.raises(ValueError, match="at least"):
        Pluck(min_frequency=0.1)


def test_the_lowest_note_is_the_frequency_register_s_floor():
    assert Pluck(min_frequency=55.0).registers["frequency"].minimum == 55.0


# --------------------------------------------------------------------------
# Known defect, pinned so it cannot change quietly


def test_a_plucked_note_leaves_a_dc_offset_behind_it():
    """The excitation burst is not zero-mean, and the loop preserves DC.

    The loop filter attenuates everything except 0 Hz, and `feedback` is set
    from how fast the FUNDAMENTAL should decay -- so DC, which the filter
    does not touch, decays far more slowly than any note does. What is left
    is a standing step on the lane that outlives the sound by a long way.

    Pinned rather than fixed: subtracting the mean from the excitation, or
    putting a DC blocker in the loop, changes what every existing pluck
    sounds like. See the board.
    """
    pluck, registers = _pluck(frequency=440.0, string_decay=2.0)
    out = _blocks(pluck, 240, registers)

    audible, settled = out[:24000], out[72000:96000]
    # The note itself is long gone.
    assert np.abs(settled - settled.mean()).max() < 0.01
    # The offset is not, and has barely moved.
    assert settled.mean() == pytest.approx(audible.mean(), rel=0.2)
    assert abs(settled.mean()) > 0.04
