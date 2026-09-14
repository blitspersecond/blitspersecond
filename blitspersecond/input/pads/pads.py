"""A pad as a thing that persists, and the collection of them.

`Pad` is an identity that owns an ingress. The identity survives everything --
a flat battery, a replug, fifteen minutes of power-save -- and the ingress is
where the frames go. `Pads` discovers them and cuts them together.

See `docs/bps/PADS_PORT.md`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from blitspersecond.common import fps

from .control import Direction
from .evdev import PadDevice, Snapshot, discover, open_pad
from .mapper import PadMapper
from .pad import PadEvent, PadIngress, PadWindow

if TYPE_CHECKING:
    from blitspersecond.input.stream import InputStream

    from .pad import PadFact, PadScan


class _Gone:
    """The reader of a pad that is not here. Answers nothing, forever.

    A disconnected pad keeps being cut, because its ring must stay contiguous
    -- "the last thirty frames" has to mean thirty frames whether or not the
    pad was present for them. This is what it reads from meanwhile.

    It is also why the reference's `_retired` list has no counterpart here:
    that existed so a dead pad's release edges stayed readable for the tick it
    died on, and a per-device ring keeps them readable for as long as any
    other window.
    """

    ranges: dict[int, object] = {}

    def read(self) -> tuple[()]:
        return ()

    def snapshot(self) -> Snapshot:
        """Nothing held, nothing anywhere. A pad that is gone holds nothing.

        Reachable when a pad disconnects between a resume and the cut that
        answers it, which is a real ordering rather than a hypothetical one.
        """
        return Snapshot(frozenset(), {})

    def close(self) -> None:
        pass


_GONE = _Gone()


class Pad:
    """One physical gamepad, for as long as this session remembers it.

    **The object outlives the connection.** A pad that vanishes becomes inert
    rather than disappearing: `connected` goes false, its held buttons release
    as real edges, and anything holding it keeps a valid reference. The same
    fingerprint reconnecting revives this object rather than making a new one,
    so a game holding `pad` still holds the player's pad afterwards.

    That matters because a pad vanishing is ORDINARY. Wireless pads power
    themselves off after about fifteen minutes untouched -- measured, and it
    happened twice during this port -- so a long cutscene is enough.
    """

    __slots__ = ("_connected", "_device", "_id", "_ingress", "_reader")

    def __init__(self, device: PadDevice, reader, rate: fps) -> None:
        self._device = device
        self._reader = reader
        self._ingress = PadIngress(reader, rate)
        self._connected = True
        self._id: int | None = None

    # -- identity ---------------------------------------------------------

    @property
    def fingerprint(self) -> str:
        """What this connection is called. Stable for as long as it exists."""
        return self._device.fingerprint

    @property
    def name(self) -> str:
        """The name a person would use, when the table knows this pad.

        Falls back to the transport's own string, which is always present and
        is usually serviceable -- "Microsoft X-Box One pad" is nobody's idea
        of editorial writing but it is not wrong.
        """
        return self._device.model or self._device.name

    @property
    def mapper(self) -> type[PadMapper]:
        """This pad's label family. Ask it what the plastic says."""
        return self._device.mapper

    @property
    def id(self) -> int | None:
        """A stable number, or None until this pad has been used.

        Handed out on first DELIBERATE input, never on discovery, which is
        what keeps a pad that was merely powered on from taking the number
        belonging to the pad somebody picked up. See `Pads.gamepad`.
        """
        return self._id

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def stream(self) -> "InputStream[PadScan, PadFact]":
        """This pad's settled history, newest last."""
        return self._ingress.stream

    # -- driven by Pads ---------------------------------------------------

    def _cut(self, frame: int) -> PadWindow:
        return self._ingress.cut(frame)

    def _identify(self, id: int) -> None:
        if self._id is not None and self._id != id:
            raise ValueError(
                f"pad {self.fingerprint} already has id {self._id}"
            )
        self._id = id

    def _resync(self) -> None:
        """Ask the device what is true, and believe it over what we thought."""
        self._ingress.resync()

    def _disconnect(self) -> None:
        """The device went away. Release what it held and go inert."""
        if not self._connected:
            return
        self._ingress.disconnect()
        self._ingress.retire(_GONE)
        self._reader.close()
        self._reader = _GONE
        self._connected = False

    def _reconnect(self, device: PadDevice, reader) -> None:
        """The same physical pad is back. Same object, same id, same ring.

        The ring is kept rather than started again: the windows from before
        the gap are still honest history of what this pad did, and a
        recogniser mid-motion when the battery died should see the gap rather
        than a pad with no past.
        """
        self._device = device
        self._reader = reader
        self._ingress.retire(reader)
        self._connected = True

    def __repr__(self) -> str:
        state = "connected" if self._connected else "disconnected"
        where = "unidentified" if self._id is None else f"id {self._id}"
        return f"<Pad {self.name!r} {state} {where}>"


#: How often to look for pads that have arrived or gone, in frames.
#: Once a second at the authored rate. Linux offers no hot-plug callback a
#: game's own loop can service, so this is a scan rather than a notification.
REFRESH_FRAMES = 60

#: How far a stick must be shoved to count as somebody using this pad, rather
#: than the pad sitting on a desk drifting. Deliberately far past any
#: deadzone: identifying the wrong pad because one of them twitched is a
#: worse failure than making a player push a bit harder.
ACTIVATION = 2 / 3


