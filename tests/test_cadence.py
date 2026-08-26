"""The cadence's rounding policy, pinned.

`PresentationCadence` distributes fixed simulation ticks over display
presentations. WHICH presentations carry them is a policy choice with three
reasonable answers, and the engine picks one -- ceiling -- through a single
line of phase rotation that reads like an implementation detail.

These tests exist because that line is the kind of thing a later tidy-up
removes. Dropping the rotation still produces the right average rate and
still passes any test that only checks ticks-per-second, so nothing else in
the suite would notice the engine had silently moved to floor scheduling.
"""

from fractions import Fraction
from math import ceil, floor

import pytest

from blitspersecond.display.internal.pacing.cadence import PresentationCadence


def gaps(target: int, refresh: float, count: int = 9):
    """Presentations between consecutive simulation ticks."""
    cadence = PresentationCadence(target, refresh)
    at = [
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


@pytest.mark.parametrize(
    "refresh, worst_ms",
    [(144, -9.72), (165, -10.61)],
)
def test_the_schedule_runs_early_of_the_ideal_grid(refresh, worst_ms):
    """The ceiling GAPS do not buy the never-early property, because the
    ORIGIN is a full frame ahead.

    A ceiling schedule places game frame k at presentation
    ceil(k * refresh / target) -- frame 1 at presentation 3 on a 144Hz
    display. The rotation here places frame 1 at presentation 1, so the
    whole sequence runs 1-2 presentations ahead of ceiling permanently and
    every frame presents early of its ideal target-rate deadline.

    Asserted so the property is not assumed by something built later. If a
    future change makes the schedule genuinely never-early, this test should
    FAIL and be rewritten -- that is the point of pinning it.
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

    assert all(error < 0 for error in errors), "every frame presents early"
    assert min(float(e) * 1000 for e in errors) == pytest.approx(worst_ms, abs=0.01)
    # Bounded, though: it is a standing phase lead, not a drift.
    assert max(errors) - min(errors) < ideal


@pytest.mark.parametrize("refresh", [144, 165, 100, 75])
def test_frame_one_ticks_on_the_first_presentation(refresh):
    """The origin, stated on its own: the engine does not wait for a full
    frame interval before the first tick. That is what shifts the schedule
    early, and it is a startup policy worth deciding deliberately."""
    assert PresentationCadence(60, refresh).advance() == 1


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
