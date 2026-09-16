"""`Duck` and `Reverb`: the two ends of the grime bed.

**Duck's lane order is its meaning**, so most of its tests are about which
lane does what: lane zero is attenuated, lane one is only listened to and is
never heard. Swapping them is a silent, plausible-sounding bug -- the output
still has audio in it -- which is why it gets an explicit test rather than
being left to the Apply tests that already prove order is preserved.

**Reverb is tested for its tail, not its timbre.** What makes it a reverb
rather than an echo is that four combs at unrelated lengths smear into
something with no single repeat period, so the tests check that the tail
persists, decays, and has no strong periodicity.
"""

import numpy as np
import pytest

from blitspersecond.audio.synth.block import BlockPool
from blitspersecond.audio.synth.bus import Bus
from blitspersecond.audio.constants import BLOCK_SIZE, SAMPLE_RATE
from blitspersecond.audio.synth.operators import Duck, Reverb
from blitspersecond.audio.synth.operators.reverb import _COMB_MS
from blitspersecond.audio.synth.registers import RegisterWrite


def _buses(signal, control=None):
    pool = BlockPool()
    first = Bus(pool)
    first.current.data[:] = signal
    if control is None:
        return (first,)
    second = Bus(pool)
    second.current.data[:] = control
    return (first, second)


def _duck(operator, signal, control, blocks=1, writes=(), **overrides):
    registers = {n: s.default for n, s in operator.registers.items()}
    registers.update(overrides)
    out = []
    for index in range(blocks):
        lanes = _buses(signal, control)
        operator.control(index * BLOCK_SIZE, dict(registers), tuple(writes))
        writes = ()
        operator.process(*lanes)
        out.append(np.array(lanes[0].current.data))
    return np.concatenate(out)


LOUD = np.ones(BLOCK_SIZE, dtype=np.float32)
QUIET = np.zeros(BLOCK_SIZE, dtype=np.float32)


# -- Duck ----------------------------------------------------------------


def test_a_silent_control_leaves_the_signal_alone():
    assert np.allclose(_duck(Duck(), LOUD, QUIET), 1.0)


def test_a_loud_control_pushes_the_signal_down():
    out = _duck(Duck(depth=1.0, attack=0.0005), LOUD, LOUD, blocks=4)
    assert out[-1] < 0.1


def test_the_control_lane_is_never_heard():
    """Only listened to. This is what makes it not a mix."""
    out = _duck(Duck(depth=0.0), QUIET, LOUD, blocks=2)
    assert np.allclose(out, 0.0)


def test_lane_order_is_the_meaning():
    """Swapping them still produces audio, which is why it needs a test."""
    ducked = _duck(Duck(depth=1.0, attack=0.0005), LOUD, LOUD, blocks=4)
    swapped = _duck(Duck(depth=1.0, attack=0.0005), LOUD, QUIET, blocks=4)
    assert ducked[-1] < 0.1
    assert np.allclose(swapped, 1.0)


def test_duck_requires_exactly_two_lanes():
    operator = Duck()
    operator.control(0, {"depth": 1.0, "attack": 0.005, "release": 0.08}, ())
    with pytest.raises(ValueError):
        operator.process(*_buses(LOUD))
    with pytest.raises(ValueError):
        operator.process()


def test_depth_sets_how_far_it_drops():
    shallow = _duck(Duck(depth=0.25, attack=0.0005), LOUD, LOUD, blocks=4)
    deep = _duck(Duck(depth=1.0, attack=0.0005), LOUD, LOUD, blocks=4)
    assert deep[-1] < shallow[-1] < 1.0


def test_attack_decides_how_fast_it_gets_out_of_the_way():
    fast = _duck(Duck(depth=1.0, attack=0.0002), LOUD, LOUD)
    slow = _duck(Duck(depth=1.0, attack=0.05), LOUD, LOUD)
    assert fast[-1] < slow[-1]


def test_release_decides_how_slowly_it_comes_back():
    def recovery(release):
        operator = Duck(depth=1.0, attack=0.0002, release=release)
        _duck(operator, LOUD, LOUD, blocks=4)
        return _duck(operator, LOUD, QUIET, blocks=2)[-1]

    assert recovery(0.001) > recovery(0.5)


def test_the_follower_carries_across_blocks():
    """A release longer than 8ms spans several blocks."""
    operator = Duck(depth=1.0, attack=0.0002, release=0.5)
    _duck(operator, LOUD, LOUD, blocks=4)
    assert operator.envelope > 0.5
    _duck(operator, LOUD, QUIET)
    assert operator.envelope > 0.1


