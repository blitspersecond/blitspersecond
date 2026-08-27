"""What a measured presentation rate is allowed to conclude.

`DeliveredRate` answers one question -- "has this window moved to a different
output?" -- from nothing but the interval between presentations. The hazard is
that a slow game and a slower display produce the identical signal, so the
estimator's value is entirely in what it REFUSES. These tests pin the
refusals; the acceptances are the easy half.
"""

import pytest

from blitspersecond.display.internal.pacing.rate import DeliveredRate


def feed(rate, outputs, count=20):
    """Deliver `count` perfectly steady presentations at `rate`."""
    estimator = DeliveredRate(lambda: outputs)
    result = None
    for _ in range(count):
        result = estimator.observe(1.0 / rate)
    return result


def test_a_panel_on_its_ntsc_rate_still_matches_its_label():
    """The whole legitimate spread is label rounding, and 1000/1001 is the
    biggest part of it: a mode enumerated as 60 delivers 59.94. Tightening the
    match to 0.3% must not break the one case it most needs to accept."""
    assert feed(60 * 1000 / 1001, (60.0,)) == 60.0
    assert feed(144 * 1000 / 1001, (144.0,)) == 144.0


def test_a_slow_game_is_not_a_display_move():
    """The regression the tolerance was tightened for.

    A game running ~150fps on a 165Hz display, with a 144Hz output also
    connected, used to match 144 at the previous 5% tolerance and re-cadence
    the simulation onto a grid the window was not on -- the exact fault
    `_retune` exists to prevent, inverted. It is a slow game, and
    `PresentationCadence` is built so a slow stream makes the game slow
    rather than hiding it.
    """
    assert feed(150.0, (165.0, 144.0)) is None


@pytest.mark.parametrize("delivered", [165.0, 170.0, 175.0, 180.0])
def test_adjacent_overclock_modes_no_longer_share_a_tolerance(delivered):
    """165/170/175/180 sit 2.9-3.0% apart -- inside the old 5% band, so each
    was a candidate for the others. At 0.3% each rate resolves to itself."""
    assert feed(delivered, (165.0, 170.0, 175.0, 180.0)) == delivered


def test_an_unsteady_stream_concludes_nothing():
    """A window straddling two outputs is paced by an interleaving of both
    vblanks. Measured on GNOME/Mutter that yields a median no display runs at,
    so agreement is required before a median counts at all."""
    estimator = DeliveredRate(lambda: (144.0, 60.0))
    result = None
    for index in range(40):
        result = estimator.observe(1 / 144 if index % 2 else 1 / 60)
    assert result is None


def test_a_rate_matching_no_connected_output_is_refused():
    """Steadiness alone is not evidence. A rock-solid 100 presentations per
    second on a machine with no 100Hz output is a capped or throttled game."""
    assert feed(100.0, (144.0, 60.0)) is None
