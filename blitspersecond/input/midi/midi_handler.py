"""Tick-aligned MIDI input over Mido's cross-platform RtMidi backend.

RtMidi invokes input callbacks on backend-owned threads.  Those callbacks do
the smallest safe thing possible: append the source port and immutable Mido
message to a deque.  ``update()`` drains that queue at the simulation boundary,
giving game code the same coherent pressed/released/held view as the keyboard.

QUARANTINED: the engine does not currently construct this handler, expose
``bps.midi``, or offer a live-MIDI install extra.  The implementation remains
here with its fake-backend tests while the ``supriya-midi`` upstream and
native-device restoration gates described in ``midi.txt`` are completed.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Optional

from blitspersecond.lifecycle import EngineState

if TYPE_CHECKING:
    from blitspersecond.system.events import EventBus

_REFRESH_TICKS = 60
_NoteIdentity = tuple[str, int, int]  # port, channel, note


def _default_backend(logger: Any = None) -> Optional[Any]:
    """Mido over RtMidi, or None if the extra was not installed.

    Reported once at INFO, not ERROR: a machine without the MIDI extra is a
    perfectly good machine that simply has no MIDI, which is the same thing it
    would say with the extra installed and nothing plugged in.  ImportError is
    the missing package; Exception covers a backend that imports but cannot
    initialise (no ALSA sequencer in a container, say) -- both mean "no MIDI
    here", and neither should stop a window opening.

    The probe call is load-bearing.  Mido resolves a backend LAZILY, so
    ``Backend("mido.backends.rtmidi")`` happily returns an object even when
    rtmidi is not installed, and the ImportError only surfaces on first real
    use -- which landed it in refresh_ports' error path and logged an ERROR for
    a normal machine.  That configuration is not exotic: mido is pure Python
    and is what a game installs to PARSE .mid scores, while rtmidi is the
    compiled half needed only for live ports.  Wanting score playback without a
    MIDI keyboard is the common case, so force the resolution here and classify
    it correctly.
    """
    try:
        import mido

        backend = mido.Backend("mido.backends.rtmidi")
        backend.get_input_names()  # force the lazy load; raises if absent
        return backend
    except ImportError:
        if logger is not None:
            logger.info(
                "MIDI backend not installed; quarantined MIDI input disabled."
            )
    except Exception as exc:  # backend present but unusable
        if logger is not None:
            logger.info(f"MIDI backend unavailable ({exc}); MIDI input disabled.")
    return None


@dataclass(frozen=True)
class MIDIEvent:
    """One message received during this tick, with its source port."""

    port: str
    message: Any


@dataclass(frozen=True)
class MIDINote:
    """One currently held physical MIDI note."""

    port: str
    channel: int
    note: int
    velocity: int
    held_for: int


class MIDIHandler:
    """Live MIDI ports presented as one coherent per-tick input device.

    The engine opens all available input ports on STARTING and closes them on
    STOPPING.  Once per second it refreshes the port list, so controllers
    connected after launch join automatically.  The common note queries are::

        midi.pressed(60)             # middle C began this tick
        midi.held(60, channel=0)
        midi.released(60)
        midi.velocity(60)
        midi.held_for(60)
        midi.held_notes              # source-aware MIDINote records

    ``messages`` preserves every MIDI message received this tick, including
    CC, aftertouch, program changes, clock and SysEx.  Convenience state is
    also retained for CC and pitch-wheel values.
    """

    def __init__(
        self,
        events: Optional["EventBus"] = None,
        *,
        backend: Any = None,
        logger: Any = None,
        refresh_ticks: int = _REFRESH_TICKS,
    ) -> None:
        if refresh_ticks < 1:
            raise ValueError("refresh_ticks must be positive")
        if backend is None:
            backend = _default_backend(logger)
        self._backend = backend
        self._logger = logger
        self._refresh_ticks = refresh_ticks
        self._queue: deque[tuple[str, str, Any]] = deque()
        self._ports: dict[str, Any] = {}
        self._held: dict[_NoteIdentity, tuple[int, int]] = {}
        self._pressed: set[_NoteIdentity] = set()
        self._released: set[_NoteIdentity] = set()
        self._messages: tuple[MIDIEvent, ...] = ()
        self._controls: dict[tuple[str, int, int], int] = {}
        self._pitch: dict[tuple[str, int], int] = {}
        self._ticks = 0
        self._running = False
        self._last_error: Optional[str] = None
        if events is not None:
            events.push_handlers(on_engine_state=self._on_engine_state)

    # -- lifecycle and ports -------------------------------------------------

    def _on_engine_state(self, previous: EngineState, current: EngineState) -> None:
        if current is EngineState.STARTING:
            self.open()
        elif current is EngineState.STOPPING:
            self.close()

    def open(self) -> None:
        """Start port discovery and open every currently available input."""
        self._running = True
        self.refresh_ports()

    def close(self) -> None:
        """Close all ports. Safe to call repeatedly."""
        self._running = False
        for name, port in tuple(self._ports.items()):
            try:
                port.close()
            except Exception as exc:
                self._report_error(f"Could not close MIDI input {name!r}: {exc}")
            finally:
                self._queue.append(("disconnect", name, None))
        self._ports.clear()

    def refresh_ports(self) -> None:
        """Reconcile open ports with the backend's current device list.

        With no backend there is nothing to reconcile and never will be, so
        this returns silently rather than reporting once a second: the absence
        was already announced once at construction."""
        if self._backend is None:
            return
        try:
            available = tuple(dict.fromkeys(self._backend.get_input_names()))
        except Exception as exc:
            self._report_error(f"MIDI input unavailable: {exc}")
            return

        self._last_error = None
        available_set = set(available)
        for name in tuple(self._ports):
            if name not in available_set:
                self._ports.pop(name).close()
                self._queue.append(("disconnect", name, None))

        for name in available:
            if name in self._ports:
                continue
            try:
                callback: Callable[[Any], None] = (
                    lambda message, source=name: self._queue.append(
                        ("message", source, message)
                    )
                )
                self._ports[name] = self._backend.open_input(
                    name, callback=callback
                )
                if self._logger is not None:
                    self._logger.info(f"MIDI input connected: {name}")
            except Exception as exc:
                self._report_error(f"Could not open MIDI input {name!r}: {exc}")

    def _report_error(self, message: str) -> None:
        if message != self._last_error and self._logger is not None:
            self._logger.error(message)
        self._last_error = message

    @property
    def port_names(self) -> tuple[str, ...]:
        """Names of all input ports currently open."""
        return tuple(self._ports)

    @property
    def connected(self) -> bool:
        return bool(self._ports)

    # -- tick snapshot -------------------------------------------------------

    def update(self) -> None:
        """Drain backend callbacks into this tick's coherent snapshot."""
        self._ticks += 1
        self._pressed.clear()
        self._released.clear()
        messages: list[MIDIEvent] = []
        while self._queue:
            kind, port, message = self._queue.popleft()
            if kind == "disconnect":
                self._release_port(port)
                continue
            # A backend callback already in flight can land just after its
            # port was closed. The queued disconnect is authoritative.
            if port not in self._ports:
                continue
            messages.append(MIDIEvent(port, message))
            self._take_message(port, message)
        self._messages = tuple(messages)

        if self._running and self._ticks % self._refresh_ticks == 0:
            self.refresh_ports()

    def _take_message(self, port: str, message: Any) -> None:
        kind = message.type
        channel = getattr(message, "channel", 0)
        if kind == "note_on" and message.velocity > 0:
            identity = (port, channel, message.note)
            self._held[identity] = (self._ticks, message.velocity)
            self._pressed.add(identity)
        elif kind == "note_off" or (kind == "note_on" and message.velocity == 0):
            identity = (port, channel, message.note)
            if self._held.pop(identity, None) is not None:
                self._released.add(identity)
        elif kind == "control_change":
            self._controls[(port, channel, message.control)] = message.value
        elif kind == "pitchwheel":
            self._pitch[(port, channel)] = message.pitch

    def _release_port(self, port: str) -> None:
        for identity in tuple(self._held):
            if identity[0] == port:
                self._held.pop(identity)
                self._released.add(identity)
        for identity in tuple(self._controls):
            if identity[0] == port:
                self._controls.pop(identity)
        for identity in tuple(self._pitch):
            if identity[0] == port:
                self._pitch.pop(identity)

    @property
    def messages(self) -> tuple[MIDIEvent, ...]:
        """Every message received this tick, in arrival order."""
        return self._messages

    # -- note queries --------------------------------------------------------

    @staticmethod
    def _matches(
        identity: _NoteIdentity,
        note: int,
        channel: Optional[int],
        port: Optional[str],
    ) -> bool:
        source, midi_channel, midi_note = identity
        return (
            midi_note == note
            and (channel is None or midi_channel == channel)
            and (port is None or source == port)
        )

    def held(
        self, note: int, channel: Optional[int] = None, port: Optional[str] = None
    ) -> bool:
        return any(self._matches(k, note, channel, port) for k in self._held)

    def pressed(
        self, note: int, channel: Optional[int] = None, port: Optional[str] = None
    ) -> bool:
        return any(self._matches(k, note, channel, port) for k in self._pressed)

    def released(
        self, note: int, channel: Optional[int] = None, port: Optional[str] = None
    ) -> bool:
        return any(self._matches(k, note, channel, port) for k in self._released)

    def held_for(
        self, note: int, channel: Optional[int] = None, port: Optional[str] = None
    ) -> int:
        starts = [
            started
            for identity, (started, _) in self._held.items()
            if self._matches(identity, note, channel, port)
        ]
        return 0 if not starts else self._ticks - min(starts)

    def velocity(
        self, note: int, channel: Optional[int] = None, port: Optional[str] = None
    ) -> int:
        velocities = [
            velocity
            for identity, (_, velocity) in self._held.items()
            if self._matches(identity, note, channel, port)
        ]
        return max(velocities, default=0)

    @property
    def held_notes(self) -> tuple[MIDINote, ...]:
        """Every physical note down, in press order and with source identity."""
        return tuple(
            MIDINote(port, channel, note, velocity, self._ticks - started)
            for (port, channel, note), (started, velocity) in self._held.items()
        )

    # -- continuous controls -------------------------------------------------

    def control(
        self,
        number: int,
        channel: Optional[int] = None,
        port: Optional[str] = None,
        default: int = 0,
    ) -> int:
        """Last value (0..127) seen for a MIDI continuous controller."""
        for (source, midi_channel, control), value in reversed(self._controls.items()):
            if (
                control == number
                and (channel is None or midi_channel == channel)
                and (port is None or source == port)
            ):
                return value
        return default

    def pitch(
        self,
        channel: Optional[int] = None,
        port: Optional[str] = None,
        default: int = 0,
    ) -> int:
        """Last pitch-wheel value (-8192..8191), centred at zero."""
        for (source, midi_channel), value in reversed(self._pitch.items()):
            if (channel is None or midi_channel == channel) and (
                port is None or source == port
            ):
                return value
        return default
