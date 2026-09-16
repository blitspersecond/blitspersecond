"""`Envelope`: the AHDSR that turns a carrier into a note.

**Tested against a constant carrier**, so the rendered block IS the envelope
and its shape can be read directly rather than inferred through a waveform.

**The live-register rules are the subject, not the basic shape.** Any
envelope gets attack/decay/release roughly right; what distinguishes this one
is what happens when a register moves while it is sounding, and every one of
those rules exists to preserve something musical:

  * attack scales by the distance still to climb, so retriggering during a
    release keeps the SLOPE rather than the duration;
  * hold remembers what it has already served, so retiming it does not
    restart it;
  * decay scales by how far it still has to fall;
  * release scales against the level it STARTED from, so a fade from a quiet
    sustain does not suddenly steepen.

Each has a test named for the musical consequence, because a test named for
the arithmetic would not say why the arithmetic is that way.
"""

import numpy as np
import pytest

from blitspersecond.audio.synth.block import BlockPool
from blitspersecond.audio.synth.bus import Bus
from blitspersecond.audio.constants import BLOCK_SIZE, SAMPLE_RATE
from blitspersecond.audio.synth.operators import Envelope
from blitspersecond.audio.synth.registers import RegisterWrite

BLOCK_SECONDS = BLOCK_SIZE / SAMPLE_RATE


def _defaults(envelope):
    return {name: spec.default for name, spec in envelope.registers.items()}


def _run(envelope, blocks=1, clock=0, writes=(), **overrides):
    """Render blocks of a constant 1.0 carrier through the envelope."""
    bus = Bus(BlockPool())
    registers = _defaults(envelope)
    registers.update(overrides)
    out = []
    for index in range(blocks):
        bus.current.data.fill(1.0)
        envelope.control(
            clock + index * BLOCK_SIZE, dict(registers), tuple(writes)
        )
        for write in writes:
            for name, value in write.values:
                registers[name] = value
        writes = ()
        envelope.process(bus)
        out.append(np.array(bus.current.data))
    return np.concatenate(out)


def _gate(at, on):
    return RegisterWrite(at, None, (("gate", on),))


def _ahdsr(a=0.0, h=0.0, d=0.0, s=1.0, r=0.0):
    return Envelope(a, h, d, s, r)


# -- the shape ----------------------------------------------------------


def test_an_ungated_envelope_is_silent():
    assert np.all(_run(_ahdsr(), blocks=2) == 0.0)


def test_a_gate_opens_the_envelope():
    signal = _run(_ahdsr(s=1.0), writes=[_gate(0, True)])
    assert np.all(signal == 1.0)


def test_attack_rises_linearly_to_full():
    attack = 2 * BLOCK_SECONDS
    signal = _run(_ahdsr(a=attack, s=1.0), blocks=3, writes=[_gate(0, True)])
    ramp = signal[: 2 * BLOCK_SIZE]

    assert ramp[0] == pytest.approx(0.0, abs=1e-3)
    assert ramp[-1] == pytest.approx(1.0, abs=1e-2)
    assert np.all(np.diff(ramp) > 0)
    assert np.allclose(np.diff(ramp), np.diff(ramp)[0], atol=1e-6)


def test_hold_stays_at_full_then_decays_to_sustain():
    signal = _run(
        _ahdsr(h=BLOCK_SECONDS, d=BLOCK_SECONDS, s=0.25),
        blocks=3,
        writes=[_gate(0, True)],
    )
    assert np.allclose(signal[:BLOCK_SIZE], 1.0)
    assert np.all(np.diff(signal[BLOCK_SIZE : 2 * BLOCK_SIZE]) < 0)
    assert signal[-1] == pytest.approx(0.25, abs=1e-3)


def test_sustain_holds_until_the_gate_closes():
    signal = _run(_ahdsr(s=0.5), blocks=3, writes=[_gate(0, True)])
    assert np.allclose(signal, 0.5)


def test_release_fades_from_the_current_level_to_silence():
    envelope = _ahdsr(s=0.5, r=BLOCK_SECONDS)
    _run(envelope, writes=[_gate(0, True)])
    signal = _run(envelope, blocks=2, gate=True, writes=[_gate(0, False)])

    assert signal[0] == pytest.approx(0.5, abs=1e-3)
    assert np.all(np.diff(signal[:BLOCK_SIZE]) < 0)
    assert np.all(signal[BLOCK_SIZE:] == 0.0)


