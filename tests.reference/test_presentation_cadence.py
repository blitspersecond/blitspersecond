"""Exact contracts for the rational simulation/presentation mapping."""

from fractions import Fraction

import pytest

from blitspersecond.display.internal import PresentationCadence


@pytest.mark.parametrize(
    ("refresh_rate", "ratio"),
    [
        (60, Fraction(1, 1)),
        (75, Fraction(4, 5)),
        (90, Fraction(2, 3)),
        (95, Fraction(12, 19)),
        (120, Fraction(1, 2)),
        (144, Fraction(5, 12)),
        (165, Fraction(4, 11)),
        (180, Fraction(1, 3)),
        (240, Fraction(1, 4)),
    ],
)
def test_common_60hz_cadences_are_short_exact_ratios(refresh_rate, ratio):
    cadence = PresentationCadence(60, refresh_rate)

    assert cadence.ratio == ratio
    assert cadence.effective_rate == pytest.approx(refresh_rate * float(ratio))


@pytest.mark.parametrize(
    ("refresh_rate", "expected"),
    [
        (60, [1, 1, 1, 1]),
        (90, [1, 0, 1, 1, 0, 1]),
        (120, [1, 0, 1, 0, 1, 0]),
        (144, [1, 0, 0, 1, 0, 1, 0, 0, 1, 0, 1, 0]),
        (165, [1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0]),
        (240, [1, 0, 0, 0, 1, 0, 0, 0]),
    ],
)
def test_cadence_patterns_tick_immediately_and_repeat(refresh_rate, expected):
    cadence = PresentationCadence(60, refresh_rate)

    actual = [cadence.advance() for _ in expected]

    assert actual == expected
    cadence.reset()
    assert [cadence.advance() for _ in expected] == expected


def test_rate_below_target_can_schedule_multiple_fixed_ticks():
    cadence = PresentationCadence(60, 30)

    # Construction rotates the first presentation to one immediate startup
    # tick; steady presentations then carry the exact 2/1 cadence.
    assert [cadence.advance() for _ in range(4)] == [1, 2, 2, 2]


def test_extreme_refresh_never_rounds_the_simulation_down_to_no_ticks():
    cadence = PresentationCadence(60, 3000, max_denominator=20)

    assert cadence.ratio == Fraction(1, 20)
    assert sum(cadence.advance() for _ in range(20)) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target_rate": 0, "refresh_rate": 60},
        {"target_rate": -1, "refresh_rate": 60},
        {"target_rate": 60, "refresh_rate": 0},
        {"target_rate": 60, "refresh_rate": -1},
        {"target_rate": 60, "refresh_rate": 60, "max_denominator": 0},
    ],
)
def test_nonpositive_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        PresentationCadence(**kwargs)
