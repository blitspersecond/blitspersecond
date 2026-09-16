"""`Sample`: a recording, decoded once and shared by everything playing it."""

import wave
from os.path import abspath
from typing import Optional

from numpy import asarray, float32, frombuffer, isfinite, ndarray

from blitspersecond.common import CacheableResource


class Sample(CacheableResource):
    """Decoded mono samples at their own rate, ready for `PCM` to play.

        shout = Sample("yeah1.wav")              # decoded from disk
        bleep = Sample("bleep", data, 8_000)     # from an array in hand

    **Immutable, and that is what makes it shareable.** The array is read-only
    from load, so every Stage playing one file reads the same memory and none of
    them has to know they are sharing. That is also why this interns without
    offering a `copy()`: there is nothing here to escape from, since a second
    identical `Sample` would differ in no way a program could observe.

    **The recording keeps its own rate**, and `PCM` resamples as it plays --
    resampling at load would fix the pitch at unity forever. `rate` is therefore
    NOT `SAMPLE_RATE`, and keeping those two apart is the point of storing it.

    **Mono, by folding.** A stereo file is averaged on the way in, because the
    graph is mono until the mixing desk and a recording carrying its own image
    could not be placed.

    The file profile, interning and playback: `bps/reference/audio/sample.md`.
    """

    _filename: str
    _data: ndarray
    _rate: int

    @classmethod
    def _cache_key(
        cls,
        source: str,
        data: Optional[ndarray] = None,
        rate: Optional[int] = None,
    ) -> Optional[str]:
        # A file has identity; an array you built does not. Two callers
        # naming one path mean the same recording. Two callers handing over
        # samples mean two recordings, however alike.
        return cls._path_key(source) if data is None else None

    def _load(
        self,
        source: str,
        data: Optional[ndarray] = None,
        rate: Optional[int] = None,
    ) -> None:
        if data is None:
            if rate is not None:
                raise TypeError(
                    "Sample(path) reads the rate from the file -- pass data "
                    "as well to declare one"
                )
            self._filename = abspath(source)
            samples, rate = _decode(self._filename)
        else:
            if rate is None:
                raise TypeError("Sample(name, data, rate) needs a rate")
            self._filename = source
            samples = asarray(data, dtype=float32).copy()

        if samples.ndim != 1 or samples.size == 0:
            raise ValueError(f"{source}: a Sample must hold a non-empty mono array")
        if not isfinite(samples).all():
            raise ValueError(f"{source}: sample data has non-finite values")
        if int(rate) <= 0:
            raise ValueError(f"{source}: sample rate must be positive")

        samples.setflags(write=False)
        self._data = samples
        self._rate = int(rate)

    @property
    def filename(self) -> str:
        """Where it came from, absolute, or the name it was minted under."""
        return self._filename

    @property
    def data(self) -> ndarray:
        """The samples, mono float32, read-only and shared."""
        return self._data

    @property
    def rate(self) -> int:
        """The recording's own rate in Hz.

        **Not `SAMPLE_RATE`**, and keeping the two apart is the whole point of
        storing it: this is what the file was recorded at, and `PCM` resamples
        from here onto the engine's rate as it plays.
        """
        return self._rate

    @property
    def duration(self) -> float:
        """How long it plays for in seconds, at unity speed."""
        return len(self._data) / self._rate

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return (
            f"Sample({self._filename!r}, {len(self._data)} samples "
            f"@ {self._rate}Hz, {self.duration:.2f}s)"
        )


def _decode(filename: str) -> tuple[ndarray, int]:
    """One 16-bit WAV from disk, folded to mono float32."""
    with wave.open(filename, "rb") as wav:
        if wav.getsampwidth() != 2:
            raise ValueError(
                f"{filename}: only 16-bit WAV is supported, this is "
                f"{wav.getsampwidth() * 8}-bit"
            )
        channels = wav.getnchannels()
        raw = frombuffer(wav.readframes(wav.getnframes()), "<i2")
        rate = wav.getframerate()
    # 32767 rather than 32768, so full-scale positive is exactly 1.0 and the
    # bottom code lands a hair past -1.0. The asymmetry is in two's complement
    # and cannot be removed, only placed: dividing by 32768 puts it on the
    # loudest positive sample instead, a step short of unity.
    samples = raw.reshape(-1, channels).mean(axis=1).astype(float32) / 32767.0
    return samples, rate
