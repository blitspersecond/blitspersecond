"""Metallic percussion: a bank of inharmonic squares, filtered and gated."""

from ..engine import AudioEngine
from ..operators import BandPass, Envelope, Gain, Metal, Tanh
from ..program import Program

#: The TR-808's six square oscillators, moved to the nearest useful primes.
#: Werner, Abel and Smith measured the originals at 205.3, 304.4, 369.6 and
#: 522.7 Hz, with the two band-pass sections centred at 540 and 800 -- see
#: *A Physically-Informed, Circuit-Bendable, Digital Model of the Roland
#: TR-808 Cymbal*, DAFx-14.
#:
#: **Primes, so the bank is pairwise coprime -- originally so that it could
#: be pregenerated.** A set of whole-number frequencies repeats every
#: `1 / gcd` seconds; a common factor of 100 would buzz a hundred times a
#: second. With a gcd of 1 the period is exactly one second, which means one
#: second of it can be generated once at preparation time and read back
#: forever instead of six oscillators running per block.
#:
#: Measured: after exactly one second all six phases return to zero within
#: 7e-15, and the rendered audio differs by one sample in 48,000 where a
#: single square edge falls either side of the sample grid. The engine does
#: not yet take the table; see the board card.
#:
#: The musical consequence comes free either way -- the longest preset here
#: rings for 0.8s, so no strike ever contains a repeat.
METAL_BANK = (211.0, 307.0, 373.0, 509.0, 557.0, 823.0)

#: The same idea with almost all of it taken away. Two partials is few enough
#: that the ear finds an interval between them, which is why a cowbell has a
#: note where a cymbal does not. Still coprime, so still a one-second period.
COWBELL_BANK = (557.0, 823.0)


def metal_drum(
    engine: AudioEngine,
    frequencies=METAL_BANK,
    *,
    duty: float = 0.48,
    tone: float = 6_500.0,
    resonance: float = 1.35,
    attack: float = 0.0005,
    decay: float = 0.045,
    drive: float = 1.7,
    level: float = 1.0,
):
    """The metallic root: hats, bells and cymbals are this at three tunings.

    Note the envelope comes AFTER the saturation here, unlike `noise_drum`.
    Driving a steady bank and then gating it keeps every strike's attack
    identical; gating first and then driving would make a quiet hit a
    different shape from a loud one.
    """
    return engine.source(
        Program(
            lambda: Metal(frequencies, duty=duty),
            lambda: BandPass(tone, q=resonance),
            lambda: Tanh(drive),
            lambda: Envelope(attack, 0.0, decay, 0.0, 0.0),
            lambda: Gain(level),
        )
    )


def hat(engine: AudioEngine, openness: float = 0.0, *, level: float = 1.0):
    """One hat, anywhere from closed to open.

    `openness` moves six tunings together -- longer decay, softer attack,
    darker tone, less drive -- because that is what opening a hi-hat
    actually does to the sound, not just how long it lasts.
    """
    openness = float(openness)
    if not 0.0 <= openness <= 1.0:
        raise ValueError("openness must be between 0 and 1")
    return metal_drum(
        engine,
        tone=6_500.0 - 400.0 * openness,
        resonance=1.35 - 0.15 * openness,
        attack=0.0005 + 0.0005 * openness,
        decay=0.045 + 0.435 * openness,
        drive=1.7 - 0.2 * openness,
        level=level,
    )


def cowbell(engine: AudioEngine, *, level: float = 1.0):
    """Two partials, a narrow band low down, and a long-ish decay."""
    return metal_drum(
        engine,
        COWBELL_BANK,
        duty=0.47,
        tone=760.0,
        resonance=0.90,
        attack=0.001,
        decay=0.16,
        drive=1.35,
        level=level,
    )


def cymbal(engine: AudioEngine, *, level: float = 1.0):
    """The full bank, bright, and ringing for most of a second."""
    return metal_drum(
        engine,
        tone=5_800.0,
        resonance=1.45,
        attack=0.001,
        decay=0.80,
        drive=1.4,
        level=level,
    )
