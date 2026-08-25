import mido
import numpy as np
import pytest

from blitspersecond.audio.driver import render
from blitspersecond.audio.engine import AudioEngine, Program
from blitspersecond.audio.operators import Envelope, Gain, Sine
from tests.examples.demo_midi import (
    demo_tune,
    load_events,
    load_midi_file,
    pool_sizes,
    schedule,
)


def sine_patch(_program):
    return lambda engine: engine.source(
        Program(
            Sine,
            lambda: Envelope(0.0, 0.0, 0.0, 1.0, 0.0),
            Gain,
        )
    )


def dominant_frequency(samples):
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    bins = np.fft.rfftfreq(len(samples), 1.0 / 48_000)
    return bins[np.argmax(spectrum)]


def test_audio_scheduler_builds_a_deterministic_stereo_stage_graph():
    outputs = []
    durations = []
    for _ in range(2):
        engine = AudioEngine()
        events, programs = load_events(demo_tune())
        durations.append(schedule(engine, events, programs))
        outputs.append(render(engine, 0.5))

    assert durations[0] == pytest.approx(16.728159375)
    assert durations[1] == durations[0]
    assert outputs[0].shape == (24_000, 2)
    assert np.all(np.isfinite(outputs[0]))
    assert np.any(outputs[0] != 0.0)
    assert np.array_equal(outputs[0], outputs[1])


def test_pool_sizes_do_not_shave_polyphony_to_a_fixed_channel_budget():
    events = []
    for note in range(40, 72):
        events.append((0.0, 0, "on", note, 100))
    for note in range(40, 72):
        events.append((1.0, 0, "off", note, 0))

    pools, drum_notes = pool_sizes(events)

    assert pools == {0: 32}
    assert drum_notes == []


def test_pitch_wheel_retunes_sounding_voices_at_the_event_sample():
    events = [
        (0.0, 0, "on", 69, 127),
        (0.5, 0, "bend", 8191, 0),
        (1.0, 0, "off", 69, 0),
    ]
    engine = AudioEngine()
    schedule(engine, events, {}, patches=sine_patch)

    samples = render(engine, 1.0)[:, 0]

    assert dominant_frequency(samples[4800:21_600]) == pytest.approx(440.0, abs=2.0)
    assert dominant_frequency(samples[28_800:45_600]) == pytest.approx(
        493.88,
        abs=2.0,
    )


def test_sustain_pedal_defers_gate_up_until_the_pedal_lifts():
    events = [
        (0.0, 0, "on", 69, 127),
        (0.05, 0, "sustain", 127, 0),
        (0.1, 0, "off", 69, 0),
        (0.2, 0, "sustain", 0, 0),
    ]
    engine = AudioEngine()
    schedule(engine, events, {}, patches=sine_patch)

    samples = render(engine, 0.25)[:, 0]

    sustained = np.sqrt(np.mean(samples[5760:8640] ** 2))
    released = np.sqrt(np.mean(samples[10_560:] ** 2))
    assert sustained > 0.05
    assert released < sustained * 0.2


def test_cc7_controls_the_channel_fader_including_its_room_send():
    events = [
        (0.0, 0, "on", 69, 127),
        (0.1, 0, "volume", 0, 0),
        (0.2, 0, "off", 69, 0),
    ]
    engine = AudioEngine()
    schedule(engine, events, {}, patches=sine_patch)

    samples = render(engine, 0.2)

    before = np.sqrt(np.mean(samples[960:4320] ** 2))
    after = np.sqrt(np.mean(samples[5760:] ** 2))
    assert before > 0.05
    assert after < before * 0.2


def test_mid_score_program_change_builds_and_selects_a_new_voice_pool():
    selected = []

    def patches(program):
        selected.append(program)
        return sine_patch(program)

    events = [
        (0.0, 0, "program", 1, 0),
        (0.0, 0, "on", 60, 100),
        (0.1, 0, "off", 60, 0),
        (0.2, 0, "program", 40, 0),
        (0.2, 0, "on", 64, 100),
        (0.3, 0, "off", 64, 0),
    ]

    schedule(AudioEngine(), events, {}, patches=patches)

    assert selected == [1, 40]


def test_load_events_preserves_supported_channel_controls_in_score_order():
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.extend(
        (
            mido.Message("program_change", channel=2, program=10, time=0),
            mido.Message("control_change", channel=2, control=7, value=96, time=0),
            mido.Message("note_on", channel=2, note=60, velocity=100, time=0),
            mido.Message("pitchwheel", channel=2, pitch=2048, time=48),
            mido.Message("control_change", channel=2, control=64, value=127, time=0),
            mido.Message("note_off", channel=2, note=60, velocity=0, time=48),
            mido.Message("control_change", channel=2, control=64, value=0, time=48),
            mido.Message("program_change", channel=2, program=40, time=48),
        )
    )

    events, programs = load_events(mid)

    assert programs == {2: 10}
    assert [(kind, value, auxiliary) for _, _, kind, value, auxiliary in events] == [
        ("program", 10, 0),
        ("volume", 96, 0),
        ("on", 60, 100),
        ("bend", 2048, 0),
        ("sustain", 127, 0),
        ("off", 60, 0),
        ("sustain", 0, 0),
        ("program", 40, 0),
    ]


def test_load_midi_file_retries_out_of_range_data_bytes(tmp_path, capsys):
    # One-track SMF containing note_on velocity 0xff, which is not a legal
    # seven-bit MIDI data byte. Mido's compatibility mode clamps it to 127.
    midi = (
        b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60"
        b"MTrk\x00\x00\x00\x08\x00\x90\x3c\xff\x00\xff\x2f\x00"
    )
    path = tmp_path / "loose.mid"
    path.write_bytes(midi)

    loaded = load_midi_file(path)

    assert loaded.tracks[0][0] == mido.Message(
        "note_on", channel=0, note=60, velocity=127, time=0,
    )
    assert "clamping them to 127" in capsys.readouterr().err


def test_load_midi_file_does_not_hide_other_parse_errors(tmp_path):
    path = tmp_path / "not-midi.mid"
    path.write_bytes(b"not a MIDI file")

    try:
        load_midi_file(path)
    except OSError as exc:
        assert "MThd not found" in str(exc)
    else:
        raise AssertionError("invalid file unexpectedly loaded")
