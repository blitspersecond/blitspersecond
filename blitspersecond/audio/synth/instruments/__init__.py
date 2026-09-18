"""Ready-made percussion voices, built from the public operator vocabulary.

**These are presets, not implementations.** Every one is an ordinary
`SourceStage` running a Program of operators anybody could have assembled;
there is no instrument runtime, no channel number, no voice stealing and no
velocity. Call a factory twice and you have two independent voices, because
a Stage IS the voice -- which of them receives a hit is the game's policy,
never the engine's.

Three roots and seven tunings, ten callables in all:

    drum        Pulse -> Resonator -> Tanh -> LowPass -> Gain   kick
    noise_drum  Noise -> BandPass -> Envelope -> Tanh -> Gain   snare, clap, shaker
    metal_drum  Metal -> BandPass -> Tanh -> Envelope -> Gain   hat, cowbell, cymbal

The register surface of each is just the raw vocabulary of the operators
inside it -- `gate`, `frequency`, `cutoff`, `level` and so on. Ergonomic
instrument knobs are a later pass, and would be a facade over exactly these.

A hit retriggers a level-gated voice with two writes at one tick:

    events.extend([
        (at, voice, {"gate": False, "level": accent}),
        (at, voice, {"gate": True}),
    ])
"""

from .drums import drum, kick
from .metal import COWBELL_BANK, METAL_BANK, cowbell, cymbal, hat, metal_drum
from .noise import clap, noise_drum, shaker, snare

__all__ = (
    "COWBELL_BANK",
    "METAL_BANK",
    "clap",
    "cowbell",
    "cymbal",
    "drum",
    "hat",
    "kick",
    "metal_drum",
    "noise_drum",
    "shaker",
    "snare",
)
