"""Sound. This is where the simple API will live.

**Alpha, and the empty space here is the point.** The machinery underneath is
settled; the surface a GAME reaches for is not built. Today a game constructs
voices and schedules register writes at sample offsets, which is the right
level for building an instrument and the wrong level for playing a sound
effect.

**So do not fill this module in passing.** What belongs at this level is what
a game says, not what the engine does to say it -- everything else has been
put one level down to keep the space clear, and the layer that goes here waits
for a real musical test case rather than being guessed at. See the board card
*Every audio caller writes its own voice allocator*.

`synth` is the graph and knows nothing about devices or files. `transport`
carries blocks out of it, and none of it is user-side: a game is handed an
`AudioEngine` that is already being driven. The seam between the two is one
method -- a transport asks for a block with `advance()`.

`Sample` and `constants` stay at this level because a caller genuinely holds
them, and neither is machinery.

The whole subsystem: `bps/reference/audio/audio.md`.
"""

from .constants import BLOCK_SIZE, SAMPLE_RATE
from .sample import Sample
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
    "Sample",
    "SourceStage",
    "Stage",
)
