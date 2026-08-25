"""The Linux evdev transport beneath :mod:`blitspersecond.input.pads`.

This module owns Linux discovery, file descriptors, the reader thread and
kernel report recovery. It emits raw, complete reports into a queue; ``Pads``
applies those reports to game-visible ``Pad`` objects on the engine tick.
"""

from __future__ import annotations

import errno
import os
import re
import select
import struct
import sys
import threading
import time
from collections import deque
from typing import Any, NamedTuple, Optional

from .codes import (
    ABS_HAT0X,
    ABS_HAT0Y,
    ABS_RX,
    ABS_RY,
    ABS_RZ,
    ABS_X,
    ABS_Y,
    ABS_Z,
    BTN_SOUTH,
    EV_ABS,
    EV_KEY,
    EV_SYN,
    SYN_DROPPED,
    SYN_REPORT,
)
from .mappers import PadMapper, mapper_for, model_for
from .pad import _AxisRange

try:
    from fcntl import ioctl as _fcntl_ioctl
except ImportError:  # non-Linux: keep the package importable
    _fcntl_ioctl = None

# struct input_event { struct timeval time; __u16 type; __u16 code; __s32 value; }
# timeval is two longs (16 bytes on 64-bit); type/code u16, value s32.
_FMT = "llHHi"
_SIZE = struct.calcsize(_FMT)

# ioctl encoding from asm-generic/ioctl.h. input_absinfo is six native ints:
# value, minimum, maximum, fuzz, flat and resolution.
_IOC_READ = 2
_IOC_TYPE = ord("E")
_KEY_BYTES = 0x300 // 8  # one bit per key through Linux KEY_MAX (0x2ff)
_ABS_INFO_FMT = "iiiiii"
_ABS_INFO_SIZE = struct.calcsize(_ABS_INFO_FMT)
_STATE_AXES = (
    ABS_X,
    ABS_Y,
    ABS_Z,
    ABS_RX,
    ABS_RY,
    ABS_RZ,
    ABS_HAT0X,
    ABS_HAT0Y,
)

_INPUT_BY_ID = "/dev/input/by-id"
_JOYSTICK_SUFFIX = "-event-joystick"
_IS_LINUX = sys.platform.startswith("linux")

# Steam Input projects virtual XInput pads with no stable physical identity.
# They share a product id, may multiply, and cannot reclaim a Pad id, so the
# transport excludes them before they enter the physical device collection.
_STEAM_VENDOR = "28de"


def _read_request(number: int, size: int) -> int:
    """Linux _IOR('E', number, size), without another evdev dependency."""
    return (_IOC_READ << 30) | (_IOC_TYPE << 8) | number | (size << 16)


def _device_ioctl(fd: int, request: int, buffer: bytearray) -> None:
    if _fcntl_ioctl is None:
        raise OSError(errno.ENOSYS, "evdev ioctl is unavailable")
    _fcntl_ioctl(fd, request, buffer, True)


class Device(NamedTuple):
    """One openable gamepad discovered by the Linux transport."""

    path: str
    name: str
    model: Optional[str]
    mapper: type[PadMapper]
    axis_ranges: Optional[dict[int, _AxisRange]] = None


class Report(NamedTuple):
    """One complete kernel input report, kept atomic until the engine tick."""

    fingerprint: str
    events: tuple[tuple[int, int, int], ...]


class Snapshot(NamedTuple):
    """Current kernel state after evdev reported that events were dropped."""

    fingerprint: str
    keys: bytes
    axes: dict[int, int]


