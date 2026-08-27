"""The cadence's rounding policy, pinned.

`PresentationCadence` distributes fixed simulation ticks over display
presentations. WHICH presentations carry them is a policy choice with three
reasonable answers, and the engine picks one -- ceiling -- through a single
line setting the initial phase, which reads like an implementation detail.

These tests exist because that line is the kind of thing a later tidy-up
"fixes". Any other starting phase still produces the right average rate and
still passes any test that only checks ticks-per-second, so nothing else in
the suite would notice the engine had silently moved to floor scheduling, or
back to the early origin this replaced.

The policy changed on 2026-08-26 -- from a rotation that started the ceiling
gap sequence two slots early to the true ceiling origin -- so the tests that
pinned the old behaviour now pin its replacement, and say so where they sit.
"""

from fractions import Fraction
from math import ceil, floor

import pytest

from blitspersecond.display.internal.pacing.cadence import PresentationCadence
from blitspersecond.display.internal.pacing.session import _RATE_TOLERANCE


def gaps(target: int, refresh: float, count: int = 9):
    """Presentations between consecutive simulation ticks.

    Slot 0 is prepended as the origin, because the schedule starts before its
    first tick: the startup prime is the gap between the run beginning and
    frame one, and dropping it would hide the very thing the ceiling rotation
    is chosen for.
    """
    cadence = PresentationCadence(target, refresh)
    at = [0] + [
        slot
        for slot in range(1, cadence.ratio.denominator * 4 + 2)
        if cadence.advance()
    ]
    return [b - a for a, b in zip(at, at[1:])][:count]


def rounded_gaps(target: int, refresh: int, round_to, count: int = 9):
    """Gaps implied by rounding the ideal cumulative presentation count.

    Exact `Fraction` arithmetic on purpose: with floats, `ceil` of an exact
    multiple such as 2.4 * 5 == 12 lands one too high the moment any epsilon
    creeps in, which silently turns ceiling into floor.
    """
    ratio = Fraction(refresh, target)
    cumulative = [round_to(ratio * k) for k in range(count + 1)]
    return [int(b - a) for a, b in zip(cumulative, cumulative[1:])]


@pytest.mark.parametrize(
    "refresh, expected",
    [
        (144, [3, 2, 3, 2, 2]),   # 12/5
        (165, [3, 3, 3, 2]),      # 11/4
        (100, [2, 2, 1]),         # 5/3
        (75, [2, 1, 1, 1]),       # 5/4
        (120, [2, 2]),            # exact
    ],
)
def test_cadence_follows_the_ceiling_schedule(refresh, expected):
    """Ticks land at ceil(k * refresh / target) presentations."""
    observed = gaps(60, refresh, len(expected))
    assert observed == expected
    assert observed == rounded_gaps(60, refresh, ceil, len(expected))


@pytest.mark.parametrize("refresh", [144, 165, 100, 75])
def test_cadence_is_not_the_floor_schedule(refresh):
    """The distinguishing test. Floor has the same average rate, so only the
    PHASE separates them -- and the phase is the whole point."""
    assert gaps(60, refresh) != rounded_gaps(60, refresh, floor)


@pytest.mark.parametrize("refresh", [144, 165, 100, 75, 120, 60])
def test_no_frame_presents_early_of_the_ideal_grid(refresh):
    """The never-early property, which the ceiling ORIGIN is what buys.

    Replaces `test_the_schedule_runs_early_of_the_ideal_grid`, which pinned
    the opposite and asked in its own docstring to be rewritten if the
    schedule ever became genuinely never-early. It did, on 2026-08-26.

    Exact `Fraction` arithmetic throughout: the 120Hz and 60Hz cases land
    frames exactly ON the deadline, and float error either way would decide
    this test rather than the schedule doing so.
    """
    cadence = PresentationCadence(60, refresh)
    ideal = Fraction(1, 60)
    presented = frames = 0
    errors = []
    for _ in range(cadence.ratio.denominator * 3):
        presented += 1
        for _ in range(cadence.advance()):
            frames += 1
            errors.append(Fraction(presented, refresh) - ideal * frames)

    assert all(error >= 0 for error in errors), "no frame presents early"


