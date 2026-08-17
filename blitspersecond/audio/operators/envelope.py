from types import MappingProxyType
from typing import cast

from numpy import add, empty, multiply

from ..common import Bus, FRAME_SIZE, RegisterSpec, RegisterWrite, SAMPLE_RATE
from ..common.constants import _SAMPLE_OFFSETS
from .operator import Operator


class Envelope(Operator):
    """A sample-accurate Attack, Hold, Decay, Sustain, Release envelope."""

    def __init__(self, attack, hold, decay, sustain, release):
        attack = float(attack)
        hold = float(hold)
        decay = float(decay)
        sustain = float(sustain)
        release = float(release)

        self.registers = MappingProxyType(
            {
                "attack": RegisterSpec(attack, minimum=0.0, unit="seconds"),
                "hold": RegisterSpec(hold, minimum=0.0, unit="seconds"),
                "decay": RegisterSpec(decay, minimum=0.0, unit="seconds"),
                "sustain": RegisterSpec(
                    sustain,
                    minimum=0.0,
                    maximum=1.0,
                    unit="linear",
                ),
                "release": RegisterSpec(release, minimum=0.0, unit="seconds"),
                "gate": RegisterSpec(False),
            }
        )

        self.state = "idle"
        self.level = 0.0
        self._remaining = 0
        self._hold_elapsed = 0
        self._release_origin = 0.0
        self._levels = empty(FRAME_SIZE, dtype="float32")
        self._clock = 0
        self._last_clock: int | None = None
        self._registers = MappingProxyType({})
        self._writes = ()

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        self._clock = clock
        self._registers = registers
        self._writes = writes

    def process(self, bus: Bus) -> Bus:
        registers = dict(self._registers) if self._writes else self._registers
        gate = registers["gate"]
        self._active_this_frame = self.state != "idle"
        if self._last_clock is None or self._clock != self._last_clock + FRAME_SIZE:
            if not self._synchronize(gate, registers):
                self._reconcile(registers)
        self._active_this_frame |= self.state != "idle"

        output = bus.current.data
        start = 0
        for write in self._writes:
            stop = write.timestamp - self._clock
            self._apply(output, start, stop, registers)

            previous_gate = gate
            changed = set()
            for name, value in write.values:
                if registers[name] != value:
                    changed.add(name)
                    # self._writes is non-empty here (we're inside its loop),
                    # so `registers` was built as a plain dict above.
                    cast(dict, registers)[name] = value
            gate = registers["gate"]
            if gate != previous_gate:
                self._synchronize(gate, registers)
            else:
                self._reconcile_changes(registers, changed)
            self._active_this_frame |= self.state != "idle"
            start = stop

        self._apply(output, start, FRAME_SIZE, registers)
        self._last_clock = self._clock
        return bus

    def _output_quiescent(self, output: Bus, input_quiescent: bool) -> bool:
        return not self._active_this_frame and self.state == "idle"

    def _apply(self, output, start, stop, registers):
        while start < stop:
            if self.state == "idle":
                output[start:stop] = 0.0
                return
            if self.state == "sustain":
                if self.level != 1.0:
                    output[start:stop] *= self.level
                return
            if self._remaining == 0:
                self._complete_phase(registers)
                continue

            length = min(stop - start, self._remaining)
            if self.state == "hold":
                self.level = 1.0
                self._hold_elapsed += length
            else:
                target = 1.0 if self.state == "attack" else 0.0
                if self.state == "decay":
                    target = registers["sustain"]
                end = self.level + (target - self.level) * length / self._remaining
                levels = self._levels[:length]
                step = (end - self.level) / length
                multiply(_SAMPLE_OFFSETS[:length], step, out=levels)
                add(levels, self.level, out=levels)
                multiply(
                    output[start : start + length],
                    levels,
                    out=output[start : start + length],
                )
                self.level = end

            self._remaining -= length
            start += length
            if self._remaining == 0:
                self._complete_phase(registers)

    def _complete_phase(self, registers):
        if self.state == "attack":
            self._begin_hold(registers)
        elif self.state == "hold":
            self._begin_decay(registers)
        elif self.state == "decay":
            self._begin_sustain(registers)
        elif self.state == "release":
            self._finish()

    def _synchronize(self, gate, registers):
        active = self.state in ("attack", "hold", "decay", "sustain")
        if gate == active:
            return False
        if gate:
            self._begin_attack(registers)
        else:
            self._begin_release(registers)
        return True

    def _reconcile(self, registers):
        if self.state == "attack":
            self._retime_attack(registers)
        elif self.state == "hold":
            self._retime_hold(registers)
        elif self.state == "decay":
            self._retime_decay(registers)
        elif self.state == "sustain":
            self.level = registers["sustain"]
        elif self.state == "release":
            self._retime_release(registers)

    def _reconcile_changes(self, registers, changed):
        if self.state == "attack" and "attack" in changed:
            self._retime_attack(registers)
        elif self.state == "hold" and "hold" in changed:
            self._retime_hold(registers)
        elif self.state == "decay" and changed.intersection({"decay", "sustain"}):
            self._retime_decay(registers)
        elif self.state == "sustain" and "sustain" in changed:
            self.level = registers["sustain"]
        elif self.state == "release" and "release" in changed:
            self._retime_release(registers)

    def _begin_attack(self, registers):
        self.state = "attack"
        self._retime_attack(registers)

    def _retime_attack(self, registers):
        self._remaining = round(registers["attack"] * SAMPLE_RATE * (1.0 - self.level))
        if self._remaining == 0:
            self._begin_hold(registers)

    def _begin_hold(self, registers):
        self.state = "hold"
        self.level = 1.0
        self._hold_elapsed = 0
        self._retime_hold(registers)

    def _retime_hold(self, registers):
        duration = round(registers["hold"] * SAMPLE_RATE)
        self._remaining = max(duration - self._hold_elapsed, 0)
        if self._remaining == 0:
            self._begin_decay(registers)

    def _begin_decay(self, registers):
        self.state = "decay"
        self.level = 1.0
        self._retime_decay(registers)

    def _retime_decay(self, registers):
        target = registers["sustain"]
        span = abs(1.0 - target)
        distance = abs(self.level - target)
        duration = round(registers["decay"] * SAMPLE_RATE)
        self._remaining = round(duration * distance / span) if span else 0
        if self._remaining == 0:
            self._begin_sustain(registers)

    def _begin_sustain(self, registers):
        self.state = "sustain"
        self.level = registers["sustain"]
        self._remaining = 0

    def _begin_release(self, registers):
        self.state = "release"
        self._release_origin = self.level
        self._retime_release(registers)

    def _retime_release(self, registers):
        duration = round(registers["release"] * SAMPLE_RATE)
        if self._release_origin == 0.0:
            self._remaining = 0
            self._finish()
            return
        self._remaining = round(duration * self.level / self._release_origin)
        if self._remaining == 0:
            self._finish()

    def _finish(self):
        self.state = "idle"
        self.level = 0.0
        self._remaining = 0
        self._release_origin = 0.0
