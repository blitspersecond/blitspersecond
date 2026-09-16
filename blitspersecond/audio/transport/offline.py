"""Rendering an engine as fast as it will go, and writing the result to disk.

Offline output is **another driver of the same boundary**, not a mode the
engine is put into. `render()` pulls `advance()` in a loop with nothing
pacing it; the engine cannot tell the difference and behaves identically, so
a graph checked here is the graph the device will play.

That is what makes it the testing surface. A render is deterministic, needs
no hardware, and finishes in a fraction of the time it represents -- so an
operator can be diffed sample by sample against an expectation, and
`render_wav()` puts the same audio somewhere you can listen to it, which is
the only way some faults are ever going to be noticed.
"""

import math
import wave
from numbers import Real

from numpy import clip, empty, float32, ndarray

from ..constants import BLOCK_SIZE, SAMPLE_RATE
from ..synth.engine import AudioEngine


def render(engine: AudioEngine, seconds: float) -> ndarray:
    """Demand complete blocks as quickly as possible, for `seconds` of audio.

    The result spans whole blocks, exactly as the engine clock does, so a
    duration landing between block boundaries rounds **up**. Asking for half
    a block gets you a whole one rather than a truncated engine state.

    Mono output comes back `(n,)` and a MixingDesk's comes back `(n, 2)`.
    Keeping a deliberately mono output mono is what lets a test look at one
    Operator's signal without a pan law in the way.
    """
    if not isinstance(engine, AudioEngine):
        raise TypeError(f"expected AudioEngine, got {type(engine)}")
    if not isinstance(seconds, Real) or isinstance(seconds, bool):
        raise TypeError("duration must be a real number of seconds")
    seconds = float(seconds)
    if not math.isfinite(seconds) or seconds < 0.0:
        raise ValueError("duration must be finite and non-negative")
    # The same claim a device Driver takes. One engine cannot be rendered and
    # played at once: both would be advancing the same clock and mutating the
    # same pool.
    if engine._running:
        raise RuntimeError("AudioEngine is already driven")

    blocks = math.ceil(seconds * SAMPLE_RATE / BLOCK_SIZE)
    channels = engine.output.channels
    shape = (
        (blocks * BLOCK_SIZE,)
        if channels == 1
        else (blocks * BLOCK_SIZE, channels)
    )
    output = empty(shape, dtype=float32)
    engine._running = True
    try:
        for index in range(blocks):
            start = index * BLOCK_SIZE
            output[start : start + BLOCK_SIZE] = engine.advance().current.data
    finally:
        # Released even if a graph raises mid-render, so a failed render does
        # not leave the engine permanently unplayable.
        engine._running = False
    return output


def write_wav(path, samples: ndarray):
    """Write mono or stereo floats as clipped signed 16-bit PCM."""
    if samples.ndim == 1:
        channels = 1
    elif samples.ndim == 2 and samples.shape[1] == 2:
        channels = 2
    else:
        raise ValueError("WAV output expects mono or two-channel stereo samples")
    # Clipped at the format's limit, never normalised. Sixteen-bit PCM has
    # no room above unity, so something has to happen there -- but rescaling
    # would quietly change the mix, and a signal driven past unity is a thing
    # an author may well be doing on purpose. `Tanh`, `Clip` and `Crush` are
    # in the vocabulary for exactly that. So the file gets what the graph
    # produced, squared off where the format runs out.
    # Truncation toward zero costs at most half an LSB, about -90 dBFS.
    frames = (clip(samples, -1.0, 1.0) * 32_767).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(frames.tobytes())


def render_wav(engine: AudioEngine, path, seconds: float) -> ndarray:
    """Render, write it to `path`, and hand the samples back for assertions."""
    samples = render(engine, seconds)
    write_wav(path, samples)
    return samples
