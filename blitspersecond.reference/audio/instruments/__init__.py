"""Stage-native instrument presets built from public audio Operators."""

from .drums import drum, kick
from .metal import cowbell, cymbal, hat, metal_drum
from .noise import clap, noise_drum, shaker, snare

__all__ = [
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
]
