from types import MappingProxyType
from typing import cast

from numpy import (
    add,
    arange,
    copyto,
    empty,
    float64,
    intp,
    minimum,
    multiply,
    subtract,
    take,
)

from blitspersecond.resources import SoundSpec

from ..common import Bus, FRAME_SIZE, RegisterSpec, RegisterWrite, SAMPLE_RATE
from .operator import Operator

_SAMPLE_OFFSETS = arange(FRAME_SIZE, dtype=float64)
_SAMPLE_OFFSETS.setflags(write=False)


class PCM(Operator):
    """Play a mono SoundSpec at its native rate after a rising gate edge."""

    def __init__(self, sound: SoundSpec, speed: float = 1.0):
        if not isinstance(sound, SoundSpec):
            raise TypeError(f"expected SoundSpec, got {type(sound).__name__}")
        speed = float(speed)
        self.registers = MappingProxyType(
            {
                "sound": RegisterSpec(sound),
                "speed": RegisterSpec(speed, minimum=0.0, unit="ratio"),
                "gate": RegisterSpec(False),
            }
        )

        self.position = 0.0
        self.active = False
        self._sound = sound
        self._gate = False
        self._clock = 0
        self._registers = MappingProxyType({})
        self._writes = ()

        self._positions = empty(FRAME_SIZE, dtype=float64)
        self._lower = empty(FRAME_SIZE, dtype=intp)
        self._upper = empty(FRAME_SIZE, dtype=intp)
        self._fractions = empty(FRAME_SIZE, dtype=float64)
        self._values = empty(FRAME_SIZE, dtype=float64)
        self._upper_values = empty(FRAME_SIZE, dtype=float64)

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        self._clock = clock
        self._registers = registers
        self._writes = writes

    def process(self, bus: Bus) -> Bus:
        self._active_this_frame = False
        sound = cast(SoundSpec, self._registers["sound"])
        speed = cast(float, self._registers["speed"])
        gate = cast(bool, self._registers["gate"])
        self._synchronize(sound, gate)

        output = bus.current.data
        start = 0
        for write in self._writes:
            stop = write.timestamp - self._clock
            self._generate(output, start, stop, sound, speed)
            for name, value in write.values:
                if name == "sound":
                    sound = cast(SoundSpec, value)
                elif name == "speed":
                    speed = cast(float, value)
                else:
                    gate = cast(bool, value)
            self._synchronize(sound, gate)
            start = stop

        self._generate(output, start, FRAME_SIZE, sound, speed)
        return bus

    def _output_quiescent(self, output: Bus, input_quiescent: bool) -> bool:
        return not self._active_this_frame and not self.active

    def _synchronize(self, sound: SoundSpec, gate: bool):
        if sound is not self._sound:
            self._sound = sound
            self.position = 0.0
            self.active = gate
        if gate != self._gate:
            self._gate = gate
            if gate:
                self.position = 0.0
                self.active = True
            else:
                self.active = False

    def _generate(
        self,
        output,
        start: int,
        stop: int,
        sound: SoundSpec,
        speed: float,
    ):
        length = stop - start
        if length == 0:
            return
        samples = output[start:stop]
        if not self.active:
            samples.fill(0.0)
            return
        self._active_this_frame = True

        data = sound.data
        last = len(data) - 1
        rate = sound.sr / SAMPLE_RATE * speed
        if rate == 0.0:
            samples.fill(data[min(int(self.position), last)])
            return

        available = max(int((last - self.position) // rate) + 1, 0)
        count = min(length, available)
        if count:
            positions = self._positions[:count]
            multiply(_SAMPLE_OFFSETS[:count], rate, out=positions)
            add(positions, self.position, out=positions)

            lower = self._lower[:count]
            copyto(lower, positions, casting="unsafe")
            upper = self._upper[:count]
            add(lower, 1, out=upper)
            minimum(upper, last, out=upper)

            fractions = self._fractions[:count]
            subtract(positions, lower, out=fractions)
            values = self._values[:count]
            upper_values = self._upper_values[:count]
            take(data, lower, out=values)
            take(data, upper, out=upper_values)
            subtract(upper_values, values, out=upper_values)
            multiply(upper_values, fractions, out=upper_values)
            add(values, upper_values, out=values)
            samples[:count] = values

        samples[count:].fill(0.0)
        self.position += length * rate
        if count < length or self.position > last:
            self.active = False
