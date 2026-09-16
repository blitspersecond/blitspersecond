"""Pulse-excited resonant drums: a beater striking a tuned body."""

from ..engine import AudioEngine
from ..operators import Gain, LowPass, Pulse, Resonator, Tanh
from ..program import Program


def drum(
    engine: AudioEngine,
    frequency: float = 120.0,
    *,
    decay: float = 0.035,
    tone: float = 1_800.0,
    drive: float = 1.5,
    level: float = 1.0,
):
    """The resonant root: kicks, toms, rims and claves are all this tuned.

    `Pulse` is the beater and `Resonator` is the skin. `Tanh` supplies the
    thickening a hard-struck drum gets from its own non-linearity, and the
    `LowPass` takes off the click that the pulse leaves behind.
    """
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
    """`drum` tuned low and long: a deep body with the top rolled well off."""
    return drum(
        engine, frequency, decay=decay, tone=tone, drive=drive, level=level
    )
