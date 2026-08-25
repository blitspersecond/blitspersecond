from ..common import Bus
from .program import Program
from .stage import Stage, StageEngine


class SourceStage(Stage):
    """Run one local source Program, or produce silence when it is empty."""

    def __init__(self, engine: StageEngine, program: Program | None = None):
        super().__init__(engine)
        self._bus = Bus(engine.pool)
        self._program = (program if program is not None else Program()).wire(self)

    def _render(self) -> Bus:
        self._bus.clear()
        if not self._program:
            return self._compositor.process()
        return self._compositor.process(self._program.process(self._bus))
