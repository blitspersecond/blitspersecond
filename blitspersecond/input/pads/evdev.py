"""Finding the gamepads Linux has, and deciding what each one is called.

Discovery only: this reads `/proc` and answers "which pads are here, and what
is each one's identity". It opens nothing and reads no input. The fds, the
calibration and the reader thread are the transport proper and sit above this.

Identity is the part with a ruling behind it -- see `docs/bps/PADS_PORT.md`,
*Identity: measured, not assumed*.
"""

from __future__ import annotations

import errno
import fcntl
import os
import re
import struct
from dataclasses import dataclass
from typing import NamedTuple

from .button import Button
from .control import Axis
from .mapper import PadMapper, mapper_for, model_for

_PROCFS = "/proc/bus/input/devices"

#: The button that says "this node is a gamepad". Selecting on a capability
#: rather than on a name or a handler admits exactly the right nodes: a
#: DualSense publishes three -- controller, motion sensors, touchpad -- and
#: only one of them claims a south face button.
BTN_SOUTH = 0x130

#: Bus types, as `/proc` prints them.
_BUS_BLUETOOTH = "0005"

#: Valve. Refused, and the refusal is not about weak identity: a Steam virtual
#: pad is a PROXY standing in front of a real device and concealing which one.
#: Every controller routed through it presents as the same vendor:product, so
#: N pads arrive as N copies of one thing. Refusing the vendor is how a
#: physical pad is seen once, as itself, instead of once directly and again
#: through something that answers to input and looks like it works.
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
    """What this CONNECTION is called, or None if it cannot be named.

    Two branches, each true for a reason, rather than a ladder of fallbacks:

    **Bluetooth names the device.** There is no socket -- a pairing is not a
    port -- and `uniq` is the pad's own MAC, which the protocol guarantees it
    has. `phys` is unusable here and must never be reached for: it is the HOST
    ADAPTER, shared by every Bluetooth device on the machine, so two pads
    would produce one fingerprint and one would silently overwrite the other.
    Measured: an Xbox Wireless Controller and a pair of headphones on this
    desk report the identical `phys`.

    **Everything else names the port.** A USB device is enumerated THROUGH a
    socket, so `phys` is a real path and always present, and it survives the
    replug and the re-enumeration that move the event node. A pad moved to a
    different socket becomes a different pad, which is the N64 rule: swapping
    two cables swaps two players, and nobody has ever been confused by it.

    **None rather than a fallthrough.** A Bluetooth pad with no `uniq` would
    land on the shared adapter and merge with its neighbours; a device with no
    `phys` has nothing to be named by at all. Refusing is visible. The
    fallthrough is the failure with no symptom.

    Identity is needed PER SESSION -- long enough to survive a flat battery and
    a replug during a run -- so neither branch needs to outlive the process,
    and nothing here is written down anywhere.
    """
    if bus == _BUS_BLUETOOTH:
        return f"bt:{uniq}" if uniq else None
    return f"port:{phys}" if phys else None


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

    Pass `procfs` to parse a captured device list instead of reading the live
    one -- which is how this is tested, and how a bug report of the form "my
    pad is not detected" becomes reproducible without the pad.

    Devices that cannot be named are not returned. That is deliberate and
    there are three ways to earn it: Valve's vendor, a Bluetooth pad with no
    MAC, and anything with no port and no MAC at all.
    """
    if procfs is None:
        try:
            procfs = open(_PROCFS).read()
        except OSError:
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

    **Ranges are not universal, and not even uniform within a pad.** A wired
    Xbox One pad reports sticks over -32768..32767, triggers over 0..1023 and
    the hat over -1..1, all at once. Read on 2026-09-11 from the pad on the
    desk. Assume one range and a fully pressed trigger reads as three percent.

    Only `minimum` and `maximum` are kept of the six fields the kernel
    returns. `fuzz` and `flat` are deliberately dropped -- see `axis_ranges`.
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

    Read-only and non-blocking, and **never `EVIOCGRAB`**. A grab is
    exclusive: it would take the pad away from the browser, the desktop and
    Steam, so alt-tabbing out of a game would kill the controller everywhere
    else. Measured on 2026-09-11 -- every pad node on that desk was held open
    by three processes at once, all of them reading happily. Sharing is how
    evdev works and asking for more is not ours to do.
    """
    return os.open(path, os.O_RDONLY | os.O_NONBLOCK)


def axis_ranges(fd: int) -> dict[int, AxisRange]:
    """Ask the kernel what each of this device's axes means.

    One `EVIOCGABS` per axis. An axis the device does not have answers
    `EINVAL` or `ENOTTY` and is simply absent from the result -- that is how
    the set of axes a pad HAS is discovered, rather than by asking separately.

    **`fuzz` and `flat` are read and discarded, deliberately.** `flat` is the
    kernel's declared deadzone and it is not the one a game wants: the pad on
    this desk declares `flat=128` and rests at 1054, eight times that, so a
    flat-sized deadzone would let its own drift through. The kernel is
    describing NOISE; a deadzone is a judgement about INTENT, it belongs with
    the pad rather than the transport, and it is a much larger number.
    """
    ranges: dict[int, AxisRange] = {}
    for code in AXES:
        info = bytearray(_ABSINFO_SIZE)
        try:
            fcntl.ioctl(fd, _read_request(0x40 + code, _ABSINFO_SIZE), info, True)
        except OSError as exc:
            if exc.errno in (errno.EINVAL, errno.ENOTTY):
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
# `INPUT_STREAM.md`: *"an evdev code and a GameInput bitfield describe the same
# physical button with different numbers, so both are translated to
# Button.FACE_SOUTH AT THEIR OWN BOUNDARY, and nothing downstream can tell
# which platform it is reading."*
#
# So this table lives here rather than on `PadMapper`. The reference put it on
# the mapper and had its Windows transport normalise into evdev codes to reuse
# it -- which makes one platform's numbering the engine's intermediate, and
# means a GameInput reading is translated twice to arrive somewhere it could
# have reached directly. A mapper's job is what a pad CALLS its buttons; what
# a transport's numbers MEAN is the transport's own business.

