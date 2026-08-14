import numpy as np

from blitspersecond.audio.common import FRAME_SIZE, SAMPLE_RATE
from blitspersecond.audio.engine import AudioEngine
from tests.examples.audio_race import build_demo, build_engine


def test_race_countdown_builds_and_renders_all_four_beats():
    engine = AudioEngine()
    build_demo(engine)

    frames = 4 * SAMPLE_RATE // FRAME_SIZE
    samples = np.concatenate(
        [engine.advance().current.data.copy() for _ in range(frames)]
    )

    assert engine.clock == 4 * SAMPLE_RATE
    assert np.all(np.isfinite(samples))
    for beat in range(4):
        second = samples[beat * SAMPLE_RATE : (beat + 1) * SAMPLE_RATE]
        assert np.max(np.abs(second)) > 0.1


def test_engine_race_missile_restarts_through_live_schedule():
    engine = AudioEngine()
    _countdown, _beep, missile = build_engine(engine)
    trigger = [
        (0, missile, {"gate": False}),
        (0, missile, {"gate": True}),
    ]

    engine.schedule(trigger)
    first = engine.advance().current.data.copy()
    engine.schedule(trigger)
    restarted = engine.advance().current.data.copy()

    assert np.max(np.abs(first)) > 0.001
    assert np.array_equal(restarted, first)
