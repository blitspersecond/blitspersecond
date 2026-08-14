"""Maintained synthetic graph fixture extracted from the Gordon experiment.

The clean render is just what enters the rack: four 36.7 Hz sub notes and a
gap, repeated once. The front render adds ring-modulated noise, saturation and
sample-rate/bit-depth reduction. The complete array adds ducked pedal grime,
ground hum, feedback echo and a small comb reverb.

"""

from blitspersecond.audio.common import SAMPLE_RATE
from blitspersecond.audio.engine import AudioEngine, Program
from blitspersecond.audio.operators import (
    Crush,
    Duck,
    Echo,
    Gain,
    LowPass,
    Noise,
    RingMod,
    Reverb,
    Sine,
    Square,
    Tanh,
)

SUB = 36.7
HUM = 41.2
# (t_on, t_off) -- four notes and a gap, twice over eight seconds.
NOTES = tuple(
    (on, on + 0.42)
    for bar in (0.0, 4.0)
    for on in (bar, bar + 0.55, bar + 1.10, bar + 1.65)
)


def ticks(seconds: float) -> int:
    return round(seconds * SAMPLE_RATE)


def gate(events, stage, level, source=None):
    for on, off in NOTES:
        if source is None:
            events.append((ticks(on), stage, {"level": level}))
            events.append((ticks(off), stage, {"level": 0.0}))
        else:
            events.append((ticks(on), stage, source, {"level": level}))
            events.append((ticks(off), stage, source, {"level": 0.0}))


def build(
    engine: AudioEngine,
    through_front: bool = False,
    through_array: bool = False,
):
    source = engine.source(Program(Sine, lambda: Gain(0.0)))
    events = [(0, source, {"frequency": SUB})]
    gate(events, source, 0.9)

    if not through_front and not through_array:
        engine.output = source
        engine.schedule(events)
        return source

    hiss = engine.source(Program(lambda: Noise(0)))
    carrier = engine.source(Program(Square))
    events.append((0, carrier, {"frequency": SUB}))

    modulation = engine.apply(Program(RingMod))
    modulation.connect(hiss)
    modulation.connect(carrier)

    ring = engine.composite(Program(lambda: LowPass(6_000.0)))
    ring.connect(modulation)
    gate(events, ring, 0.35, modulation)

    front_input = engine.composite()
    front_input.connect(source)
    front_input.connect(ring)

    front = engine.composite(
        Program(
            lambda: Tanh(7.0),
            lambda: Crush(bits=9, rate=16_000.0),
        )
    )
    front.connect(front_input)

    if not through_array:
        engine.output = front
        engine.schedule(events)
        return front

    dirt_source = engine.source(Program(lambda: Noise(1)))
    dirt_filter = engine.composite(Program(lambda: LowPass(700.0, q=0.8)))
    dirt_filter.connect(dirt_source)
    events.append((0, dirt_filter, dirt_source, {"level": 0.10}))
    dirt = engine.apply(
        Program(lambda: Duck(depth=3.0, attack=0.004, release=0.35))
    )
    dirt.connect(dirt_filter)
    dirt.connect(front)

    hum_source = engine.source(Program(Sine, lambda: Gain(0.07)))
    events.append((0, hum_source, {"frequency": HUM}))
    hum = engine.apply(
        Program(lambda: Duck(depth=3.0, attack=0.004, release=0.5))
    )
    hum.connect(hum_source)
    hum.connect(front)

    master = engine.composite()
    master.connect(front)
    master.connect(dirt)
    master.connect(hum)

    output = engine.composite(
        Program(
            lambda: Echo(
                round(0.375 * SAMPLE_RATE),
                feedback=0.35,
                mix=0.30,
            ),
            lambda: Reverb(0.35),
            lambda: Tanh(1.4),
        )
    )
    output.connect(master)
    engine.output = output
    engine.schedule(events)
    return output
