"""Pure contracts for the native Windows GameInput transport."""

from collections import deque

import pytest

from blitspersecond.input.pads import Button, DualSenseMapper, Pad, Pads
from blitspersecond.input.pads.codes import (
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
from blitspersecond.input.pads.evdev import key_state
from blitspersecond.input.pads.gameinput import (
    GameInput,
    _A,
    _AXIS_RANGES,
    _B,
    _DPAD_DOWN,
    _DPAD_LEFT,
    _DeviceInfo,
    _dpad_switch,
    _GamepadState,
    _LEFT_SHOULDER,
    _MENU,
    _RIGHT_SHOULDER,
    _STICK_MAX,
    _TRIGGER_MAX,
    _VIEW,
    _X,
    _Y,
    _snapshot,
)


def state(**values) -> _GamepadState:
    result = _GamepadState()
    for name, value in values.items():
        setattr(result, name, value)
    return result


def test_fixed_gamepad_buttons_translate_to_canonical_physical_codes():
    snapshot = _snapshot(
        "gameinput:1",
        state(
            buttons=(
                _A
                | _B
                | _X
                | _Y
                | _LEFT_SHOULDER
                | _RIGHT_SHOULDER
                | _MENU
                | _VIEW
            )
        ),
    )

    for code in (
        BTN_SOUTH,
        BTN_EAST,
        BTN_WEST,
        BTN_NORTH,
        BTN_TL,
        BTN_TR,
        BTN_START,
        BTN_SELECT,
    ):
        assert key_state(snapshot.keys, code) == 1


def test_fixed_gamepad_axes_preserve_bps_orientation_and_ranges():
    snapshot = _snapshot(
        "gameinput:1",
        state(
            leftTrigger=0.5,
            rightTrigger=1.0,
            leftThumbstickX=-1.0,
            leftThumbstickY=1.0,
            rightThumbstickX=0.25,
            rightThumbstickY=-0.5,
        ),
    )

    assert snapshot.axes[ABS_X] == -_STICK_MAX
    assert snapshot.axes[ABS_Y] == -_STICK_MAX  # GameInput up -> BPS negative
    assert snapshot.axes[ABS_RX] == round(0.25 * _STICK_MAX)
    assert snapshot.axes[ABS_RY] == round(0.5 * _STICK_MAX)
    assert snapshot.axes[ABS_Z] == round(0.5 * _TRIGGER_MAX)
    assert snapshot.axes[ABS_RZ] == _TRIGGER_MAX


def test_fixed_gamepad_dpad_becomes_an_evdev_shaped_hat():
    snapshot = _snapshot(
        "gameinput:1",
        state(buttons=_DPAD_LEFT | _DPAD_DOWN),
    )

    assert snapshot.axes[ABS_HAT0X] == -1
    assert snapshot.axes[ABS_HAT0Y] == 1


@pytest.mark.parametrize(
    ("position", "expected"),
    [
        (0, (0, 0)),
        (1, (0, -1)),
        (2, (1, -1)),
        (3, (1, 0)),
        (4, (1, 1)),
        (5, (0, 1)),
        (6, (-1, 1)),
        (7, (-1, 0)),
        (8, (-1, -1)),
    ],
)
def test_generic_eight_way_hat_repairs_fixed_gamepad_dpad(
    position, expected
):
    # The Switch Pro's fixed-format view drops diagonal d-pad bits while the
    # generic switch view in the same reading preserves all eight positions.
    snapshot = _snapshot(
        "gameinput:1",
        state(buttons=_DPAD_DOWN),
        position,
    )

    assert (
        snapshot.axes[ABS_HAT0X],
        snapshot.axes[ABS_HAT0Y],
    ) == expected


def test_only_hardware_validated_devices_select_the_generic_dpad():
    dualshock_4 = _DeviceInfo()
    dualshock_4.vendorId = 0x054C
    dualshock_4.productId = 0x05C4
    dualshock_4.controllerSwitchCount = 1
    switch_pro = _DeviceInfo()
    switch_pro.vendorId = 0x057E
    switch_pro.productId = 0x2009
    switch_pro.controllerSwitchCount = 1
    unknown = _DeviceInfo()
    unknown.vendorId = 0x1234
    unknown.productId = 0x5678
    unknown.controllerSwitchCount = 1

    assert _dpad_switch(dualshock_4) == 0
    assert _dpad_switch(switch_pro) == 0
    assert _dpad_switch(unknown) is None


def test_complete_gameinput_snapshots_drive_existing_pad_edges():
    fingerprint = "gameinput:1"
    pad = Pad(
        fingerprint,
        name="Wireless Controller",
        model="DualSense",
        mapper=DualSenseMapper,
        axis_ranges=dict(_AXIS_RANGES),
    )
    pads = Pads()
    pads._pads = [pad]
    pads._by_fingerprint = {fingerprint: pad}
    pads._known_by_fingerprint = {fingerprint: pad}

    pads._queue.append(
        _snapshot(
            fingerprint,
            state(buttons=_A, leftThumbstickX=1.0),
        )
    )
    pads.update()

    assert pad.pressed(Button.FACE_SOUTH)
    assert pad.held(Button.FACE_SOUTH)
    assert pad.label(Button.FACE_SOUTH) == "cross"
    assert pad.left_stick() == pytest.approx((1.0, 0.0))

    pads._queue.append(_snapshot(fingerprint, state()))
    pads.update()

    assert pad.released(Button.FACE_SOUTH)
    assert not pad.held(Button.FACE_SOUTH)
    assert pad.left_stick() == (0.0, 0.0)


def test_gameinput_is_inert_off_windows(monkeypatch):
    monkeypatch.setattr(
        "blitspersecond.input.pads.gameinput._IS_WINDOWS",
        False,
    )
    transport = GameInput(deque())

    assert transport.open() == {}
    assert not transport.running
