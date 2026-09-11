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

#: The axes worth asking about: two sticks, two triggers, one hat.
AXES = (ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ, ABS_HAT0X, ABS_HAT0Y)

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
        ranges[code] = AxisRange(minimum, maximum)
    return ranges
