"""The Linux transport: find the pads, name them, calibrate them, read them.

Reports come out as ordered `(type, code, value)` triples; what a control
MEANS is the pad's business, not this layer's.

Design record: `bps/reference/input/evdev.md`.
"""

from __future__ import annotations

import errno
import fcntl
import os
import re
import struct
from dataclasses import dataclass
from typing import NamedTuple

from blitspersecond.system.logger import Logger

from .button import Button
from .control import Axis
from .mapper import PadMapper, mapper_for, model_for

_PROCFS = "/proc/bus/input/devices"

#: The button that says "this node is a gamepad". Selecting on a capability
#: rather than on a name or a handler admits exactly the right nodes: a
#: DualSense publishes three -- controller, motion sensors, touchpad -- and
#: only one of them claims a south face button.
BTN_SOUTH = 0x130

#: Valve. Refused because a Steam virtual pad is a PROXY concealing which real
#: device it stands in front of, and they multiply -- so one physical pad
#: arrives as several. Why refusing the vendor is the right cut, and why a pad
#: counted twice is worse than one counted never:
#: `bps/reference/input/evdev.md`.
_STEAM_VENDOR = "28de"


@dataclass(frozen=True, slots=True)
class PadDevice:
    """One gamepad Linux is currently offering, and how to open it."""

    #: This connection's identity, stable for as long as it exists. See
    #: `_fingerprint`.
    fingerprint: str
    #: The event node to open. NOT identity -- it moves across a replug.
    path: str
    #: The transport's own string, always present. `model` is the name a
    #: person would use, when the table knows this pad.
    name: str
    vendor: str
    product: str
    model: str | None
    mapper: type[PadMapper]


def _fingerprint(bus: str, uniq: str, phys: str) -> str | None:
    """What this connection is called, or None if it cannot be named.

    **The device names itself if it can; otherwise the port names it.**

        uniq  ->  the device's own serial or MAC
        phys  ->  the socket it is plugged into

    **It branches on what a device OFFERS, never on the bus.** A bus rule
    refuses a DualShock 4 on a cable outright -- `hid-playstation` names it by
    MAC and leaves `phys` empty on both links. Do not reintroduce one.

    `uniq` first because `phys` is the HOST ADAPTER on Bluetooth, shared by
    every device paired to it. None rather than a fallthrough, because an
    identity invented from an event node changes on the next replug.

    Why this is the rule, and the N64 trade it accepts:
    `bps/reference/input/evdev.md`, *Naming a connection*.
    """
    if uniq:
        return f"id:{uniq}"
    if phys:
        return f"port:{phys}"
    return None


def _field(block: str, pattern: str) -> str:
    match = re.search(pattern, block)
    return match.group(1) if match else ""


def _has_key_bit(block: str, code: int) -> bool:
    """Whether `code` is set in a procfs KEY capability mask."""
    match = re.search(r"B: KEY=(.*)", block)
    if not match:
        return False
    groups = match.group(1).split()
    groups.reverse()  # proc prints the most-significant group first
    index, bit = divmod(code, 64)
    if index >= len(groups):
        return False
    return bool(int(groups[index], 16) & (1 << bit))


def discover(procfs: str | None = None) -> dict[str, PadDevice]:
    """Every gamepad Linux is offering, by fingerprint.

    Pass `procfs` to parse a captured device list instead of the live one --
    how this is tested, and how "my pad is not detected" becomes reproducible
    without the pad.

    The three ways a device is not offered: `bps/reference/input/evdev.md`.
    """
    if procfs is None:
        try:
            procfs = open(_PROCFS).read()
        except OSError as exc:
            # No pads rather than an exception -- a game must run on a box
            # with no procfs. Said, because "no pads detected" with a
            # readable reason beats the same symptom with none.
            # Constructed here rather than at module scope: a Logger()
            # at import time installs a handler before anything has
            # configured one.
            Logger().warning(f"Cannot read {_PROCFS}, so no pads: {exc}.")
            return {}

    found: dict[str, PadDevice] = {}
    for block in procfs.split("\n\n"):
        identity = re.search(
            r"I: Bus=(\w+) Vendor=(\w+) Product=(\w+)", block
        )
        if identity is None or not _has_key_bit(block, BTN_SOUTH):
            continue
        bus, vendor, product = (part.lower() for part in identity.groups())
        if vendor == _STEAM_VENDOR:
            continue

        handlers = _field(block, r"Handlers=(.*)").split()
        event = next((h for h in handlers if h.startswith("event")), None)
        if event is None:
            continue

        fingerprint = _fingerprint(
            bus,
            _field(block, r"U: Uniq=(.*)").strip(),
            _field(block, r"P: Phys=(.*)").strip(),
        )
        if fingerprint is None or fingerprint in found:
            # A repeat cannot happen: Bluetooth MACs are distinct and two
            # devices cannot share one USB port path. If it ever does, the
            # first is kept rather than the last -- two pads sharing state
            # would be worse than one of them being missing, and missing is
            # the symptom somebody notices.
            continue

        found[fingerprint] = PadDevice(
            fingerprint=fingerprint,
            path=f"/dev/input/{event}",
            name=_field(block, r'N: Name="([^"]*)"') or "gamepad",
            vendor=vendor,
            product=product,
            model=model_for(vendor, product),
            mapper=mapper_for(vendor, product),
        )
    return found


