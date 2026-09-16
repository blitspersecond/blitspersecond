"""`Envelope`: the shape that decides whether a carrier is heard.

An oscillator runs forever; this is what makes it a note. Attack, Hold,
Decay, Sustain, Release, driven by one `gate` register -- and every one of
those five is a **live** register, not a construction-time setting, so a
score can reshape a note while it is sounding.

**There is no per-sample loop.** Each phase is a straight line, so a block is
rendered as a handful of analytic segments cut at write timestamps. That is
what keeps a hundred voices affordable in Python.

**Gate is level-triggered, not edge-triggered.** Writing `gate=True` when it
is already true is legato and changes nothing. An explicit retrigger is two
writes at the same sample -- `gate=False` then `gate=True` -- which is why
insertion order breaks timestamp ties in the engine's heap.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import cast

from numpy import add, empty, multiply, ndarray

from ..bus import Bus
from ...constants import BLOCK_SIZE, SAMPLE_RATE, _SAMPLE_OFFSETS
from ..registers import RegisterSpec, RegisterWrite
from .operator import Operator


class Envelope(Operator):
    """A sample-accurate AHDSR, reshapeable while it sounds."""

    #: On the class so `_output_quiescent` is answerable before the first
    #: render, without an `__init__` every subclass would have to remember.
    _active_this_block = False

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
                    sustain, minimum=0.0, maximum=1.0, unit="linear"
                ),
                "release": RegisterSpec(release, minimum=0.0, unit="seconds"),
                "gate": RegisterSpec(False),
            }
        )

        self.state = "idle"
        self.level = 0.0
        #: Samples left in the current phase. Carried across blocks exactly,
        #: so a long attack does not re-round its way off course.
        self._remaining = 0
        #: How much of Hold has been served. Retiming Hold must not restart
        #: it -- shortening it below what has already elapsed ends it now.
        self._hold_elapsed = 0
        #: The level Release began from. Retiming Release scales against this
        #: rather than against 1.0, so a fade started from a quiet sustain
        #: keeps its shape instead of suddenly steepening.
        self._release_origin = 0.0
        self._levels = empty(BLOCK_SIZE, dtype="float32")
        self._clock = 0
        #: The block this last ran on. A gap means the Stage was asleep or
        #: unreached, so committed register writes may have moved underneath
        #: this operator and its phase has to be reconciled before resuming.
        self._last_clock: int | None = None
        self._registers: Mapping[str, object] = MappingProxyType({})
        self._writes: tuple[RegisterWrite, ...] = ()

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        self._clock = clock
        self._registers = registers
        self._writes = writes

    def process(self, bus: Bus) -> Bus:
        # Copied only when there is something to change. With no writes the
        # settled view is read straight through, which is the common case.
        registers = dict(self._registers) if self._writes else self._registers
        gate = registers["gate"]
        self._active_this_block = self.state != "idle"
        if self._last_clock is None or self._clock != self._last_clock + BLOCK_SIZE:
            # Not the block after the last one we ran. Something changed
            # without us seeing it happen, so square up against what the
            # registers now say before producing anything.
            if not self._synchronize(gate, registers):
                self._reconcile(registers)
        self._active_this_block |= self.state != "idle"

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
                    # Non-empty `_writes` means `registers` was built as a
                    # plain dict above, so this is safe to mutate.
                    cast(dict, registers)[name] = value
            gate = registers["gate"]
            if gate != previous_gate:
                self._synchronize(gate, registers)
            else:
                # Writing the value it already holds is not an edge and
                # retriggers nothing.
                self._reconcile_changes(registers, changed)
            self._active_this_block |= self.state != "idle"
            start = stop

        self._apply(output, start, BLOCK_SIZE, registers)
        self._last_clock = self._clock
        return bus

    def _output_quiescent(self, output: Bus, input_quiescent: bool) -> bool:
        # Idle AND idle for the whole block: an envelope that finished halfway
        # through still produced audio in the first half.
        return not self._active_this_block and self.state == "idle"

    # -- rendering ---------------------------------------------------------

    def _apply(self, output: ndarray, start: int, stop: int, registers):
        """Multiply one span of the block by the envelope, phase by phase."""
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

            # A phase can end inside this span, so take what is left of it and
            # come round again for the next one.
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

    # -- reacting to control ------------------------------------------------

    def _synchronize(self, gate, registers):
        """Move to match the gate, if it disagrees with where we are."""
        active = self.state in ("attack", "hold", "decay", "sustain")
        if gate == active:
            return False
        if gate:
            self._begin_attack(registers)
        else:
            self._begin_release(registers)
        return True

    def _reconcile(self, registers):
        """Re-derive timing for the current phase from the settled values."""
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
        """Retime only if a register this phase actually depends on moved."""
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

    # -- phases --------------------------------------------------------------

    def _begin_attack(self, registers):
        # From the CURRENT level, not from zero. Retriggering during a release
        # picks up from wherever the fade had reached, so a fast repeat does
        # not click back to silence first.
        self.state = "attack"
        self._retime_attack(registers)

    def _retime_attack(self, registers):
        # Scaled by the distance still to climb, which keeps the SLOPE the
        # attack time asks for. Starting half way up takes half as long.
        self._remaining = round(
            registers["attack"] * SAMPLE_RATE * (1.0 - self.level)
        )
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
        # `decay` is the time for a FULL 1.0 -> sustain fall, so a decay
        # already part-way down takes proportionally less of it.
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
            # Released from silence; there is nothing to fade.
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
