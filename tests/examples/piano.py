"""A tiny polyphonic piano built from independent Stage voices.

The bottom of the screen is a two-octave-ish piano.  The home row plays the
white notes and the number row plays the black notes in the gaps where a real
piano has them::

    black:     2   3       5   6   7       9   0
    white:   Q   W   E   R   T   Y   U   I   O   P

The computer keyboard uses a 32-voice runtime pool, so every held note has an
independent envelope and chords release cleanly. ``Piano.tick`` also accepts a
tick-snapshotted MIDI view for the quarantined live-MIDI adapter; MIDI notes use
their real pitch and notes in the pictured range light the matching on-screen
key. MIDI velocity is transport data, not part of the synth Voice lifecycle.

Run from the repo root:  python tests/examples/piano.py
Close the window to quit.
"""

import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
ROOT = Path(__file__).resolve().parents[2]

from blitspersecond import BlitsPerSecond
from blitspersecond.audio.engine import AudioEngine, Program, Stage
from blitspersecond.audio.operators import Envelope, Gain, LowPass, Square
from blitspersecond.colors import CYAN, GRAY, NAVY, Pen, TRANSPARENT, WHITE
from blitspersecond.graphics import GlyphEngine, PixelBuffer, TileEngine
from blitspersecond.graphics.glyph import CharSet
from blitspersecond.input import Predicate, key
from blitspersecond.resources import ImageSpec
from blitspersecond.console import Console

WIDTH, HEIGHT = 640, 360
PIANO_TOP = 172
WHITE_TOP = 188
WHITE_BOTTOM = 344
WHITE_LEFT = 40
WHITE_WIDTH = 56
BLACK_WIDTH = 32
BLACK_BOTTOM = 282

@dataclass(frozen=True)
class PianoKey:
    symbol: Predicate
    label: str
    midi_note: int
    white_index: int
    black: bool = False


WHITE_KEYS = tuple(
    PianoKey(symbol, label, note, index)
    for index, (symbol, label, note) in enumerate(
        zip(
            (key.Q, key.W, key.E, key.R, key.T, key.Y, key.U, key.I, key.O, key.P),
            "QWERTYUIOP",
            (60, 62, 64, 65, 67, 69, 71, 72, 74, 76),
        )
    )
)

# white_index is the white key immediately to the left of the accidental.
BLACK_KEYS = tuple(
    PianoKey(symbol, label, note, white_index, black=True)
    for symbol, label, note, white_index in zip(
        (key._2, key._3, key._5, key._6, key._7, key._9, key._0),
        "2356790",
        (61, 63, 66, 68, 70, 73, 75),
        (0, 1, 3, 4, 5, 7, 8),
    )
)

KEYS = WHITE_KEYS + BLACK_KEYS
VOICE_COUNT = 32


def note_frequency(midi_note: int) -> float:
    return 440.0 * 2.0 ** ((midi_note - 69) / 12.0)


class PianoDisplay(Protocol):
    def draw(self, held: set[Predicate]) -> None: ...


class Piano:
    """Independent audio gates plus the pressed-key display state."""

    def __init__(self, audio: AudioEngine, view: PianoDisplay) -> None:
        self.audio = audio
        self.view = view
        self.voices = tuple(self._make_voice() for _ in range(VOICE_COUNT))
        self.desk = audio.mixing_desk()
        for voice in self.voices:
            self.desk.connect(voice)
        audio.output = self.desk
        audio.schedule(
            [
                (0, self.desk, voice, {"level": 0.16})
                for voice in self.voices
            ]
        )

        self._free = deque(self.voices)
        self._active = {}
        self._order = deque()

    def _make_voice(self) -> Stage:
        return self.audio.source(
            Program(
                Square,
                lambda: LowPass(7_000.0, q=0.9),
                lambda: Envelope(0.003, 0.0, 0.16, 0.48, 0.10),
                lambda: Gain(0.72),
            )
        )

    def _note_on(self, identity, note: int, events: list) -> None:
        if identity in self._active:
            voice = self._active[identity]
            self._order.remove(identity)
        elif self._free:
            voice = self._free.popleft()
        else:
            victim = self._order.popleft()
            voice = self._active.pop(victim)

        # Gate low then high is an explicit retrigger. Keeping both writes in
        # one schedule batch also keeps a chord on one unresolved audio
        # boundary if the device callback lands concurrently.
        events.append((0, voice, {"gate": False}))
        events.append(
            (
                0,
                voice,
                {"frequency": note_frequency(note), "gate": True},
            )
        )
        self._active[identity] = voice
        self._order.append(identity)

    def _note_off(self, identity, events: list) -> None:
        voice = self._active.pop(identity, None)
        if voice is None:  # already stolen, or note-off without note-on
            return
        events.append((0, voice, {"gate": False}))
        self._order.remove(identity)
        self._free.append(voice)

    def tick(self, keyboard, midi=None) -> None:
        changed = False
        events = []
        for piano_key in KEYS:
            identity = ("computer", piano_key.symbol)
            if keyboard.pressed(piano_key.symbol):
                self._note_on(identity, piano_key.midi_note, events)
                changed = True
            if keyboard.released(piano_key.symbol):
                self._note_off(identity, events)
                changed = True

        if midi is not None:
            for event in midi.messages:
                message = event.message
                if message.type not in ("note_on", "note_off"):
                    continue
                identity = ("midi", event.port, message.channel, message.note)
                # MIDI encodes note-off as either note_off or note_on with a
                # zero velocity. Normalize that transport quirk to gate up.
                if message.type == "note_on" and message.velocity > 0:
                    self._note_on(identity, message.note, events)
                else:
                    self._note_off(identity, events)
                changed = True

        if events:
            self.audio.schedule(events)

        if changed:
            held = {k.symbol for k in KEYS if keyboard.held(k.symbol)}
            if midi is not None:
                midi_notes = {note.note for note in midi.held_notes}
                held.update(k.symbol for k in KEYS if k.midi_note in midi_notes)
            self.view.draw(held)