# -- axes -------------------------------------------------------------------

ABS_X = 0x00
ABS_Y = 0x01
ABS_Z = 0x02
ABS_RX = 0x03
ABS_RY = 0x04
ABS_RZ = 0x05
ABS_HAT0X = 0x10
ABS_HAT0Y = 0x11

ABS_GAS = 0x09
ABS_BRAKE = 0x0A

#: The axes worth asking about: two sticks, two triggers, one d-pad -- and
#: `GAS`/`BRAKE`, which are where the triggers live under the generic HID
#: driver. They are queried even though most pads do not have them BECAUSE
#: most pads do not: their presence is what `layout_for` branches on.
AXES = (
    ABS_X,
    ABS_Y,
    ABS_Z,
    ABS_RX,
    ABS_RY,
    ABS_RZ,
    ABS_GAS,
    ABS_BRAKE,
    ABS_HAT0X,
    ABS_HAT0Y,
)

#: `struct input_absinfo`: value, minimum, maximum, fuzz, flat, resolution.
_ABSINFO = "iiiiii"
_ABSINFO_SIZE = struct.calcsize(_ABSINFO)


def _read_request(number: int, size: int) -> int:
    """Linux `_IOR('E', number, size)`, without a dependency for it."""
    return (2 << 30) | (0x45 << 8) | number | (size << 16)


class AxisRange(NamedTuple):
    """What one axis's numbers mean, as the kernel reports them.

    **Ranges are not uniform even within one pad** -- a wired Xbox One pad
    reports sticks over -32768..32767, triggers over 0..1023 and the hat over
    -1..1, all at once. Never assume one range.

    Only `minimum` and `maximum` are kept of the kernel's six fields. See
    `bps/reference/input/evdev.md`, *Asking what the numbers mean*.
    """

    minimum: int
    maximum: int

    def centred(self, value: int) -> float:
        """This axis as -1..1, neutral in the middle. For sticks and hats.

        The midpoint is integer, not floating: `(min + max + 1) // 2` gives 0
        for -32768..32767 and 128 for 0..255, which are the two common neutral
        values. A floating midpoint would put both of them slightly off centre
        forever.

        The two halves are scaled separately because they are not the same
        size -- a signed range has one more step below the midpoint than above
        it -- so a stick pushed fully either way reaches exactly 1.
        """
        centre = (self.minimum + self.maximum + 1) // 2
        extent = centre - self.minimum if value < centre else self.maximum - centre
        if extent <= 0:
            return 0.0
        return max(-1.0, min(1.0, (value - centre) / extent))

    def travel(self, value: int) -> float:
        """This axis as 0..1, zero at rest. For triggers.

        A trigger is not a stick with a centre: it rests at its minimum and
        one direction is all it has. Reading one through `centred` would
        report a released trigger as fully negative.
        """
        extent = self.maximum - self.minimum
        if extent <= 0:
            return 0.0
        return max(0.0, min(1.0, (value - self.minimum) / extent))