def test_a_write_lands_on_its_sample():
    out = _duck(
        Duck(depth=0.0, attack=0.0002),
        LOUD,
        LOUD,
        writes=[RegisterWrite(200, None, (("depth", 1.0),))],
    )
    assert np.allclose(out[:200], 1.0)
    assert out[-1] < 1.0


def test_a_zero_time_is_instant_rather_than_an_error():
    out = _duck(Duck(depth=1.0, attack=0.0), LOUD, LOUD)
    assert out[1] == pytest.approx(0.0, abs=1e-6)


# -- Reverb ---------------------------------------------------------------


def _reverb(operator, blocks, first=None, **overrides):
    bus = Bus(BlockPool())
    registers = {n: s.default for n, s in operator.registers.items()}
    registers.update(overrides)
    out = []
    for index in range(blocks):
        bus.current.data.fill(0.0)
        if index == 0 and first is not None:
            bus.current.data[:] = first
        operator.control(index * BLOCK_SIZE, dict(registers), ())
        operator.process(bus)
        out.append(np.array(bus.current.data))
    return np.concatenate(out)


def test_reverb_leaves_a_tail_after_the_input_stops():
    strike = np.zeros(BLOCK_SIZE, dtype=np.float32)
    strike[0] = 1.0
    signal = _reverb(Reverb(1.0), blocks=30, first=strike)

    assert np.max(np.abs(signal[BLOCK_SIZE:])) > 0.001


def test_the_tail_decays():
    strike = np.zeros(BLOCK_SIZE, dtype=np.float32)
    strike[0] = 1.0
    signal = _reverb(Reverb(1.0), blocks=60, first=strike)
    early = np.max(np.abs(signal[5 * BLOCK_SIZE : 15 * BLOCK_SIZE]))
    late = np.max(np.abs(signal[45 * BLOCK_SIZE :]))
    assert late < early


def test_amount_sets_how_much_is_heard():
    strike = np.zeros(BLOCK_SIZE, dtype=np.float32)
    strike[0] = 1.0
    wet = np.max(np.abs(_reverb(Reverb(1.0), 20, first=strike)[BLOCK_SIZE:]))
    dry = np.max(np.abs(_reverb(Reverb(0.0), 20, first=strike)[BLOCK_SIZE:]))
    assert dry == 0.0
    assert wet > 0.0


def test_the_dry_signal_survives_underneath():
    """The wet is mixed UNDER, not substituted for, the input."""
    steady = np.full(BLOCK_SIZE, 0.5, dtype=np.float32)
    out = _reverb(Reverb(0.0), 1, first=steady)
    assert np.allclose(out, 0.5)


def test_the_combs_are_not_in_simple_ratios():
    """Otherwise their repeats stack and the tail rings at one pitch."""
    lengths = [round(ms / 1000 * SAMPLE_RATE) for ms in _COMB_MS]
    for index, first in enumerate(lengths):
        for second in lengths[index + 1 :]:
            assert first != second
            assert abs(first / second - round(first / second)) > 0.05


def test_every_comb_is_longer_than_a_block():
    """Why this vectorises where Echo and Duck do not: the recurrence never
    closes inside one block, so whole slices can move at a time."""
    for ms in _COMB_MS:
        assert round(ms / 1000 * SAMPLE_RATE) > BLOCK_SIZE


def test_reverb_settles_once_every_comb_has_gone_quiet():
    reverb = Reverb(1.0)
    bus = Bus(BlockPool())
    bus.current.data.fill(1.0)
    reverb.control(0, {"amount": 1.0}, ())
    reverb.process(bus)

    settled = False
    for index in range(400):
        bus.current.data.fill(0.0)
        reverb.control((index + 1) * BLOCK_SIZE, {"amount": 1.0}, ())
        reverb.process(bus)
        settled = reverb._output_quiescent(bus, True)
        if settled:
            break
    assert settled is True


def test_a_reverb_claims_nothing_while_it_runs():
    reverb = Reverb(0.5)
    bus = Bus(BlockPool())
    reverb.control(0, {"amount": 0.5}, ())
    reverb.process(bus)
    buffers = [b for b in reverb._buffers]

    for index in range(20):
        reverb.control((index + 1) * BLOCK_SIZE, {"amount": 0.5}, ())
        reverb.process(bus)

    assert [b for b in reverb._buffers] == buffers
