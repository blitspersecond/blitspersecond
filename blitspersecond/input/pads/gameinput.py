"""Native Windows GameInput transport beneath :mod:`input.pads`.

The ABI and fixed-format translation are carried from the validated reference
engine. The rebuilt engine changes one important transport decision: it walks
GameInput's packet history with ``GetNextReading`` and hands each complete
state to the input cut in order. Only a lost history cursor becomes a
``Snapshot`` resync.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import sys
import threading
from typing import Any

from .evdev import (
    ABS_HAT0X,
    ABS_HAT0Y,
    ABS_RX,
    ABS_RY,
    ABS_RZ,
    ABS_X,
    ABS_Y,
    ABS_Z,
    BTN_EAST,
    BTN_NORTH,
    BTN_SELECT,
    BTN_SOUTH,
    BTN_START,
    BTN_TL,
    BTN_TR,
    BTN_WEST,
    AxisRange,
    PadDevice,
    Snapshot,
    layout_for,
)
from .mapper import mapper_for, model_for
from .pad import Reading

_IS_WINDOWS = sys.platform == "win32"
_CALL = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
_HRESULT = ctypes.c_long
_ULONG = ctypes.c_ulong

_GAMEPAD = 0x00040000
_CONNECTED = 0x00000001
_BLOCKING_ENUMERATION = 2

# HRESULT_FROM_WIN32 values from GameInput.h. READING_NOT_FOUND is the normal
# end of a history walk; every other failure invalidates the cursor.
_READING_NOT_FOUND = -2088108029

_MENU = 0x00000001
_VIEW = 0x00000002
_A = 0x00000004
_B = 0x00000008
_X = 0x00000010
_Y = 0x00000020
_DPAD_UP = 0x00000040
_DPAD_DOWN = 0x00000080
_DPAD_LEFT = 0x00000100
_DPAD_RIGHT = 0x00000200
_LEFT_SHOULDER = 0x00000400
_RIGHT_SHOULDER = 0x00000800

_GENERIC_DPAD_DEVICES = {
    (0x054C, 0x05C4),  # DualShock 4: Bluetooth hardware validation
    (0x057E, 0x2009),  # Nintendo Switch Pro Controller
}

_SWITCH_DPAD = {
    0: (0, 0),
    1: (0, -1),
    2: (1, -1),
    3: (1, 0),
    4: (1, 1),
    5: (0, 1),
    6: (-1, 1),
    7: (-1, 0),
    8: (-1, -1),
}

_STICK_MAX = 32767
_TRIGGER_MAX = 65535
_AXIS_RANGES = {
    ABS_X: AxisRange(-_STICK_MAX, _STICK_MAX),
    ABS_Y: AxisRange(-_STICK_MAX, _STICK_MAX),
    ABS_RX: AxisRange(-_STICK_MAX, _STICK_MAX),
    ABS_RY: AxisRange(-_STICK_MAX, _STICK_MAX),
    ABS_Z: AxisRange(0, _TRIGGER_MAX),
    ABS_RZ: AxisRange(0, _TRIGGER_MAX),
    ABS_HAT0X: AxisRange(-1, 1),
    ABS_HAT0Y: AxisRange(-1, 1),
}

_BUTTON_CODES = {
    _MENU: BTN_START,
    _VIEW: BTN_SELECT,
    _A: BTN_SOUTH,
    _B: BTN_EAST,
    _X: BTN_WEST,
    _Y: BTN_NORTH,
    _LEFT_SHOULDER: BTN_TL,
    _RIGHT_SHOULDER: BTN_TR,
}


class _AppLocalDeviceId(ctypes.Structure):
    _fields_ = [("value", ctypes.c_ubyte * 32)]


class _Usage(ctypes.Structure):
    _fields_ = [("page", ctypes.c_uint16), ("id", ctypes.c_uint16)]


class _Version(ctypes.Structure):
    _fields_ = [
        ("major", ctypes.c_uint16),
        ("minor", ctypes.c_uint16),
        ("build", ctypes.c_uint16),
        ("revision", ctypes.c_uint16),
    ]


class _GameInputString(ctypes.Structure):
    _fields_ = [
        ("sizeInBytes", ctypes.c_uint32),
        ("codePointCount", ctypes.c_uint32),
        ("data", ctypes.c_char_p),
    ]


class _DeviceInfo(ctypes.Structure):
    """GameInput v0's GameInputDeviceInfo layout."""

    _fields_ = [
        ("infoSize", ctypes.c_uint32),
        ("vendorId", ctypes.c_uint16),
        ("productId", ctypes.c_uint16),
        ("revisionNumber", ctypes.c_uint16),
        ("interfaceNumber", ctypes.c_uint8),
        ("collectionNumber", ctypes.c_uint8),
        ("usage", _Usage),
        ("hardwareVersion", _Version),
        ("firmwareVersion", _Version),
        ("deviceId", _AppLocalDeviceId),
        ("deviceRootId", _AppLocalDeviceId),
        ("deviceFamily", ctypes.c_int32),
        ("capabilities", ctypes.c_uint32),
        ("supportedInput", ctypes.c_uint32),
        ("supportedRumbleMotors", ctypes.c_uint32),
        ("inputReportCount", ctypes.c_uint32),
        ("outputReportCount", ctypes.c_uint32),
        ("featureReportCount", ctypes.c_uint32),
        ("controllerAxisCount", ctypes.c_uint32),
        ("controllerButtonCount", ctypes.c_uint32),
        ("controllerSwitchCount", ctypes.c_uint32),
        ("touchPointCount", ctypes.c_uint32),
        ("touchSensorCount", ctypes.c_uint32),
        ("forceFeedbackMotorCount", ctypes.c_uint32),
        ("hapticFeedbackMotorCount", ctypes.c_uint32),
        ("deviceStringCount", ctypes.c_uint32),
        ("deviceDescriptorSize", ctypes.c_uint32),
        ("inputReportInfo", ctypes.c_void_p),
        ("outputReportInfo", ctypes.c_void_p),
        ("featureReportInfo", ctypes.c_void_p),
        ("controllerAxisInfo", ctypes.c_void_p),
        ("controllerButtonInfo", ctypes.c_void_p),
        ("controllerSwitchInfo", ctypes.c_void_p),
        ("keyboardInfo", ctypes.c_void_p),
        ("mouseInfo", ctypes.c_void_p),
        ("touchSensorInfo", ctypes.c_void_p),
        ("motionInfo", ctypes.c_void_p),
        ("arcadeStickInfo", ctypes.c_void_p),
        ("flightStickInfo", ctypes.c_void_p),
        ("gamepadInfo", ctypes.c_void_p),
        ("racingWheelInfo", ctypes.c_void_p),
        ("uiNavigationInfo", ctypes.c_void_p),
        ("forceFeedbackMotorInfo", ctypes.c_void_p),
        ("hapticFeedbackMotorInfo", ctypes.c_void_p),
        ("displayName", ctypes.POINTER(_GameInputString)),
        ("deviceStrings", ctypes.c_void_p),
        ("deviceDescriptorData", ctypes.c_void_p),
    ]


