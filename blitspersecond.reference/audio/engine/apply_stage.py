from .connection import _Connection
from .program import Program
from .stage import Stage, StageEngine


class ApplyStage(Stage):
    """Apply one shared Program across all connected input lanes."""

    def __init__(self, engine: StageEngine, program: Program):
        super().__init__(engine)
        self._connections: list[_Connection] = []
        self._program = program.wire(self)

    def connect(self, stage: Stage):
        self._engine._assert_topology_mutable()
        if stage._engine is not self._engine:
            raise ValueError("cannot connect a Stage from another AudioEngine")
        for connection in self._connections:
            if connection.source is stage:
                return
        self._connections.append(_Connection(self, stage))
        self._wake()

    def disconnect(self, stage: Stage):
        self._engine._assert_topology_mutable()
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

    def _render(self):
        if not self._connections or not self._program:
            return self._compositor.process()
        inputs = [connection.process() for connection in self._connections]
        return self._compositor.process(self._program.process(*inputs))

    def _inputs_quiescent(self) -> bool:
        return all(connection.quiescent for connection in self._connections)
