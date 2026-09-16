"""`Sample` and `PCM`: a recording, and the voice that plays one.

**The strong tests are the exact ones.** A recording already at 48 kHz played
at unity speed must come back sample-for-sample identical -- no interpolation
error, no off-by-one at either end, no repeated final sample -- and a linear
ramp resampled at any speed must land exactly on the line, because linear
interpolation of a straight line is that line. Those two pin the arithmetic
far harder than a tolerance on a sine would, and everything else here is
about *when* a voice sounds rather than what it sounds like.

**Playback state is gate-edge state, and edges are where samplers go wrong.**
Retriggering, stopping mid-sound, a gate that goes high and stays high, a
sound swapped while a note is held: each is its own test, because each is a
different path through the one function that folds registers into position.
"""

import wave

import numpy as np
import pytest

from blitspersecond.audio.synth.block import BlockPool
from blitspersecond.audio.synth.bus import Bus
from blitspersecond.audio.constants import BLOCK_SIZE, SAMPLE_RATE
from blitspersecond.audio.synth.operators import PCM
from blitspersecond.audio.synth.registers import RegisterWrite
from blitspersecond.audio.sample import Sample


def _wav(path, data, rate=22050, channels=1, width=2):
    raw = np.asarray(data)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(np.rint(raw * 32767).astype("<i2").tobytes())
    return path


def _blocks(pcm, count, registers, writes=()):
    """Run the voice for whole blocks, as the engine would.

    Writes are handed to the block their timestamp falls in and nowhere else,
    which is what `AudioEngine` does. Passing one to the wrong block would let
    an operator index past the end of a block and never be noticed.
    """
    bus = Bus(BlockPool())
    registers = dict(registers)
    out = []
    for index in range(count):
        clock = index * BLOCK_SIZE
        due = tuple(
            write for write in writes if clock <= write.timestamp < clock + BLOCK_SIZE
        )
        bus.current.data[:] = 0.0
        pcm.control(clock, dict(registers), due)
        for write in due:
            for name, value in write.values:
                registers[name] = value
        pcm.process(bus)
        out.append(np.array(bus.current.data))
    return np.concatenate(out)


def _ramp(length, rate=SAMPLE_RATE):
    """A straight line, which linear interpolation must reproduce exactly."""
    return Sample("ramp", np.linspace(-1.0, 1.0, length), rate)


def _voice(sound, speed=1.0, gate=True, **overrides):
    """A voice and its settled register view, taken from its own defaults.

    Built from `operator.registers` rather than written out, so a register
    added later reaches every test here instead of turning them all into a
    `KeyError` that says nothing about what changed.
    """
    operator = PCM(sound, speed=speed)
    registers = {
        name: spec.default for name, spec in operator.registers.items()
    }
    registers["gate"] = gate
    registers.update(overrides)
    return operator, registers


# --------------------------------------------------------------------------
# Sample


def test_a_wav_loads_as_read_only_mono_float32(tmp_path):
    sound = Sample(str(_wav(tmp_path / "a.wav", np.linspace(-1, 1, 500))))
    assert sound.data.dtype == np.float32
    assert sound.data.ndim == 1
    assert len(sound) == 500
    assert sound.rate == 22050
    assert sound.duration == pytest.approx(500 / 22050)
    # Shared with every voice playing it, so nothing may write through it.
    assert not sound.data.flags.writeable


def test_naming_one_file_twice_decodes_it_once(tmp_path):
    path = str(_wav(tmp_path / "a.wav", np.linspace(-1, 1, 500)))
    assert Sample(path) is Sample(path)


def test_samples_handed_over_are_never_shared(tmp_path):
    data = np.linspace(-1, 1, 64)
    assert Sample("bleep", data, 8000) is not Sample("bleep", data, 8000)


def test_reloading_drops_the_shared_decode(tmp_path):
    path = str(_wav(tmp_path / "a.wav", np.linspace(-1, 1, 500)))
    first = Sample(path)
    assert Sample.reload(path) is True
    assert Sample(path) is not first


def test_a_stereo_file_is_folded_down_the_middle(tmp_path):
    left, right = np.full(64, 0.5), np.full(64, -0.25)
    interleaved = np.empty(128)
    interleaved[0::2], interleaved[1::2] = left, right
    sound = Sample(str(_wav(tmp_path / "s.wav", interleaved, channels=2)))
    assert len(sound) == 64
    assert sound.data == pytest.approx(0.125, abs=1e-4)