class _GamepadState(ctypes.Structure):
    _fields_ = [
        ("buttons", ctypes.c_uint32),
        ("leftTrigger", ctypes.c_float),
        ("rightTrigger", ctypes.c_float),
        ("leftThumbstickX", ctypes.c_float),
        ("leftThumbstickY", ctypes.c_float),
        ("rightThumbstickX", ctypes.c_float),
        ("rightThumbstickY", ctypes.c_float),
    ]


def _method(
    obj: ctypes.c_void_p,
    index: int,
    restype: Any,
    *argtypes: Any,
) -> Any:
    vtable = ctypes.cast(
        obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
    ).contents
    return _CALL(restype, ctypes.c_void_p, *argtypes)(vtable[index])


def _add_ref(obj: ctypes.c_void_p) -> None:
    _method(obj, 1, _ULONG)(obj)


def _release(obj: ctypes.c_void_p | None) -> None:
    if obj:
        _method(obj, 2, _ULONG)(obj)


def _device_info(device: ctypes.c_void_p) -> _DeviceInfo:
    address = _method(device, 3, ctypes.c_void_p)(device)
    if not address:
        raise RuntimeError("IGameInputDevice.GetDeviceInfo returned null")
    return ctypes.cast(address, ctypes.POINTER(_DeviceInfo)).contents


def _display_name(info: _DeviceInfo) -> str:
    if info.displayName and info.displayName.contents.data:
        size = info.displayName.contents.sizeInBytes
        data = ctypes.string_at(info.displayName.contents.data, size)
        return data.rstrip(b"\0").decode("utf-8", errors="replace")
    return "GameInput controller"


def _fingerprint(info: _DeviceInfo) -> str:
    return f"gameinput:{bytes(info.deviceId.value).hex()}"


