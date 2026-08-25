"""Synthetic fixtures extracted from the Wipeout countdown experiments."""

import numpy as np

from blitspersecond.audio.common import FRAME_SIZE, SAMPLE_RATE
from blitspersecond.audio.engine import AudioEngine, Program
from blitspersecond.audio.operators import Delay, Envelope, Gain, PCM, Sine
from blitspersecond.resources import SoundSpec


def _sound(name: str, frequency: float) -> SoundSpec:
    samples = np.arange(FRAME_SIZE * 4)
    data = 0.35 * np.sin(2.0 * np.pi * frequency * samples / SAMPLE_RATE)
    return SoundSpec(name, data, SAMPLE_RATE)


COUNTDOWN = tuple(_sound(f"countdown-{n}", 220.0 + n * 55.0) for n in range(4))
MISSILE = _sound("missile", 660.0)


def build_demo(engine: AudioEngine) -> None:
    events = []
    spoken = engine.composite()
    for beat, sound in enumerate(COUNTDOWN):
        voice = engine.source(Program(lambda sound=sound: PCM(sound), lambda: Gain(1.25)))
        spoken.connect(voice)
        events.append((beat * SAMPLE_RATE, voice, {"gate": True}))

    beep = engine.source(
        Program(Sine, lambda: Envelope(0.001, 0.0, 0.12, 0.0, 0.01), lambda: Gain(0.25))
    )
    for beat in range(4):
        timestamp = beat * SAMPLE_RATE
        frequency = 1_760.0 if beat == 3 else 880.0
        events.append((timestamp, beep, {"frequency": frequency, "gate": True}))
        events.append((timestamp + round(0.2 * SAMPLE_RATE), beep, {"gate": False}))

    echo = engine.composite(Program(lambda: Delay(round(0.28 * SAMPLE_RATE))))
    echo.connect(spoken)
    events.append((0, echo, spoken, {"level": 0.35}))

    output = engine.mixing_desk()
    output.connect(spoken)
    output.connect(beep)
    output.connect(echo)
    engine.output = output
    engine.schedule(events)


def build_engine(engine: AudioEngine):
    countdown = []
    dry = engine.composite()
    for sound in COUNTDOWN:
        voice = engine.source(Program(lambda sound=sound: PCM(sound), lambda: Gain(1.25)))
        countdown.append(voice)
        dry.connect(voice)

    beep = engine.source(
        Program(Sine, lambda: Envelope(0.001, 0.0, 0.12, 0.0, 0.01), lambda: Gain(0.25))
    )
    missile = engine.source(Program(lambda: PCM(MISSILE), lambda: Gain(1.2)))
    dry.connect(missile)

    echo = engine.composite(Program(lambda: Delay(round(0.28 * SAMPLE_RATE))))
    echo.connect(dry)
    output = engine.mixing_desk()
    output.connect(dry)
    output.connect(beep)
    output.connect(echo)
    engine.output = output
    engine.schedule([(0, echo, dry, {"level": 0.35})])
    return tuple(countdown), beep, missile