class Pads:
    """Every gamepad this session knows about, cut together.

    **Two ways to reach a pad, and they are different modes rather than one
    design with a convenience attached.**

    *Wiggle a pad.* A single-player game never thinks about identity: whatever
    somebody used is `pads.gamepad`, and the title screen asks for
    `pad.mapper.label(...)` so it says "Press A" or "Press cross" as
    appropriate. Nothing has to be assigned and nothing has to be configured.

    *Ports.* Four people on a sofa and which pad is whose is the whole
    problem. That is a separate subsystem above this one, and it maps
    explicitly rather than inferring from who pressed something first.

    This is the discovery layer both rest on. It has NO CAP: five pads can sit
    on a desk while four sit on the sofa, and limiting the list here would let
    a pad that was merely powered on take a place from one somebody picked up.
    """

    __slots__ = ("_known", "_rate", "_refresh", "_next_id", "_frames")

    def __init__(self, rate: fps, refresh_frames: int = REFRESH_FRAMES) -> None:
        if refresh_frames < 1:
            raise ValueError("refresh_frames must be positive")
        self._rate = rate
        self._refresh = refresh_frames
        #: Every pad ever seen this session, by fingerprint, connected or not.
        #: Permanent: a disconnected pad stays so the same object revives.
        self._known: dict[str, Pad] = {}
        self._next_id = 1
        self._frames = 0

    # -- enumeration ------------------------------------------------------

    def __iter__(self):
        """Every CONNECTED pad. The mainline: a game wants each pad."""
        return iter([pad for pad in self._known.values() if pad.connected])

    def __len__(self) -> int:
        return sum(1 for pad in self._known.values() if pad.connected)

    def __getitem__(self, id: int) -> Pad:
        pad = self.get(id)
        if pad is None:
            raise IndexError(f"no connected pad with id {id}")
        return pad

    def get(self, id: int) -> Pad | None:
        """The connected pad with this stable id, or None."""
        return next(
            (
                pad
                for pad in self._known.values()
                if pad.connected and pad.id == id
            ),
            None,
        )

    @property
    def gamepad(self) -> Pad | None:
        """The pad a single-player game should listen to, or None.

        The lowest id among connected pads that have been used -- so it is
        whoever picked one up first, and it does NOT change when somebody else
        joins later. That stability is the point: a title screen showing the
        right glyph must not swap to a different pad's letters because a
        second controller woke up.
        """
        used = [
            pad for pad in self._known.values() if pad.connected and pad.id
        ]
        return min(used, key=lambda pad: pad.id or 0, default=None)

    @property
    def known(self) -> tuple[Pad, ...]:
        """Every pad this session has seen, including departed ones.

        Departed pads are kept because they come back: the same fingerprint
        revives the same object, with its id and its history. A `Ports` layer
        holds these, which is why they must not be discarded.
        """
        return tuple(self._known.values())

    # -- the engine's boundary --------------------------------------------

    def cut(self, frame: int) -> None:
        """Settle every pad's interval. Called once per frame by `Loop`.

        Discovery runs first, so a pad plugged in between frames is cut on
        this one rather than waiting for the next.

        **Every KNOWN pad is cut, not every connected one.** A departed pad's
        ring has to stay contiguous or "the last thirty frames" would mean
        thirty frames of presence rather than thirty frames, and a recogniser
        reading across a dropout would silently splice two moments together.
        """
        if self._frames % self._refresh == 0:
            self.refresh()
        self._frames += 1

        for pad in self._known.values():
            window = pad._cut(frame)
            if pad.id is None and pad.connected and _deliberate(window):
                pad._identify(self._next_id)
                self._next_id += 1

    def flush(self) -> None:
        """Resync every pad against what is actually true.

        Called on resume. Ticks stopped and the world moved on unwatched, so
        anything the kernel buffered meanwhile describes a game nobody was
        playing -- but unlike the keyboard, a pad can be ASKED. A player who
        held a direction through the pause still holds it, and saying
        otherwise would be inventing a release they did not perform.
        """
        for pad in self._known.values():
            if pad.connected:
                pad._resync()

    def refresh(self) -> None:
        """Reconcile against the pads that are actually here."""
        present = discover()

        for fingerprint, pad in self._known.items():
            if pad.connected and fingerprint not in present:
                pad._disconnect()

        for fingerprint, device in present.items():
            pad = self._known.get(fingerprint)
            if pad is not None and pad.connected:
                continue
            try:
                reader = open_pad(device)
            except OSError:
                # Permissions, or it left between the scan and the open.
                # Neither is exceptional and the next refresh tries again.
                continue
            if pad is None:
                self._known[fingerprint] = Pad(device, reader, self._rate)
            else:
                pad._reconnect(device, reader)

    def close(self) -> None:
        """Release every device. Safe to call twice."""
        for pad in self._known.values():
            pad._disconnect()


def _deliberate(window: PadWindow) -> bool:
    """Did somebody just USE this pad, as opposed to it sitting there?

    Read off the settled window rather than signalled up from the transport,
    because the window already says what happened and a second channel for
    the same information is a second thing to keep in step.

    A press counts, and so does a d-pad leaving neutral. A stick counts only
    when it is shoved most of the way, because a pad at rest drifts -- the one
    on this desk sits around 1050 of 32767 and wanders while nobody touches
    it.
    """
    for fact in window.facts:
        if fact.kind is PadEvent.PRESS:
            return True
        if fact.kind is PadEvent.DIRECTION and fact.direction is not Direction.NEUTRAL:
            return True
        if fact.kind is PadEvent.AXIS and abs(fact.value) > ACTIVATION:
            return True
    return False
