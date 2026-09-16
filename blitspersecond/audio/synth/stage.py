"""A Stage: one node of the audio graph, and everything common to all of them.

A Stage is where a `Program` is bound, where registers live, and where the
graph is *pulled* from. Nothing pushes audio: the engine demands its terminal
Stage, that Stage demands whatever feeds it, and the demand walks depth-first
to the sources. One pull per block, and the answer is cached.

Stage owns four mechanisms that every concrete form inherits:

**The settled cache.** A Stage renders at most once per block. Fan-out is
therefore free and, more importantly, correct -- one oscillator feeding six
lanes advances its phase once, not six times.

**The feedback boundary.** A pull that arrives while this Stage is already
rendering gets its PREVIOUS settled output instead of recursing. That is what
makes a cycle in the graph terminate and read as a one-block delay rather
than as a stack overflow.

**Quiescence.** A Stage that is silent and has nothing left to produce is
skipped entirely, so a hundred idle voices cost nothing. Being wrong in one
direction is inaudible and in the other direction is a dropped note, so every
uncertainty here resolves to "stay awake".

**Control filtering.** Each Operator is handed only the writes naming
registers it actually declared, already resolved against any signal driving
them.

Stage deliberately has no `connect()`. Audio inputs are the business of the
concrete forms -- a source has none, and giving the base one would let a
source silently become an effect. Register *signals* are common to all of
them, so those live here.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from types import MappingProxyType
from typing import Protocol

from .block import BlockPool
from .bus import Bus, StereoBus
from .operators import Mix, Operator
from .register_signals import _RegisterSignals
from .registers import RegisterSpec, RegisterWrite


class StageEngine(Protocol):
    """The engine services a Stage consumes, and nothing more.

    Spelled structurally so a Stage can be driven by a small stub with a
    clock in it, rather than by something pretending to be an AudioEngine.
    """

    @property
    def pool(self) -> BlockPool: ...

    @property
    def clock(self) -> int: ...

    @property
    def block(self) -> int: ...

    @property
    def writes(self) -> tuple[RegisterWrite, ...]: ...

    @property
    def _prepared_writes(self) -> tuple[RegisterWrite, ...]: ...

    def _assert_topology_mutable(self) -> None: ...


class InputLane(Protocol):
    """One audio input's private lane, as far as the Stage base is concerned.

    The concrete class is `_Connection`, which imports Stage -- so naming it
    here would be a cycle, and this says the part that matters anyway: a lane
    is something you can read registers off and attach a signal to. Narrower
    than `program.Lane`, which is the same object seen by whatever wires
    Operators onto it.
    """

    @property
    def registers(self) -> Mapping[str, object]: ...

    @property
    def write_specs(self) -> Mapping[str, RegisterSpec]: ...

    def _connect_register(self, source: "Stage", name: str) -> None: ...

    def _disconnect_register(self, source: "Stage", name: str) -> None: ...


class Stage(ABC):
    """The abstract processing boundary: registers, caching, compositing."""

    #: How many channels this Stage outputs. Only a MixingDesk is not 1;
    #: everything upstream of one stays mono, because panning is the only
    #: thing in the graph that needs two channels and it happens last.
    channels = 1

    def __init__(self, engine: StageEngine):
        # Building a Stage IS a topology change, so it is refused while a
        # Driver owns the engine. The graph is frozen for the whole run.
        engine._assert_topology_mutable()
        self._engine = engine
        #: What this Stage accepts: name -> RegisterSpec. Plain comparable
        #: data rather than a callback, so two Programs declaring the same
        #: register can be checked for agreement by equality.
        self._register_specs: dict[str, RegisterSpec] = {}
        #: What it currently holds. Separate from the specs above because a
        #: Program wired later must validate against the schema without
        #: resetting live values or mistaking a changed value for a conflict.
        self._registers: dict[str, object] = {}
        self._register_spec_view = MappingProxyType(self._register_specs)
        self._register_view = MappingProxyType(self._registers)
        self._register_signals = _RegisterSignals(
            engine,
            self._register_specs,
            self._registers,
        )
        self._output = self._make_output()
        self._compositor = Mix(self._output)
        self._settled_at: int | None = None
        #: The last completed output. Also the answer to a recursive pull, and
        #: to a pull while quiescent -- which is why it starts as the empty
        #: output rather than as None.
        self._settled = self._output
        self._rendering = False
        #: Starts False: a Stage that has never rendered has not proved
        #: anything, and proof is what this flag means.
        self._quiescent = False
        self._checking_quiescent = False

    # -- registers ------------------------------------------------------

    def _declare_registers(self, registers):
        for name, spec in registers.items():
            if not isinstance(spec, RegisterSpec):
                raise TypeError(f"register {name!r} has no RegisterSpec")
            # Two Programs may legitimately declare the same register -- three
            # lanes each holding an Envelope share one `gate`. They must agree
            # about what it is.
            if name in self._register_specs and self._register_specs[name] != spec:
                raise ValueError(f"conflicting declaration for register {name!r}")
            self._register_specs.setdefault(name, spec)
            self._registers.setdefault(name, spec.default)

    def control(self, operator: Operator):
        """Hand one Operator its values and the writes that concern it.

        Two filters, and both matter. An Operator sees only registers it
        declared, so an Envelope is not handed a `frequency` write meant for
        the oscillator beside it. And it sees no writes at all for a register
        currently driven by a signal, because the signal overrides the stored
        value completely -- delivering both would let a write briefly win
        against the thing that is supposed to be overriding it.
        """
        # TODO: Index the block's writes by Stage and register if graph size
        # ever makes these per-Operator scans material.
        registers = self._register_signals.resolve()
        signal_names = self._register_signals.names
        stage_writes = self.writes
        if not stage_writes:
            operator.control(self.clock, registers, ())
            return

        writes = []
        for write in stage_writes:
            values = tuple(
                (name, value)
                for name, value in write.values
                if name in operator.registers and name not in signal_names
            )
            if values:
                writes.append(RegisterWrite(write.timestamp, self, values))
        operator.control(self.clock, registers, tuple(writes))

    @property
    def registers(self):
        """This Stage's live values, read-only. Writes go through schedule()."""
        return self._register_view

    @property
    def register_specs(self):
        """What this Stage accepts, read-only, for graph tooling."""
        return self._register_spec_view

    def registers_for(self, source: "Stage"):
        """This Stage's values with one input lane's own values overlaid."""
        return self._connection(source).registers

    def register_specs_for(self, source: "Stage"):
        """What one input lane accepts, read-only."""
        return self._connection(source).write_specs

    @property
    def writes(self) -> tuple[RegisterWrite, ...]:
        """This block's writes addressed to the Stage itself, not to a lane."""
        writes = self._engine.writes
        if not writes:
            return ()
        return tuple(
            write
            for write in writes
            if write.stage is self and write.connection is None
        )

    # -- register signals -----------------------------------------------

    def connect_signal(
        self,
        source: "Stage",
        *,
        to: str | None = None,
        lane: "Stage | None" = None,
    ):
        """Drive one register from another Stage's audio.

        `to` names the register. `lane` addresses one existing audio input's
        copy of it; without `lane` the signal drives the Stage's own.
        """
        self._engine._assert_topology_mutable()
        if to is None:
            raise ValueError("a register signal connection requires to=register")
        target = self if lane is None else self._connection(lane)
        target._connect_register(source, to)

    def disconnect_signal(
        self,
        source: "Stage",
        *,
        to: str | None = None,
        lane: "Stage | None" = None,
    ):
        self._engine._assert_topology_mutable()
        if to is None:
            raise ValueError("a register signal disconnection requires to=register")
        target = self if lane is None else self._connection(lane)
        target._disconnect_register(source, to)

    def _connect_register(self, source: "Stage", name: str):
        self._register_signals.connect(name, source)
        self._wake()

    def _disconnect_register(self, source: "Stage", name: str):
        self._register_signals.disconnect(name, source)
        self._wake()

    # -- rendering -------------------------------------------------------

    def _make_output(self) -> Bus | StereoBus:
        return Bus(self._engine.pool)

    def process(self) -> Bus | StereoBus:
        """Settle this Stage for the current block, or hand back what settled.

        The three early exits are three different questions, in order:
        already done this block; nothing to do; or asked again while still
        answering, which is the feedback case.
        """
        current = self.block
        if self._settled_at == current:
            return self._settled
        if self.quiescent:
            # Marked settled without rendering, which is what freezes a
            # sleeping oscillator's phase. On wake it resumes where it was; it
            # does not race forward through samples nobody heard.
            self._settled_at = current
            return self._settled
        if self._rendering:
            # A cycle. Hand back the PREVIOUS block's output -- the caller
            # copies it immediately, so this Stage may replace its output at
            # the end of this render without aliasing what the cycle read.
            # The first time round that is the zero-filled initial output.
            return self._settled

        self._rendering = True
        try:
            settled = self._render()
        finally:
            self._rendering = False
        self._settled = settled
        self._settled_at = current
        self._quiescent = settled.current.quiescent
        return self._settled

    @abstractmethod
    def _render(self) -> Bus | StereoBus:
        """Produce this block's output. The concrete form's only real job."""

    @property
    def quiescent(self) -> bool:
        """Whether this Stage can be skipped until something wakes it.

        Every branch that cannot prove silence answers False, including the
        re-entrant one: a cycle asking whether it is asleep is a question with
        no fixed point, and staying awake is the answer that only costs CPU.
        """
        if self._checking_quiescent:
            return False
        self._checking_quiescent = True
        try:
            if not self._quiescent or not self._inputs_quiescent():
                return False
            # A connected signal keeps its target awake. Sleeping would be
            # correct only if a change in the control source could wake this
            # Stage, and there are no reverse links to do that yet.
            if self._register_signals.names:
                return False
            # `_prepared_writes`, not `writes`: asking whether a Stage is
            # asleep must never pull a scheduled batch into the current block.
            # Only ordinary resolution admits writes.
            return not any(
                write.stage is self
                for write in self._engine._prepared_writes
            )
        finally:
            self._checking_quiescent = False

    def _inputs_quiescent(self) -> bool:
        """Whether everything feeding this Stage is asleep. No inputs, so yes."""
        return True

    def _wake(self) -> None:
        self._quiescent = False

    # -- clocks and lanes -------------------------------------------------

    @property
    def clock(self) -> int:
        """The absolute sample the current block begins at."""
        return self._engine.clock

    @property
    def block(self) -> int:
        return self._engine.block

    def _connection(self, source: "Stage") -> InputLane:
        """The lane fed by `source`. Only the forms with inputs have any."""
        raise ValueError("Stage has no input connected from the given Stage")
