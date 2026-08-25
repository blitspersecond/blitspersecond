"""Envelope-shaped noise percussion presets."""

from ..engine import AudioEngine, Program
from ..operators import BandPass, Echo, Envelope, Gain, Noise, Tanh


def noise_drum(
    engine: AudioEngine,
    *,
    seed: int = 0,
    colour: float = 2_400.0,
    resonance: float = 0.65,
    attack: float = 0.0005,
    decay: float = 0.13,
    drive: float = 1.8,
    level: float = 1.0,
):
    """Build the shaped-noise root used for snares and shakers."""
    return engine.source(
        Program(
            lambda: Noise(seed),
            lambda: BandPass(colour, q=resonance),
            lambda: Envelope(attack, 0.0, decay, 0.0, 0.0),
            lambda: Tanh(drive),
            lambda: Gain(level),
        )
    )


def snare(engine: AudioEngine, *, seed: int = 0, level: float = 1.0):
    """Build the compact bright-noise snare tuning."""
    return noise_drum(
        engine,
        seed=seed,
        colour=2_400.0,
        resonance=0.65,
        attack=0.0005,
        decay=0.13,
        drive=1.8,
        level=level,
    )


def clap(engine: AudioEngine, *, seed: int = 1, level: float = 1.0):
    """Build a short noise strike with a compact repeating burst train."""
    return engine.source(
        Program(
            lambda: Noise(seed),
            lambda: BandPass(1_450.0, q=0.62),
            lambda: Envelope(0.0003, 0.0, 0.13, 0.0, 0.0),
            lambda: Tanh(1.8),
            lambda: Echo(800, feedback=0.62, mix=0.78),
            lambda: Gain(level),
        )
    )


def shaker(engine: AudioEngine, *, seed: int = 2, level: float = 1.0):
    """Build the short, high-colour tuning of :func:`noise_drum`."""
    return noise_drum(
        engine,
        seed=seed,
        colour=7_200.0,
        resonance=0.62,
        attack=0.0004,
        decay=0.055,
        drive=1.35,
        level=level,
    )
