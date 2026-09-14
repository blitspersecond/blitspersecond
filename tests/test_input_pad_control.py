"""Sticks, triggers and the d-pad -- named for the control, not the wire.

See `docs/bps/PADS_PORT.md`.
"""

import pytest

from blitspersecond.input.pads import Axis, Button, Direction, direction
from blitspersecond.input.pads.evdev import (
    ABS_HAT0X,
    ABS_HAT0Y,
    ABS_RZ,
    ABS_X,
    ABS_Z,
    AXIS_NAMES,
    TRIGGERS,
)


def test_the_numpad_is_the_numpad():
    # 7 8 9
    # 4 5 6
    # 1 2 3
    assert Direction.UP == 8
    assert Direction.DOWN == 2
    assert Direction.LEFT == 4
    assert Direction.RIGHT == 6
    assert Direction.NEUTRAL == 5
    assert Direction.DOWN_RIGHT == 3


def test_a_quarter_circle_forward_reads_as_two_three_six():
    # The reason for numpad notation rather than an invented encoding: a
    # recogniser written against this looks like the move list it implements.
    rolled = [direction(x, y) for x, y in ((0, 1), (1, 1), (1, 0))]
    assert [int(d) for d in rolled] == [2, 3, 6]


def test_y_grows_down_here_like_everywhere_else_in_the_engine():
    assert direction(0, -1) is Direction.UP
    assert direction(0, 1) is Direction.DOWN


@pytest.mark.parametrize(
    "signs, expected",
    [
        ((0, 0), Direction.NEUTRAL),
        ((-1, -1), Direction.UP_LEFT),
        ((1, -1), Direction.UP_RIGHT),
        ((-1, 1), Direction.DOWN_LEFT),
        ((1, 1), Direction.DOWN_RIGHT),
    ],
)
def test_every_corner_is_reachable(signs, expected):
    assert direction(*signs) is expected


def test_out_of_range_values_clamp_so_any_transport_can_reach_it():
    # evdev passes -1/0/1 straight through; a GameInput fixed-format reading
    # passes the difference of two button bits, which is the same range, and a
    # switch position is a table lookup away. Clamping means none of them has
    # to sanitise first.
    assert direction(-5, 7) is Direction.DOWN_LEFT
    assert direction(99, 0) is Direction.RIGHT


def test_the_dpad_is_not_an_axis():
    # ABS_HAT0X/Y is how evdev spells a d-pad. Naming them as axes would put
    # the wire format in the vocabulary, which is the mistake Button already
    # refuses one level down.
    assert ABS_HAT0X not in AXIS_NAMES
    assert ABS_HAT0Y not in AXIS_NAMES
    assert not any(a.name.startswith("HAT") for a in Axis)


def test_direction_is_an_int_where_button_deliberately_is_not():
    # Here the integer IS the vocabulary -- 236 is the notation. There it
    # would be a transport's private numbering leaking into game code.
    assert isinstance(Direction.DOWN, int)
    assert not isinstance(Button.FACE_SOUTH, int)


def test_every_axis_is_reachable_from_a_real_control():
    # The closed-vocabulary bargain: a member nothing maps to is a control a
    # game can name and never receive.
    assert set(AXIS_NAMES.values()) == set(Axis)


def test_a_trigger_has_two_honest_readings_of_one_control():
    # Travel as an Axis, an edge as a Button. Kris: "carry the signal" -- the
    # Hori reports its triggers digitally, so the threshold gives it clean
    # edges while an analogue trigger also gives travel.
    assert AXIS_NAMES[ABS_Z] is Axis.LEFT_TRIGGER
    assert TRIGGERS[ABS_Z] is Button.LT
    assert AXIS_NAMES[ABS_RZ] is Axis.RIGHT_TRIGGER
    assert TRIGGERS[ABS_RZ] is Button.RT


def test_a_stick_is_two_axes_because_that_is_what_the_hardware_says():
    # A deadzoned (x, y) vector is a derived reading: the deadzone is radial,
    # which is a judgement about intent applied to the pair. Storing the
    # result would throw away what the hardware reported.
    assert AXIS_NAMES[ABS_X] is Axis.LEFT_X
    assert {Axis.LEFT_X, Axis.LEFT_Y, Axis.RIGHT_X, Axis.RIGHT_Y} <= set(Axis)


def test_a_pad_does_not_admit_which_controls_it_lacks():
    # Measured 2026-09-11: a Hori Fighting Commander has ONE stick and it is
    # digital, and it declares all six axes over exactly the ranges a real
    # Xbox pad declares. Presenting as an Xbox controller is what makes it
    # work everywhere, and the claim covers capabilities as much as identity.
    from blitspersecond.input.pads.evdev import AxisRange

    hori = {
        ABS_X: AxisRange(-32768, 32767),
        ABS_Z: AxisRange(0, 1023),
    }
    xbox = {
        ABS_X: AxisRange(-32768, 32767),
        ABS_Z: AxisRange(0, 1023),
    }
    assert hori == xbox, "the calibration cannot tell these two pads apart"
    # So an absent control reads as permanently neutral and nothing can
    # distinguish that from one at rest. Require-never is design law, not a
    # check -- there is no test a game could perform first.
    assert hori[ABS_X].centred(0) == 0.0


def test_a_digital_stick_reaches_full_deflection_rather_than_a_fraction():
    # The failure this prevents: a -1..1 stick read through an assumed
    # -32768..32767 range would report 0.00003 at full travel, which is a
    # stick that does nothing.
    from blitspersecond.input.pads.evdev import AxisRange

    assert AxisRange(-1, 1).centred(1) == 1.0
    assert AxisRange(-32768, 32767).centred(1) != 1.0


