"""Contracts for measuring the display rate that is actually pacing us.

The dangerous failure is not missing a monitor change; it is inventing one.
Re-cadencing on a rate no display is running at converts "this game is too
slow" into "this game is now correctly paced at a lower rate", which hides a
performance problem and contradicts PresentationCadence's deliberate choice
that a slow presentation stream makes the game slow.
"""

import pytest

from blitspersecond.display.internal.presentation.rate import DeliveredRate


def feed(estimator, rate, frames, *, jitter=0.0):
    """Feed `frames` intervals at `rate`; return the last verdict."""
    verdict = None
    period = 1.0 / rate
    for index in range(frames):
        offset = jitter * period * (1 if index % 2 else -1)
        verdict = estimator.observe(period + offset)
    return verdict


def constant(*rates):
    return lambda: rates


def test_steady_rate_matching_an_output_is_adopted():
    estimator = DeliveredRate(constant(144.0, 60.0), window=20)

    assert feed(estimator, 60.0, 19) is None, "must not rule before the window fills"
    assert feed(estimator, 60.0, 1) == 60.0


def test_estimate_snaps_onto_the_output_rate_not_the_raw_median():
    # A real 59.96Hz output measured with ordinary jitter still re-cadences
    # onto the enumerated rate rather than onto whatever the median was.
    estimator = DeliveredRate(constant(143.935698, 59.962844), window=20)

    assert feed(estimator, 59.9, 25, jitter=0.01) == 59.962844


def test_a_slow_game_is_not_mistaken_for_a_slower_display():
    # Half-rate on a 144Hz display: a perfectly steady 72Hz that no output is
    # running at. This must never re-cadence.
    estimator = DeliveredRate(constant(144.0, 60.0), window=20)

    assert feed(estimator, 72.0, 200) is None


def test_a_mixed_straddle_stream_is_rejected():
    # Measured on Mutter with a window overlapping a 144Hz and a 60Hz output:
    # the intervals alternate and the median lands near 69Hz, which is not a
    # rate anything is running at.
    estimator = DeliveredRate(constant(144.0, 60.0), window=20)

    verdict = None
    for index in range(200):
        verdict = estimator.observe(1 / 144 if index % 2 else 1 / 60)
    assert verdict is None


def test_a_stalled_swap_does_not_poison_the_window():
    estimator = DeliveredRate(constant(144.0, 60.0), window=20)
    feed(estimator, 60.0, 19)

    # A withheld presentation is not evidence about the display, and must
    # discard the run rather than average into it.
    assert estimator.observe(2.0) is None
    assert feed(estimator, 60.0, 19) is None, "evidence should have been dropped"
    assert feed(estimator, 60.0, 1) == 60.0


@pytest.mark.parametrize("interval", [0.0, -0.001])
def test_nonsense_intervals_are_rejected(interval):
    estimator = DeliveredRate(constant(60.0), window=4)

    assert estimator.observe(interval) is None


def test_reset_discards_accumulated_evidence():
    estimator = DeliveredRate(constant(60.0), window=20)
    feed(estimator, 60.0, 19)
    estimator.reset()

    assert feed(estimator, 60.0, 19) is None
    assert feed(estimator, 60.0, 1) == 60.0


def test_outputs_are_read_once_and_not_per_frame():
    calls = []

    def outputs():
        calls.append(1)
        return (60.0,)

    estimator = DeliveredRate(outputs, window=5)
    estimator.refresh_outputs()
    feed(estimator, 60.0, 100)

    assert len(calls) == 1, "enumeration costs ~1ms; it must not be per frame"


def test_a_persistently_unmatched_rate_eventually_re_reads_the_outputs():
    # The laptop-into-external-monitor case: the display that can explain the
    # measured rate did not exist when the outputs were last read.
    available = [(60.0,)]

    def outputs():
        return available[0]

    estimator = DeliveredRate(outputs, window=10, recheck_after=20)
    estimator.refresh_outputs()

    assert feed(estimator, 144.0, 15) is None
    available[0] = (60.0, 144.0)
    assert feed(estimator, 144.0, 30) == 144.0


def test_unreadable_outputs_never_re_cadence():
    def outputs():
        raise OSError("display went away")

    estimator = DeliveredRate(outputs, window=5)
    estimator.refresh_outputs()

    assert feed(estimator, 60.0, 50) is None


@pytest.mark.parametrize("window", [0, 1])
def test_degenerate_configuration_is_rejected(window):
    with pytest.raises(ValueError):
        DeliveredRate(constant(60.0), window=window)
