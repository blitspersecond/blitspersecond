"""The synthesis graph: what makes a sample, and what decides when.

A graph of `Stage`s, pulled once per 400-sample block from whichever one is
terminal. Demand walks depth-first from there, so a Stage nothing listens to
costs nothing, and a Stage several things listen to renders once and is
cached. Nothing here pushes: a block exists because something asked for it.

**A Stage is a place in the graph; a Program is what it does there.** The
Program is a list of operator factories, the Stage binds one instance of each
for itself, and the same Program can build a hundred voices that share no
state. Arity is declared by inheritance -- `Operator` takes one lane,
`Compositor` takes many and heads a chain.

**Control is timestamped writes, never setters.** A Stage declares registers;
`AudioEngine.schedule()` takes a relative timeline of writes against them; an
operator is handed this block's writes with its settled values and cuts the
block at each one. That is why a note can start between two samples rather
than on a block boundary, and why nothing in a game ever holds an audio
object it has to poke.

**Everything in here is mono until the mixing desk.** Position is a `pan` on
a desk strip rather than something an operator carries, so one vocabulary
serves every voice and a recording cannot arrive with an image baked into it.

Nothing in this package knows about a device, a file, or a clock other than
the sample count `AudioEngine` keeps. The things that pull it -- `Driver`
live, `render` offline -- are its neighbours in `blitspersecond.audio`.
"""

from .block import Block, BlockPool, StereoBlock
from .bus import Bus, StereoBus
from .engine import AudioEngine
from .mixing_desk import MixingDesk
from .program import Program
from .registers import RegisterScope, RegisterSpec, RegisterWrite
from .stage import Stage
from .stages import ApplyStage, CompositeStage, SourceStage

__all__ = (
    "ApplyStage",
    "AudioEngine",
    "Block",
    "BlockPool",
    "Bus",
    "CompositeStage",
    "MixingDesk",
    "Program",
    "RegisterScope",
    "RegisterSpec",
    "RegisterWrite",
    "SourceStage",
    "Stage",
    "StereoBlock",
    "StereoBus",
)
