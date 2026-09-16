"""Reports in, windows out -- where every vocabulary meets the transport.

The reader is a stub: the fold has no idea what is underneath it, and canned
reports are how a roll or a dropped run becomes reproducible without a pad.
"""

import pytest

from blitspersecond.common import fps
from blitspersecond.input.pads import (
    Axis,
    Button,
    Direction,
    PadEvent,
    Reading,
)
from blitspersecond.input.pads.evdev import (
    ABS_HAT0X,
    ABS_HAT0Y,
    ABS_X,
    ABS_Z,
    BTN_SOUTH,
    BTN_TL,
    BTN_THUMBL,
    EV_ABS,
    EV_KEY,
    AxisRange,
    Report,
    Snapshot,
)
from blitspersecond.input.pads.pad import PadIngress

STICK = AxisRange(-32768, 32767)
TRIGGER = AxisRange(0, 1023)
HAT = AxisRange(-1, 1)
RANGES = {ABS_X: STICK, ABS_Z: TRIGGER, ABS_HAT0X: HAT, ABS_HAT0Y: HAT}


class Stub:
    """A reader that hands over whatever a test says arrived."""

    def __init__(self, ranges=None):
        from blitspersecond.input.pads.evdev import layout_for

        self.ranges = RANGES if ranges is None else ranges
        self.layout = layout_for(self.ranges)
        self.queued = []
        self.drops = 0

    def arrive(self, *items):
        self.queued.extend(items)

    def read(self):
        items, self.queued = tuple(self.queued), []
        # A real reader counts a drop as it emits the snapshot that replaces
        # what was lost, so this does too.
        self.drops += sum(isinstance(item, Snapshot) for item in items)
        return items


def ingress(ranges=None):
    stub = Stub(ranges)
    return stub, PadIngress(stub, fps.SIXTY)


def report(*events):
    return Report(tuple(events))


def kinds(window):
    return [f.kind for f in window.facts]


def test_a_press_is_an_edge_and_a_held_button():
    stub, pad = ingress()
    stub.arrive(report((EV_KEY, BTN_SOUTH, 1)))
    scan = pad.cut(1).scan
    assert scan.pressed == frozenset({Button.FACE_SOUTH})
    assert scan.held == frozenset({Button.FACE_SOUTH})
    assert scan.released == frozenset()


def test_autorepeat_is_not_a_fresh_edge():
    # evdev value 2 is the OS repeating a key. Holding a button must not read
    # as pressing it sixty times.
    stub, pad = ingress()
    stub.arrive(report((EV_KEY, BTN_SOUTH, 1)))
    pad.cut(1)
    stub.arrive(report((EV_KEY, BTN_SOUTH, 2)))
    scan = pad.cut(2).scan
    assert scan.pressed == frozenset()
    assert scan.held == frozenset({Button.FACE_SOUTH})


def test_a_button_outside_the_vocabulary_never_arrives():
    stub, pad = ingress()
    stub.arrive(report((EV_KEY, BTN_THUMBL, 1)))
    window = pad.cut(1)
    assert window.facts == ()
    assert window.scan.held == frozenset()


def test_a_transition_carries_the_held_set_its_own_button_is_in():
    stub, pad = ingress()
    stub.arrive(
        report((EV_KEY, BTN_SOUTH, 1)),
        report((EV_KEY, BTN_TL, 1)),
        report((EV_KEY, BTN_SOUTH, 0)),
    )
    facts = pad.cut(1).facts
    assert facts[0].held == frozenset({Button.FACE_SOUTH})
    assert facts[1].held == frozenset({Button.FACE_SOUTH, Button.LB})
    assert facts[2].held == frozenset({Button.FACE_SOUTH, Button.LB})


# -- the d-pad ------------------------------------------------------------


def test_a_diagonal_in_one_report_is_one_direction_change():
    # The two halves arrive together because the transport says so. Folding
    # per event would emit DOWN then DOWN_LEFT and invent a position the
    # player never passed through.
    stub, pad = ingress()
    stub.arrive(report((EV_ABS, ABS_HAT0X, -1), (EV_ABS, ABS_HAT0Y, 1)))
    window = pad.cut(1)
    assert kinds(window) == [PadEvent.DIRECTION]
    assert window.facts[0].direction is Direction.DOWN_LEFT
    assert window.scan.direction is Direction.DOWN_LEFT


def test_a_diagonal_across_two_reports_is_two_changes_because_it_was():
    # Here the transport IS saying they were separate, so the intermediate is
    # real and must survive.
    stub, pad = ingress()
    stub.arrive(
        report((EV_ABS, ABS_HAT0Y, 1)),
        report((EV_ABS, ABS_HAT0X, -1)),
    )
    facts = pad.cut(1).facts
    assert [f.direction for f in facts] == [Direction.DOWN, Direction.DOWN_LEFT]


