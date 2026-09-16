"""`Noise`, `Tanh`, `Pulse`, `Resonator`: what a drum is made of.

**`Pulse` and `Resonator` are tested as the pair they are.** A pulse alone is
a click and a resonator alone is silent; the thing worth asserting is that
striking one with the other produces a decaying tone at the pitch asked for,
which is a drum. Testing each in isolation would check the arithmetic and
miss the instrument.

**Determinism is a contract here, not a convenience.** `Noise` is seeded at
construction precisely so a snare is the same snare every time it is struck
and an offline render can be diffed at all. That gets its own test.
"""

import numpy as np
import pytest

from blitspersecond.audio.synth.block import BlockPool
from blitspersecond.audio.synth.bus import Bus
from blitspersecond.audio.constants import BLOCK_SIZE, SAMPLE_RATE
from blitspersecond.audio.synth.operators import Noise, Pulse, Resonator, Tanh
from blitspersecond.audio.synth.registers import RegisterWrite


def _defaults(operator):
    return {name: spec.default for name, spec in operator.registers.items()}


def _run(operator, blocks=1, writes=(), clock=0, carrier=None, **overrides):
    bus = Bus(BlockPool())
    registers = _defaults(operator) if operator.registers else {}
    registers.update(overrides)
    out = []
    for index in range(blocks):
        if carrier is not None:
            bus.current.data[:] = carrier
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


def _gate(at, on=True):
    return RegisterWrite(at, None, (("gate", on),))


# -- Noise ---------------------------------------------------------------


def test_noise_fills_the_block_between_minus_one_and_one():
    signal = _run(Noise(), blocks=4)
    assert signal.min() >= -1.0 and signal.max() <= 1.0
    assert signal.std() == pytest.approx(1 / np.sqrt(3), abs=0.05)


def test_noise_is_the_same_noise_every_time():
    """So a snare is one snare, and a render can be diffed."""
    assert np.array_equal(_run(Noise(7), blocks=3), _run(Noise(7), blocks=3))


def test_different_seeds_give_different_noise():
    assert not np.array_equal(_run(Noise(1), blocks=2), _run(Noise(2), blocks=2))


def test_noise_keeps_going_across_blocks():
    """Not the same block repeated: the generator carries its own state."""
    signal = _run(Noise(), blocks=2).reshape(2, BLOCK_SIZE)
    assert not np.array_equal(signal[0], signal[1])


def test_noise_is_flat_across_the_spectrum():
    signal = _run(Noise(), blocks=60)
    spectrum = np.abs(np.fft.rfft(signal * np.hanning(len(signal))))
    bands = np.array_split(spectrum[1:], 8)
    energies = [float(np.mean(b)) for b in bands]
    assert max(energies) / min(energies) < 1.5


def test_noise_never_settles():
    assert Noise()._output_quiescent(Bus(BlockPool()), True) is False


def test_a_noise_seed_must_be_a_non_negative_integer():
    with pytest.raises(TypeError):
        Noise(1.5)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Noise(-1)


# -- Tanh ----------------------------------------------------------------


def test_tanh_leaves_full_scale_at_full_scale():
    """Drive changes the shape, not the level -- so it is safe mid-chain."""
    carrier = np.full(BLOCK_SIZE, 1.0, dtype=np.float32)
    for drive in (0.5, 2.0, 8.0):
        assert _run(Tanh(drive), carrier=carrier).max() == pytest.approx(1.0, abs=1e-5)


def test_tanh_squashes_what_is_between():
    """A half-scale input comes back louder relative to the peak: compression."""
    carrier = np.full(BLOCK_SIZE, 0.5, dtype=np.float32)
    assert _run(Tanh(4.0), carrier=carrier)[0] > 0.5


def test_drive_walks_a_sine_toward_a_square():
    """The real property, rather than an arbitrary multiple.

    Saturation adds odd harmonics, monotonically with drive -- and in the
    limit a hard-driven sine IS a square, whose third harmonic is exactly
    1/3 of its fundamental. Measured: 0.020 at drive 0.5, rising through
    0.169 at 2.0 to 0.329 at 16.0, converging on 0.333 rather than growing
    without bound. Asserting the destination catches a wrong curve; asserting
    "five times bigger" only catches a flat one.
    """
    hz = 480.0
    carrier = np.sin(
        2 * np.pi * hz * np.arange(BLOCK_SIZE) / SAMPLE_RATE
    ).astype(np.float32)

    def third_harmonic(drive):
        out = _run(Tanh(drive), blocks=1, carrier=carrier)
        spectrum = np.abs(np.fft.rfft(out * np.hanning(len(out))))
        bin_one = int(round(hz * len(out) / SAMPLE_RATE))
        return float(spectrum[bin_one * 3] / spectrum[bin_one])

    ratios = [third_harmonic(drive) for drive in (0.5, 1.0, 2.0, 4.0, 8.0, 16.0)]
    assert ratios == sorted(ratios)
    assert ratios[0] < 0.05
    assert ratios[-1] == pytest.approx(1 / 3, abs=0.02)


def test_tanh_has_no_corner():
    """Unlike a hard clip: the curve bends continuously all the way up."""
    ramp = np.linspace(-2.0, 2.0, BLOCK_SIZE, dtype=np.float32)
    out = _run(Tanh(2.0), carrier=ramp)
    assert np.all(np.diff(out) > 0)


def test_a_drive_write_lands_on_its_sample():
    carrier = np.full(BLOCK_SIZE, 0.5, dtype=np.float32)
    out = _run(
        Tanh(1.0),
        carrier=carrier,
        writes=[RegisterWrite(200, None, (("drive", 8.0),))],
    )
    assert not np.allclose(out[:200], out[200:])