def _dpad_switch(info: _DeviceInfo) -> int | None:
    identity = (info.vendorId, info.productId)
    if identity in _GENERIC_DPAD_DEVICES and info.controllerSwitchCount == 1:
        return 0
    return None


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _snapshot(
    state: _GamepadState,
    switch_position: int | None = None,
) -> Snapshot:
    """Translate one fixed-format GameInput state to the canonical raw shape."""
    buttons = frozenset(
        code for mask, code in _BUTTON_CODES.items() if state.buttons & mask
    )
    dpad_x = int(bool(state.buttons & _DPAD_RIGHT)) - int(
        bool(state.buttons & _DPAD_LEFT)
    )
    dpad_y = int(bool(state.buttons & _DPAD_DOWN)) - int(
        bool(state.buttons & _DPAD_UP)
    )
    if switch_position is not None:
        dpad_x, dpad_y = _SWITCH_DPAD.get(switch_position, (0, 0))
    axes = {
        ABS_X: round(_clamp(state.leftThumbstickX, -1.0, 1.0) * _STICK_MAX),
        # GameInput follows XInput: positive Y is up. BPS follows evdev, where
        # up is negative.
        ABS_Y: round(-_clamp(state.leftThumbstickY, -1.0, 1.0) * _STICK_MAX),
        ABS_RX: round(_clamp(state.rightThumbstickX, -1.0, 1.0) * _STICK_MAX),
        ABS_RY: round(-_clamp(state.rightThumbstickY, -1.0, 1.0) * _STICK_MAX),
        ABS_Z: round(_clamp(state.leftTrigger, 0.0, 1.0) * _TRIGGER_MAX),
        ABS_RZ: round(_clamp(state.rightTrigger, 0.0, 1.0) * _TRIGGER_MAX),
        ABS_HAT0X: dpad_x,
        ABS_HAT0Y: dpad_y,
    }
    return Snapshot(buttons, axes)


@dataclass
class _KnownDevice:
    pointer: ctypes.c_void_p
    device: PadDevice
    connected: bool
    switch_count: int
    dpad_switch: int | None


class GameInputReader:
    """One device's cursor into GameInput's half-second reading history."""

    layout = layout_for(_AXIS_RANGES)

    def __init__(self, owner: "GameInput", known: _KnownDevice) -> None:
        self._owner = owner
        self._known = known
        self._previous: ctypes.c_void_p | None = None
        self._closed = False
        self.ranges = dict(_AXIS_RANGES)
        self.drops = 0

    def read(self) -> tuple[Reading | Snapshot, ...]:
        if self._closed:
            return ()
        return self._owner._read(self)

    def snapshot(self) -> Snapshot:
        if self._closed:
            return Snapshot(frozenset(), {})
        return self._owner._snapshot(self)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            _release(self._previous)
            self._previous = None


_DeviceCallback = _CALL(
    None,
    ctypes.c_uint64,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_uint64,
    ctypes.c_uint32,
    ctypes.c_uint32,
)


