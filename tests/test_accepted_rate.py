"""What rate a panel will carry without a beat."""

from fractions import Fraction

import pytest

from blitspersecond.common import fps
from blitspersecond.internal.pacing.accepted_rate import (
    RATE_TOLERANCE,
    AcceptedRate,
    accept_rate,
)

#: Every panel rate the engine claims to carry. The NTSC ones are the reason
#: this module exists: each is its nominal over 1.001, and 143.998848 is DWM's
#: live clock on a panel whose mode says 144.
PANELS = (
    59.94, 60, 75, 90, 100, 119.88, 120, 143.998848, 144, 165, 175, 200,
    239.76, 240, 280, 360, 500,
)


def test_the_accepted_grid_divides_the_panels_grid():
    """The property the whole module exists for.

    `numerator` ticks per `denominator` slots means the two grids land on each
    other forever, so nothing slides and no frame is left without a slot.
    """
    for panel in PANELS:
        for rate in fps:
            accepted = accept_rate(int(rate), panel)
            period = 1.0 / panel
            assert accepted.step * accepted.ratio.numerator == pytest.approx(
                period * accepted.ratio.denominator
            )


def test_a_non_nominal_panel_is_carried_one_tick_per_slot():
    """The case this was built for: 59.94Hz stops beating against 60."""
    accepted = accept_rate(60, 59.94)
    assert accepted.nominal == 60
    assert accepted.ratio == Fraction(1, 1)
    assert accepted.rate == pytest.approx(59.94)
    assert accepted.error == pytest.approx(-0.001)
    assert not accepted.exact


def test_an_ordinary_panel_is_left_completely_alone():
    """Nothing changes where nothing was wrong. 144Hz already closes at 5/12."""
    accepted = accept_rate(60, 144)
    assert accepted.ratio == Fraction(5, 12)
    assert accepted.rate == 60.0
    assert accepted.exact
    assert accepted.step == pytest.approx(1 / 60)


def test_the_ratio_comes_from_the_nominal_rate_and_the_step_from_the_measured():
    """Both halves. Same ratio either side; only the step moves."""
    mode = accept_rate(60, 144)
    live = accept_rate(60, 143.998848)
    assert mode.ratio == live.ratio == Fraction(5, 12)
    assert live.step > mode.step
    assert live.rate == pytest.approx(59.99952)


def test_fitting_the_ratio_to_the_measured_rate_would_rebuild_the_beat():
    """Why nominal, stated as an executable comparison.

    1000/999 delivers exactly 60.000fps on a 59.94Hz panel and realigns the
    grids once every 16.7 seconds -- the fault, restored, looking precise.
    """
    accepted = accept_rate(60, 59.94)
    assert accepted.pattern_seconds < 0.02

    fitted = Fraction(60 / 59.94).limit_denominator(999)
    assert fitted == Fraction(1000, 999)
    assert 59.94 * float(fitted) == pytest.approx(60.0)
    assert fitted.denominator / 59.94 > 16.0


def test_an_off_nominal_panel_is_not_a_rate_error():
    """Asserted against the imported tolerance, never a literal beside it.

    The property under test is the RELATIONSHIP between a panel's deviation
    and the threshold; a copied constant is what would drift. 59.94 lands at
    0.001000000000000038, so a hand-picked bound of 0.001 fails on float dust.
    """
    for panel in (59.94, 119.88, 143.998848, 239.76):
        assert abs(accept_rate(60, panel).error) < RATE_TOLERANCE


def test_every_authored_rate_is_carried_on_every_panel_we_know_of():
    for rate in fps:
        for panel in PANELS:
            assert accept_rate(int(rate), panel).within_tolerance


def test_the_pattern_never_runs_long_enough_to_read_as_a_beat():
    for rate in fps:
        for panel in PANELS:
            assert accept_rate(int(rate), panel).pattern_seconds <= 0.34


def test_a_panel_below_the_crossover_is_left_alone():
    """The regression guard. Decimation by discard must not change.

    A 40Hz cap against a 60fps game is not a beat -- 60/40 is exactly 3/2 --
    so nothing here touches it and the display still simply gets less.
    """
    accepted = accept_rate(60, 40)
    assert accepted.ratio == Fraction(3, 2)
    assert accepted.exact
    assert accepted.rate == pytest.approx(60.0)


def test_an_oddly_rated_panel_still_lands_within_tolerance():
    """No bound to fall outside of, so a strange rate degrades gently."""
    accepted = accept_rate(60, 87.3)
    assert accepted.nominal == 87
    assert abs(accepted.error) < RATE_TOLERANCE


@pytest.mark.parametrize("desired,refresh", [(0, 60), (-60, 60), (60, 0), (60, -1)])
def test_non_positive_arguments_refuse(desired, refresh):
    with pytest.raises(ValueError):
        accept_rate(desired, refresh)


def test_an_accepted_rate_is_frozen():
    with pytest.raises(AttributeError):
        accept_rate(60, 144).rate = 30  # type: ignore[misc]