BTN_SOUTH = 0x130
BTN_EAST = 0x131
BTN_WEST = 0x133
BTN_NORTH = 0x134
BTN_TL = 0x136
BTN_TR = 0x137
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
    """Which evdev axis is which control, for one particular device."""

    axes: dict[int, Axis]
    triggers: dict[int, Button]


#: The `xpad` layout: what a Microsoft pad looks like over USB, and what the
#: dedicated Sony drivers present too.
_XPAD = Layout(
    axes=dict(AXIS_NAMES),
    triggers=dict(TRIGGERS),
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
)


def layout_for(ranges: dict[int, AxisRange]) -> Layout:
    """Work out which axis is which, from what this device declares.

    **The layout follows the kernel driver, not the device and not the link.**
    Measured across six pads on 2026-09-14: a wired Xbox pad and a Bluetooth
    DualSense both use the `xpad` arrangement, while the same Xbox pad over
    Bluetooth does not -- Sony pads get a dedicated driver and Xbox pads over
    Bluetooth fall through to generic HID.

    The consequence of getting it wrong is not subtle. Read a Bluetooth Xbox
    pad through the `xpad` layout and its right stick is dead while both
    triggers report permanently held, because `ABS_Z`/`ABS_RZ` are sticks
    resting near the middle of a 0..65535 range -- 0.499 travel against a
    0.118 threshold.

    **So it is decided by capability rather than by identity.** A device that
    declares `ABS_GAS` and `ABS_BRAKE` has its triggers there; nothing else
    does. That is a duck test, and it is the only practical option anyway:
    since the layout follows the driver, a vendor:product table would have to
    encode which driver claims what on which kernel, and that is precisely the
    two-thousand-entry problem this engine refuses.
    """
    if ABS_GAS in ranges and ABS_BRAKE in ranges:
        return _GENERIC
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

    A report is the kernel's own simultaneity group, ended by `SYN_REPORT`.
    Everything in it happened together, and the ORDER OF REPORTS is what makes
    a roll through down, down-forward, forward reconstructible rather than
    flattened into wherever the stick finished.

    `events` are raw `(type, code, value)` triples. Translating them into
    `Button` and axis values is the pad's job, not the transport's.
    """

    events: tuple[tuple[int, int, int], ...]


class Snapshot(NamedTuple):
    """What the device says is true NOW, asked for rather than observed.

    Emitted in place of the reports that were lost. Where a `Report` says
    "these changed together", this says "this is the state, never mind how it
    got here" -- which is all that can honestly be offered after a drop.

    A reader applying this must treat it as REPLACING its knowledge rather
    than adding to it: a button absent from `buttons` is up, including one it
    thought was held. That is the whole point. The failure a drop causes is a
    release landing in the lost run and leaving a button held for good, and
    only a wholesale replacement fixes it.
    """

    #: Raw evdev codes currently down. Translating them is the pad's job.
    buttons: frozenset[int]
    #: Raw axis code -> current value, for the axes the device has.
    axes: dict[int, int]


class PadReader:
    """One open pad, read once per frame.

    **There is no thread and no `select`.** The kernel keeps a per-client
    buffer on each evdev fd -- sized from the device's capabilities, at least
    64 events -- and holds everything until it is read. So the kernel IS the
    staging buffer this subsystem would otherwise build: a reader thread
    copying that buffer into one of ours a few milliseconds sooner does the
    same job twice, and buys a cross-thread handoff, a lock, and the engine's
    only concurrency.

    Kris, 2026-09-11: *"we don't need to read until the cut; anything between
    frames can happily sit there."*

    What it costs is freshness, which this engine has already bought once and
    priced: input that lands during a frame's tail waits for the next cut, the
    same one-frame granularity the keyboard accepts. What it risks is the
    kernel buffer overflowing between cuts, which is `SYN_DROPPED`, which has
    to be handled whether or not a thread exists.
    """

    __slots__ = (
        "_dropped_at",
        "_drops",
        "_fd",
        "_pending",
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

    @property
    def drops(self) -> int:
        """How many times the kernel has dropped events on this device.

        Cumulative and never reset. Nonzero means input was lost; the `Desync`
        markers in the stream say where.
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
                break  # drained
            except OSError:
                break  # gone; discovery notices on the next scan
            if not data:
                break
            self._frame(data, reports)
            if len(data) < _EVENT_SIZE * _READ_EVENTS:
                break

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
        except OSError:
            keys = bytearray(_KEY_BYTES)  # answer "nothing held" over guessing
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
            except OSError:
                continue
            axes[code] = struct.unpack(_ABSINFO, bytes(info))[0]
        return Snapshot(frozenset(buttons), axes)

    def close(self) -> None:
        """Close the node. Safe to call twice."""
        if self._fd >= 0:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = -1


def open_pad(device: PadDevice) -> PadReader:
    """Open a discovered pad and ask it what its axes mean."""
    fd = open_node(device.path)
    try:
        return PadReader(fd, axis_ranges(fd))
    except BaseException:
        os.close(fd)
        raise