def test_a_quarter_circle_keeps_its_path():
    # The whole reason the ring exists, in the notation the subject uses.
    stub, pad = ingress()
    for x, y in ((0, 1), (1, 1), (1, 0)):
        stub.arrive(report((EV_ABS, ABS_HAT0X, x), (EV_ABS, ABS_HAT0Y, y)))
    window = pad.cut(1)
    assert [int(f.direction) for f in window.facts] == [2, 3, 6]
    assert window.scan.direction is Direction.RIGHT


def test_releasing_the_dpad_returns_to_neutral():
    stub, pad = ingress()
    stub.arrive(report((EV_ABS, ABS_HAT0X, 1)))
    pad.cut(1)
    stub.arrive(report((EV_ABS, ABS_HAT0X, 0)))
    assert pad.cut(2).scan.direction is Direction.NEUTRAL


# -- axes and triggers ----------------------------------------------------


def test_an_axis_fact_carries_the_normalised_value_it_moved_to():
    stub, pad = ingress()
    stub.arrive(report((EV_ABS, ABS_X, -32768)))
    window = pad.cut(1)
    assert window.facts[0].axis is Axis.LEFT_X
    assert window.facts[0].value == -1.0
    assert window.scan.axes[Axis.LEFT_X] == -1.0


def test_a_trigger_is_travel_and_an_edge_at_once():
    # Kris: "carry the signal". Both readings of one control.
    stub, pad = ingress()
    stub.arrive(report((EV_ABS, ABS_Z, 1023)))
    window = pad.cut(1)
    assert window.scan.axes[Axis.LEFT_TRIGGER] == 1.0
    assert window.scan.pressed == frozenset({Button.LT})
    assert kinds(window) == [PadEvent.AXIS, PadEvent.PRESS]


def test_a_trigger_below_the_threshold_is_travel_without_an_edge():
    stub, pad = ingress()
    stub.arrive(report((EV_ABS, ABS_Z, 20)))     # 20/1023, under 30/255
    window = pad.cut(1)
    assert 0.0 < window.scan.axes[Axis.LEFT_TRIGGER] < 0.1
    assert window.scan.pressed == frozenset()
    assert kinds(window) == [PadEvent.AXIS]


def test_a_digital_trigger_gives_clean_edges():
    # The Hori reports its triggers 0-or-full. One threshold is all it needs.
    stub, pad = ingress()
    stub.arrive(report((EV_ABS, ABS_Z, 1023)))
    assert pad.cut(1).scan.pressed == frozenset({Button.LT})
    stub.arrive(report((EV_ABS, ABS_Z, 0)))
    assert pad.cut(2).scan.released == frozenset({Button.LT})


def test_no_deadzone_is_applied_because_intent_is_not_the_records_job():
    # The Xbox pad on the desk rests around 1050 of 32767. That is what it
    # said, and it is what the scan reports; deciding it means "nothing"
    # belongs to whatever reads it.
    stub, pad = ingress()
    stub.arrive(report((EV_ABS, ABS_X, 1050)))
    assert pad.cut(1).scan.axes[Axis.LEFT_X] != 0.0


def test_an_axis_with_no_calibration_is_dropped_rather_than_guessed():
    stub, pad = ingress(ranges={})
    stub.arrive(report((EV_ABS, ABS_X, -32768)))
    window = pad.cut(1)
    assert window.facts == ()
    assert window.scan.axes[Axis.LEFT_X] == 0.0


# -- resync and disconnect ------------------------------------------------


def test_a_snapshot_replaces_rather_than_adds():
    # The failure this exists for: a release lost in a dropped run leaves a
    # button held for good. Only a wholesale replacement heals it.
    stub, pad = ingress()
    stub.arrive(report((EV_KEY, BTN_SOUTH, 1)))
    assert pad.cut(1).scan.held == frozenset({Button.FACE_SOUTH})

    stub.arrive(Snapshot(frozenset(), {}))
    scan = pad.cut(2).scan
    assert scan.held == frozenset()
    assert scan.released == frozenset({Button.FACE_SOUTH})
    assert scan.resynced is True


def test_a_snapshot_can_report_a_button_nobody_saw_pressed():
    stub, pad = ingress()
    stub.arrive(Snapshot(frozenset({BTN_TL}), {ABS_X: 32767}))
    scan = pad.cut(1).scan
    assert scan.held == frozenset({Button.LB})
    assert scan.pressed == frozenset({Button.LB})
    assert scan.axes[Axis.LEFT_X] == 1.0
    assert scan.resynced is True


def test_an_ordinary_window_is_not_marked_resynced():
    stub, pad = ingress()
    stub.arrive(report((EV_KEY, BTN_SOUTH, 1)))
    assert pad.cut(1).scan.resynced is False


