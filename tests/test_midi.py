"""MIDIHandler tests use a fake Mido backend: no hardware or GL required."""

import mido

from blitspersecond.input.midi import MIDIHandler


class FakePort:
    def __init__(self, name, callback):
        self.name = name
        self.callback = callback
        self.closed = False

    def send(self, message):
        self.callback(message)

    def close(self):
        self.closed = True


class FakeBackend:
    def __init__(self, names=("keys",)):
        self.names = list(names)
        self.ports = {}

    def get_input_names(self):
        return self.names

    def open_input(self, name, callback):
        port = FakePort(name, callback)
        self.ports[name] = port
        return port


def make(names=("keys",), refresh_ticks=60):
    backend = FakeBackend(names)
    midi = MIDIHandler(backend=backend, refresh_ticks=refresh_ticks)
    midi.open()
    return backend, midi


def test_note_edges_velocity_and_duration():
    backend, midi = make()
    backend.ports["keys"].send(
        mido.Message("note_on", channel=2, note=60, velocity=91)
    )
    midi.update()
    assert midi.pressed(60) and midi.held(60, channel=2)
    assert midi.velocity(60) == 91
    assert midi.held_for(60) == 0
    assert midi.held_notes[0].port == "keys"

    midi.update()
    assert not midi.pressed(60) and midi.held_for(60) == 1
    backend.ports["keys"].send(
        mido.Message("note_off", channel=2, note=60, velocity=20)
    )
    midi.update()
    assert midi.released(60) and not midi.held(60)


def test_zero_velocity_note_on_is_note_off_and_subtick_edges_survive():
    backend, midi = make()
    port = backend.ports["keys"]
    port.send(mido.Message("note_on", note=64, velocity=100))
    port.send(mido.Message("note_on", note=64, velocity=0))
    midi.update()
    assert midi.pressed(64) and midi.released(64)
    assert not midi.held(64)


def test_chords_channels_and_message_order():
    backend, midi = make()
    port = backend.ports["keys"]
    sent = [
        mido.Message("note_on", channel=0, note=60, velocity=70),
        mido.Message("note_on", channel=0, note=64, velocity=80),
        mido.Message("note_on", channel=1, note=67, velocity=90),
    ]
    for message in sent:
        port.send(message)
    midi.update()
    assert {note.note for note in midi.held_notes} == {60, 64, 67}
    assert midi.held(67, channel=1) and not midi.held(67, channel=0)
    assert [event.message for event in midi.messages] == sent


def test_controls_pitch_and_unabridged_messages():
    backend, midi = make()
    port = backend.ports["keys"]
    port.send(mido.Message("control_change", channel=3, control=64, value=127))
    port.send(mido.Message("pitchwheel", channel=3, pitch=-1200))
    port.send(mido.Message("aftertouch", channel=3, value=45))
    midi.update()
    assert midi.control(64, channel=3) == 127
    assert midi.pitch(channel=3) == -1200
    assert [event.message.type for event in midi.messages] == [
        "control_change",
        "pitchwheel",
        "aftertouch",
    ]


def test_hotplug_and_disconnect_release_held_notes():
    backend, midi = make(refresh_ticks=1)
    backend.ports["keys"].send(mido.Message("note_on", note=72, velocity=100))
    midi.update()
    assert midi.held(72)

    old_port = backend.ports["keys"]
    backend.names[:] = ["pads"]
    midi.update()  # refresh queues the disconnect after this tick's snapshot
    midi.update()
    assert old_port.closed
    assert midi.released(72) and not midi.held(72)
    assert midi.port_names == ("pads",)


def test_close_is_idempotent():
    backend, midi = make()
    port = backend.ports["keys"]
    midi.close()
    midi.close()
    assert port.closed and not midi.connected