def test_only_sixteen_bit_wav_is_read(tmp_path):
    path = _wav(tmp_path / "b.wav", np.linspace(-1, 1, 64), width=1)
    with pytest.raises(ValueError, match="16-bit"):
        Sample(str(path))


def test_handed_samples_are_checked(tmp_path):
    with pytest.raises(ValueError, match="non-empty mono"):
        Sample("empty", np.zeros(0), 8000)
    with pytest.raises(ValueError, match="non-empty mono"):
        Sample("stereo", np.zeros((8, 2)), 8000)
    with pytest.raises(ValueError, match="non-finite"):
        Sample("nan", np.array([0.0, np.nan]), 8000)
    with pytest.raises(ValueError, match="rate must be positive"):
        Sample("slow", np.zeros(8), 0)


def test_a_path_and_a_rate_are_not_both_accepted(tmp_path):
    path = str(_wav(tmp_path / "a.wav", np.linspace(-1, 1, 64)))
    with pytest.raises(TypeError, match="reads the rate from the file"):
        Sample(path, None, 48000)
    with pytest.raises(TypeError, match="needs a rate"):
        Sample("bleep", np.zeros(8))


# --------------------------------------------------------------------------
# PCM: what comes out


def test_a_recording_at_the_engine_rate_plays_back_exactly():
    # No resampling to do at all, so anything other than the original samples
    # is arithmetic this operator invented.
    sound = _ramp(1000)
    pcm, registers = _voice(sound)
    out = _blocks(pcm, 4, registers)
    assert np.array_equal(out[:1000], sound.data)
    assert not np.any(out[1000:])


@pytest.mark.parametrize("speed", (0.25, 0.459375, 0.5, 1.0, 2.0, 3.7))
def test_resampling_a_straight_line_stays_on_the_line(speed):
    # Linear interpolation of a line IS the line, whatever the step, so the
    # expected value is known in closed form rather than approximated.
    sound = _ramp(2000)
    pcm, registers = _voice(sound, speed=speed)
    out = _blocks(pcm, 12, registers)
    played = int(np.count_nonzero(np.arange(len(out)) * speed <= len(sound) - 1))
    positions = np.arange(played) * speed
    assert out[:played] == pytest.approx(
        np.interp(positions, np.arange(len(sound)), sound.data), abs=1e-6
    )


def test_a_slower_rate_is_resampled_up_to_the_engine_s():
    # 22,050 Hz is 0.459 of a stored sample per output sample, which is the
    # ratio real recordings arrive at.
    sound = Sample("tone", np.sin(2 * np.pi * 1000 * np.arange(22050) / 22050), 22050)
    pcm, registers = _voice(sound)
    out = _blocks(pcm, 130, registers)
    spectrum = np.abs(np.fft.rfft(out[:48000] * np.hanning(48000)))
    peak = np.fft.rfftfreq(48000, 1 / SAMPLE_RATE)[np.argmax(spectrum)]
    assert peak == pytest.approx(1000.0, abs=1.0)


@pytest.mark.parametrize(
    "speed,expected", ((1.0, 1000), (2.0, 500), (0.5, 2000), (4.0, 250))
)
def test_speed_scales_the_duration(speed, expected):
    pcm, registers = _voice(_ramp(1000), speed=speed)
    out = _blocks(pcm, 8, registers)
    assert len(out) - np.argmax(out[::-1] != 0) == pytest.approx(expected, abs=1)


def test_speed_zero_freezes_on_the_sample_it_reached():
    pcm, registers = _voice(_ramp(1000))
    out = _blocks(
        pcm, 3, registers, writes=(RegisterWrite(BLOCK_SIZE, None, (("speed", 0.0),)),)
    )
    frozen = out[BLOCK_SIZE:]
    assert np.all(frozen == frozen[0])
    # Frozen exactly where it had got to: the next sample it would have read,
    # held. Not reset, not silent, and not the previous sample repeated.
    sound = pcm.registers["sample"].default
    assert frozen[0] == sound.data[BLOCK_SIZE]
    assert pcm.active


# --------------------------------------------------------------------------
# PCM: when it sounds


def test_an_ungated_voice_is_silent():
    pcm, registers = _voice(_ramp(1000), gate=False)
    assert not np.any(_blocks(pcm, 4, registers))