class PianoView:
    """A full-screen RGB canvas whose piano occupies the lower half."""

    def __init__(self) -> None:
        self.buffer = PixelBuffer(ImageSpec(size=(WIDTH, HEIGHT), mode="RGB"))
        self.draw(set())

    @staticmethod
    def _rect(px, x0, y0, x1, y1, colour) -> None:
        px[y0:y1, x0:x1] = colour

    def draw(self, held: set[Predicate]) -> None:
        with self.buffer.edit() as px:
            # A restrained stage behind the bright keyboard.
            px[:] = (11, 14, 24)
            for y in range(PIANO_TOP):
                glow = max(0, y - 70) // 9
                px[y, :] = (11 + glow // 3, 14 + glow // 2, 24 + glow)
            self._rect(px, 0, PIANO_TOP, WIDTH, HEIGHT, (19, 24, 38))
            self._rect(px, 0, PIANO_TOP, WIDTH, PIANO_TOP + 3, (63, 210, 190))

            # Whites first; black keys overlap them, as on the instrument.
            for piano_key in WHITE_KEYS:
                x0 = WHITE_LEFT + piano_key.white_index * WHITE_WIDTH
                x1 = x0 + WHITE_WIDTH
                colour = (92, 220, 202) if piano_key.symbol in held else (236, 239, 232)
                self._rect(px, x0 + 1, WHITE_TOP, x1 - 1, WHITE_BOTTOM, colour)
                self._rect(px, x0 + 1, WHITE_BOTTOM - 5, x1 - 1, WHITE_BOTTOM, (141, 148, 151))

            for piano_key in BLACK_KEYS:
                centre = WHITE_LEFT + (piano_key.white_index + 1) * WHITE_WIDTH
                x0 = centre - BLACK_WIDTH // 2
                x1 = centre + BLACK_WIDTH // 2
                colour = (244, 101, 119) if piano_key.symbol in held else (31, 35, 47)
                self._rect(px, x0, WHITE_TOP, x1, BLACK_BOTTOM, colour)
                self._rect(px, x0 + 3, BLACK_BOTTOM - 7, x1 - 3, BLACK_BOTTOM, (10, 12, 18))


def make_labels() -> Console:
    console = Console(GlyphEngine(CharSet.ASCII8X12))
    console.pen = Pen(TRANSPARENT, CYAN, NAVY)
    title = "BLITS PER SECOND  /  POLYPHONIC PIANO"
    help_text = "hold computer keys together for a chord"
    console.cursor = ((console.cols - len(title)) // 2, 4)
    console.write(title)
    console.pen = Pen(TRANSPARENT, GRAY, NAVY)
    console.cursor = ((console.cols - len(help_text)) // 2, 7)
    console.write(help_text)

    # Cell centres line up with the pixel geometry above.
    console.pen = Pen(TRANSPARENT, WHITE, TRANSPARENT)
    for piano_key in WHITE_KEYS:
        col = (WHITE_LEFT + piano_key.white_index * WHITE_WIDTH + WHITE_WIDTH // 2) // 8
        console.cursor = (col, 27)
        console.write(piano_key.label)
    console.pen = Pen(TRANSPARENT, CYAN, NAVY)
    for piano_key in BLACK_KEYS:
        centre = WHITE_LEFT + (piano_key.white_index + 1) * WHITE_WIDTH
        console.cursor = (centre // 8, 18)
        console.write(piano_key.label)
    return console


def main() -> None:
    bps = BlitsPerSecond()
    view = PianoView()
    board = TileEngine(
        view.buffer,
        tilewidth=view.buffer.width,
        tileheight=view.buffer.height,
    )
    board.blit(0, 0, 0)
    labels = make_labels()  # the glyph layer lands above the board
    labels.glyphs.after(board)

    piano = Piano(bps.audio, view)
    bps.run(lambda engine: piano.tick(engine.keyboard))


if __name__ == "__main__":
    main()
