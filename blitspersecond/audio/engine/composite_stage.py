from ..common import Bus
from .connection import _Connection
from .program import Program
from .stage import Stage, StageEngine


class CompositeStage(Stage):
    """Process connected Stages through parallel Programs, then composite."""

    def __init__(self, engine: StageEngine, program: Program | None = None):
        super().__init__(engine)
        self._connections: list[_Connection] = []
        self._program = program if program is not None else Program()
        self._program.declare(self)

    def connect(self, stage: Stage):
        if stage._engine is not self._engine:
            raise ValueError("cannot connect a Stage from another AudioEngine")
        for connection in self._connections:
            if connection.source is stage:
                return
        connection = _Connection(self, stage, self._program)
        self._connections.append(connection)
        self._wake()

    def disconnect(self, stage: Stage):
        for connection in self._connections:
            if connection.source is stage:
                self._connections.remove(connection)
                connection.release()
                self._wake()
                break

    def _connection(self, source: Stage) -> _Connection:
        for connection in self._connections:
            if connection.source is source:
                return connection
        raise ValueError("Stage has no input connected from the given Stage")

    def _render(self) -> Bus:
        inputs = []
        for connection in self._connections:
            if connection.quiescent:
                continue
            output = connection.process()
            if not output.current.quiescent:
                inputs.append(output)
        return self._compositor.process(*inputs)

    def _inputs_quiescent(self) -> bool:
        return all(connection.quiescent for connection in self._connections)
