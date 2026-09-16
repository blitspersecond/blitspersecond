"""The concrete Stage forms: what a node does with its inputs and its Program.

Four shapes, distinguished only by the relationship between inputs and
Program. Everything else -- registers, caching, quiescence -- is `Stage`.

    SourceStage      no inputs; one local Program
    CompositeStage   any inputs; one Program instance PER lane
    ApplyStage       any inputs; one Program SHARED across lanes
    MixingDesk       any inputs; stereo terminal output (see mixing_desk)

The interesting pair is Composite against Apply. A Composite gives each lane
its own chain and sums the results, which is what an instrument wants: three
notes through three envelopes into one output. An Apply hands every lane at
once to a single chain's head operator, which is what a cross-input effect
needs: RingMod multiplying two signals, or Duck using one to push down
another. One is parallel, the other is a junction.
"""

from typing import Generic, TypeVar

from .bus import Bus, StereoBus
from .connection import _Connection
from .program import Program
from .stage import Stage, StageEngine

#: What kind of lane a given input Stage builds. A MixingDesk's strips carry
#: placement as well as level, and it must not have to cast its own lanes
#: back to what it built.
_ConnectionT = TypeVar("_ConnectionT", bound=_Connection)


class SourceStage(Stage):
    """A Stage that makes sound rather than changing it.

    Has no `connect()`, and that absence is deliberate: a source Program
    cannot silently become an effect Program because somebody wired an input
    into it. If you want inputs, you wanted a different Stage.

    With no Program it produces silence -- its compositor is given nothing,
    which is exactly what an unconnected node should sound like.
    """

    def __init__(self, engine: StageEngine, program: Program | None = None):
        super().__init__(engine)
        #: The working lane the Program runs on, held across blocks so a
        #: source allocates nothing per block.
        self._bus = Bus(engine.pool)
        self._program = (program if program is not None else Program()).wire(self)

    def _render(self) -> Bus | StereoBus:
        # Cleared every block, not merely overwritten. Mix is destructive and
        # hands this lane back somebody else's old row, so "what was here
        # last time" is not this Stage's to reuse.
        self._bus.clear()
        if not self._program:
            return self._compositor.process()
        return self._compositor.process(self._program.process(self._bus))


class _InputStage(Stage, Generic[_ConnectionT]):
    """Shared machinery for the Stage forms that accept audio inputs.

    `connect`, `disconnect`, lane lookup and input quiescence are identical
    across Composite, Apply and the MixingDesk -- the three differ only in
    what KIND of lane they build and what they do with the results. So the
    difference is one hook, `_make_connection`, rather than the same twenty
    lines written out three times.
    """

    def __init__(self, engine: StageEngine):
        super().__init__(engine)
        self._connections: list[_ConnectionT] = []

    def _make_connection(self, source: Stage) -> _ConnectionT:
        raise NotImplementedError

    def connect(self, stage: Stage):
        """Add an input. Connecting the same Stage twice does nothing.

        Idempotent rather than an error, because "this is connected" is the
        state the caller wanted and they now have it.
        """
        self._engine._assert_topology_mutable()
        if stage._engine is not self._engine:
            raise ValueError("cannot connect a Stage from another AudioEngine")
        for connection in self._connections:
            if connection.source is stage:
                return
        self._connections.append(self._make_connection(stage))
        self._wake()

    def disconnect(self, stage: Stage):
        """Remove an input and release its lane. Unknown Stages are ignored."""
        self._engine._assert_topology_mutable()
        for connection in self._connections:
            if connection.source is stage:
                self._connections.remove(connection)
                connection.release()
                self._wake()
                break

    def _connection(self, source: Stage) -> _ConnectionT:
        for connection in self._connections:
            if connection.source is source:
                return connection
        raise ValueError("Stage has no input connected from the given Stage")

    def _inputs_quiescent(self) -> bool:
        return all(connection.quiescent for connection in self._connections)


class CompositeStage(_InputStage[_Connection]):
    """Every input gets its own copy of the Program, and the results are summed.

    The instrument shape. Three lanes each holding an Envelope keep three
    independent envelope states while observing one Stage `gate`, because the
    Program is a blueprint wired afresh per lane and the registers live on the
    Stage.

    An empty Program makes each lane a plain `Gain` into the mix, which is how
    you build a submix bus.
    """

    def __init__(self, engine: StageEngine, program: Program | None = None):
        super().__init__(engine)
        self._program = program if program is not None else Program()
        # Declared without wiring: the Stage must be able to say what it
        # accepts before anything is connected to it.
        self._program.declare(self)

    def _make_connection(self, source: Stage) -> _Connection:
        return _Connection(self, source, self._program)

    def _render(self) -> Bus | StereoBus:
        inputs = []
        for connection in self._connections:
            # Sleeping lanes are left out of the mix entirely, so the cost of
            # a block is the voices actually sounding. Adding silence to a sum
            # changes nothing, which is what makes this safe here.
            if connection.quiescent:
                continue
            output = connection.process()
            if not output.current.quiescent:
                inputs.append(output)
        return self._compositor.process(*inputs)


class ApplyStage(_InputStage[_Connection]):
    """Every input meets in ONE Program, at its first Operator.

    The cross-input shape: RingMod multiplying two lanes together, Duck using
    a second lane to push down the first. The head Operator reduces the lanes
    to one bus and the rest of the chain is ordinary unary work.

    **Lanes keep their positions, so a sleeping one is still passed.** Unlike
    a Composite, where a silent lane can simply be left out of a sum, here
    lane order is meaning -- Duck's first input is the signal and its second
    is the control. Dropping a quiet lane would promote the one after it.

    Requires a Program. There is no sensible default: with nothing to reduce
    them, several inputs have no single answer.
    """

    def __init__(self, engine: StageEngine, program: Program):
        super().__init__(engine)
        # Wired to the Stage with no connection, so a Lane-scoped Operator in
        # this Program is refused -- correct, because there is no single input
        # identity for it to belong to.
        self._program = program.wire(self)

    def _make_connection(self, source: Stage) -> _Connection:
        return _Connection(self, source)

    def _render(self) -> Bus | StereoBus:
        if not self._connections or not self._program:
            return self._compositor.process()
        inputs = [connection.process() for connection in self._connections]
        return self._compositor.process(self._program.process(*inputs))
