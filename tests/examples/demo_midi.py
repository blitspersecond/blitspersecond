"""MIDI score performed by a deterministic Stage-based fantasy sound chip.

The MIDI file provides notes and timing; the engine is the instrument — a
tone bank of Program patches with GM program numbers bucketed onto them,
the way Saturn games shipped sequences plus their own SCSP tone data
rather than General MIDI.

Voice allocation happens as a *scheduling pass*: a MIDI file is a score, so
per-MIDI-channel polyphony is measured up front, Stage pools are sized to the
score, and note allocation is resolved into one deterministic batch of
timestamped register writes before a single sample renders. Pitch wheel,
sustain pedal, program changes and channel volume are score policy over the
same register graph. Ignored for now: aftertouch, other CCs, and RPN-selected
pitch-bend ranges.

    python tests/examples/demo_midi.py [song.mid]     # no arg: built-in demo tune
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import mido
from blitspersecond.audio.common import SAMPLE_RATE
from blitspersecond.audio.driver import render_wav
from blitspersecond.audio.engine import AudioEngine, Program
from blitspersecond.audio.instruments import cymbal, drum, hat, kick, snare
from blitspersecond.audio.operators import (
    Duck,
    Echo,
    Envelope,
    Gain,
    LowPass,
    Noise,
    Reverb,
    Saw,
    Sine,
    Square,
    Tanh,
    Triangle,
)

SR = SAMPLE_RATE


# -- the tone bank: GM's 128 programs, proudly bucketed onto a few patches ----


def _voice(
    engine,
    source,
    *,
    attack,
    decay,
    sustain,
    release,
    cutoff=None,
    q=0.7071,
):
    operators = [source]
    if cutoff is not None:
        operators.append(lambda: LowPass(cutoff, q=q))
    operators.extend(
        (
            lambda: Envelope(attack, 0.0, decay, sustain, release),
            Gain,
        )
    )
    return engine.source(Program(*operators))


def gm_patch(program):
    """Map one GM program number to an AudioEngine -> SourceStage builder."""
    if program <= 7:  # pianos
        return lambda engine: _voice(
            engine,
            Square,
            attack=0.003,
            decay=0.15,
            sustain=0.25,
            release=0.05,
            cutoff=5_200.0,
        )
    if program <= 15:  # chromatic percussion
        return lambda engine: _voice(
            engine,
            Triangle,
            attack=0.003,
            decay=0.15,
            sustain=0.25,
            release=0.05,
        )
    if program <= 23:  # organs
        return lambda engine: _voice(
            engine,
            Square,
            attack=0.008,
            decay=0.10,
            sustain=0.60,
            release=0.08,
        )
    if program <= 31:  # guitars
        return lambda engine: _voice(
            engine,
            Saw,
            attack=0.003,
            decay=0.15,
            sustain=0.25,
            release=0.05,
            cutoff=4_600.0,
            q=1.2,
        )
    if program <= 39:  # basses
        return lambda engine: _voice(
            engine,
            Saw,
            attack=0.008,
            decay=0.10,
            sustain=0.60,
            release=0.08,
            cutoff=2_200.0,
            q=2.0,
        )
    if program <= 55:  # strings, ensembles
        return lambda engine: _voice(
            engine,
            Triangle,
            attack=0.06,
            decay=0.20,
            sustain=0.70,
            release=0.25,
        )
    if program <= 79:  # brass, reeds, pipes
        return lambda engine: _voice(
            engine,
            Square,
            attack=0.008,
            decay=0.10,
            sustain=0.60,
            release=0.08,
            cutoff=7_000.0,
        )
    if program <= 95:  # synth leads/pads
        return lambda engine: _voice(
            engine,
            Saw,
            attack=0.008,
            decay=0.10,
            sustain=0.60,
            release=0.08,
        )
    return lambda engine: _voice(
        engine,
        Triangle,
        attack=0.003,
        decay=0.15,
        sustain=0.25,
        release=0.05,
    )


def drum_patch(note):
    """Map one GM drum note to a tuned percussion Stage builder."""
    if note in (35, 36):  # kicks
        return lambda engine: kick(engine, level=1.2)
    if note in (38, 40, 37):  # snares / rimshot
        return lambda engine: snare(engine, seed=note, level=0.9)
    if note in (42, 44):  # closed hats
        return lambda engine: hat(engine, openness=0.0, level=0.6)
    if note == 46:  # open hat
        return lambda engine: hat(engine, openness=1.0, level=0.5)
    if note in (49, 51, 52, 55, 57, 59):  # cymbals
        return lambda engine: cymbal(engine, level=0.5)
    frequency = 75.0 * 2.0 ** ((note - 41) / 18.0)
    return lambda engine: drum(
        engine,
        frequency,
        decay=0.08,
        tone=2_400.0,
        drive=1.6,
        level=0.7,
    )


def note_freq(n):
    return 440.0 * 2.0 ** ((n - 69) / 12.0)


# -- score extraction ----------------------------------------------------------


def load_midi_file(path):
    """Load an SMF, tolerating out-of-range data bytes from loose exporters.

    Standard MIDI data bytes are seven-bit values. Some otherwise usable files
    contain values above 127; mido can clamp those values in ``clip`` mode, but
    strict parsing is retained for well-formed files so corruption is visible.
    """
    try:
        return mido.MidiFile(path)
    except OSError as exc:
        if str(exc) != "data byte must be in range 0..127":
            raise
        print(
            f"warning: {path}: out-of-range MIDI data bytes; " "clamping them to 127",
            file=sys.stderr,
        )
        return mido.MidiFile(path, clip=True)


def load_events(mid):
    """Extract notes and supported channel controls at real-second times.

    Every event has ``(time, channel, kind, value, auxiliary)`` shape. Note
    events use note and velocity for the final pair; control events use their
    program, wheel, or controller value followed by zero. ``programs`` holds
    only time-zero channel defaults; later changes remain ordered events.
    """
    events, programs = [], {}
    t = 0.0
    for msg in mid:
        t += msg.time
        if msg.type == "program_change":
            events.append((t, msg.channel, "program", msg.program, 0))
            if t == 0.0:
                programs[msg.channel] = msg.program
        elif msg.type == "note_on" and msg.velocity > 0:
            events.append((t, msg.channel, "on", msg.note, msg.velocity))
        elif msg.type in ("note_off", "note_on"):
            events.append((t, msg.channel, "off", msg.note, 0))
        elif msg.type == "pitchwheel":
            events.append((t, msg.channel, "bend", msg.pitch, 0))
        elif msg.type == "control_change" and msg.control == 64:
            events.append((t, msg.channel, "sustain", msg.value, 0))
        elif msg.type == "control_change" and msg.control == 7:
            events.append((t, msg.channel, "volume", msg.value, 0))
    return events, programs


def _program_pool_sizes(events, programs):
    """Return peak simultaneous voices for each ``(channel, program)``."""
    current_program = dict(programs)
    pedal = {}
    held = {}
    latched = {}
    active = {}
    peak = {}
    drum_notes = []

    def start(channel, program):
        key = (channel, program)
        active[key] = active.get(key, 0) + 1
        peak[key] = max(peak.get(key, 0), active[key])

    def stop(channel, program):
        key = (channel, program)
        active[key] -= 1

    for _time, channel, kind, value, _auxiliary in events:
        if kind == "program":
            current_program[channel] = value
            continue
        if channel == 9:
            if kind == "on" and value not in drum_notes:
                drum_notes.append(value)
            continue
        if kind == "sustain":
            down = value >= 64
            if pedal.get(channel, False) and not down:
                for _note, program in latched.pop(channel, ()):
                    stop(channel, program)
            pedal[channel] = down
            continue
        if kind == "on":
            key = (channel, value)
            program = current_program.get(channel, 0)
            previous = held.get(key)
            if previous is not None:
                if previous == program:
                    continue
                stop(channel, previous)
            held[key] = program
            start(channel, program)
            continue
        if kind == "off":
            key = (channel, value)
            program = held.pop(key, None)
            if program is None:
                continue
            if pedal.get(channel, False):
                latched.setdefault(channel, []).append((value, program))
            else:
                stop(channel, program)

    return {key: size for key, size in peak.items() if size > 0}, drum_notes


def pool_sizes(events, programs=None):
    """Voice Stages needed per MIDI channel.

    Melodic channels need their peak polyphony; the drum channel (9) needs
    one per distinct note because each tuned source is built once and then
    retriggered, exactly like a hardware tone bank."""
    detailed, drum_notes = _program_pool_sizes(events, programs or {})
    peak = {}
    for (channel, _program), size in detailed.items():
        peak[channel] = peak.get(channel, 0) + size
    if drum_notes:
        peak[9] = len(drum_notes)
    return peak, drum_notes


def schedule(
    engine,
    events,
    programs,
    offset=0.0,
    grime=False,
    patches=gm_patch,
    drums=drum_patch,
):
    """Resolve a MIDI score into a complete Stage graph and write batch.

    ``offset`` inserts silence before the score without changing the MIDI
    event data, which is useful when a realtime recording needs a lead-in.

    ``patches`` and ``drums`` are tone-bank lookups returning SourceStage
    builders. A song can supply its own voicings without altering the shared
    bank. MIDI velocity is ordinary gain automation; note life remains the
    two-state ``gate`` register used by every other instrument.
    """
    if not isinstance(engine, AudioEngine):
        raise TypeError(f"expected AudioEngine, got {type(engine)}")
    offset = float(offset)
    if not math.isfinite(offset) or offset < 0:
        raise ValueError("offset must be non-negative")
    events = tuple(events)
    program_pools, drum_notes = _program_pool_sizes(events, programs)

    pool = {}
    channels = {channel for channel, _program in program_pools}
    if drum_notes:
        channels.add(9)
    groups = {channel: engine.composite() for channel in channels}
    drum_voice = {}
    for key, size in program_pools.items():
        channel, program = key
        patch = patches(program)
        voices = [patch(engine) for _ in range(size)]
        for voice in voices:
            groups[channel].connect(voice)
        pool[key] = voices

    drum_voices = [drums(note)(engine) for note in drum_notes]
    drum_voice.update(zip(drum_notes, drum_voices))
    for voice in drum_voices:
        groups[9].connect(voice)

    # Each MIDI channel is first mixed to mono, then appears on the terminal
    # desk as one ordinary level-and-pan strip. The mono aggregate also feeds
    # the shared room effect and, optionally, the modeled machine-noise bed.
    timeline = []
    music = engine.composite()
    desk = engine.mixing_desk()
    faders = {}
    for channel, group in groups.items():
        fader = engine.composite()
        fader.connect(group)
        faders[channel] = fader
        music.connect(fader)
        desk.connect(fader)
        pan = ((channel * 37) % 100) / 100.0 * 1.2 - 0.6
        base_level = 0.31 if channel == 9 else 0.27
        timeline.append(
            (0, desk, fader, {"level": base_level, "pan": pan})
        )

    room = engine.composite(
        Program(
            lambda: Echo(round(0.26 * SR), feedback=0.25, mix=1.0),
            lambda: Reverb(0.18),
            lambda: Tanh(1.15),
        )
    )
    room.connect(music)
    desk.connect(room)
    timeline.append((0, desk, room, {"level": 0.07, "pan": 0.0}))

    if grime:
        # BFG Division's note/sub-note histogram is overwhelmingly F-sharp, so
        # its modeled ground hum sits at F#1 instead of demo_gordon's E1.
        dirt_source = engine.source(
            Program(lambda: Noise(1009), lambda: LowPass(700.0))
        )
        dirt = engine.apply(
            Program(lambda: Duck(depth=0.5, attack=0.02, release=0.35))
        )
        dirt.connect(dirt_source)
        dirt.connect(music)
        desk.connect(dirt)
        timeline.append((0, desk, dirt, {"level": 0.10, "pan": 0.0}))

        hum_source = engine.source(Program(Sine))
        hum = engine.apply(
            Program(lambda: Duck(depth=0.5, attack=0.02, release=0.35))
        )
        hum.connect(hum_source)
        hum.connect(music)
        desk.connect(hum)
        timeline.extend(
            (
                (0, hum_source, {"frequency": 46.2493}),
                (0, desk, hum, {"level": 0.036, "pan": 0.0}),
            )
        )

    current_program = dict(programs)
    bends = {}
    pedal = {}
    held = {}  # (channel, note) -> (SourceStage, program)
    latched = {}  # channel -> [(note, SourceStage, program)]
    free = {key: list(voices) for key, voices in pool.items()}
    scheduled = 0

    def frequency(channel, note):
        semitones = bends.get(channel, 0) / 8192.0 * 2.0
        return note_freq(note) * 2.0 ** (semitones / 12.0)

    def release(at, channel, voice, program):
        timeline.append((at, voice, {"gate": False}))
        free[(channel, program)].append(voice)

    for t, mch, kind, value, auxiliary in events:
        at = round((t + offset) * SR)
        if kind == "program":
            current_program[mch] = value
            continue
        if kind == "volume":
            fader = faders.get(mch)
            if fader is not None:
                timeline.append(
                    (
                        at,
                        fader,
                        groups[mch],
                        {"level": value / 127.0},
                    )
                )
            continue
        if kind == "bend":
            bends[mch] = value
            for (channel, note), (voice, _program) in held.items():
                if channel == mch:
                    timeline.append(
                        (at, voice, {"frequency": frequency(mch, note)})
                    )
            for note, voice, _program in latched.get(mch, ()):
                timeline.append(
                    (at, voice, {"frequency": frequency(mch, note)})
                )
            continue
        if kind == "sustain":
            down = value >= 64
            if pedal.get(mch, False) and not down:
                for _note, voice, program in latched.pop(mch, ()):
                    release(at, mch, voice, program)
            pedal[mch] = down
            continue
        if mch == 9:
            voice = drum_voice.get(value)
            if voice is None or kind == "off":  # unmapped note, or one-shot end
                continue
            # A false/true pair makes every strike an edge even when a one-shot
            # source is retriggered before the preceding gate is released.
            timeline.append((at, voice, {"gate": False}))
            timeline.append(
                (
                    at,
                    voice,
                    {"level": (auxiliary / 127.0) ** 1.5, "gate": True},
                )
            )
            scheduled += 1
            continue
        key = (mch, value)
        if kind == "off":
            active = held.pop(key, None)
            if active is not None:
                voice, program = active
                if pedal.get(mch, False):
                    latched.setdefault(mch, []).append(
                        (value, voice, program)
                    )
                else:
                    release(at, mch, voice, program)
            continue
        if kind != "on":
            continue

        program = current_program.get(mch, 0)
        active = held.get(key)
        if active is not None and active[1] == program:
            voice = active[0]  # same-note, same-patch retrigger
        else:
            if active is not None:
                release(at, mch, active[0], active[1])
            available = free[(mch, program)]
            if not available:
                raise RuntimeError("score voice-pool analysis was inconsistent")
            voice = available.pop(0)
        timeline.append((at, voice, {"gate": False}))
        timeline.append(
            (
                at,
                voice,
                {
                    "frequency": frequency(mch, value),
                    "level": (auxiliary / 127.0) ** 1.5,
                    "gate": True,
                },
            )
        )
        held[key] = (voice, program)
        scheduled += 1

    engine.output = desk
    engine.schedule(timeline)
    duration = offset + (events[-1][0] if events else 0.0) + 1.5
    print(
        f"pools: {pool_sizes(events, programs)[0]}  "
        f"notes={scheduled}  grime={grime}  "
        f"duration={duration:.1f}s"
    )
    return duration


# -- built-in fallback tune (also proves mido round-trip) ----------------------


def demo_tune():
    mid = mido.MidiFile()
    tr = mido.MidiTrack()
    mid.tracks.append(tr)
    tpb = mid.ticks_per_beat
    tr.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(126), time=0))
    tr.append(mido.Message("program_change", channel=0, program=33, time=0))
    tr.append(mido.Message("program_change", channel=1, program=81, time=0))

    bass = (36, 36, 43, 36, 41, 41, 39, 43)
    lead = (60, 63, 67, 70, 72, 70, 67, 63)
    events = []  # (abs_ticks, message args)
    for bar in range(8):
        b0 = bar * 4 * tpb
        for i, n in enumerate(bass):
            t0 = b0 + i * tpb // 2
            events.append((t0, "note_on", 0, n, 96))
            events.append((t0 + tpb // 2 - 10, "note_off", 0, n, 0))
        if bar >= 2:
            for i, n in enumerate(lead):
                t0 = b0 + i * tpb // 2
                nn = n + (12 if bar % 4 == 3 and i > 4 else 0)
                events.append((t0, "note_on", 1, nn, 80 + (i % 3) * 12))
                events.append((t0 + tpb // 3, "note_off", 1, nn, 0))
        for beat in range(4):
            t0 = b0 + beat * tpb
            events.append((t0, "note_on", 9, 36, 110))
            events.append((t0 + 30, "note_off", 9, 36, 0))
            events.append((t0 + tpb // 2, "note_on", 9, 42, 70))
            events.append((t0 + tpb // 2 + 20, "note_off", 9, 42, 0))
            if beat in (1, 3):
                events.append((t0, "note_on", 9, 38, 100))
                events.append((t0 + 40, "note_off", 9, 38, 0))
    events.sort(key=lambda e: e[0])
    prev = 0
    for abs_t, kind, ch, note, vel in events:
        tr.append(
            mido.Message(kind, channel=ch, note=note, velocity=vel, time=abs_t - prev)
        )
        prev = abs_t
    return mid


def main():
    if len(sys.argv) > 1:
        mid = load_midi_file(sys.argv[1])
        name = Path(sys.argv[1]).stem
    else:
        mid = demo_tune()
        name = "demo_tune"

    engine = AudioEngine()
    events, programs = load_events(mid)
    seconds = schedule(engine, events, programs)
    data = render_wav(
        engine,
        Path(__file__).parent / f"midi_{name}.wav",
        seconds,
    )
    print(
        f"midi_{name}.wav: {seconds:.1f}s  "
        f"rms={float((data ** 2).mean() ** 0.5):.4f}"
    )


if __name__ == "__main__":
    main()