def test_a_zero_length_envelope_jumps_straight_to_sustain():
    assert np.all(_run(_ahdsr(s=0.75), writes=[_gate(0, True)]) == 0.75)


# -- gate is a level, not an edge ---------------------------------------


def test_gating_twice_is_legato_not_a_retrigger():
    """Writing True while already True must change nothing."""
    envelope = _ahdsr(a=BLOCK_SECONDS, s=1.0)
    _run(envelope, blocks=2, writes=[_gate(0, True)])
    steady = _run(envelope, gate=True)
    again = _run(envelope, gate=True, writes=[_gate(0, True)])
    assert np.allclose(steady, again)


def test_a_retrigger_is_two_writes_at_one_sample():
    """gate=False then gate=True, which is why write order breaks ties."""
    envelope = _ahdsr(a=BLOCK_SECONDS, s=1.0, r=BLOCK_SECONDS)
    _run(envelope, blocks=2, writes=[_gate(0, True)])

    signal = _run(
        envelope,
        gate=True,
        writes=[_gate(100, False), _gate(100, True)],
    )

    assert signal[99] == pytest.approx(1.0, abs=1e-3)
    assert np.all(np.diff(signal[100:]) <= 0) or np.all(
        np.diff(signal[100:]) >= 0
    )


def test_a_retrigger_during_release_starts_from_where_the_fade_reached():
    """The musical point: a fast repeat does not click back to silence."""
    envelope = _ahdsr(a=BLOCK_SECONDS, s=1.0, r=4 * BLOCK_SECONDS)
    _run(envelope, blocks=2, writes=[_gate(0, True)])
    _run(envelope, gate=True, writes=[_gate(0, False)])

    mid_release = envelope.level
    assert 0.0 < mid_release < 1.0

    signal = _run(envelope, writes=[_gate(0, True)])
    assert signal[0] == pytest.approx(mid_release, abs=1e-2)


# -- live registers: each rule, by its musical consequence ---------------


def test_retiming_attack_keeps_the_slope_not_the_duration():
    """Half way up, an attack of the same length has half as far to go."""
    envelope = _ahdsr(a=4 * BLOCK_SECONDS, s=1.0)
    _run(envelope, blocks=2, writes=[_gate(0, True)])
    half_way = envelope.level
    remaining_before = envelope._remaining

    envelope._retime_attack({**_defaults(envelope), "attack": 4 * BLOCK_SECONDS})

    assert half_way == pytest.approx(0.5, abs=0.05)
    assert envelope._remaining == pytest.approx(remaining_before, rel=0.05)


def test_retiming_hold_does_not_restart_it():
    envelope = _ahdsr(h=4 * BLOCK_SECONDS, d=BLOCK_SECONDS, s=0.0)
    _run(envelope, blocks=2, writes=[_gate(0, True)])
    assert envelope.state == "hold"
    served = envelope._hold_elapsed

    _run(envelope, gate=True, hold=4 * BLOCK_SECONDS)

    assert envelope._hold_elapsed > served


def test_shortening_hold_below_what_has_elapsed_ends_it_now():
    envelope = _ahdsr(h=8 * BLOCK_SECONDS, d=BLOCK_SECONDS, s=0.5)
    _run(envelope, blocks=3, writes=[_gate(0, True)])
    assert envelope.state == "hold"

    _run(envelope, gate=True, writes=[RegisterWrite(0, None, (("hold", 0.0),))])

    assert envelope.state in ("decay", "sustain")


def test_retiming_release_keeps_a_quiet_fade_from_steepening():
    """Release scales against the level it STARTED from, not against 1.0."""
    loud = _ahdsr(s=1.0, r=4 * BLOCK_SECONDS)
    quiet = _ahdsr(s=0.25, r=4 * BLOCK_SECONDS)
    for envelope in (loud, quiet):
        _run(envelope, writes=[_gate(0, True)])
        _run(envelope, gate=True, writes=[_gate(0, False)])

    # Both are one block into a four-block release, so both should have a
    # quarter of their own fade behind them.
    assert loud.level / 1.0 == pytest.approx(quiet.level / 0.25, abs=0.05)


def test_changing_sustain_while_sustaining_moves_the_level():
    envelope = _ahdsr(s=0.5)
    _run(envelope, writes=[_gate(0, True)])
    signal = _run(
        envelope, gate=True, writes=[RegisterWrite(200, None, (("sustain", 0.2),))]
    )
    assert np.allclose(signal[:200], 0.5)
    assert np.allclose(signal[200:], 0.2)


