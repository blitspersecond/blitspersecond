"""The pointer's inverse of the present transform.

Headless by construction: the mapping takes a viewport, so every geometry
worth asserting is one `integer_viewport` call away and no window is needed.
"""

import pytest

from blitspersecond.common import Location
from blitspersecond.common.screen import SCREEN
from blitspersecond.input.kbm.mouse import screen_location
from blitspersecond.internal.window import integer_viewport


def viewport(window_width: int, window_height: int):
    return integer_viewport(
        SCREEN.width, SCREEN.height, window_width, window_height
    )


def test_an_exact_fit_maps_one_to_one_with_the_y_axis_flipped():
    fit = viewport(640, 360)
    # Pyglet's origin is bottom-left, so the window's top row is y = 359.
    assert screen_location(0, 359, fit) == Location(0, 0)
    assert screen_location(639, 0, fit) == Location(639, 359)
    assert screen_location(0, 0, fit) == Location(0, 359)


def test_a_whole_scale_maps_every_window_pixel_of_a_screen_pixel_to_it():
    fit = viewport(2560, 1440)  # exactly 4x, no margins
    assert fit.scale == 4
    # The four window rows of internal row 0, and the first outside it.
    assert screen_location(0, 1439, fit) == Location(0, 0)
    assert screen_location(0, 1436, fit) == Location(0, 0)
    assert screen_location(0, 1435, fit) == Location(0, 1)
    # The four window columns of internal column 0.
    assert screen_location(3, 1439, fit) == Location(0, 0)
    assert screen_location(4, 1439, fit) == Location(1, 0)


def test_the_letterbox_holds_the_pointer_at_the_edge_it_left_through():
    # 1280x800 fits 2x (1280x720), leaving 40 rows of margin top and bottom.
    fit = viewport(1280, 800)
    assert (fit.scale, fit.x, fit.y) == (2, 0, 40)
    assert screen_location(0, 40, fit) == Location(0, 359)
    assert screen_location(0, 759, fit) == Location(0, 0)
    # Out in the margin, top and bottom: held at the row it left through.
    assert screen_location(0, 0, fit) == Location(0, 359)
    assert screen_location(0, 799, fit) == Location(0, 0)


def test_a_clamped_pointer_slides_along_the_edge_rather_than_stopping_dead():
    # The behaviour the OS gives for free at an exact fit: push past the edge
    # and that axis sticks while the other keeps tracking.
    fit = viewport(1600, 720)  # 2x, 160 columns of pillarbox each side
    assert (fit.scale, fit.x) == (2, 160)
    assert screen_location(1500, 719, fit) == Location(639, 0)
    assert screen_location(1500, 400, fit) == Location(639, 159)
    assert screen_location(1500, 0, fit) == Location(639, 359)


def test_an_odd_remainder_does_not_split_evenly_and_is_not_assumed_to():
    # 1280x801 leaves 81 rows: 40 below, 41 above. A mapping that assumed
    # symmetry would be a pixel out at one edge and not the other.
    fit = viewport(1280, 801)
    assert (fit.y, fit.height) == (40, 720)
    assert screen_location(0, 40, fit) == Location(0, 359)
    assert screen_location(0, 759, fit) == Location(0, 0)


def test_a_fractional_window_coordinate_floors_rather_than_truncating():
    # Pyglet divides by the display scale on the way out, so HiDPI delivers
    # floats. Flooring keeps a fraction inside the pixel it fell in.
    fit = viewport(1280, 720)
    assert screen_location(2.9, 719, fit) == Location(1, 0)
    assert screen_location(3.0, 719, fit) == Location(1, 0)
    assert screen_location(4.0, 719, fit) == Location(2, 0)


def test_truncation_toward_zero_would_lose_the_column_left_of_the_picture():
    # int() sends -0.5 to 0; floor sends it to -1, which then clamps to 0 as
    # a clamp rather than as an accident. One window pixel left of a 2x
    # pillarbox must be the same answer as ten thousand pixels left of it.
    fit = viewport(1600, 720)
    assert screen_location(159, 0, fit) == Location(0, 359)
    assert screen_location(0, 0, fit) == Location(0, 359)


@pytest.mark.parametrize(
    "window", [(640, 360), (1280, 720), (1366, 768), (1920, 1080)]
)
def test_every_answer_is_a_pixel_on_the_screen_and_every_pixel_is_reachable(
    window,
):
    fit = viewport(*window)
    seen = set()
    for wx in range(window[0]):
        for wy in range(window[1]):
            landed = screen_location(wx, wy, fit)
            assert 0 <= landed.x < SCREEN.width
            assert 0 <= landed.y < SCREEN.height
            seen.add(landed)
    assert len(seen) == SCREEN.width * SCREEN.height


# -- the ingress ---------------------------------------------------------
#
# Headless: an EventBus, an ingress, and a viewport stated by the test. No
# window is constructed anywhere, and the cut is driven by hand.

from blitspersecond.common import fps
from blitspersecond.input.kbm.mouse import (
    BACK,
    BUTTONS,
    MIDDLE,
    PRIMARY,
    SECONDARY,
    MouseEvent,
    MouseIngress,
)
from blitspersecond.internal import EventBus


