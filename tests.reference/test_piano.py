from types import SimpleNamespace

import numpy as np
import pytest

from blitspersecond.audio.engine import AudioEngine
from tests.examples.piano import Piano, PianoView, WHITE_KEYS


class FakeView:
    def __init__(self):
        self.held = set()

    def draw(self, held):
        self.held = held


class QuietKeyboard:
    def pressed(self, symbol):
        return False

    def released(self, symbol):
        return False

    def held(self, symbol):
        return False


def midi_with(*messages, held_notes=()):
    events = tuple(
        SimpleNamespace(port="controller", message=message) for message in messages
    )
    return SimpleNamespace(messages=events, held_notes=held_notes)


def test_piano_builds_independent_stage_voices_and_one_desk():
    audio = AudioEngine()
    piano = Piano(audio, FakeView())

    assert len(piano.voices) == 32
    assert len({id(voice) for voice in piano.voices}) == 32
    assert audio.output is piano.desk
    assert all("frequency" in voice.register_specs for voice in piano.voices)
    assert all("gate" in voice.register_specs for voice in piano.voices)


def test_midi_chord_uses_independent_voices():
    import mido

    audio, view = AudioEngine(), FakeView()
    piano = Piano(audio, view)
    messages = (
        mido.Message("note_on", note=60, velocity=30),
        mido.Message("note_on", note=64, velocity=110),
        mido.Message("note_on", note=67, velocity=80),
    )
    held = tuple(SimpleNamespace(note=n) for n in (60, 64, 67))
    piano.tick(QuietKeyboard(), midi_with(*messages, held_notes=held))
    samples = audio.advance().current.data.copy()
    voices = tuple(piano._active.values())

    assert len(voices) == 3 and len({id(voice) for voice in voices}) == 3
    assert [voice.registers["frequency"] for voice in voices] == pytest.approx(
        [261.625565, 329.627557, 391.995436]
    )
    assert all(voice.registers["gate"] is True for voice in voices)
    assert np.max(np.abs(samples)) > 0.0
    assert {key.symbol for key in WHITE_KEYS if key.midi_note in (60, 64, 67)} <= view.held


def test_midi_note_off_releases_only_its_voice():
    import mido

    audio, view = AudioEngine(), FakeView()
    piano = Piano(audio, view)
    piano.tick(
        QuietKeyboard(),
        midi_with(
            mido.Message("note_on", note=48, velocity=90),
            mido.Message("note_on", note=55, velocity=90),
        ),
    )
    audio.advance()
    first, second = tuple(piano._active.values())
    piano.tick(
        QuietKeyboard(),
        midi_with(mido.Message("note_off", note=48, velocity=0)),
    )
    audio.advance()

    assert first.registers["gate"] is False
    assert second.registers["gate"] is True
    assert tuple(piano._active.values()) == (second,)


def test_view_can_draw_midi_highlights_headlessly():
    view = PianoView()
    view.draw({WHITE_KEYS[0].symbol, WHITE_KEYS[2].symbol})
    assert view.buffer.dirty