def test_retargeting_decay_travels_at_the_same_rate_to_the_new_sustain():
    """Rate is preserved, not duration -- the same rule attack follows.

    `decay` is the time for a full 1.0 -> sustain fall, so it names a RATE.
    Retargeted from level 0.5 to a sustain of 0.8, the journey is 0.3 while
    the nominal span is only 0.2, so it correctly takes 1.5x the decay time
    rather than squeezing a longer move into the same window.
    """
    envelope = _ahdsr(d=4 * BLOCK_SECONDS, s=0.0)
    _run(envelope, blocks=2, writes=[_gate(0, True)])
    assert envelope.state == "decay"
    assert envelope.level == pytest.approx(0.5, abs=0.05)

    # Four more blocks covers two thirds of a six-block journey.
    _run(envelope, blocks=4, gate=True, decay=4 * BLOCK_SECONDS, sustain=0.8)
    assert envelope.level == pytest.approx(0.7, abs=1e-2)

    # And the rest of it arrives.
    _run(envelope, blocks=3, gate=True, decay=4 * BLOCK_SECONDS, sustain=0.8)
    assert envelope.state == "sustain"
    assert envelope.level == pytest.approx(0.8, abs=1e-2)


# -- writes land on the sample ------------------------------------------


def test_a_gate_write_opens_the_envelope_at_its_exact_sample():
    signal = _run(_ahdsr(s=1.0), writes=[_gate(137, True)])
    assert np.all(signal[:137] == 0.0)
    assert np.all(signal[137:] == 1.0)


def test_writes_are_offset_from_the_engine_clock():
    clock = 48_000
    signal = _run(_ahdsr(s=1.0), clock=clock, writes=[_gate(clock + 50, True)])
    assert np.all(signal[:50] == 0.0)
    assert np.all(signal[50:] == 1.0)


def test_a_phase_boundary_inside_a_block_is_handled():
    """Attack ending mid-block must hand over without a discontinuity."""
    signal = _run(
        _ahdsr(a=BLOCK_SECONDS / 2, s=1.0), writes=[_gate(0, True)]
    )
    assert np.max(np.abs(np.diff(signal))) < 0.02
    assert signal[-1] == pytest.approx(1.0, abs=1e-3)


# -- quiescence ----------------------------------------------------------


def test_an_idle_envelope_settles():
    envelope = _ahdsr()
    bus = Bus(BlockPool())
    _run(envelope)
    assert envelope._output_quiescent(bus, True) is True


def test_a_sounding_envelope_stays_awake():
    envelope = _ahdsr(s=1.0)
    bus = Bus(BlockPool())
    _run(envelope, writes=[_gate(0, True)])
    assert envelope._output_quiescent(bus, True) is False


def test_a_block_where_a_note_ended_is_not_quiescent():
    """It produced audio in the first half; settling would truncate it."""
    envelope = _ahdsr(s=1.0, r=BLOCK_SECONDS / 4)
    bus = Bus(BlockPool())
    _run(envelope, writes=[_gate(0, True)])
    _run(envelope, gate=True, writes=[_gate(0, False)])

    assert envelope.state == "idle"
    assert envelope._output_quiescent(bus, True) is False


def test_the_block_after_a_note_ends_settles():
    envelope = _ahdsr(s=1.0, r=BLOCK_SECONDS / 4)
    bus = Bus(BlockPool())
    _run(envelope, writes=[_gate(0, True)])
    _run(envelope, gate=True, writes=[_gate(0, False)])
    _run(envelope)

    assert envelope._output_quiescent(bus, True) is True


# -- resuming after the Stage was asleep ---------------------------------


def test_an_envelope_reconciles_after_a_gap_in_rendering():
    """A skipped block means the registers may have moved without us."""
    envelope = _ahdsr(s=0.5)
    _run(envelope, writes=[_gate(0, True)])

    # Jump the clock, as a sleeping Stage would, with a new sustain settled.
    signal = _run(envelope, clock=10 * BLOCK_SIZE, gate=True, sustain=0.9)

    assert np.allclose(signal, 0.9)


def test_a_gate_that_closed_while_asleep_is_noticed_on_waking():
    envelope = _ahdsr(s=1.0, r=0.0)
    _run(envelope, writes=[_gate(0, True)])

    signal = _run(envelope, clock=10 * BLOCK_SIZE, gate=False)

    assert envelope.state == "idle"
    assert np.all(signal == 0.0)
