import numpy as np
import pytest

from blitspersecond.audio.common import SAMPLE_RATE
from blitspersecond.audio.driver import render
from blitspersecond.audio.engine import AudioEngine
from tests.examples.audio_gordon import NOTES, SUB, build, ticks


def test_gordon_input_is_the_scored_raw_sub_with_silent_gaps():
    engine = AudioEngine()
    source = build(engine)

    samples = render(engine, NOTES[1][0])
    first = samples[: ticks(NOTES[0][1])]
    gap = samples[ticks(NOTES[0][1]) : ticks(NOTES[1][0])]
    spectrum = np.abs(np.fft.rfft(first * np.hanning(len(first))))
    frequencies = np.fft.rfftfreq(len(first), 1.0 / SAMPLE_RATE)

    assert source.registers["frequency"] == SUB
    assert frequencies[np.argmax(spectrum)] == pytest.approx(SUB, abs=2.5)
    assert np.max(np.abs(first)) == pytest.approx(0.9)
    assert np.all(gap == 0.0)


def test_gordon_front_adds_audible_upper_content_but_preserves_the_score():
    raw_engine = AudioEngine()
    build(raw_engine)
    raw = render(raw_engine, NOTES[1][0])
    front_engine = AudioEngine()
    build(front_engine, through_front=True)
    front = render(front_engine, NOTES[1][0])

    note_end = ticks(NOTES[0][1])
    next_note = ticks(NOTES[1][0])
    raw_spectrum = np.abs(np.fft.rfft(raw[:note_end]))
    front_spectrum = np.abs(np.fft.rfft(front[:note_end]))
    frequencies = np.fft.rfftfreq(note_end, 1.0 / SAMPLE_RATE)
    upper = frequencies >= 1_000.0

    assert np.sum(front_spectrum[upper]) > np.sum(raw_spectrum[upper]) * 10
    # The low-pass and sample hold have a short, real state tail after the
    # scheduled gain closes; the rest of the score gap remains silent.
    assert np.any(front[note_end : note_end + 30] != 0.0)
    assert np.all(front[note_end + 30 : next_note] == 0.0)


def test_gordon_array_reveals_deterministic_grime_and_effect_tails_in_the_gap():
    outputs = []
    for _ in range(2):
        engine = AudioEngine()
        build(engine, through_array=True)
        outputs.append(render(engine, NOTES[1][0]))

    note_end = ticks(NOTES[0][1])
    next_note = ticks(NOTES[1][0])
    gap = outputs[0][note_end + 30 : next_note]

    assert np.array_equal(outputs[0], outputs[1])
    assert np.sqrt(np.mean(gap**2)) > 0.001
