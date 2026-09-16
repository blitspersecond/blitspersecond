"""One connected input, and the private lane its audio travels down.

A Stage never processes another Stage's output directly. Every input gets a
`_Connection`: its own Bus, its own level, and -- on a Composite Stage -- its
own instance of the Stage's Program. That is what lets three inputs share one
`gate` while each keeps its own envelope state.

The copy at the top of the lane is not avoidable even at unity gain. The
upstream Stage's settled block is cached and may be feeding several lanes at
once, so a lane that worked in place would be editing a block its siblings
are about to read.
"""

from collections import ChainMap
from types import MappingProxyType
from typing import cast

from .bus import Bus
from .operators import Gain
from .program import Program
from .register_signals import _RegisterSignals
from .registers import RegisterScope, RegisterSpec, RegisterWrite
from .stage import Stage


class _InputGain(Gain):
    """The structural level every connection gets, scoped to the lane.

    A subclass rather than a constructor flag because scope is a property of
    the Operator class, and the two really are different operators: the
    public `Gain` stays Stage-scoped so it can sit inside a SourceStage
    Program, where there is no lane for it to belong to.
    """

    scope = RegisterScope.LANE


class _Connection:
    """One input's lane: a copy, a level, and optionally a Program.

    Holds two register layers of its own. `_registers` are values this lane
    OWNS -- lane-scoped declarations like its own `level`. `_override_specs`
    are Stage registers this lane is merely *allowed* to be given its own
    value for, which is what makes `schedule()` accept a four-item event
    naming this input without the lane having claimed the register yet.
    """

    def __init__(
        self,
        stage: Stage,
        source: Stage,
        program: Program | None = None,
    ):
        if source.channels != 1:
            raise ValueError("cannot connect a stereo Stage to a mono input")
        self.stage = stage
        self.source = source
        self.bus = Bus(stage._engine.pool)
        self._register_specs: dict[str, RegisterSpec] = {}
        self._registers: dict[str, object] = {}
        self._override_specs: dict[str, RegisterSpec] = {}
        # Reading falls through the lane to the Stage: a Stage value
        # broadcasts to every lane that has not claimed that name.
        self._register_view = MappingProxyType(
            ChainMap(self._registers, stage._registers)
        )
        # What may be WRITTEN here: this lane's own registers, plus the Stage
        # registers it has been permitted to shadow.
        self._write_spec_view = MappingProxyType(
            ChainMap(self._register_specs, self._override_specs)
        )
        self._register_signals = _RegisterSignals(
            stage._engine,
            self._write_spec_view,
            self._registers,
            parent=stage._register_signals,
        )
        self.active = True
        self._quiescent = False
        self.gain = _InputGain()
        self._declare_registers(self.gain.registers)
        self.program = None if program is None else program.wire(stage, self)

    # -- registers -------------------------------------------------------

    def _declare_registers(self, registers):
        for name, spec in registers.items():
            if not isinstance(spec, RegisterSpec):
                raise TypeError(f"register {name!r} has no RegisterSpec")
            if name in self._register_specs and self._register_specs[name] != spec:
                raise ValueError(f"conflicting declaration for register {name!r}")
            self._register_specs.setdefault(name, spec)
            self._registers.setdefault(name, spec.default)

    def _allow_overrides(self, registers):
        """Permit this lane to be addressed for a Stage-scoped register.

        Permission only. The lane does not own the register until something
        actually writes to it here, so until then a Stage write still reaches
        it like any other lane.
        """
        for name, spec in registers.items():
            if name in self._override_specs and self._override_specs[name] != spec:
                raise ValueError(f"conflicting declaration for register {name!r}")
            self._override_specs.setdefault(name, spec)

    @property
    def registers(self):
        return self._register_view

    @property
    def write_specs(self):
        return self._write_spec_view

    def control(self, operator):
        """Hand one Operator this lane's values and the writes that reach it.

        **The shadowing rule is resolved here, in timestamp order.** A Stage
        write broadcasts to every lane -- until a lane is given its own value
        for that register, and from that sample onward the broadcast no longer
        reaches it. So this walks the block's writes in order and grows the
        set of names this lane owns as it goes: a lane write at sample 100
        shadows a Stage write at 200, and does not shadow one at 50.

        That ordering is the whole subtlety. Collecting the lane's names up
        front instead would make a Stage write earlier in the same block
        vanish, and collecting them afterwards would let it win.
        """
        registers = self._register_signals.resolve()
        local_signal_names = self._register_signals.names
        stage_signal_names = self.stage._register_signals.names
        local_names = set(self._registers).union(local_signal_names)
        writes = []
        for write in self.stage._engine.writes:
            if write.stage is not self.stage:
                continue
            if write.connection is self:
                # From here on, this name is the lane's own.
                local_names.update(name for name, _value in write.values)
                values = tuple(
                    (name, value)
                    for name, value in write.values
                    if name in operator.registers
                    and name not in local_signal_names
                )
            elif write.connection is None:
                values = tuple(
                    (name, value)
                    for name, value in write.values
                    if name in operator.registers
                    and name not in local_names
                    and name not in stage_signal_names
                )
            else:
                # Addressed to a different lane of the same Stage.
                continue
            if values:
                writes.append(
                    RegisterWrite(write.timestamp, self.stage, values, self)
                )
        operator.control(self.stage.clock, registers, tuple(writes))

    def _connect_register(self, source: Stage, name: str):
        self._register_signals.connect(name, source)
        self._quiescent = False
        self.stage._wake()

    def _disconnect_register(self, source: Stage, name: str):
        self._register_signals.disconnect(name, source)
        self._quiescent = False
        self.stage._wake()

    # -- processing -------------------------------------------------------

    @property
    def quiescent(self) -> bool:
        """Whether this lane can be left out of the Stage's mix entirely."""
        if not self._quiescent or not self.source.quiescent:
            return False
        if self._register_signals.names:
            return False
        # A write addressed to the Stage reaches every lane, so either
        # address wakes this one.
        return not any(
            write.stage is self.stage
            and (write.connection is None or write.connection is self)
            for write in self.stage._engine._prepared_writes
        )

    def process(self) -> Bus:
        """Pull the source, copy it in, and walk the lane."""
        self.bus.ingest(cast(Bus, self.source.process()))
        self.control(self.gain)
        # Mutates in place and hands back the same bus; the level is applied
        # to the lane's own copy, never to the source's block.
        self.gain.process(self.bus)
        if self.program is not None:
            output = self.program.process(self.bus)
        else:
            output = self.bus
        self._quiescent = output.current.quiescent
        return output

    def release(self):
        """Give up this lane's row. Scheduled writes naming it are then dead.

        `active` is what the engine checks before committing a write to a
        disconnected lane, so automation aimed at a lane that has gone does
        not resurrect on reconnect -- a new connection builds fresh defaults.
        """
        self.active = False
        self._quiescent = True
        self.bus.release()
