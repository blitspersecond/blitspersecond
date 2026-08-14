"""Rectangular-bank metallic percussion presets."""

from ..engine import AudioEngine, Program
from ..operators import BandPass, Envelope, Gain, Metal, Tanh

METAL_BANK = (211.0, 307.0, 373.0, 509.0, 557.0, 823.0)
COWBELL_BANK = (557.0, 823.0)


def metal_drum(
    engine: AudioEngine,
    frequencies=METAL_BANK,
    *,
    duty: float = 0.48,
    colour: float = 6_500.0,
    resonance: float = 1.35,
    attack: float = 0.0005,
    decay: float = 0.045,
    drive: float = 1.7,
    level: float = 1.0,
):
    """Build the metallic root used for hats, bells and cymbals."""
    return engine.source(
        Program(
            lambda: Metal(frequencies, duty=duty),
            lambda: BandPass(colour, q=resonance),
            lambda: Tanh(drive),
            lambda: Envelope(attack, 0.0, decay, 0.0, 0.0),
            lambda: Gain(level),
        )
    )


def hat(
    engine: AudioEngine,
    openness: float = 0.0,
    *,
    level: float = 1.0,
):
    """Build one metallic hat tuning from closed through open."""
    openness = float(openness)
    if not 0.0 <= openness <= 1.0:
        raise ValueError("openness must be between 0 and 1")
    return metal_drum(
        engine,
        colour=6_500.0 - 400.0 * openness,
        resonance=1.35 - 0.15 * openness,
        attack=0.0005 + 0.0005 * openness,
        decay=0.045 + 0.435 * openness,
        drive=1.7 - 0.2 * openness,
        level=level,
    )


def cowbell(engine: AudioEngine, *, level: float = 1.0):
    """Build the two-oscillator bell tuning of :func:`metal_drum`."""
    return metal_drum(
        engine,
        COWBELL_BANK,
        duty=0.47,
        colour=760.0,
        resonance=0.90,
        attack=0.001,
        decay=0.16,
        drive=1.35,
        level=level,
    )


def cymbal(engine: AudioEngine, *, level: float = 1.0):
    """Build the long bright tuning of :func:`metal_drum`."""
    return metal_drum(
        engine,
        colour=5_800.0,
        resonance=1.45,
        attack=0.001,
        decay=0.80,
        drive=1.4,
        level=level,
    )