def open_node(path: str) -> int:
    """Open one event node for reading.

    Read-only and non-blocking, and **never `EVIOCGRAB`**: a grab is
    exclusive, so alt-tabbing out of a game would kill the controller for the
    browser, the desktop and Steam too. Sharing is how evdev works.
    See `bps/reference/input/evdev.md`.

    `O_NONBLOCK` is what lets the drain run to `EAGAIN` with no thread.
    """
    return os.open(path, os.O_RDONLY | os.O_NONBLOCK)


def axis_ranges(fd: int) -> dict[int, AxisRange]:
    """Ask the kernel what each of this device's axes means.

    One `EVIOCGABS` per axis. An axis the device does not have answers
    `EINVAL` or `ENOTTY` and is simply absent from the result -- that is how
    the set of axes a pad HAS is discovered, rather than by asking separately.

    **`fuzz` and `flat` are read and discarded, deliberately.** The kernel is
    describing NOISE; a deadzone is a judgement about INTENT and a much larger
    number, and it belongs to whatever reads the pad. Do not adopt `flat` as
    one -- the figures that settle it are in `bps/reference/input/evdev.md`.
    """
    ranges: dict[int, AxisRange] = {}
    for code in AXES:
        info = bytearray(_ABSINFO_SIZE)
        try:
            fcntl.ioctl(fd, _read_request(0x40 + code, _ABSINFO_SIZE), info, True)
        except OSError as exc:
            if exc.errno in (errno.EINVAL, errno.ENOTTY):
                # NOT a swallowed error. This is the probe's ANSWER: the
                # device has no such axis, which is how the set it does have
                # is discovered. Anything else is a real failure and raises.
                continue
            raise
        _value, minimum, maximum, _fuzz, _flat, _resolution = struct.unpack(
            _ABSINFO, bytes(info)
        )
        if minimum == maximum:
            # DECLARED BUT EMPTY. Real pads do this for controls they do not
            # have: both Bluetooth Xbox pads on the test desk declare
            # `ABS_RX`/`ABS_RY` this way, and a Switch Pro declares
            # `ABS_Z`/`ABS_RZ` this way because its triggers are digital and
            # arrive as buttons. An axis with no range is an axis that is not
            # there, so it is left out rather than carried as a zero-width one.
            continue
        ranges[code] = AxisRange(minimum, maximum)
    return ranges


# -- the vocabulary, at this transport's boundary ---------------------------
#
# Each transport translates its own codes to `Button`, so nothing downstream
# can tell which platform it is reading. **This table must not move onto
# `PadMapper`**: that would make one platform's numbering the engine's
# intermediate and translate the other twice. A mapper's job is what a pad
# CALLS its buttons; what a transport's numbers MEAN is its own business.
# See `bps/reference/input/evdev.md`, *The vocabulary is translated here*.

BTN_EAST = 0x131
BTN_WEST = 0x133
BTN_NORTH = 0x134
BTN_TL = 0x136
BTN_TR = 0x137
#: The digital trigger buttons. On a pad with analogue triggers these arrive
#: BESIDE the axis; on one without them they are the only way ZL and ZR ever
#: appear. See `layout_for`.
BTN_TL2 = 0x138
BTN_TR2 = 0x139

BTN_SELECT = 0x13A
BTN_START = 0x13B

#: Named so the absence below is visibly deliberate rather than an oversight.
#: The stick-clicks are never mapped -- see `Button`.
BTN_THUMBL = 0x13D
BTN_THUMBR = 0x13E

#: evdev code -> `Button`. Exhaustive: every member of the vocabulary that
#: arrives as a key appears exactly once, and nothing else is here.
BUTTONS: dict[int, Button] = {
    BTN_SOUTH: Button.FACE_SOUTH,
    BTN_EAST: Button.FACE_EAST,
    BTN_WEST: Button.FACE_WEST,
    BTN_NORTH: Button.FACE_NORTH,
    BTN_TL: Button.LB,
    BTN_TR: Button.RB,
    BTN_SELECT: Button.SELECT,
    BTN_START: Button.START,
}

#: evdev axis code -> `Axis`. The d-pad is absent on purpose: ABS_HAT0X/Y are
#: how evdev spells a d-pad, and a d-pad is not two axes. See `Direction`.
AXIS_NAMES: dict[int, Axis] = {
    ABS_X: Axis.LEFT_X,
    ABS_Y: Axis.LEFT_Y,
    ABS_RX: Axis.RIGHT_X,
    ABS_RY: Axis.RIGHT_Y,
    ABS_Z: Axis.LEFT_TRIGGER,
    ABS_RZ: Axis.RIGHT_TRIGGER,
}