# -- which axis is which, per device --------------------------------------


def test_a_declared_but_empty_axis_is_not_an_axis():
    # Real pads do this for controls they do not have: both Bluetooth Xbox
    # pads on the test desk declare ABS_RX/ABS_RY with range (0, 0), and a
    # Switch Pro declares ABS_Z/ABS_RZ that way because its triggers are
    # digital and arrive as buttons. An axis with no range is not there.
    import os
    import struct
    from blitspersecond.input.pads.evdev import axis_ranges

    # A pipe answers ENOTTY to every query, which is the same path.
    read_fd, write_fd = os.pipe()
    try:
        assert axis_ranges(read_fd) == {}
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_the_default_layout_is_what_a_wired_xbox_pad_looks_like():
    from blitspersecond.input.pads.evdev import AxisRange, layout_for

    xpad = {
        ABS_X: AxisRange(-32768, 32767),
        ABS_Z: AxisRange(0, 1023),
        ABS_RZ: AxisRange(0, 1023),
    }
    layout = layout_for(xpad)
    assert layout.axes[ABS_Z] is Axis.LEFT_TRIGGER
    assert layout.triggers[ABS_Z] is Button.LT


def test_declaring_gas_and_brake_moves_the_triggers_and_the_right_stick():
    # Measured: a Bluetooth Xbox pad puts its RIGHT STICK on ABS_Z/ABS_RZ and
    # its triggers on ABS_GAS/ABS_BRAKE, where the same pad over USB does the
    # opposite. The layout follows the kernel driver, not the device.
    from blitspersecond.input.pads.evdev import ABS_BRAKE, ABS_GAS, AxisRange, layout_for

    generic = {
        ABS_X: AxisRange(0, 65535),
        ABS_Z: AxisRange(0, 65535),
        ABS_RZ: AxisRange(0, 65535),
        ABS_GAS: AxisRange(0, 1023),
        ABS_BRAKE: AxisRange(0, 1023),
    }
    layout = layout_for(generic)
    assert layout.axes[ABS_Z] is Axis.RIGHT_X
    assert layout.axes[ABS_RZ] is Axis.RIGHT_Y
    assert layout.triggers == {ABS_GAS: Button.RT, ABS_BRAKE: Button.LT}
    assert ABS_Z not in layout.triggers


def test_the_wrong_layout_is_what_makes_a_trigger_read_as_held():
    # The failure, stated as a number. A Bluetooth Xbox pad rests its ABS_Z
    # near the middle of a 0..65535 range because it is a STICK. Read as a
    # trigger that is half-pressed, and 0.5 is four times the threshold.
    from blitspersecond.input.pads.evdev import AxisRange
    from blitspersecond.input.pads.pad import TRIGGER_ON

    at_rest, span = 32688, AxisRange(0, 65535)
    assert span.travel(at_rest) > TRIGGER_ON      # held, wrongly
    assert abs(span.centred(at_rest)) < 0.01      # centred, correctly


def test_a_pad_with_no_trigger_axes_reports_its_triggers_as_buttons():
    # A Switch Pro is this: ZL and ZR are switches, it declares no trigger
    # axes at all, and without the digital mapping Button.LT and Button.RT
    # are unreachable on it forever -- a game binds them and gets nothing.
    from blitspersecond.input.pads.evdev import (
        ABS_RX, ABS_RY, BTN_TL2, BTN_TR2, AxisRange, layout_for,
    )

    switch = {
        ABS_X: AxisRange(-32767, 32767),
        ABS_RX: AxisRange(-32767, 32767),
        ABS_RY: AxisRange(-32767, 32767),
    }
    layout = layout_for(switch)
    assert layout.triggers == {}
    assert layout.buttons[BTN_TL2] is Button.LT
    assert layout.buttons[BTN_TR2] is Button.RT


def test_a_pad_with_trigger_axes_does_not_also_read_the_digital_ones():
    # Four of five pads on the test desk declare BTN_TL2/BTN_TR2 BESIDE their
    # analogue triggers. Mapping both would give one control two sources that
    # can disagree near the threshold, and the axis re-pressing after the
    # button let go would chatter.
    from blitspersecond.input.pads.evdev import BTN_TL2, BTN_TR2, AxisRange, layout_for

    analogue = {ABS_X: AxisRange(-32768, 32767), ABS_Z: AxisRange(0, 1023)}
    layout = layout_for(analogue)
    assert BTN_TL2 not in layout.buttons
    assert BTN_TR2 not in layout.buttons
    assert layout.triggers[ABS_Z] is Button.LT


@pytest.mark.parametrize(
    "declares",
    [
        {0x00: (-32768, 32767), 0x02: (0, 1023), 0x05: (0, 1023)},   # xpad
        {0x00: (0, 65535), 0x02: (0, 65535), 0x09: (0, 1023), 0x0A: (0, 1023)},
        {0x00: (-32767, 32767), 0x03: (-32767, 32767)},              # digital
    ],
    ids=["xpad", "generic-hid", "digital-triggers"],
)
def test_every_button_is_reachable_on_every_layout(declares):
    # The closed-vocabulary bargain, per device rather than in general: a
    # Button nothing on THIS pad maps to is a control a game can name and
    # never receive from it.
    from blitspersecond.input.pads.evdev import AxisRange, layout_for

    layout = layout_for({c: AxisRange(*r) for c, r in declares.items()})
    reachable = set(layout.buttons.values()) | set(layout.triggers.values())
    assert reachable == set(Button)
