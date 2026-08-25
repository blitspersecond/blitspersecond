"""Snapshot discovered gamepads onto the engine tick.

Pads owns the live collection, stable first-use IDs and non-blocking
identification. Each Pad owns its normalised state. Virtual P1-P4 routing is a
separate concern in ``input.ports``.

    for pad in bps.pads:                 # every connected pad
        if pad.pressed(Button.FACE_SOUTH):
            jump(pad)
    first = bps.pads[1]                  # stable one-based identity

Platform discovery and report transport live in the internal ``evdev`` and
``gameinput`` modules.
"""

from __future__ import annotations

from collections import deque
import sys
from typing import TYPE_CHECKING, Any, Iterator, Optional

from blitspersecond.lifecycle import EngineState

from .codes import EV_ABS, EV_KEY
from .evdev import Device, Evdev, Report, Snapshot, key_state
from .gameinput import GameInput
from .identification import Identification
from .pad import Pad

if TYPE_CHECKING:
    from blitspersecond.system.events import EventBus

_REFRESH_TICKS = 60  # rescan for hot-plugged pads once a second
_IS_WINDOWS = sys.platform == "win32"


class Pads:
    """Every connected gamepad, enumerable, snapshotted once per tick.

    The engine opens every gamepad on STARTING, closes them on STOPPING, and
    rescans once a second so a pad picked up mid-session appears::

        for pad in bps.pads:
            if pad.pressed(Button.FACE_SOUTH):
                pad_jumped(pad)
        len(bps.pads)          # how many are plugged in -- no cap
        bps.pads[1]            # live pad with stable id 1
    """

    def __init__(
        self,
        events: Optional["EventBus"] = None,
        *,
        logger: Any = None,
        refresh_ticks: int = _REFRESH_TICKS,
    ) -> None:
        if refresh_ticks < 1:
            raise ValueError("refresh_ticks must be positive")
        self._logger = logger
        self._refresh_ticks = refresh_ticks
        # Transport -> tick handoff. Ordinary tests may inject a raw tuple
        # directly; evdev adds atomic Report/Snapshot items.
        self._queue: deque = deque()
        self._transport = (
            GameInput(self._queue, logger=logger)
            if _IS_WINDOWS
            else Evdev(self._queue, logger=logger)
        )
        # Every connected pad, in discovery order. UNBOUNDED:
        #   * No cap. Five pads can sit on a desk while only four sit on the
        #     sofa, so the four-route limit belongs to Ports.
        #     Capping here let a pad that was merely powered-on steal a slot
        #     from the pad someone actually picked up.
        #   * Discovery assigns no index. First deliberate input gives a pad
        #     its permanent Pads identity; reconnect reclaims it by fingerprint.
        #     This identity is independent of virtual Port mappings.
        self._pads: list[Pad] = []
        self._by_fingerprint: dict[str, Pad] = {}
        # Permanent physical proxies, including disconnected zombies retained
        # by Ports/Seats. _by_fingerprint above is only the live routing table.
        self._known_by_fingerprint: dict[str, Pad] = {}
        self._ids_by_fingerprint: dict[str, int] = {}
        # Like keyboard Input, identification is an engine-tick operation, not
        # a blocking wait. A newer request supersedes a stale one.
        self._identification: Optional[Identification] = None
        # Pads that vanished during THIS tick. They are off the list already,
        # but their force-release edges must stay readable for the rest of the
        # tick they died on (that IS the stop condition a game watches for), and
        # then be cleared. So they get one final _begin_tick next update, and
        # are dropped. Without this a retired pad would report released()==True
        # forever, which is the opposite of inert.
        self._retired: list[Pad] = []
        self._ticks = 0
        if events is not None:
            events.push_handlers(on_engine_state=self._on_engine_state)

    # -- lifecycle -----------------------------------------------------------

    def _on_engine_state(self, previous: EngineState, current: EngineState) -> None:
        if current is EngineState.STARTING:
            self.open()
        elif current is EngineState.STOPPING:
            self.close()

    def open(self) -> None:
        """Discover and open every gamepad, and start the reader thread."""
        self._relist(self._transport.open())

    def close(self) -> None:
        """Stop the reader and close all device fds. Safe to call repeatedly."""
        self._transport.close()
        for pad in self._known_by_fingerprint.values():
            pad._disconnect()
        self._pads = []
        self._by_fingerprint = {}
        self._known_by_fingerprint = {}
        self._ids_by_fingerprint = {}
        if self._identification is not None:
            self._identification._interrupt()
        self._identification = None
        self._retired = []

    # -- enumeration ---------------------------------------------------------

    def __iter__(self) -> Iterator[Pad]:
        """Every connected pad. This is the mainline: a game almost always
        wants "each pad", not "discovery entry 2"."""
        return iter(self._pads)

    def __len__(self) -> int:
        return len(self._pads)

    def __getitem__(self, id: int) -> Pad:
        """The connected pad with this stable one-based id."""
        pad = self.get(id)
        if pad is None:
            raise IndexError(f"no connected pad {id}") from None
        return pad

    def get(self, id: int) -> Optional[Pad]:
        """The connected pad with this stable id, or None."""
        return next((pad for pad in self._pads if pad.id == id), None)

    def identify(self) -> Identification:
        """Identify the next deliberately used Pad without blocking.

        Any deliberate input completes the request by default. Chain
        ``until(buttons)`` to restrict it to selected button presses.
        """
        if self._identification is not None:
            self._identification._interrupt()
        self._identification = Identification()
        return self._identification

    def _identify(self, pad: Pad) -> None:
        id = self._ids_by_fingerprint.get(pad.fingerprint)
        newly_identified = id is None
        if id is None:
            id = len(self._ids_by_fingerprint) + 1
            self._ids_by_fingerprint[pad.fingerprint] = id
        pad._identify(id)
        if newly_identified and self._logger is not None:
            self._logger.info(
                f"Pad identified: {pad.name} ({pad.fingerprint}) -> id {id}"
            )

    @property
    def gamepad(self) -> Optional[Pad]:
        """The connected identified Pad with the lowest stable id, or None."""
        identified = [pad for pad in self._pads if pad.id is not None]
        return min(identified, key=lambda pad: pad.id or 0, default=None)

    def refresh_devices(self) -> None:
        """Reconcile with the gamepads currently present: open the new ones,
        close the vanished ones, rebuild the pad list. Pads that are still here
        keep their existing Pad object -- and therefore their state -- so a
        rescan mid-hold is invisible to a game."""
        self._relist(self._transport.refresh())

    def _relist(self, present: dict[str, Device]) -> None:
        """Rebuild the live list, retaining objects which are still present."""
        kept, gone = [], []
        for pad in self._pads:
            (kept if pad.fingerprint in present else gone).append(pad)
        for pad in gone:
            # Force-release its held buttons as edges on THIS tick, then mark it
            # inert. A game or Port holding this object sees the release and the
            # disconnect; nothing sees a stuck button. _retired keeps the edges
            # readable until the next tick clears them.
            pad._disconnect()
            self._retired.append(pad)
            if self._logger is not None:
                self._logger.info(f"Pad disconnected: {pad.name} ({pad.fingerprint})")
        live = {pad.fingerprint for pad in kept}
        added = []
        for fp, device in present.items():
            if fp in live:
                continue
            pad = self._known_by_fingerprint.get(fp)
            if pad is None:
                pad = Pad(
                    fp,
                    name=device.name,
                    mapper=device.mapper,
                    model=device.model,
                    axis_ranges=device.axis_ranges,
                )
                self._known_by_fingerprint[fp] = pad
            else:
                pad._connect(
                    name=device.name,
                    mapper=device.mapper,
                    model=device.model,
                    axis_ranges=device.axis_ranges,
                )
                if pad in self._retired:
                    self._retired.remove(pad)
            added.append(pad)
        self._pads = kept + added
        self._by_fingerprint = {pad.fingerprint: pad for pad in self._pads}
        if self._logger is not None:
            for pad in added:
                identity = "unidentified" if pad.id is None else f"id {pad.id}"
                self._logger.info(
                    f"Pad connected: {pad.name} ({pad.mapper.__name__}, "
                    f"{pad.fingerprint}) -> {identity}"
                )

    # -- tick snapshot (main thread) -----------------------------------------

    def update(self) -> None:
        """Drain the reader's queue into this tick's coherent snapshot: open a
        new tick on every pad, then route each event to the pad it came from."""
        self._ticks += 1
        for pad in self._pads:
            pad._begin_tick(self._ticks)
        for pad in self._retired:  # one last tick, to clear their release edges
            pad._begin_tick(self._ticks)
        self._retired.clear()
        self._transport.poll()
        while self._queue:
            item = self._queue.popleft()
            if isinstance(item, Report):
                fingerprint = item.fingerprint
                events = item.events
            elif isinstance(item, Snapshot):
                fingerprint = item.fingerprint
                pad = self._by_fingerprint.get(fingerprint)
                if pad is None:
                    continue
                events = tuple(
                    (EV_KEY, code, key_state(item.keys, code))
                    for code in pad.mapper.BUTTON_MAP
                ) + tuple((EV_ABS, code, value) for code, value in item.axes.items())
            else:
                fingerprint, etype, code, value = item
                events = ((etype, code, value),)
            pad = self._by_fingerprint.get(fingerprint)
            if pad is None:
                continue  # events from a pad that just vanished: drop
            for etype, code, value in events:
                self._apply_event(pad, etype, code, value)
        if self._transport.running and (
            self._transport.changed
            or self._ticks % self._refresh_ticks == 0
        ):
            self.refresh_devices()

    def _apply_event(self, pad: Pad, etype: int, code: int, value: int) -> None:
        activated, pressed = pad._feed(etype, code, value)
        if pad.id is None and activated:
            self._identify(pad)
        identification = self._identification
        if identification is not None and identification.listening:
            identification._consider(pad, activated=activated, pressed=pressed)
            if identification.completed:
                self._identification = None