#: Trigger AXES that become buttons. `LT` and `RT` do not arrive as keys: a
#: trigger is an axis, and a threshold turns its travel into edges. Which
#: threshold is the pad's judgement, not the transport's -- this only says
#: which axis becomes which button.
TRIGGERS: dict[int, Button] = {ABS_Z: Button.LT, ABS_RZ: Button.RT}


class Layout(NamedTuple):
    """Which control is which, for one particular device."""

    axes: dict[int, Axis]
    #: Axis code -> the `Button` its travel thresholds into.
    triggers: dict[int, Button]
    #: Key code -> `Button`. Per device because a pad with no trigger AXES
    #: reports ZL and ZR as plain buttons, and that is the only way they come.
    buttons: dict[int, Button]


#: The `xpad` layout: what a Microsoft pad looks like over USB, and what the
#: dedicated Sony drivers present too.
_XPAD = Layout(
    axes=dict(AXIS_NAMES),
    triggers=dict(TRIGGERS),
    buttons=dict(BUTTONS),
)

#: The generic-HID layout. `ABS_Z`/`ABS_RZ` are the RIGHT STICK here, not
#: triggers, and the triggers are on `ABS_GAS`/`ABS_BRAKE`.
_GENERIC = Layout(
    axes={
        ABS_X: Axis.LEFT_X,
        ABS_Y: Axis.LEFT_Y,
        ABS_Z: Axis.RIGHT_X,
        ABS_RZ: Axis.RIGHT_Y,
        ABS_GAS: Axis.RIGHT_TRIGGER,
        ABS_BRAKE: Axis.LEFT_TRIGGER,
    },
    triggers={ABS_GAS: Button.RT, ABS_BRAKE: Button.LT},
    buttons=dict(BUTTONS),
)

#: No trigger axes at all, so the digital buttons ARE the triggers. A Switch
#: Pro is this: ZL and ZR are switches, and without these two entries
#: `Button.LT` and `Button.RT` are unreachable on it forever.
_DIGITAL_TRIGGERS = Layout(
    axes={
        ABS_X: Axis.LEFT_X,
        ABS_Y: Axis.LEFT_Y,
        ABS_RX: Axis.RIGHT_X,
        ABS_RY: Axis.RIGHT_Y,
    },
    triggers={},
    buttons=dict(BUTTONS) | {BTN_TL2: Button.LT, BTN_TR2: Button.RT},
)


def layout_for(ranges: dict[int, AxisRange]) -> Layout:
    """Work out which axis is which, from what this device declares.

    **The layout follows the kernel DRIVER -- not the device, not the link.**
    A wired Xbox pad and a Bluetooth DualSense are both `xpad`; the same Xbox
    pad over Bluetooth is not.

    Decided by capability rather than identity, and a vendor:product table
    could not do it: it would have to encode which driver claims what on which
    kernel. The measurements, and what the wrong layout looks like on a real
    pad: `bps/reference/input/evdev.md`, *Which axis is which follows the
    DRIVER*.
    """
    if ABS_GAS in ranges and ABS_BRAKE in ranges:
        return _GENERIC
    if ABS_Z not in ranges and ABS_RZ not in ranges:
        # No trigger axes. `BTN_TL2`/`BTN_TR2` are then the only way ZL and
        # ZR arrive, so they become the triggers -- and they are mapped ONLY
        # here. A pad with both would have two sources for one control that
        # can disagree near the threshold, and the axis re-pressing after the
        # button let go would chatter.
        return _DIGITAL_TRIGGERS
    return _XPAD


# -- reading ----------------------------------------------------------------

EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03

SYN_REPORT = 0
SYN_DROPPED = 3

#: `struct input_event`: a timeval, then type, code, value.
_EVENT = "llHHi"
_EVENT_SIZE = struct.calcsize(_EVENT)

#: One bit per key through Linux `KEY_MAX` (0x2ff).
_KEY_BYTES = 0x300 // 8

#: How much to ask for per `read`. Not a limit -- the loop drains to EAGAIN --
#: just the size of each trip into the kernel.
_READ_EVENTS = 64


