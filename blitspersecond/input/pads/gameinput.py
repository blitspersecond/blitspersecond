"""Native Windows GameInput transport beneath :mod:`input.pads`.

GameInput owns discovery, hot-plug state and fixed-format gamepad readings.
This module translates each complete reading into the same canonical snapshot
that the Pad layer consumes on Linux.  Pad therefore remains the sole owner of
tick edges, deadzones, identification and mapper policy.

The binding deliberately uses the stable GameInput v0 interface.  Newer
runtimes remain binary-compatible with it.  The full native controller matrix
was run after a newer redistributable had been installed mid-bring-up, so the
untouched Windows inbox runtime still needs a clean-install retest before BPS
sets any redistribution policy.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import sys
import threading
from typing import Any

from .codes import (
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
)
from .evdev import Device, Snapshot
from .mappers import mapper_for, model_for
from .pad import _AxisRange

_IS_WINDOWS = sys.platform == "win32"
_CALL = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
_HRESULT = ctypes.c_long
_ULONG = ctypes.c_ulong

_GAMEPAD = 0x00040000
_CONNECTED = 0x00000001
_BLOCKING_ENUMERATION = 2

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

_SWITCH_CENTRE = 0
_SWITCH_UP = 1
_SWITCH_UP_RIGHT = 2
_SWITCH_RIGHT = 3
_SWITCH_DOWN_RIGHT = 4
_SWITCH_DOWN = 5
_SWITCH_DOWN_LEFT = 6
_SWITCH_LEFT = 7
_SWITCH_UP_LEFT = 8
_SWITCH_DPAD = {
    _SWITCH_CENTRE: (0, 0),
    _SWITCH_UP: (0, -1),
    _SWITCH_UP_RIGHT: (1, -1),
    _SWITCH_RIGHT: (1, 0),
    _SWITCH_DOWN_RIGHT: (1, 1),
    _SWITCH_DOWN: (0, 1),
    _SWITCH_DOWN_LEFT: (-1, 1),
    _SWITCH_LEFT: (-1, 0),
    _SWITCH_UP_LEFT: (-1, -1),
}

_KEY_BYTES = 0x300 // 8
_STICK_MAX = 32767
_TRIGGER_MAX = 65535
_AXIS_RANGES = {
    ABS_X: _AxisRange(-_STICK_MAX, _STICK_MAX),
    ABS_Y: _AxisRange(-_STICK_MAX, _STICK_MAX),
    ABS_RX: _AxisRange(-_STICK_MAX, _STICK_MAX),
    ABS_RY: _AxisRange(-_STICK_MAX, _STICK_MAX),
    ABS_Z: _AxisRange(0, _TRIGGER_MAX),
    ABS_RZ: _AxisRange(0, _TRIGGER_MAX),
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


def _release(obj: ctypes.c_void_p) -> None:
    if obj:
        _method(obj, 2, _ULONG)(obj)


def _device_info(device: ctypes.c_void_p) -> _DeviceInfo:
    address = _method(device, 3, ctypes.c_void_p)(device)
    if not address:
        raise RuntimeError("IGameInputDevice.GetDeviceInfo returned null")
    return ctypes.cast(address, ctypes.POINTER(_DeviceInfo)).contents


def _device_status(device: ctypes.c_void_p) -> int:
    return int(_method(device, 4, ctypes.c_uint32)(device))


def _display_name(info: _DeviceInfo) -> str:
    if info.displayName and info.displayName.contents.data:
        size = info.displayName.contents.sizeInBytes
        data = ctypes.string_at(info.displayName.contents.data, size)
        return data.rstrip(b"\0").decode("utf-8", errors="replace")
    return "GameInput controller"


def _fingerprint(info: _DeviceInfo) -> str:
    return f"gameinput:{bytes(info.deviceId.value).hex()}"


def _dpad_switch(info: _DeviceInfo) -> int | None:
    """Select a hardware-validated generic switch d-pad workaround.

    Other controllers retain the standard fixed-format GameInput d-pad until
    their hardware demonstrates that it needs a quirk of its own.
    """
    identity = (info.vendorId, info.productId)
    if identity in _GENERIC_DPAD_DEVICES and info.controllerSwitchCount == 1:
        return 0
    return None


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _snapshot(
    fingerprint: str,
    state: _GamepadState,
    switch_position: int | None = None,
) -> Snapshot:
    """Translate one fixed-format GameInput state into a complete Pad snapshot."""
    keys = bytearray(_KEY_BYTES)
    for mask, code in _BUTTON_CODES.items():
        if state.buttons & mask:
            keys[code // 8] |= 1 << (code % 8)

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
    return Snapshot(fingerprint, bytes(keys), axes)


@dataclass
class _KnownDevice:
    pointer: ctypes.c_void_p
    device: Device
    connected: bool
    switch_count: int
    dpad_switch: int | None


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
    """Windows GameInput discovery and snapshot transport."""

    def __init__(self, queue: Any, *, logger: Any = None) -> None:
        self._queue = queue
        self._logger = logger
        self._lock = threading.RLock()
        self._game_input = ctypes.c_void_p()
        self._token = ctypes.c_uint64()
        self._devices: dict[str, _KnownDevice] = {}
        self._callback: Any = None
        self._running = False
        self._changed = False

    @property
    def running(self) -> bool:
        return self._running

    @property
    def changed(self) -> bool:
        with self._lock:
            return self._changed

    def open(self) -> dict[str, Device]:
        if self._running:
            return self.refresh()
        if not _IS_WINDOWS:
            return {}

        try:
            # Guarded by _IS_WINDOWS above; WinDLL just isn't in the
            # Linux typeshed stub this checks against.
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
            return {}

        self._running = True
        return self.refresh()

    def close(self) -> None:
        self._running = False
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
            self._changed = False
        if self._game_input:
            _release(self._game_input)
            self._game_input = ctypes.c_void_p()
        self._callback = None

    def refresh(self) -> dict[str, Device]:
        with self._lock:
            self._changed = False
            return {
                fingerprint: known.device
                for fingerprint, known in self._devices.items()
                if known.connected
            }

    def poll(self) -> None:
        if not self._running:
            return
        with self._lock:
            devices = tuple(self._devices.items())

        get_current = _method(
            self._game_input,
            4,
            _HRESULT,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )
        for fingerprint, known in devices:
            status = _device_status(known.pointer)
            connected = bool(status & _CONNECTED)
            if connected != known.connected:
                with self._lock:
                    known.connected = connected
                    self._changed = True
            if not connected:
                continue

            reading = ctypes.c_void_p()
            result = get_current(
                self._game_input,
                _GAMEPAD,
                known.pointer,
                ctypes.byref(reading),
            )
            if result < 0 or not reading:
                continue
            try:
                state = _GamepadState()
                get_state = _method(
                    reading,
                    22,
                    ctypes.c_bool,
                    ctypes.POINTER(_GamepadState),
                )
                if get_state(reading, ctypes.byref(state)):
                    switch_position = None
                    if known.dpad_switch is not None:
                        switches = (
                            ctypes.c_int32 * known.switch_count
                        )()
                        get_switches = _method(
                            reading,
                            13,
                            ctypes.c_uint32,
                            ctypes.c_uint32,
                            ctypes.POINTER(ctypes.c_int32),
                        )
                        count = get_switches(
                            reading,
                            known.switch_count,
                            switches,
                        )
                        if count > known.dpad_switch:
                            switch_position = switches[known.dpad_switch]
                    self._queue.append(
                        _snapshot(fingerprint, state, switch_position)
                    )
            finally:
                _release(reading)

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
                    dpad_switch = _dpad_switch(info)
                    known = _KnownDevice(
                        pointer=pointer,
                        device=Device(
                            path="",
                            name=_display_name(info),
                            model=model_for(vendor, product),
                            mapper=mapper_for(vendor, product),
                            axis_ranges=dict(_AXIS_RANGES),
                        ),
                        connected=connected,
                        switch_count=info.controllerSwitchCount,
                        dpad_switch=dpad_switch,
                    )
                    self._devices[fingerprint] = known
                else:
                    known.connected = connected
                self._changed = True
        except Exception as exc:
            self._log("error", f"GameInput device callback failed: {exc}")

    def _log(self, level: str, message: str) -> None:
        if self._logger is not None:
            getattr(self._logger, level)(message)
