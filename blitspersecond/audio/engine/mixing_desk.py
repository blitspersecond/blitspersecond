from ..common.bus import StereoBus
from ..operators.pan import _Pan
from .connection import _Connection
from .stage import Stage, StageEngine


class _DeskConnection(_Connection):
    """One MixingDesk strip: structural Gain followed by stereo Pan."""

    def __init__(self, stage: Stage, source: Stage):
        super().__init__(stage, source)
        self.pan = _Pan()
        self._declare_registers(self.pan.registers)
        self.stereo = StereoBus()

    def process_stereo(self) -> StereoBus:
        mono = super().process()
        self.control(self.pan)
        output = self.pan.route(mono, self.stereo)
        self._quiescent = output.current.quiescent
        return output


class MixingDesk(Stage):
    """Terminal stereo Stage with one level-and-pan strip per mono input."""

    channels = 2

    def __init__(self, engine: StageEngine):
        super().__init__(engine)
        self._connections: list[_DeskConnection] = []

    def _make_output(self) -> StereoBus:
        return StereoBus()

    def connect(self, stage: Stage):
        if stage._engine is not self._engine:
            raise ValueError("cannot connect a Stage from another AudioEngine")
        for connection in self._connections:
            if connection.source is stage:
                return
        self._connections.append(_DeskConnection(self, stage))
        self._wake()

    def disconnect(self, stage: Stage):
        for connection in self._connections:
            if connection.source is stage:
                self._connections.remove(connection)
                connection.release()
                self._wake()
                break

    def _connection(self, source: Stage) -> _DeskConnection:
        for connection in self._connections:
            if connection.source is source:
                return connection
        raise ValueError("Stage has no input connected from the given Stage")

    def _render(self) -> StereoBus:
        inputs = []
        for connection in self._connections:
            if connection.quiescent:
                continue
            output = connection.process_stereo()
            if not output.current.quiescent:
                inputs.append(output)
        return self._compositor.process(*inputs)

    def _inputs_quiescent(self) -> bool:
        return all(connection.quiescent for connection in self._connections)