def test_tanh_passes_quiescence_through():
    assert Tanh()._output_quiescent(Bus(BlockPool()), True) is True


# -- Pulse ---------------------------------------------------------------


def test_an_ungated_pulse_is_silent():
    assert np.all(_run(Pulse(), blocks=2) == 0.0)


def test_a_rising_edge_emits_a_burst_of_the_declared_length():
    signal = _run(Pulse(64), writes=[_gate(0)])
    assert np.all(signal[:64] == 1.0)
    assert np.all(signal[64:] == 0.0)


def test_a_burst_starts_at_the_exact_sample():
    signal = _run(Pulse(32), writes=[_gate(100)])
    assert np.all(signal[:100] == 0.0)
    assert np.all(signal[100:132] == 1.0)


def test_holding_the_gate_does_not_extend_or_repeat_the_burst():
    """Two hits need two edges."""
    signal = _run(Pulse(64), blocks=3, writes=[_gate(0)], gate=False)
    assert signal[:64].sum() == 64.0
    assert signal[64:].sum() == 0.0


def test_a_second_edge_fires_a_second_burst():
    signal = _run(
        Pulse(32),
        writes=[_gate(0, True), _gate(100, False), _gate(200, True)],
    )
    assert np.all(signal[:32] == 1.0)
    assert np.all(signal[200:232] == 1.0)
    assert np.all(signal[232:] == 0.0)


def test_a_burst_spans_a_block_boundary():
    signal = _run(Pulse(200), blocks=2, writes=[_gate(BLOCK_SIZE - 100)])
    assert np.all(signal[BLOCK_SIZE - 100 : BLOCK_SIZE + 100] == 1.0)
    assert np.all(signal[BLOCK_SIZE + 100 :] == 0.0)


def test_a_pulse_interrupted_by_sleep_is_dropped_rather_than_resumed():
    """A burst is milliseconds long; resuming one would place it wrongly."""
    pulse = Pulse(BLOCK_SIZE * 2)
    _run(pulse, writes=[_gate(0)])
    assert pulse._remaining > 0

    resumed = _run(pulse, clock=10 * BLOCK_SIZE, gate=True)
    assert np.all(resumed == 0.0)


def test_a_pulse_settles_once_its_burst_is_over():
    pulse = Pulse(32)
    bus = Bus(BlockPool())
    _run(pulse, writes=[_gate(0)])
    assert pulse._output_quiescent(bus, True) is False
    _run(pulse, gate=True)
    assert pulse._output_quiescent(bus, True) is True


def test_a_pulse_length_must_be_a_whole_number_of_ticks():
    with pytest.raises(TypeError):
        Pulse(48.0)  # type: ignore[arg-type]


# -- Resonator, and the drum it makes with Pulse -------------------------


def test_a_resonator_names_its_decay_apart_from_an_envelope_s():
    """So a Program can hold both without a register collision."""
    assert "resonance_decay" in Resonator().registers
    assert "decay" not in Resonator().registers


def test_striking_a_resonator_rings_at_the_pitch_asked_for():
    """The pair, as an instrument: a pulse in, a tuned decaying tone out."""
    hz = 220.0
    strike = _run(Pulse(48), blocks=20, writes=[_gate(0)])
    resonator = Resonator(hz, 0.15)
    bus = Bus(BlockPool())
    out = []
    registers = {"frequency": hz, "resonance_decay": 0.15}
    for index in range(20):
        bus.current.data[:] = strike[index * BLOCK_SIZE : (index + 1) * BLOCK_SIZE]
        resonator.control(index * BLOCK_SIZE, dict(registers), ())
        resonator.process(bus)
        out.append(np.array(bus.current.data))
    signal = np.concatenate(out)

    spectrum = np.abs(np.fft.rfft(signal * np.hanning(len(signal))))
    peak_hz = float(np.argmax(spectrum)) * SAMPLE_RATE / len(signal)
    assert peak_hz == pytest.approx(hz, rel=0.1)


def test_a_longer_decay_rings_for_longer():
    def tail(decay):
        strike = _run(Pulse(48), blocks=30, writes=[_gate(0)])
        resonator = Resonator(220.0, decay)
        bus = Bus(BlockPool())
        out = []
        for index in range(30):
            bus.current.data[:] = strike[
                index * BLOCK_SIZE : (index + 1) * BLOCK_SIZE
            ]
            resonator.control(
                index * BLOCK_SIZE,
                {"frequency": 220.0, "resonance_decay": decay},
                (),
            )
            resonator.process(bus)
            out.append(np.array(bus.current.data))
        signal = np.concatenate(out)
        return float(np.max(np.abs(signal[-4 * BLOCK_SIZE :])))

    assert tail(0.4) > tail(0.05) * 5


def test_a_resonator_settles_after_its_ring_has_gone():
    resonator = Resonator(220.0, 0.01)
    bus = Bus(BlockPool())
    bus.current.data.fill(1.0)
    resonator.control(0, {"frequency": 220.0, "resonance_decay": 0.01}, ())
    resonator.process(bus)

    settled = False
    for index in range(40):
        bus.current.data.fill(0.0)
        resonator.control(
            (index + 1) * BLOCK_SIZE,
            {"frequency": 220.0, "resonance_decay": 0.01},
            (),
        )
        resonator.process(bus)
        settled = resonator._output_quiescent(bus, True)
    assert settled is True
