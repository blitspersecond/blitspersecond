"""SoundSpec: a pure description of decoded audio for the sampler.

Mirrors ImageSpec: no cache identity, no device state — just the decoded
samples and their native rate. ResourceManager.sound() memo-caches these per
file; PCM reads the shared array in place (it is marked read-only), so one
decode serves every Stage that plays the sample.
"""

from __future__ import annotations

import wave

import numpy as np


class SoundSpec:
    def __init__(self, filename: str, data: np.ndarray, sr: int) -> None:
        data = np.asarray(data, dtype=np.float64)
        if data.ndim != 1 or len(data) == 0:
            raise ValueError("sound data must be a non-empty mono array")
        if not np.all(np.isfinite(data)):
            raise ValueError("sound data must contain only finite samples")
        sr = int(sr)
        if sr <= 0:
            raise ValueError("sound sample rate must be positive")
        data.setflags(write=False)  # shared read-only, like a cached ImageSpec
        self._filename = filename
        self._data = data
        self._sr = sr

    @property
    def filename(self) -> str:
        return self._filename

    @property
    def data(self) -> np.ndarray:
        return self._data

    @property
    def sr(self) -> int:
        return self._sr

    @property
    def duration(self) -> float:
        return len(self._data) / self._sr

    def __repr__(self) -> str:
        return (f"SoundSpec({self._filename!r}, {len(self._data)} samples "
                f"@ {self._sr}Hz, {self.duration:.2f}s)")


def load_sound_spec(filename) -> SoundSpec:
    with wave.open(str(filename), "rb") as wav:
        if wav.getsampwidth() != 2:
            raise ValueError("only 16-bit WAV supported")
        raw = np.frombuffer(wav.readframes(wav.getnframes()), "<i2")
        data = raw.reshape(-1, wav.getnchannels()).mean(axis=1) / 32767.0
        return SoundSpec(str(filename), data, wav.getframerate())