def test_a_rising_edge_starts_it_on_that_exact_sample():
    at = 137
    sound = _ramp(1000)
    pcm, registers = _voice(sound, gate=False)
    out = _blocks(
        pcm, 2, registers, writes=(RegisterWrite(at, None, (("gate", True),)),)
    )
    assert not np.any(out[:at])
    assert out[at] == pytest.approx(sound.data[0])
    assert out[at + 10] == pytest.approx(sound.data[10])


def test_a_falling_edge_stops_it_dead():
    at = BLOCK_SIZE + 50
    pcm, registers = _voice(_ramp(2000))
    out = _blocks(
        pcm, 3, registers, writes=(RegisterWrite(at, None, (("gate", False),)),)
    )
    assert np.any(out[:at])
    assert not np.any(out[at:])


def test_holding_the_gate_does_not_loop_or_retrigger():
    sound = _ramp(500)
    pcm, registers = _voice(sound)
    out = _blocks(pcm, 6, registers)
    assert np.array_equal(out[:500], sound.data)
    assert not np.any(out[500:])


def test_a_second_rising_edge_replays_from_the_start():
    sound = _ramp(300)
    pcm, registers = _voice(sound)
    out = _blocks(
        pcm,
        4,
        registers,
        writes=(
            RegisterWrite(BLOCK_SIZE, None, (("gate", False),)),
            RegisterWrite(BLOCK_SIZE + 100, None, (("gate", True),)),
        ),
    )
    assert np.array_equal(out[:300], sound.data)
    assert np.array_equal(out[BLOCK_SIZE + 100 : BLOCK_SIZE + 400], sound.data)


def test_swapping_the_sound_rewinds_to_the_start_of_the_new_one():
    first, second = _ramp(2000), Sample("other", np.full(600, 0.75), SAMPLE_RATE)
    pcm, registers = _voice(first)
    out = _blocks(
        pcm, 4, registers, writes=(RegisterWrite(BLOCK_SIZE, None, (("sample", second),)),)
    )
    assert np.array_equal(out[:BLOCK_SIZE], first.data[:BLOCK_SIZE])
    assert out[BLOCK_SIZE : BLOCK_SIZE + 600] == pytest.approx(0.75)
    assert not np.any(out[BLOCK_SIZE + 600 :])


def test_a_voice_settles_once_the_recording_has_run_out():
    sound = _ramp(300)
    pcm, registers = _voice(sound)
    bus = Bus(BlockPool())

    pcm.control(0, dict(registers), ())
    pcm.process(bus)
    # It sounded in this block even though it finished inside it.
    assert pcm._output_quiescent(bus, True) is False

    pcm.control(BLOCK_SIZE, dict(registers), ())
    pcm.process(bus)
    assert pcm._output_quiescent(bus, True) is True


def test_a_voice_gated_on_at_the_last_sample_of_a_block_stays_awake():
    # It has emitted nothing yet, so "did it sound" is not the question --
    # "is it going to" is.
    pcm, registers = _voice(_ramp(1000), gate=False)
    bus = Bus(BlockPool())
    pcm.control(0, dict(registers), (RegisterWrite(BLOCK_SIZE - 1, None, (("gate", True),)),))
    pcm.process(bus)
    assert pcm._output_quiescent(bus, True) is False


def test_a_voice_needs_a_sample():
    with pytest.raises(TypeError, match="expected Sample"):
        PCM(np.zeros(100))


# --------------------------------------------------------------------------
# PCM: seeking and scrubbing


def test_the_cue_point_is_where_a_gate_edge_starts_from():
    # Written while stopped, the head parks there and waits for the gate.
    sound = _ramp(2000)
    pcm, registers = _voice(sound, gate=False)
    out = _blocks(
        pcm,
        3,
        registers,
        writes=(
            RegisterWrite(10, None, (("playhead", 700 / SAMPLE_RATE),)),
            RegisterWrite(BLOCK_SIZE, None, (("gate", True),)),
        ),
    )
    assert not np.any(out[:BLOCK_SIZE])
    assert np.array_equal(out[BLOCK_SIZE : BLOCK_SIZE + 300], sound.data[700:1000])


def test_a_seek_lands_on_the_exact_sample_asked_for():
    # What a cutting tool needs: the sample at the cut, not the one beside
    # it. A 48kHz recording at unity speed makes that checkable exactly.
    sound = _ramp(4000)
    for target in (0, 1, 999, 1234, 3999):
        pcm, registers = _voice(sound, gate=False)
        out = _blocks(
            pcm,
            2,
            registers,
            writes=(
                RegisterWrite(0, None, (("playhead", target / SAMPLE_RATE),)),
                RegisterWrite(1, None, (("gate", True),)),
            ),
        )
        assert out[1] == sound.data[target]