class GameInput:
    """Windows discovery and ordered-reading transport."""

    def __init__(self, *, logger: Any = None) -> None:
        self._logger = logger
        self._lock = threading.RLock()
        self._game_input = ctypes.c_void_p()
        self._token = ctypes.c_uint64()
        self._devices: dict[str, _KnownDevice] = {}
        self._readers: set[GameInputReader] = set()
        self._callback: Any = None
        self._running = False

    def discover(self) -> dict[str, PadDevice]:
        if not self._running and not self._open():
            return {}
        with self._lock:
            return {
                fingerprint: known.device
                for fingerprint, known in self._devices.items()
                if known.connected
            }

    def open_pad(self, device: PadDevice) -> GameInputReader:
        with self._lock:
            known = self._devices.get(device.fingerprint)
            if known is None or not known.connected:
                raise OSError("GameInput device disconnected before open")
            reader = GameInputReader(self, known)
            self._readers.add(reader)
            return reader

    def close(self) -> None:
        self._running = False
        for reader in tuple(self._readers):
            reader.close()
        self._readers.clear()
        if self._game_input and self._token.value:
            try:
                unregister = _method(
                    self._game_input,
                    13,
                    ctypes.c_bool,
                    ctypes.c_uint64,
                    ctypes.c_uint64,
                )
                unregister(self._game_input, self._token.value, 1_000_000)
            except Exception as exc:
                self._log("error", f"Could not unregister GameInput callback: {exc}")
        self._token.value = 0
        with self._lock:
            for known in self._devices.values():
                _release(known.pointer)
            self._devices.clear()
        if self._game_input:
            _release(self._game_input)
            self._game_input = ctypes.c_void_p()
        self._callback = None

    def _open(self) -> bool:
        if self._running:
            return True
        if not _IS_WINDOWS:
            return False
        try:
            dll = ctypes.WinDLL("GameInput.dll")  # pyright: ignore[reportAttributeAccessIssue]
            create = dll.GameInputCreate
            create.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
            create.restype = _HRESULT
            result = create(ctypes.byref(self._game_input))
            if result < 0 or not self._game_input:
                raise OSError(f"GameInputCreate failed: 0x{result & 0xffffffff:08x}")

            self._callback = _DeviceCallback(self._on_device)
            register = _method(
                self._game_input,
                9,
                _HRESULT,
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_void_p,
                _DeviceCallback,
                ctypes.POINTER(ctypes.c_uint64),
            )
            result = register(
                self._game_input,
                None,
                _GAMEPAD,
                _CONNECTED,
                _BLOCKING_ENUMERATION,
                None,
                self._callback,
                ctypes.byref(self._token),
            )
            if result < 0:
                raise OSError(
                    "IGameInput.RegisterDeviceCallback failed: "
                    f"0x{result & 0xffffffff:08x}"
                )
        except Exception as exc:
            self._log("error", f"Could not open GameInput: {exc}")
            self.close()
            return False
        self._running = True
        return True

    def _get_current(self, known: _KnownDevice) -> ctypes.c_void_p | None:
        reading = ctypes.c_void_p()
        result = _method(
            self._game_input,
            4,
            _HRESULT,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )(
            self._game_input,
            _GAMEPAD,
            known.pointer,
            ctypes.byref(reading),
        )
        return reading if result >= 0 and reading else None

    def _read(self, reader: GameInputReader) -> tuple[Reading | Snapshot, ...]:
        with self._lock:
            if not reader._known.connected:
                return ()
            if reader._previous is None:
                current = self._get_current(reader._known)
                if current is None:
                    return ()
                reader._previous = current
                state = self._state(current, reader._known)
                return (state,) if state is not None else ()

            items: list[Reading | Snapshot] = []
            get_next = _method(
                self._game_input,
                5,
                _HRESULT,
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p),
            )
            while reader._previous is not None:
                following = ctypes.c_void_p()
                result = get_next(
                    self._game_input,
                    reader._previous,
                    _GAMEPAD,
                    reader._known.pointer,
                    ctypes.byref(following),
                )
                if result == _READING_NOT_FOUND:
                    break
                if result < 0 or not following:
                    # The cursor fell out of the half-second history (or the
                    # device changed under it). Replace with current truth and
                    # let PadScan.resynced disclose the lost transitions.
                    _release(reader._previous)
                    reader._previous = self._get_current(reader._known)
                    reader.drops += 1
                    if reader._previous is not None:
                        state = self._state(reader._previous, reader._known)
                        if state is not None:
                            items.append(state)
                    break

                _release(reader._previous)
                reader._previous = following
                state = self._state(following, reader._known)
                if state is not None:
                    items.append(Reading(state.buttons, state.axes))
            return tuple(items)

    def _snapshot(self, reader: GameInputReader) -> Snapshot:
        with self._lock:
            _release(reader._previous)
            reader._previous = self._get_current(reader._known)
            if reader._previous is None:
                return Snapshot(frozenset(), {})
            state = self._state(reader._previous, reader._known)
            return state or Snapshot(frozenset(), {})

    @staticmethod
    def _state(
        reading: ctypes.c_void_p,
        known: _KnownDevice,
    ) -> Snapshot | None:
        state = _GamepadState()
        get_state = _method(
            reading,
            22,
            ctypes.c_bool,
            ctypes.POINTER(_GamepadState),
        )
        if not get_state(reading, ctypes.byref(state)):
            return None
        switch_position = None
        if known.dpad_switch is not None:
            switches = (ctypes.c_int32 * known.switch_count)()
            count = _method(
                reading,
                13,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.POINTER(ctypes.c_int32),
            )(reading, known.switch_count, switches)
            if count > known.dpad_switch:
                switch_position = switches[known.dpad_switch]
        return _snapshot(state, switch_position)

    def _on_device(
        self,
        _token: int,
        _context: int,
        address: int,
        _timestamp: int,
        current_status: int,
        _previous_status: int,
    ) -> None:
        try:
            pointer = ctypes.c_void_p(address)
            info = _device_info(pointer)
            fingerprint = _fingerprint(info)
            connected = bool(current_status & _CONNECTED)
            with self._lock:
                known = self._devices.get(fingerprint)
                if known is None:
                    vendor = f"{info.vendorId:04x}"
                    product = f"{info.productId:04x}"
                    _add_ref(pointer)
                    known = _KnownDevice(
                        pointer=pointer,
                        device=PadDevice(
                            fingerprint=fingerprint,
                            path="",
                            name=_display_name(info),
                            vendor=vendor,
                            product=product,
                            model=model_for(vendor, product),
                            mapper=mapper_for(vendor, product),
                        ),
                        connected=connected,
                        switch_count=info.controllerSwitchCount,
                        dpad_switch=_dpad_switch(info),
                    )
                    self._devices[fingerprint] = known
                else:
                    known.connected = connected
        except Exception as exc:
            self._log("error", f"GameInput device callback failed: {exc}")

    def _log(self, level: str, message: str) -> None:
        if self._logger is not None:
            getattr(self._logger, level)(message)
