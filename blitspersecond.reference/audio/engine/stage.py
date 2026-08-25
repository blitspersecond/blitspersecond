from abc import ABC, abstractmethod
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol

from ..common import Bus, RegisterSpec, RegisterWrite
from ..common.bus import FramePool, StereoBus
from ..operators import Mix, Operator
from .register_input import _RegisterInputs

if TYPE_CHECKING:
    from .connection import _Connection


class StageEngine(Protocol):
    """The engine surface required by a Stage."""

    pool: FramePool

    @property
    def clock(self) -> int: ...

    @property
    def frame(self) -> int: ...

    @property
    def writes(self) -> tuple[RegisterWrite, ...]: ...

    @property
    def _prepared_writes(self) -> tuple[RegisterWrite, ...]: ...

    def _assert_topology_mutable(self) -> None: ...


class Stage(ABC):
    """Common register, timing, caching, and compositing machinery."""

    channels = 1

    def __init__(self, engine: StageEngine):
        engine._assert_topology_mutable()
        self._engine = engine
        self._register_specs = {}
        self._registers = {}
        self._register_spec_view = MappingProxyType(self._register_specs)
        self._register_view = MappingProxyType(self._registers)
        self._register_inputs = _RegisterInputs(
            engine,
            self._register_specs,
            self._registers,
        )
        self._output = self._make_output()
        # Mix is deliberately shape-polymorphic: ordinary Stages feed it mono
        # Buses while MixingDesk feeds it stereo Buses.
        self._compositor: Any = Mix(self._output)
        self._settled_at: int | None = None
        self._settled = self._output
        self._rendering = False
        self._quiescent = False
        self._checking_quiescent = False

    def _declare_registers(self, registers):
        for name, spec in registers.items():
            if not isinstance(spec, RegisterSpec):
                raise TypeError(f"register {name!r} has no RegisterSpec")
            if name in self._register_specs and self._register_specs[name] != spec:
                raise ValueError(f"conflicting declaration for register {name!r}")
            self._register_specs.setdefault(name, spec)
            self._registers.setdefault(name, spec.default)

    def control(self, operator: Operator):
        # TODO: Index the current Frame's writes by Stage and register if graph
        # size makes these per-Operator scans material.
        registers = self._register_inputs.resolve()
        signal_names = self._register_inputs.names
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

    def connect_signal(
        self,
        source: "Stage",
        *,
        to: str | None = None,
        lane: "Stage | None" = None,
    ):
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
        self._register_inputs.connect(name, source)
        self._wake()

    def _disconnect_register(self, source: "Stage", name: str):
        self._register_inputs.disconnect(name, source)
        self._wake()

    def _make_output(self) -> Bus | StereoBus:
        return Bus(self._engine.pool)

    def process(self) -> Bus | StereoBus:
        current = self.frame
        if self._settled_at == current:
            return self._settled
        if self.quiescent:
            self._settled_at = current
            return self._settled
        if self._rendering:
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

    @property
    def quiescent(self) -> bool:
        """Whether this Stage can be skipped until a new stimulus arrives."""
        if self._checking_quiescent:
            return False
        self._checking_quiescent = True
        try:
            if not self._quiescent or not self._inputs_quiescent():
                return False
            # Signal dependencies do not yet keep reverse wake links. Remaining
            # awake while one is connected preserves continuous-control behaviour.
            if self._register_inputs.names:
                return False
            return not any(
                write.stage is self
                for write in self._engine._prepared_writes
            )
        finally:
            self._checking_quiescent = False

    def _inputs_quiescent(self) -> bool:
        return True

    def _wake(self) -> None:
        self._quiescent = False

    @abstractmethod
    def _render(self) -> Bus | StereoBus:
        pass

    @property
    def clock(self) -> int:
        return self._engine.clock

    @property
    def frame(self) -> int:
        return self._engine.frame

    @property
    def registers(self):
        return self._register_view

    @property
    def register_specs(self):
        """Read-only register socket declarations for graph tooling."""
        return self._register_spec_view

    def registers_for(self, source: "Stage"):
        """Read Stage registers with one connected input's values overlaid."""
        return self._connection(source).registers

    def register_specs_for(self, source: "Stage"):
        """Read register socket declarations for one connected input lane."""
        return self._connection(source).write_specs

    def _connection(self, source: "Stage") -> "_Connection":
        raise ValueError("Stage has no input connected from the given Stage")

    @property
    def writes(self) -> tuple[RegisterWrite, ...]:
        writes = self._engine.writes
        if not writes:
            return ()
        return tuple(
            write
            for write in writes
            if write.stage is self and write.connection is None
        )
