"""The synthesis graph: what makes a sample, and what decides when.

A graph of `Stage`s, pulled once per block from whichever one is terminal.
Nothing pushes: a block exists because something asked for it.

Four invariants everything here depends on, and none of them is configurable:

- **A Stage is a place in the graph; a Program is what it does there.** A
  Program stores zero-argument FACTORIES, so wiring one twice builds two
  chains that share no state. Handing one Operator to two lanes would have
  them share a filter's history and an oscillator's phase.
- **Control is timestamped writes, never setters.** An operator is handed this
  block's writes and cuts the block at each one.
- **Everything is mono until the mixing desk.** Position is a `pan` on a desk
  strip, so nothing upstream carries a second channel.
- **Arity is declared by inheritance.** `Operator` takes one lane;
  `Compositor` takes many and heads a chain.

Nothing in this package knows about a device, a file, or any clock other than
the sample count `AudioEngine` keeps.

Stages, lanes and the pull: `bps/reference/audio/graph.md`.
Registers and the timeline: `bps/reference/audio/control.md`.
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
