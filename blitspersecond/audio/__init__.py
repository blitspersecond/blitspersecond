"""Sound. This is where the simple API will live.

**Alpha, and the empty space here is the point.** The machinery is settled --
the block grid, the pull graph, the operator vocabulary -- but the surface a
GAME reaches for is not built yet. Today a game constructs voices and
schedules register writes at sample offsets, which is the right level for
building an instrument and the wrong level for playing a sound effect. That
layer waits for a real musical test case rather than being guessed at; see the
board.

So this module is deliberately thin, and everything underneath it has been put
one level down to keep it that way. What ends up at this level is what a game
says, not what the engine does to say it.

## What is underneath, and why it is underneath

`synth` is the graph: stages, operators, buses, registers, and the engine that
pulls them. It knows nothing about devices or files. Building an instrument
means reaching in here, which is exactly the reaching the missing API exists
to remove.

`transport` is everything that carries blocks out of the graph -- PortAudio,
the worker thread, the per-platform device choice, and the offline renderer,
which is the same job with a file on the end of it instead of a sound card.
**None of it is user-side**: a game is handed an `AudioEngine` that is already
being driven and never opens a stream or picks a device.

The seam between those two is one method. A transport asks the graph for a
block with `advance()` and writes it somewhere; that is the whole of its
dependency on synthesis.

`Sound` and `constants` stay at this level because a caller genuinely holds
them: a recording is loaded by a game and read by a waveform display as
readily as by `PCM`, and the sample rate is a number the device and the graph
have to agree on. Neither is machinery.

## The thread, and the clock

Audio is the one subsystem that runs on its own thread, deliberately. The
output stream's blocking write is what paces synthesis, and the thread is
what keeps it off the sixteen milliseconds the simulation and the picture
share.

It is also the one place with a clock the engine does not tell the time by.
The sample clock counts what the device has actually consumed; the game
counts frames. They stay apart -- nothing in a game reads the sample clock,
and anything needing wall time asks the engine for it.
"""

from .constants import BLOCK_SIZE, SAMPLE_RATE
from .sound import Sound
from .synth import (
    ApplyStage,
    AudioEngine,
    CompositeStage,
    MixingDesk,
    Program,
    RegisterScope,
    RegisterSpec,
    RegisterWrite,
    SourceStage,
    Stage,
)

__all__ = (
    "ApplyStage",
    "AudioEngine",
    "BLOCK_SIZE",
    "CompositeStage",
    "MixingDesk",
    "Program",
    "RegisterScope",
    "RegisterSpec",
    "RegisterWrite",
    "SAMPLE_RATE",
    "Sound",
    "SourceStage",
    "Stage",
)
