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


def test_a_closed_vocabulary_is_not_a_promise_every_pad_has_all_of_it():
    # A Hori Fighting Commander has one stick and it is digital: RIGHT_X and
    # RIGHT_Y have no answer on it, and LEFT_X answers only its extremes.
    # EVIOCGABS reports that honestly -- an axis the device lacks is absent
    # from its calibration rather than present and wrong.
    from blitspersecond.input.pads.evdev import AxisRange

    fightpad = {ABS_X: AxisRange(-1, 1)}          # one digital stick, no more
    assert AXIS_NAMES[ABS_X] in Axis
    assert fightpad[ABS_X].centred(-1) == -1.0
    assert fightpad[ABS_X].centred(0) == 0.0
    assert fightpad[ABS_X].centred(1) == 1.0
    # And the axes it does not have simply are not there to read.
    assert all(code not in fightpad for code in (ABS_Z, ABS_RZ))


def test_a_digital_stick_reaches_full_deflection_rather_than_a_fraction():
    # The failure this prevents: a -1..1 stick read through an assumed
    # -32768..32767 range would report 0.00003 at full travel, which is a
    # stick that does nothing.
    from blitspersecond.input.pads.evdev import AxisRange

    assert AxisRange(-1, 1).centred(1) == 1.0
    assert AxisRange(-32768, 32767).centred(1) != 1.0
