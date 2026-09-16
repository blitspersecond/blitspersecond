"""`PCM`: a recording played back as a voice, gated like any other source."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import cast

from numpy import (
    add,
    arange,
    copyto,
    empty,
    float32,
    float64,
    intp,
    minimum,
    multiply,
    subtract,
    take,
)

from ..bus import Bus
from ...constants import BLOCK_SIZE, SAMPLE_RATE
from ..registers import RegisterSpec, RegisterWrite
from ...sound import Sound
from .operator import Operator

#: The block's sample offsets in float64, which the shared float32 ramp in
#: `constants` cannot serve here. Everywhere else an offset is a phase within
#: one block; this one is added to an absolute position inside a recording,
#: and float32 stops being able to tell neighbouring samples apart about
#: sixteen million in -- around six minutes of 44.1 kHz audio.
_OFFSETS = arange(BLOCK_SIZE, dtype=float64)
_OFFSETS.setflags(write=False)


class PCM(Operator):
    """Play a `Sound` from its start on a rising gate, resampled as it goes.

    A sampler voice, and deliberately no more than that: it is a source like
    `Sine` or `Noise`, sits at the head of a Program, and everything that
    happens to a recording afterwards -- envelope, filter, pan -- is the same
    vocabulary anything else gets. A drum machine is `PCM` into `Gain`; a
    voice line is `PCM` on its own.

    **The gate edge is what starts it, not the write.** Raising `gate`
    rewinds to the cue point and plays; lowering it stops dead. Holding it
    high does not loop or retrigger, so a sound that has run out stays
    finished until the gate falls and rises again -- which makes a rapid
    retrigger a deliberate pair of writes rather than something that can
    happen by accident.

    **`playhead` is the cue point AND the seek**, in seconds, and which of
    the two it is depends on whether it is written. Holding a value, it is
    where the next gate edge starts from. WRITTEN, it moves the head then and
    there -- under a running sound that is a jump cut, under a stopped one it
    parks the head and waits. Scrubbing is a stream of writes; seeking is
    one; playing a region is a write and a gate, with a second gate write
    timed at the out point.

    A sampler never touches it. The default is zero, so a gate edge rewinds
    to the start exactly as it would if the register did not exist -- which
    is why a drum hit is still one write and not two.

    **`speed` is a resampling ratio, and so it is a pitch.** The recording's
    own rate is the other half: a 22 kHz sample at speed 1.0 plays at its
    recorded pitch by reading 0.459 of a stored sample per output sample,
    interpolating between neighbours. Doubling the speed raises it an octave
    and halves its duration, exactly as a tape or a tracker does, because
    there is no time-stretching here and pretending otherwise would be a
    different and much larger instrument.

    **Speed zero freezes rather than stops.** It holds whichever sample it had
    reached, which is a click or a drone rather than silence, and `gate` is
    what silences a voice. A speed register that stopped the sound would be a
    second gate with different manners.

    Writing a different `Sound` to the `sound` register restarts it at the
    cue point, because a position measured in the old recording means nothing
    in the new one. In seconds it at least means the same thing in both,
    which is the other half of why `playhead` is not in samples.
    """

    def __init__(self, sound: Sound, speed: float = 1.0):
        if not isinstance(sound, Sound):
            raise TypeError(f"expected Sound, got {type(sound).__name__}")
        self.registers = MappingProxyType(
            {
                "sound": RegisterSpec(sound),
                "speed": RegisterSpec(float(speed), minimum=0.0, unit="ratio"),
                # Where a gate edge starts from, and where a write jumps to.
                # Seconds rather than the recording's own samples, so it
                # survives the `sound` register being written and means the
                # same thing at every recorded rate.
                "playhead": RegisterSpec(0.0, minimum=0.0, unit="seconds"),
                "gate": RegisterSpec(False),
            }
        )

        #: How far into the recording playback has reached, in its samples.
        #: Fractional, because the two rates rarely divide.
        self.position = 0.0
        #: Whether a gate edge has started it and it has not run out.
        self.active = False

        self._sound = sound
        self._gate = False
        self._sounded_this_block = False
        self._clock = 0
        self._registers: Mapping[str, object] = MappingProxyType({})
        self._writes: tuple[RegisterWrite, ...] = ()

        # Scratch, sized for a whole block and sliced to what each segment
        # needs. Resampling is five array operations over the block; done
        # into fresh arrays it would be five allocations a hundred and twenty
        # times a second per sounding voice.
        self._positions = empty(BLOCK_SIZE, dtype=float64)
        self._indices = empty(BLOCK_SIZE, dtype=intp)
        # float32 to match the recording. `take` into an array of another
        # dtype quietly buffers -- it reads the destination it is about to
        # overwrite, which for a scratch array is whatever was in that memory.
        self._values = empty(BLOCK_SIZE, dtype=float32)
        self._neighbours = empty(BLOCK_SIZE, dtype=float32)

    def control(self, clock: int, registers, writes: tuple[RegisterWrite, ...]):
        self._clock = clock
        self._registers = registers
        self._writes = writes

    def process(self, bus: Bus) -> Bus:
        self._sounded_this_block = False
        sound = cast(Sound, self._registers["sound"])
        speed = cast(float, self._registers["speed"])
        playhead = cast(float, self._registers["playhead"])
        gate = cast(bool, self._registers["gate"])
        # No seek at the top of a block: the settled view still carries
        # whatever was last written, and re-reading it as a seek would drag
        # the head back to the cue point a hundred and twenty times a second.
        self._synchronise(sound, gate, playhead, seeked=False)

        output = bus.current.data
        start = 0
        for write in self._writes:
            stop = write.timestamp - self._clock
            self._generate(output, start, stop, sound, speed)
            seeked = False
            for name, value in write.values:
                if name == "sound":
                    sound = cast(Sound, value)
                elif name == "speed":
                    speed = cast(float, value)
                elif name == "playhead":
                    playhead = cast(float, value)
                    seeked = True
                else:
                    gate = cast(bool, value)
            self._synchronise(sound, gate, playhead, seeked)
            start = stop

        self._generate(output, start, BLOCK_SIZE, sound, speed)
        return bus

    def _output_quiescent(self, output: Bus, input_quiescent: bool) -> bool:
        # Both halves are needed. `active` alone would keep a voice awake
        # after the gate fell mid-block, and `_sounded_this_block` alone would
        # retire one that was gated on at the very last sample of a block and
        # has not had a chance to emit anything yet.
        return not self._sounded_this_block and not self.active

    def _synchronise(self, sound: Sound, gate: bool, playhead: float, seeked: bool):
        """Fold register values into playback state, at one instant.

        `seeked` says the playhead was WRITTEN here, which is not the same as
        it holding a value. A write moves the head; a value merely says where
        the next gate edge will start.
        """
        if sound is not self._sound:
            # A new recording starts at the cue point, and inherits whatever
            # the gate is doing: swapped in under a held gate, it plays.
            self._sound = sound
            self.position = playhead * sound.rate
            self.active = gate
        if gate != self._gate:
            self._gate = gate
            if gate:
                self.position = playhead * sound.rate
            self.active = gate
        elif seeked:
            # Mid-playback, so the head moves under a sound that is already
            # running. Stopped, it moves silently and waits there.
            self.position = playhead * sound.rate

    def _generate(self, output, start: int, stop: int, sound: Sound, speed: float):
        length = stop - start
        if length <= 0:
            return
        samples = output[start:stop]
        if not self.active:
            samples.fill(0.0)
            return
        self._sounded_this_block = True

        data = sound.data
        last = len(data) - 1
        # One output sample advances this far through the recording: the two
        # rates and the speed register, in one number.
        step = sound.rate / SAMPLE_RATE * speed

        if step == 0.0:
            samples.fill(data[min(int(self.position), last)])
            return

        # How many more output samples can be read before the position walks
        # past the final stored sample. Computed rather than clipped, so the
        # tail of a recording is silence and not its last value repeated.
        available = max(int((last - self.position) // step) + 1, 0)
        count = min(length, available)

        if count:
            positions = self._positions[:count]
            multiply(_OFFSETS[:count], step, out=positions)
            add(positions, self.position, out=positions)

            indices = self._indices[:count]
            copyto(indices, positions, casting="unsafe")  # truncates, as floor
            values = self._values[:count]
            take(data, indices, out=values)
            # `positions` becomes the fraction between each pair, which is all
            # that is still wanted from it, while `indices` still holds the
            # lower sample of the pair.
            subtract(positions, indices, out=positions)

            # And now `indices` becomes the NEIGHBOUR above, the lower sample
            # having been read out of it already. Clamped at the last stored
            # sample so the final interpolation has something to reach for --
            # harmless, because at that point the fraction has reached zero.
            add(indices, 1, out=indices)
            minimum(indices, last, out=indices)
            neighbours = self._neighbours[:count]
            take(data, indices, out=neighbours)

            subtract(neighbours, values, out=neighbours)
            multiply(neighbours, positions, out=neighbours)
            add(values, neighbours, out=values)
            samples[:count] = values

        samples[count:].fill(0.0)
        self.position += length * step
        if count < length or self.position > last:
            self.active = False