@pytest.mark.parametrize("refresh", [144, 165, 100, 75, 120, 60])
def test_every_frame_lands_inside_its_own_window(refresh):
    """The other half: late, but never later than its own frame interval.

    Holds for every refresh at or above the target rate, because a present
    lands below `k/target + 1/refresh`. Below the target it lapses, which
    reads like a floor and is not one: at 40Hz the ratio exceeds 1, ticks
    batch, and a frame "slipping its window" is just what showing 40 frames
    of a 60Hz simulation means. Parametrised at and above the target only,
    because that is the range the guarantee is claimed for.
    """
    cadence = PresentationCadence(60, refresh)
    ideal = Fraction(1, 60)
    presented = frames = 0
    for _ in range(cadence.ratio.denominator * 3):
        presented += 1
        for _ in range(cadence.advance()):
            frames += 1
            error = Fraction(presented, refresh) - ideal * frames
            assert error < ideal, f"frame {frames} slipped a whole interval"


@pytest.mark.parametrize(
    "refresh, prime",
    [(144, 2), (165, 2), (120, 1), (100, 1), (75, 1), (60, 0)],
)
def test_the_run_starts_with_a_prime(refresh, prime):
    """Frame one waits for a full frame interval rather than taking whatever
    is left of one.

    Replaces `test_frame_one_ticks_on_the_first_presentation`, which pinned
    the old origin. The prime is `ceil(refresh / target) - 1` presentations
    that dispatch and re-present but do not simulate; at 60Hz it is zero,
    where lockstep leaves nothing to spare by definition.
    """
    cadence = PresentationCadence(60, refresh)
    assert [cadence.advance() for _ in range(prime)] == [0] * prime
    assert cadence.advance() == 1


def test_individual_intervals_may_still_be_short():
    """Ceiling bounds the CUMULATIVE deadline, not every interval.

    Integer fitting cannot do better -- 2.4 presentations per frame has to be
    spent as whole presentations -- and it is harmless because a presentation
    carrying no tick re-presents the prepared frame rather than advancing the
    simulation. Asserted so nobody reads the guarantee as stronger than it is.
    """
    assert min(gaps(60, 144)) == 2          # 2/144 = 13.9ms, under 1/60
    assert max(gaps(60, 144)) == 3


def test_reset_restores_the_starting_phase():
    """Resume must re-enter the pattern where construction did, or the
    schedule's phase drifts every time the engine is suspended."""
    cadence = PresentationCadence(60, 144)
    first = [cadence.advance() for _ in range(12)]
    cadence.reset()
    assert [cadence.advance() for _ in range(12)] == first


@pytest.mark.parametrize(
    "refresh, ratio, effective",
    [
        (175, (12, 35), 60.0),   # the factory overclock on a 165Hz part
        (500, (3, 25), 60.0),
    ],
)
def test_the_bound_expresses_rates_that_need_a_big_denominator(
    refresh, ratio, effective
):
    """Regression for a silent 2% rate error.

    `limit_denominator` does not fail when it cannot express a ratio; it
    returns the nearest one it CAN build. Under the old bound of 20, 175Hz
    resolved to 7/20 and ran the simulation at 61.25 FPS -- 2% fast forever,
    with nothing logged. 60/175 is 12/35 and 60/500 is 3/25; both need a
    denominator above 20 and neither is exotic hardware.
    """
    cadence = PresentationCadence(60, refresh)
    assert (cadence.ratio.numerator, cadence.ratio.denominator) == ratio
    assert cadence.effective_rate == pytest.approx(effective)
    assert cadence.rate_error == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize(
    "refresh, expected_ratio",
    [(144, (5, 12)), (165, (4, 11)), (120, (1, 2)), (60, (1, 1)), (90, (2, 3))],
)
def test_raising_the_bound_moved_nothing_that_already_worked(
    refresh, expected_ratio
):
    """The bound went 20 -> 40. Every rate that already resolved exactly must
    resolve to the SAME ratio, or the fix traded one silent change for
    another."""
    cadence = PresentationCadence(60, refresh)
    assert (
        cadence.ratio.numerator,
        cadence.ratio.denominator,
    ) == expected_ratio


@pytest.mark.parametrize("refresh", [59.94, 143.98])
def test_an_off_nominal_panel_is_not_a_rate_error(refresh):
    """`rate_error` measures two things and only one is a fault.

    A 59.94Hz panel gives 1/1 and a -0.1% deviation, and that is the design
    working: no smaller rational is closer (the exact one is 1000/999), and a
    notional second on that display really is 1001ms. Correcting it would mean
    double-ticking onto adjacent presentations, which is what this class
    exists to avoid. So the deviation must stay well inside the tolerance the
    session warns on -- asserted against that constant rather than a number
    copied beside it, because the whole point is the RELATIONSHIP between the
    two, and a duplicated literal is what would drift.

    Measured 2026-08-26: 59.94 -> -0.0010, 143.98 -> -0.0001, against a
    tolerance of 0.005.
    """
    cadence = PresentationCadence(60, refresh)
    assert abs(cadence.rate_error) < _RATE_TOLERANCE
