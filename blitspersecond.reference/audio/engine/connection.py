from collections import ChainMap
from types import MappingProxyType
from typing import cast

from ..common import Bus, RegisterScope, RegisterSpec, RegisterWrite
from ..operators import Gain
from .program import Program
from .register_input import _RegisterInputs
from .stage import Stage


class _InputGain(Gain):
    scope = RegisterScope.LANE


class _Connection:
    """One connected Stage and its private, automatically gained lane."""

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
        self._register_specs = {}
        self._registers = {}
        self._override_specs = {}
        self._register_view = MappingProxyType(
            ChainMap(self._registers, stage._registers)
        )
        self._write_spec_view = MappingProxyType(
            ChainMap(self._register_specs, self._override_specs)
        )
        self._register_inputs = _RegisterInputs(
            stage._engine,
            self._write_spec_view,
            self._registers,
            parent=stage._register_inputs,
        )
        self.active = True
        self._quiescent = False
        self.gain = _InputGain()
        self._declare_registers(self.gain.registers)
        self.program = (
            None if program is None else program.wire(stage, self)
        )

    def _declare_registers(self, registers):
        for name, spec in registers.items():
            if not isinstance(spec, RegisterSpec):
                raise TypeError(f"register {name!r} has no RegisterSpec")
            if (
                name in self._register_specs
                and self._register_specs[name] != spec
            ):
                raise ValueError(f"conflicting declaration for register {name!r}")
            self._register_specs.setdefault(name, spec)
            self._registers.setdefault(name, spec.default)

    def _allow_overrides(self, registers):
        for name, spec in registers.items():
            if (
                name in self._override_specs
                and self._override_specs[name] != spec
            ):
                raise ValueError(f"conflicting declaration for register {name!r}")
            self._override_specs.setdefault(name, spec)

    @property
    def registers(self):
        return self._register_view

    @property
    def write_specs(self):
        return self._write_spec_view

    def control(self, operator):
        registers = self._register_inputs.resolve()
        local_signal_names = self._register_inputs.names
        stage_signal_names = self.stage._register_inputs.names
        local_names = set(self._registers).union(local_signal_names)
        writes = []
        for write in self.stage._engine.writes:
            if write.stage is not self.stage:
                continue
            if write.connection is self:
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
                continue
            if values:
                writes.append(
                    RegisterWrite(
                        write.timestamp,
                        self.stage,
                        values,
                        self,
                    )
                )
        operator.control(self.stage.clock, registers, tuple(writes))

    def _connect_register(self, source: Stage, name: str):
        self._register_inputs.connect(name, source)
        self._quiescent = False
        self.stage._wake()

    def _disconnect_register(self, source: Stage, name: str):
        self._register_inputs.disconnect(name, source)
        self._quiescent = False
        self.stage._wake()

    @property
    def quiescent(self) -> bool:
        if not self._quiescent or not self.source.quiescent:
            return False
        if self._register_inputs.names:
            return False
        return not any(
            write.stage is self.stage
            and (write.connection is None or write.connection is self)
            for write in self.stage._engine._prepared_writes
        )

    def process(self) -> Bus:
        self.bus.ingest(cast(Bus, self.source.process()))
        self.control(self.gain)
        self.gain.process(self.bus)
        if self.program is not None:
            output = self.program.process(self.bus)
        else:
            output = self.bus
        self._quiescent = output.current.quiescent
        return output

    def release(self):
        self.active = False
        self._quiescent = True
        self.bus.release()