class Report(NamedTuple):
    """One complete kernel input report: what changed at the same instant.

    The kernel's own simultaneity group, ended by `SYN_REPORT`. Everything in
    it happened together, and the ORDER OF REPORTS is what makes a roll
    reconstructible rather than flattened into where the stick finished.

    `events` are raw `(type, code, value)` triples; translating them is the
    pad's job.
    """

    events: tuple[tuple[int, int, int], ...]


class Snapshot(NamedTuple):
    """What the device says is true NOW, asked for rather than observed.

    Emitted in place of the reports that were lost.

    **A reader must treat this as REPLACING its knowledge, never adding to
    it**: a button absent from `buttons` is up, including one it thought was
    held. A release lost in a dropped run leaves a button held for good, and
    only a wholesale replacement heals it. See
    `bps/reference/input/evdev.md`, *When the kernel drops*.
    """

    #: Raw evdev codes currently down. Translating them is the pad's job.
    buttons: frozenset[int]
    #: Raw axis code -> current value, for the axes the device has.
    axes: dict[int, int]


class PadReader:
    """One open pad, read once per frame.

    **There is no thread and no `select`, and none should be added.** The
    kernel already buffers each evdev fd until it is read, so a reader thread
    does the same job twice and buys a cross-thread handoff, a lock, and the
    engine's only concurrency. The engine is single threaded and polls.

    What it costs is freshness -- a frame's granularity, which the keyboard
    accepts too. What it risks is the buffer overrunning between cuts, which
    is `SYN_DROPPED` and has to be handled either way. See
    `bps/reference/input/evdev.md`, *Reading at the cut*.
    """

    __slots__ = (
        "_dropped_at",
        "_drops",
        "_fd",
        "_logger",
        "_pending",
        "_said_gone",
        "layout",
        "ranges",
    )

    def __init__(self, fd: int, ranges: dict[int, AxisRange]) -> None:
        self._fd = fd
        self.ranges = ranges
        #: Resolved once, at open, from what this device declares. Asked of
        #: the reader rather than looked up globally, because two pads on one
        #: desk can disagree about what `ABS_Z` means.
        self.layout = layout_for(ranges)
        # Events seen since the last SYN_REPORT. **Held across cuts on
        # purpose**: a report whose SYN_REPORT has not arrived yet is not
        # finished, and splitting it over two frames would report as
        # sequential two things the kernel is saying were simultaneous.
        self._pending: list[tuple[int, int, int]] = []
        self._drops = 0
        #: Where in this drain the stream stopped being trustworthy.
        self._dropped_at: int | None = None
        self._logger = Logger()
        #: Said once per CONNECTION, not per frame. A vanished device fails
        #: every read until the next discovery scan notices it, which is up
        #: to a second of them; a reconnect builds a fresh reader, so the
        #: flag resets exactly when the connection does.
        self._said_gone = False

    @property
    def drops(self) -> int:
        """How many times the kernel has dropped events on this device.

        Cumulative for as long as this connection lasts. Nonzero means input
        was lost; `PadScan.resynced` is what says which windows it landed in.
        """
        return self._drops

    def read(self) -> tuple[Report | Snapshot, ...]:
        """Drain whatever has arrived since the last call, in order.

        Returns complete reports only. Anything after the last `SYN_REPORT`
        stays pending for the next cut.
        """
        reports: list[Report | Snapshot] = []
        while True:
            try:
                data = os.read(self._fd, _EVENT_SIZE * _READ_EVENTS)
            except BlockingIOError:
                break  # Drained: the ordinary exit, every frame. Never log.
            except OSError as exc:
                # The device went away mid-read -- a flat battery or an
                # unplug. Ordinary, and discovery makes it official on the
                # next scan; said once so a dead pad does not narrate every
                # frame until then.
                if not self._said_gone:
                    self._said_gone = True
                    self._logger.info(
                        f"Pad fd {self._fd} stopped reading: {exc}."
                    )
                break
            if not data:
                # EOF on an evdev node, which should not happen: the node
                # either has events, says EAGAIN, or fails. Worth seeing.
                if not self._said_gone:
                    self._said_gone = True
                    self._logger.warning(
                        f"Pad fd {self._fd} read returned no data; "
                        "treating the device as gone."
                    )
                break
            self._frame(data, reports)
            if len(data) < _EVENT_SIZE * _READ_EVENTS:
                break  # Short read means drained. Ordinary. Never log.

        if self._dropped_at is not None:
            # Everything from the drop onwards is superseded. The snapshot is
            # taken AFTER the drain, so it already includes whatever those
            # reports would have said -- keeping them would apply stale
            # history on top of current truth, in the wrong order.
            del reports[self._dropped_at:]
            self._dropped_at = None
            self._pending.clear()
            reports.append(self.snapshot())
        return tuple(reports)

    def _frame(self, data: bytes, reports: list[Report | Snapshot]) -> None:
        for offset in range(0, len(data) - _EVENT_SIZE + 1, _EVENT_SIZE):
            # The timeval is unpacked and dropped. Within a frame everything
            # is simultaneous by construction, so a microsecond stamp would be
            # a second game clock; order survives the cut and time does not.
            _sec, _usec, kind, code, value = struct.unpack_from(
                _EVENT, data, offset
            )
            if kind == EV_SYN:
                if code == SYN_DROPPED:
                    # The partial report this belonged to has a hole in it, so
                    # it is discarded rather than emitted missing its middle.
                    # Where the stream stopped being trustworthy is recorded;
                    # the drain replaces everything from here with a snapshot.
                    self._pending.clear()
                    self._drops += 1
                    if self._dropped_at is None:
                        self._dropped_at = len(reports)
                elif code == SYN_REPORT and self._pending:
                    reports.append(Report(tuple(self._pending)))
                    self._pending.clear()
                continue
            if kind in (EV_KEY, EV_ABS):
                self._pending.append((kind, code, value))

    def snapshot(self) -> Snapshot:
        """Ask the device what is true now.

        `EVIOCGKEY` returns one bit per key for everything currently down, and
        `EVIOCGABS` carries each axis's current value in its first field. Both
        are the kernel's own record of device state, so neither depends on
        having seen the events that produced it -- which is exactly why this
        is the answer to a drop.

        Also the right thing on a resume, where ticks stopped and the world
        moved on without anybody watching.
        """
        buttons: set[int] = set()
        keys = bytearray(_KEY_BYTES)
        try:
            fcntl.ioctl(self._fd, _read_request(0x18, _KEY_BYTES), keys, True)
        except OSError as exc:
            # Answer "nothing held" rather than guess -- but this has a
            # consequence a reader will see: every held button reads as
            # released in the window that recovers.
            keys = bytearray(_KEY_BYTES)
            self._logger.warning(
                f"Pad fd {self._fd} would not report held buttons ({exc}); "
                "answering nothing held, so a held button will read released."
            )
        for index, byte in enumerate(keys):
            if byte:
                buttons.update(
                    index * 8 + bit for bit in range(8) if byte & (1 << bit)
                )

        axes: dict[int, int] = {}
        for code in self.ranges:
            info = bytearray(_ABSINFO_SIZE)
            try:
                fcntl.ioctl(
                    self._fd, _read_request(0x40 + code, _ABSINFO_SIZE), info, True
                )
            except OSError as exc:
                # The axis was calibrated at open, so failing now is the
                # device going away mid-snapshot rather than an absent
                # control. Left out; it keeps its last known value.
                self._logger.debug(
                    f"Pad fd {self._fd} would not report axis {code:#x} "
                    f"({exc}); it keeps its last value this frame."
                )
                continue
            axes[code] = struct.unpack(_ABSINFO, bytes(info))[0]
        return Snapshot(frozenset(buttons), axes)

    def close(self) -> None:
        """Close the node. Safe to call twice."""
        if self._fd >= 0:
            try:
                os.close(self._fd)
            except OSError as exc:
                # The fd is forgotten either way -- there is no second thing
                # to try -- but a close that fails means a descriptor leaked,
                # and a session leaking one per pad is worth being able to see.
                self._logger.warning(
                    f"Pad fd {self._fd} would not close: {exc}."
                )
            self._fd = -1


def open_pad(device: PadDevice) -> PadReader:
    """Open a discovered pad and ask it what its axes mean."""
    fd = open_node(device.path)
    try:
        return PadReader(fd, axis_ranges(fd))
    except BaseException:
        os.close(fd)
        raise
