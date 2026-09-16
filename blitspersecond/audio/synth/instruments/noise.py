"""Shaped-noise percussion: a burst of hiss with a band and an envelope."""

from ..engine import AudioEngine
from ..operators import BandPass, Echo, Envelope, Gain, Noise, Tanh
from ..program import Program


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
    """The shaped-noise root: snares and shakers are this at two tunings.

    White noise has every frequency, so where the band sits IS the
    instrument; the envelope decides whether it reads as a hit or a hiss.
    Each preset takes its own `seed`, so two of them are audibly different
    instruments rather than the same noise twice.
    """
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
    """Bright and compact, centred where a snare's rattle lives."""
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
    """A snare with a very short, very fed-back echo on it.

    A hand clap is several hands slightly out of time, and that is what the
    `Echo` is doing: 800 ticks is about 17ms, too short to hear as a repeat
    and just long enough to smear one strike into a cluster.
    """
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
    """`noise_drum` up high and very short."""
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