class Evdev:
    """Discover and read Linux gamepads into a thread-safe handoff queue."""

    def __init__(self, queue: deque, *, logger: Any = None) -> None:
        self._queue = queue
        self._logger = logger
        self._lock = threading.Lock()
        self._fds: dict[str, int] = {}
        self._reader: Optional[threading.Thread] = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    @property
    def changed(self) -> bool:
        """Hot-plug is reconciled by the periodic evdev rescan."""
        return False

    def poll(self) -> None:
        """The evdev reader thread already fills the queue asynchronously."""

    def open(self) -> dict[str, Device]:
        """Open current devices and start the reader thread."""
        if not _IS_LINUX:
            return {}
        self._running = True
        present = self.refresh()
        if self._reader is None or not self._reader.is_alive():
            self._reader = threading.Thread(
                target=self._read_loop,
                name="Pads",
                daemon=True,
            )
            self._reader.start()
        return present

    def close(self) -> None:
        """Stop reading and close every device. Safe to repeat."""
        self._running = False
        reader, self._reader = self._reader, None
        if reader is not None and reader.is_alive():
            reader.join(timeout=0.5)
        with self._lock:
            for fingerprint, fd in tuple(self._fds.items()):
                try:
                    os.close(fd)
                except OSError:
                    pass
                self._fds.pop(fingerprint, None)

    def refresh(self) -> dict[str, Device]:
        """Return every successfully opened gamepad currently present."""
        if not _IS_LINUX:
            return {}
        present = self._scan()
        with self._lock:
            for fingerprint in tuple(self._fds):
                if fingerprint not in present:
                    try:
                        os.close(self._fds[fingerprint])
                    except OSError:
                        pass
                    self._fds.pop(fingerprint)
            for fingerprint, device in present.items():
                if fingerprint in self._fds:
                    continue
                try:
                    fd = os.open(device.path, os.O_RDONLY | os.O_NONBLOCK)
                    try:
                        ranges = self._axis_ranges(fd)
                    except OSError:
                        os.close(fd)
                        raise
                    self._fds[fingerprint] = fd
                    present[fingerprint] = device._replace(axis_ranges=ranges)
                except OSError as exc:
                    if self._logger is not None:
                        self._logger.error(
                            f"Could not open pad {device.name!r}: {exc}"
                        )
        return {
            fingerprint: device
            for fingerprint, device in present.items()
            if fingerprint in self._fds
        }

    def _scan(self) -> dict[str, Device]:
        """Find physical gamepad event nodes in ``/proc``.

        Bluetooth pads lack by-id links, so ``Uniq`` is preferred, followed by
        a USB by-id serial and then ``Phys``. BTN_SOUTH selects the gamepad node
        without requiring the legacy ``js`` handler or admitting sensor and
        touchpad sibling nodes.
        """
        byid = self._byid_serials()
        found: dict[str, Device] = {}
        try:
            blocks = open("/proc/bus/input/devices").read().split("\n\n")
        except OSError:
            return found
        for block in blocks:
            tokens = self._field(block, r"Handlers=(.*)").split()
            event = next((token for token in tokens if token.startswith("event")), None)
            if event is None or not self._has_key_bit(block, BTN_SOUTH):
                continue
            vendor = self._field(block, r"Vendor=([0-9a-fA-F]+)").lower()
            product = self._field(block, r"Product=([0-9a-fA-F]+)").lower()
            if vendor == _STEAM_VENDOR:
                continue
            path = f"/dev/input/{event}"
            name = self._field(block, r'N: Name="([^"]*)"') or "gamepad"
            uniq = self._field(block, r"U: Uniq=(.*)").strip()
            if uniq:
                fingerprint = f"uniq:{uniq}"
            elif os.path.realpath(path) in byid:
                fingerprint = byid[os.path.realpath(path)]
            else:
                phys = self._field(block, r"P: Phys=(.*)").strip()
                fingerprint = f"phys:{phys}" if phys else f"{name}:{event}"
            found[fingerprint] = Device(
                path=path,
                name=name,
                model=model_for(vendor, product),
                mapper=mapper_for(vendor, product),
            )
        return found

    @staticmethod
    def _field(block: str, pattern: str) -> str:
        match = re.search(pattern, block)
        return match.group(1) if match else ""

    @staticmethod
    def _has_key_bit(block: str, code: int) -> bool:
        """Return whether ``code`` is set in a procfs KEY capability mask."""
        match = re.search(r"B: KEY=(.*)", block)
        if not match:
            return False
        groups = match.group(1).split()
        groups.reverse()  # proc prints the most-significant group first
        index, bit = divmod(code, 64)
        if index >= len(groups):
            return False
        return bool(int(groups[index], 16) & (1 << bit))

    @staticmethod
    def _byid_serials() -> dict[str, str]:
        """Map USB event-node realpaths to stable by-id basenames."""
        out: dict[str, str] = {}
        try:
            names = os.listdir(_INPUT_BY_ID)
        except OSError:
            return out
        for name in names:
            if name.endswith(_JOYSTICK_SUFFIX):
                target = os.path.realpath(os.path.join(_INPUT_BY_ID, name))
                out[target] = name[: -len(_JOYSTICK_SUFFIX)]
        return out

    def _read_loop(self) -> None:
        pending: dict[str, list[tuple[int, int, int]]] = {}
        desynced: set[str] = set()
        while self._running:
            with self._lock:
                fdmap = {fd: fingerprint for fingerprint, fd in self._fds.items()}
            active = set(fdmap.values())
            for fingerprint in tuple(pending):
                if fingerprint not in active:
                    pending.pop(fingerprint)
            desynced.intersection_update(active)
            if not fdmap:
                time.sleep(0.03)
                continue
            try:
                ready, _, _ = select.select(list(fdmap), [], [], 0.1)
            except (OSError, ValueError):
                continue
            for fd in ready:
                fingerprint = fdmap.get(fd)
                if fingerprint is None:
                    continue
                try:
                    data = os.read(fd, _SIZE * 64)
                except (BlockingIOError, OSError):
                    continue
                is_desynced = self._buffer_chunk(
                    fingerprint,
                    fd,
                    data,
                    pending.setdefault(fingerprint, []),
                    fingerprint in desynced,
                )
                if is_desynced:
                    desynced.add(fingerprint)
                else:
                    desynced.discard(fingerprint)

    def _buffer_chunk(
        self,
        fingerprint: str,
        fd: int,
        data: bytes,
        pending: list[tuple[int, int, int]],
        desynced: bool,
    ) -> bool:
        """Frame bytes into reports and recover after ``SYN_DROPPED``."""
        for offset in range(0, len(data) - _SIZE + 1, _SIZE):
            _seconds, _micros, event_type, code, value = struct.unpack(
                _FMT, data[offset : offset + _SIZE]
            )
            if event_type == EV_SYN:
                if code == SYN_DROPPED:
                    pending.clear()
                    desynced = True
                elif code == SYN_REPORT:
                    if desynced:
                        self._queue.append(self._snapshot(fd, fingerprint))
                        desynced = False
                    elif pending:
                        self._queue.append(Report(fingerprint, tuple(pending)))
                    pending.clear()
                continue
            if not desynced and event_type in (EV_KEY, EV_ABS):
                pending.append((event_type, code, value))
        return desynced

    def _snapshot(self, fd: int, fingerprint: str) -> Snapshot:
        """Query current button and axis state after a dropped report."""
        keys = bytearray(_KEY_BYTES)
        try:
            _device_ioctl(
                fd,
                _read_request(0x18, len(keys)),  # EVIOCGKEY
                keys,
            )
        except OSError as exc:
            if self._logger is not None:
                self._logger.error(
                    f"Could not resynchronise pad {fingerprint}: {exc}"
                )
            return Snapshot(
                fingerprint,
                bytes(keys),
                {code: 0 for code in _STATE_AXES},
            )

        axes: dict[int, int] = {}
        for code in _STATE_AXES:
            info = bytearray(_ABS_INFO_SIZE)
            try:
                _device_ioctl(
                    fd,
                    _read_request(0x40 + code, len(info)),  # EVIOCGABS
                    info,
                )
            except OSError as exc:
                if exc.errno in (errno.EINVAL, errno.ENOTTY):
                    continue
                if self._logger is not None:
                    self._logger.error(
                        f"Could not resynchronise pad {fingerprint}: {exc}"
                    )
                return Snapshot(
                    fingerprint,
                    bytes(_KEY_BYTES),
                    {axis: 0 for axis in _STATE_AXES},
                )
            axes[code] = struct.unpack_from(_ABS_INFO_FMT, info)[0]
        return Snapshot(fingerprint, bytes(keys), axes)

    @staticmethod
    def _axis_ranges(fd: int) -> dict[int, _AxisRange]:
        """Read each supported axis's calibration from ``EVIOCGABS``."""
        ranges: dict[int, _AxisRange] = {}
        for code in _STATE_AXES:
            info = bytearray(_ABS_INFO_SIZE)
            try:
                _device_ioctl(
                    fd,
                    _read_request(0x40 + code, len(info)),  # EVIOCGABS
                    info,
                )
            except OSError as exc:
                if exc.errno in (errno.EINVAL, errno.ENOTTY):
                    continue
                raise
            _value, minimum, maximum, _fuzz, _flat, _resolution = (
                struct.unpack_from(_ABS_INFO_FMT, info)
            )
            ranges[code] = _AxisRange(minimum, maximum)
        return ranges


def key_state(keys: bytes, code: int) -> int:
    """Read one evdev key bit from a resynchronisation snapshot."""
    index, bit = divmod(code, 8)
    return int(index < len(keys) and bool(keys[index] & (1 << bit)))
