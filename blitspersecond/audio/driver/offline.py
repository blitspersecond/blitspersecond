"""Unpaced AudioEngine rendering and mono or stereo WAV output."""

import math
import wave
from numbers import Real

from numpy import clip, empty, float32

from ..common import FRAME_SIZE, SAMPLE_RATE
from ..engine import AudioEngine


def render(engine: AudioEngine, seconds: float):
    """Demand complete Frames as quickly as possible for ``seconds``.

    The result, like the engine clock, always spans complete engine Frames.
    A duration between Frame boundaries is therefore rounded upward.
    """
    if not isinstance(engine, AudioEngine):
        raise TypeError(f"expected AudioEngine, got {type(engine)}")
    if not isinstance(seconds, Real) or isinstance(seconds, bool):
        raise TypeError("duration must be a real number of seconds")
    seconds = float(seconds)
    if not math.isfinite(seconds) or seconds < 0.0:
        raise ValueError("duration must be finite and non-negative")
    if engine._running:
        raise RuntimeError("AudioEngine is already driven")

    frame_count = math.ceil(seconds * SAMPLE_RATE / FRAME_SIZE)
    channels = engine.output.channels
    shape = (
        (frame_count * FRAME_SIZE,)
        if channels == 1
        else (frame_count * FRAME_SIZE, channels)
    )
    output = empty(shape, dtype=float32)
    engine._running = True
    try:
        for index in range(frame_count):
            start = index * FRAME_SIZE
            output[start : start + FRAME_SIZE] = engine.advance().current.data
    finally:
        engine._running = False
    return output


def write_wav(path, samples):
    """Write mono or stereo floats as clipped signed 16-bit PCM."""
    if samples.ndim == 1:
        channels = 1
    elif samples.ndim == 2 and samples.shape[1] == 2:
        channels = 2
    else:
        raise ValueError("WAV output expects mono or two-channel stereo samples")
    frames = (clip(samples, -1.0, 1.0) * 32_767).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(frames.tobytes())


def render_wav(engine: AudioEngine, path, seconds: float):
    """Render complete engine Frames, write them to ``path``, and return them."""
    samples = render(engine, seconds)
    write_wav(path, samples)
    return samples