def _mouse(window=(1280, 720), rate: fps = fps.SIXTY):
    """An ingress mapping against a stated window. 1280x720 is 2x, no margins."""
    events = EventBus()
    fit = viewport(*window)
    return events, MouseIngress(events, rate, lambda: fit)


def _kinds(window):
    return [(fact.kind, fact.button) for fact in window.facts]


def test_the_pointer_starts_in_the_middle_because_nothing_has_measured_it():
    _, mouse = _mouse()
    scan = mouse.cut(0).scan
    assert scan.position == Location(320, 180)
    assert scan.delta == (0.0, 0.0)
    assert scan.held == frozenset()


def test_a_click_is_reported_where_it_happened_not_where_the_pointer_ended():
    events, mouse = _mouse()
    # Click at the left edge, then sweep to the right before the frame ends.
    events.dispatch_event("on_mouse_press", 0, 719, PRIMARY, 0)
    events.dispatch_event("on_mouse_motion", 1278, 719, 1278, 0)

    window = mouse.cut(1)

    press = window.facts[0]
    assert (press.kind, press.button) == (MouseEvent.PRESS, PRIMARY)
    assert press.position == Location(0, 0)
    # The scan ends where the pointer finished; the fact does not move with it.
    assert window.scan.position == Location(639, 0)


def test_motion_keeps_its_path_rather_than_collapsing_to_a_total():
    events, mouse = _mouse()
    for x in (100, 200, 300):
        events.dispatch_event("on_mouse_motion", x, 719, 100, 0)

    window = mouse.cut(1)

    assert [f.position for f in window.facts] == [
        Location(50, 0),
        Location(100, 0),
        Location(150, 0),
    ]
    # Summed in the scan, in internal pixels: 300 window px at 2x is 150.
    assert window.scan.delta == (150.0, 0.0)


def test_delta_is_internal_pixels_with_y_growing_down():
    events, mouse = _mouse()
    # Pyglet's dy is up-positive; the screen's y grows down.
    events.dispatch_event("on_mouse_motion", 640, 360, 10, 8)
    window = mouse.cut(1)
    assert window.scan.delta == (5.0, -4.0)
    assert window.facts[0].delta == (5.0, -4.0)


def test_a_drag_at_the_screen_edge_keeps_its_distance():
    # 1600x720 pillarboxes at 2x, so window x 1440.. is off the picture.
    events, mouse = _mouse(window=(1600, 720))
    events.dispatch_event("on_mouse_motion", 1500, 360, 200, 0)
    events.dispatch_event("on_mouse_motion", 1560, 360, 60, 0)

    window = mouse.cut(1)

    # Position is held at the edge...
    assert window.scan.position == Location(639, 179)
    # ...but the motion that ran past it is not eaten.
    assert window.scan.delta == (130.0, 0.0)


def test_a_sub_frame_click_still_edges_and_the_held_set_closes():
    events, mouse = _mouse()
    events.dispatch_event("on_mouse_press", 0, 719, PRIMARY, 0)
    events.dispatch_event("on_mouse_release", 0, 719, PRIMARY, 0)

    scan = mouse.cut(1).scan

    assert scan.pressed == frozenset({PRIMARY})
    assert scan.released == frozenset({PRIMARY})
    assert scan.held == frozenset()


def test_a_transition_carries_the_held_set_its_own_button_is_in():
    events, mouse = _mouse()
    events.dispatch_event("on_mouse_press", 0, 719, PRIMARY, 0)
    events.dispatch_event("on_mouse_press", 0, 719, SECONDARY, 0)
    events.dispatch_event("on_mouse_release", 0, 719, PRIMARY, 0)

    facts = mouse.cut(1).facts

    # A press carries the set AFTER its button was added.
    assert facts[0].held == frozenset({PRIMARY})
    assert facts[1].held == frozenset({PRIMARY, SECONDARY})
    # A release carries the set BEFORE its button was removed.
    assert facts[2].held == frozenset({PRIMARY, SECONDARY})


def test_a_button_beyond_the_ideal_mouse_never_reaches_a_window():
    events, mouse = _mouse()
    grid_button = 64
    assert grid_button not in BUTTONS
    events.dispatch_event("on_mouse_press", 0, 719, grid_button, 0)
    events.dispatch_event("on_mouse_press", 0, 719, MIDDLE, 0)

    window = mouse.cut(1)

    assert _kinds(window) == [(MouseEvent.PRESS, MIDDLE)]
    assert window.scan.held == frozenset({MIDDLE})


def test_horizontal_scroll_is_dropped_and_vertical_is_summed():
    events, mouse = _mouse()
    events.dispatch_event("on_mouse_scroll", 640, 360, -3.0, 1.0)
    events.dispatch_event("on_mouse_scroll", 640, 360, 7.0, 2.0)

    window = mouse.cut(1)

    assert window.scan.scroll == 3.0
    assert [f.scroll for f in window.facts] == [1.0, 2.0]


