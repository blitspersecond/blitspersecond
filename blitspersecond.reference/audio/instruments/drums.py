"""Pulse-excited resonant drum presets."""

from ..engine import AudioEngine, Program
from ..operators import Gain, LowPass, Pulse, Resonator, Tanh


def drum(
    engine: AudioEngine,
    frequency: float = 120.0,
    *,
    decay: float = 0.035,
    tone: float = 1_800.0,
    drive: float = 1.5,
    level: float = 1.0,
):
    """Build the resonant root used for kicks, toms, rims and claves."""
    return engine.source(
        Program(
            Pulse,
            lambda: Resonator(frequency, decay),
            lambda: Tanh(drive),
            lambda: LowPass(tone, q=0.65),
            lambda: Gain(level),
        )
    )


def kick(
    engine: AudioEngine,
    frequency: float = 56.0,
    *,
    decay: float = 0.068,
    tone: float = 420.0,
    drive: float = 2.0,
    level: float = 1.0,
):
    """Build the low, long-ringing tuning of :func:`drum`."""
    return drum(
        engine,
        frequency,
        decay=decay,
        tone=tone,
        drive=drive,
        level=level,
    )