def test_writing_the_playhead_mid_sound_is_a_jump_cut():
    at = 200
    sound = _ramp(4000)
    pcm, registers = _voice(sound)
    out = _blocks(
        pcm,
        2,
        registers,
        writes=(RegisterWrite(at, None, (("playhead", 3000 / SAMPLE_RATE),)),),
    )
    # Playing from the top up to the cut...
    assert np.array_equal(out[:at], sound.data[:at])
    # ...and from 3000 after it, on that sample and not the next one.
    assert np.array_equal(out[at : at + 100], sound.data[3000:3100])


def test_scrubbing_is_a_stream_of_seeks_within_one_block():
    sound = _ramp(4000)
    pcm, registers = _voice(sound)
    out = _blocks(
        pcm,
        1,
        registers,
        writes=tuple(
            RegisterWrite(offset, None, (("playhead", target / SAMPLE_RATE),))
            for offset, target in ((100, 3000), (200, 1000), (300, 2000))
        ),
    )
    assert out[100] == sound.data[3000]
    assert out[200] == sound.data[1000]
    assert out[300] == sound.data[2000]


def test_a_settled_playhead_does_not_drag_the_head_back_each_block():
    # The register keeps its value after the write, and every later block
    # sees it in the settled view. Re-reading that as a seek would restart
    # the sound 120 times a second and it would never advance at all.
    sound = _ramp(4000)
    pcm, registers = _voice(sound, gate=False, playhead=500 / SAMPLE_RATE)
    out = _blocks(pcm, 4, registers, writes=(RegisterWrite(0, None, (("gate", True),)),))
    assert np.array_equal(out[:1600], sound.data[500:2100])


def test_a_region_plays_from_its_in_point_to_its_out_point():
    sound = _ramp(4000)
    pcm, registers = _voice(sound, gate=False)
    out = _blocks(
        pcm,
        3,
        registers,
        writes=(
            RegisterWrite(0, None, (("playhead", 1200 / SAMPLE_RATE), ("gate", True))),
            RegisterWrite(500, None, (("gate", False),)),
        ),
    )
    assert np.array_equal(out[:500], sound.data[1200:1700])
    assert not np.any(out[500:])


def test_seeking_past_the_end_leaves_it_silent_and_settled():
    sound = _ramp(1000)
    pcm, registers = _voice(sound)
    bus = Bus(BlockPool())
    write = RegisterWrite(0, None, (("playhead", 2.0),))
    pcm.control(0, dict(registers), (write,))
    pcm.process(bus)
    assert not np.any(bus.current.data)

    pcm.control(BLOCK_SIZE, dict(registers), ())
    pcm.process(bus)
    assert pcm._output_quiescent(bus, True) is True


def test_a_sampler_that_never_writes_a_playhead_is_unchanged():
    # The cue point defaults to zero, so a drum hit is one write and a
    # retrigger rewinds to the start, exactly as before the register existed.
    sound = _ramp(300)
    pcm, registers = _voice(sound)
    assert registers["playhead"] == 0.0
    out = _blocks(
        pcm,
        4,
        registers,
        writes=(
            RegisterWrite(BLOCK_SIZE, None, (("gate", False),)),
            RegisterWrite(BLOCK_SIZE + 100, None, (("gate", True),)),
        ),
    )
    assert np.array_equal(out[:300], sound.data)
    assert np.array_equal(out[BLOCK_SIZE + 100 : BLOCK_SIZE + 400], sound.data)


def test_the_cue_point_survives_a_recording_being_swapped():
    # In seconds, so it means the same thing in a 22kHz stem as in a 48kHz
    # one -- which is the case a sample count could not have served.
    first = _ramp(4000)
    second = Sample("other", np.linspace(1.0, -1.0, 2000), 24000)
    pcm, registers = _voice(first, gate=False)
    out = _blocks(
        pcm,
        2,
        registers,
        writes=(
            RegisterWrite(0, None, (("playhead", 0.01), ("gate", True))),
            RegisterWrite(BLOCK_SIZE, None, (("sample", second),)),
        ),
    )
    # 0.01s into a 48kHz recording is sample 480.
    assert out[0] == first.data[480]
    # ...and sample 240 of a 24kHz one, played back at half a sample per step.
    assert out[BLOCK_SIZE] == pytest.approx(second.data[240], abs=1e-6)