def test_focus_loss_releases_everything_and_says_it_was_not_the_player():
    events, mouse = _mouse()
    events.dispatch_event("on_mouse_press", 0, 719, PRIMARY, 0)
    events.dispatch_event("on_mouse_press", 0, 719, BACK, 0)
    mouse.cut(1)

    events.dispatch_event("on_deactivate")
    scan = mouse.cut(2).scan

    assert scan.held == frozenset()
    assert scan.released == frozenset({PRIMARY, BACK})
    assert scan.cleared is True


def test_the_clear_unwinds_one_button_at_a_time():
    events, mouse = _mouse()
    events.dispatch_event("on_mouse_press", 0, 719, PRIMARY, 0)
    events.dispatch_event("on_mouse_press", 0, 719, SECONDARY, 0)
    mouse.cut(1)

    events.dispatch_event("on_deactivate")
    facts = mouse.cut(2).facts

    # Each release carries the set as it stood at that moment, not the empty
    # end state -- so a reader watching the unwind sees a coherent sequence.
    assert [f.held for f in facts] == [
        frozenset({PRIMARY, SECONDARY}),
        frozenset({SECONDARY}),
    ]


def test_entering_the_window_moves_the_pointer_without_claiming_it_travelled():
    events, mouse = _mouse()
    events.dispatch_event("on_mouse_enter", 1278, 719)

    window = mouse.cut(1)

    assert window.scan.position == Location(639, 0)
    assert window.scan.delta == (0.0, 0.0)
    assert window.facts == ()


def test_position_survives_a_flush_because_it_is_state_not_history():
    events, mouse = _mouse()
    events.dispatch_event("on_mouse_motion", 1278, 719, 0, 0)
    events.dispatch_event("on_mouse_press", 1278, 719, PRIMARY, 0)
    mouse.cut(1)

    events.dispatch_event("on_mouse_motion", 0, 0, -1278, -719)
    mouse.flush()
    scan = mouse.cut(2).scan

    # The motion staged during the suspension is gone...
    assert scan.delta == (0.0, 0.0)
    assert scan.position == Location(639, 0)
    # ...and nothing is left held.
    assert scan.held == frozenset()
    assert scan.cleared is True


def test_every_frame_cuts_even_with_nothing_to_report():
    _, mouse = _mouse()
    for frame in range(4):
        window = mouse.cut(frame)
        assert window.frame == frame
        assert window.facts == ()
    assert len(mouse.stream) == 4


def test_staging_is_bounded_and_says_how_much_it_lost():
    events, mouse = _mouse()
    for _ in range(600):
        events.dispatch_event("on_mouse_motion", 640, 360, 1, 0)

    window = mouse.cut(1)

    assert len(window.facts) == 512
    assert mouse.dropped == 88


def test_an_ingress_handler_never_swallows_an_event():
    # A truthy return is EVENT_HANDLED, which would stop the event reaching
    # Lifecycle and every other device on the same bus.
    #
    # The observer is pushed BEFORE the ingress deliberately. Pyglet
    # dispatches most-recently-pushed first, so an observer pushed after the
    # ingress would run first and see the event whether or not the ingress
    # swallowed it -- the test would pass on a broken engine. Pushed first, it
    # sits under the ingress and is reached only if the ingress falls through.
    events = EventBus()
    seen = []
    events.push_handlers(on_mouse_press=lambda *a: seen.append(a))
    fit = viewport(1280, 720)
    mouse = MouseIngress(events, fps.SIXTY, lambda: fit)

    events.dispatch_event("on_mouse_press", 0, 719, PRIMARY, 0)

    assert seen == [(0, 719, PRIMARY, 0)]
    assert mouse.cut(1).scan.pressed == frozenset({PRIMARY})


def test_a_release_after_a_clear_does_not_report_itself_twice():
    # The same rule the keyboard keeps: the clear already released PRIMARY,
    # and the player's real release must not arrive as a second fact carrying
    # a held-set its own button is not in.
    events, mouse = _mouse()
    events.dispatch_event("on_mouse_press", 100, 100, PRIMARY, 0)
    mouse.cut(0)
    events.dispatch_event("on_deactivate")
    cleared = mouse.cut(1)
    events.dispatch_event("on_mouse_release", 100, 100, PRIMARY, 0)
    after = mouse.cut(2)

    assert [(f.kind, f.button) for f in cleared.facts] == [
        (MouseEvent.RELEASE, PRIMARY)
    ]
    assert cleared.scan.released == frozenset({PRIMARY})
    assert after.facts == ()
    assert after.scan.released == frozenset()


def test_every_transition_carries_the_held_set_its_own_button_is_in():
    events, mouse = _mouse()
    events.dispatch_event("on_mouse_press", 10, 10, PRIMARY, 0)
    events.dispatch_event("on_mouse_press", 10, 10, SECONDARY, 0)
    events.dispatch_event("on_mouse_release", 10, 10, PRIMARY, 0)
    events.dispatch_event("on_mouse_release", 10, 10, PRIMARY, 0)  # unmatched
    events.dispatch_event("on_deactivate")
    window = mouse.cut(0)

    for fact in window.facts:
        if fact.kind in (MouseEvent.PRESS, MouseEvent.RELEASE):
            assert fact.button in fact.held, fact
