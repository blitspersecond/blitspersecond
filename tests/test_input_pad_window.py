"""What a pad's settled interval is: a scan of state, and ordered facts.

The same two halves the keyboard and the pointer produce. See
`docs/bps/INPUT_STREAM.md`.
"""

import pytest

from blitspersecond.input.kbm import KeyScan
from blitspersecond.input.kbm.mouse import MouseScan
from blitspersecond.input.pads import (
    NEUTRAL_AXES,
    Axis,
    Button,
    Direction,
    PadEvent,
    PadFact,
    PadScan,
)


def scan(**overrides):
    fields = dict(
        held=frozenset(),
        pressed=frozenset(),
        released=frozenset(),
        axes=NEUTRAL_AXES,
        direction=Direction.NEUTRAL,
    )
    return PadScan(**(fields | overrides))


def test_every_axis_is_present_even_on_a_pad_that_has_none_of_them():
    # A pad does not admit which controls it lacks, so an absent axis is
    # indistinguishable from one at rest. Leaving it out would only move that
    # from a neutral reading to a KeyError.
    assert set(NEUTRAL_AXES) == set(Axis)
    assert all(value == 0.0 for value in NEUTRAL_AXES.values())


def test_the_axes_a_reader_holds_cannot_be_written_through():
    # Windows are frozen because readers keep them. A plain dict here would
    # be a hole in that, and the one mutable thing in a frozen record is
    # exactly where somebody eventually writes.
    with pytest.raises(TypeError):
        scan().axes[Axis.LEFT_X] = 1.0


def test_a_scan_says_where_things_settled_not_how_they_got_there():
    settled = scan(
        held=frozenset({Button.LB}),
        pressed=frozenset({Button.LB}),
        direction=Direction.DOWN_RIGHT,
    )
    assert settled.held == frozenset({Button.LB})
    assert settled.direction is Direction.DOWN_RIGHT
    assert settled.released == frozenset()


def test_a_resync_is_marked_because_those_edges_were_not_the_player():
    # The transport dropped events and the pad was asked what was true
    # instead, so a release here may be a button nobody let go of.
    assert scan().resynced is False
    assert scan(released=frozenset({Button.RT}), resynced=True).resynced is True


def test_a_disconnect_is_marked_and_is_not_the_same_as_a_resync():
    # Both produce releases nobody performed; they are different events and a
    # game responds to them differently -- one pauses, the other carries on.
    gone = scan(released=frozenset({Button.FACE_SOUTH}), disconnected=True)
    assert gone.disconnected is True
    assert gone.resynced is False


def test_a_transition_carries_the_held_set_its_own_button_is_in():
    press = PadFact(
        PadEvent.PRESS, Button.LB, None, 0.0, None, frozenset({Button.LB})
    )
    release = PadFact(
        PadEvent.RELEASE, Button.LB, None, 0.0, None, frozenset({Button.LB})
    )
    assert Button.LB in press.held
    assert Button.LB in release.held


def test_an_axis_fact_carries_what_it_moved_to():
    fact = PadFact(PadEvent.AXIS, None, Axis.LEFT_X, -1.0, None, frozenset())
    assert (fact.axis, fact.value) == (Axis.LEFT_X, -1.0)
    assert fact.button is None


def test_a_direction_fact_is_one_position_not_two_components():
    # A diagonal arrives as two axis changes in ONE report, so it is a single
    # change. Emitting UP then UP_LEFT would invent an intermediate the player
    # never passed through, in the stream whose whole job is being the path.
    fact = PadFact(
        PadEvent.DIRECTION, None, None, 0.0, Direction.DOWN_LEFT, frozenset()
    )
    assert fact.direction is Direction.DOWN_LEFT
    assert int(fact.direction) == 1


def test_a_window_is_frozen_because_readers_keep_it():
    with pytest.raises(Exception):
        scan().direction = Direction.UP


def test_the_three_devices_agree_on_what_a_scan_looks_like():
    # A reader who has understood one has understood the others: held,
    # pressed and released mean the same thing on all three, and each carries
    # its own "these edges were not the player" flag.
    for fields in (KeyScan.__slots__, MouseScan.__slots__, PadScan.__slots__):
        assert {"held", "pressed", "released"} <= set(fields)
    assert "cleared" in KeyScan.__slots__
    assert "cleared" in MouseScan.__slots__
    assert "resynced" in PadScan.__slots__
