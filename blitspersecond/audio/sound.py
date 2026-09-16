"""`Sound`: a recorded sample, decoded once and shared by everything playing it."""

import wave
from os.path import abspath
from typing import Optional

from numpy import asarray, float32, frombuffer, isfinite, ndarray

from blitspersecond.common import CacheableResource


class Sound(CacheableResource):
    """Decoded mono samples at their own rate, ready for `PCM` to play.

    Two roads into one constructor, told apart by whether samples come with
    it:

        shout = Sound("yeah1.wav")                 # decoded from disk
        bleep = Sound("bleep", samples, 8_000)     # from an array in hand

    **Immutable, and that is what makes it shareable.** The array is marked
    read-only at load and nothing here can change it, so every Stage playing
    the same file reads the same memory and no one has to know they are
    sharing. `PCM` indexes straight into it at sample rate rather than taking
    a copy per voice.

    That immutability is also why this interns without offering a `copy()`.
    A `PixelBuffer` needs one because a caller may want to draw into their own
    pixels, and interning without an escape would make that impossible. There
    is nothing here to escape from -- a second identical `Sound` would differ
    from the first in no way a program could observe, so handing back the
    first is not a compromise. What interning buys is the decode: naming a
    file in ten places decodes it once.

    **The sample keeps its own rate.** A 22 kHz recording is stored at 22 kHz
    and resampled by `PCM` as it plays, because that is where the speed
    register lives -- resampling at load would fix the pitch at unity forever
    and cost memory to do it.

    **Mono, by folding.** A stereo file is averaged down on the way in. The
    graph is mono until the mixing desk, where position is a `pan` on a Stage
    rather than something baked into a recording; a sample carrying its own
    stereo image could not be placed.
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
                    "Sound(path) reads the rate from the file -- pass samples "
                    "as well to declare one"
                )
            self._filename = abspath(source)
            samples, rate = _decode(self._filename)
        else:
            if rate is None:
                raise TypeError("Sound(name, samples, rate) needs a rate")
            self._filename = source
            samples = asarray(data, dtype=float32).copy()

        if samples.ndim != 1 or samples.size == 0:
            raise ValueError(f"{source}: sound data must be a non-empty mono array")
        if not isfinite(samples).all():
            raise ValueError(f"{source}: sound data has non-finite samples")
        if int(rate) <= 0:
            raise ValueError(f"{source}: sound sample rate must be positive")

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
        """The recording's own sample rate in Hz, not the engine's."""
        return self._rate

    @property
    def duration(self) -> float:
        """How long it plays for in seconds, at unity speed."""
        return len(self._data) / self._rate

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return (
            f"Sound({self._filename!r}, {len(self._data)} samples "
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