def test_an_observed_full_state_reading_is_history_not_a_resync():
    stub, pad = ingress()
    stub.arrive(Reading(frozenset({BTN_SOUTH}), {ABS_X: 32767}))

    window = pad.cut(1)

    assert window.scan.resynced is False
    assert window.scan.pressed == frozenset({Button.FACE_SOUTH})
    assert window.scan.axes[Axis.LEFT_X] == 1.0


def test_observed_full_states_emit_only_what_changed_between_packets():
    stub, pad = ingress()
    stub.arrive(Snapshot(frozenset(), {ABS_X: 0, ABS_Z: 0}))
    pad.cut(0)
    stub.arrive(
        Reading(frozenset(), {ABS_X: 0, ABS_Z: 0}),
        Reading(frozenset({BTN_SOUTH}), {ABS_X: 0, ABS_Z: 0}),
        Reading(frozenset({BTN_SOUTH}), {ABS_X: 32767, ABS_Z: 0}),
    )

    window = pad.cut(1)

    assert [fact.kind for fact in window.facts] == [
        PadEvent.PRESS,
        PadEvent.AXIS,
    ]


def test_a_disconnect_releases_everything_as_real_edges():
    # A dead battery mid-hold must read as a release, never a stuck button.
    stub, pad = ingress()
    stub.arrive(report((EV_KEY, BTN_SOUTH, 1), (EV_KEY, BTN_TL, 1)))
    pad.cut(1)

    pad.disconnect()
    scan = pad.cut(2).scan
    assert scan.held == frozenset()
    assert scan.released == frozenset({Button.FACE_SOUTH, Button.LB})
    assert scan.disconnected is True
    assert scan.resynced is False


def test_the_disconnect_unwinds_one_button_at_a_time():
    # Each release carries the set as it stood, so a recogniser watching sees
    # a coherent sequence rather than everything vanishing at once.
    stub, pad = ingress()
    stub.arrive(report((EV_KEY, BTN_SOUTH, 1), (EV_KEY, BTN_TL, 1)))
    pad.cut(1)
    pad.disconnect()
    facts = pad.cut(2).facts
    assert [f.held for f in facts] == [
        frozenset({Button.FACE_SOUTH, Button.LB}),
        frozenset({Button.LB}),
    ]


def test_a_disconnect_returns_the_axes_and_the_dpad_to_neutral():
    stub, pad = ingress()
    stub.arrive(report((EV_ABS, ABS_X, 32767), (EV_ABS, ABS_HAT0X, 1)))
    pad.cut(1)
    pad.disconnect()
    scan = pad.cut(2).scan
    assert scan.axes[Axis.LEFT_X] == 0.0
    assert scan.direction is Direction.NEUTRAL


# -- the boundary ---------------------------------------------------------


def test_every_frame_cuts_even_with_nothing_to_report():
    stub, pad = ingress()
    for frame in range(4):
        window = pad.cut(frame)
        assert window.frame == frame
        assert window.facts == ()
    assert len(pad.stream) == 4


def test_a_settled_window_does_not_change_when_more_arrives():
    stub, pad = ingress()
    stub.arrive(report((EV_KEY, BTN_SOUTH, 1)))
    window = pad.cut(1)
    stub.arrive(report((EV_KEY, BTN_SOUTH, 0)))
    pad.cut(2)
    assert window.scan.held == frozenset({Button.FACE_SOUTH})


def test_state_outlives_the_facts_that_produced_it():
    stub, pad = ingress()
    stub.arrive(report((EV_KEY, BTN_SOUTH, 1)))
    pad.cut(1)
    for frame in range(2, 40):
        window = pad.cut(frame)
    assert window.facts == ()
    assert window.scan.held == frozenset({Button.FACE_SOUTH})


def test_the_ingress_asks_the_READER_which_axis_is_which():
    # Two pads on one desk can disagree about what ABS_Z means, so the layout
    # is resolved per device at open and carried by the reader. A global table
    # would give one of them a dead right stick and two stuck triggers.
    from blitspersecond.input.pads.evdev import ABS_BRAKE, ABS_GAS, ABS_RZ, layout_for

    generic_ranges = {
        ABS_X: AxisRange(0, 65535),
        ABS_Z: AxisRange(0, 65535),
        ABS_RZ: AxisRange(0, 65535),
        ABS_GAS: AxisRange(0, 1023),
        ABS_BRAKE: AxisRange(0, 1023),
    }
    stub, pad = ingress(ranges=generic_ranges)
    assert stub.layout is layout_for(generic_ranges)

    # ABS_Z is this pad's right stick, resting near the middle of its range.
    stub.arrive(report((EV_ABS, ABS_Z, 32688)))
    scan = pad.cut(1).scan
    assert abs(scan.axes[Axis.RIGHT_X]) < 0.01
    assert scan.pressed == frozenset()          # and no trigger fired
