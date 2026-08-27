"""The metrics rings: conservation, honesty about absence, and units.

The arithmetic here is exact rather than approximate -- nothing measures
wall time, because the whole subsystem is keyed on the frame counter and
folds on eviction. So these assert the cascade's bookkeeping, not the
machine's speed.

The expensive test is `test_totals_are_conserved_across_all_levels`, which
has to run ~35,000 frames because that is what it takes to close a MINUTE:
level 1 must fill (512 seconds) before its evictions begin feeding level 2,
and then 60 of those evictions make one minute. Level 2 is where a cascade
bug hides -- both bugs found while building this were invisible until data
reached it.
"""

import numpy as np
import pytest

from blitspersecond.common import SingletonMeta
from blitspersecond.system.metrics import Metrics, carry_forward
from blitspersecond.system.metrics.stream import (
    FRAMES,
    MINUTES,
    SECONDS,
    SECONDS_PER_MINUTE,
    frame_slot,
)

FPS = 60


@pytest.fixture
def metrics():
    SingletonMeta._instances.pop(Metrics, None)
    yield Metrics(framerate=FPS)
    SingletonMeta._instances.pop(Metrics, None)


def _run(metrics, frames, push=None):
    """Service `frames` game frames, calling `push(n)` inside each."""
    for n in range(frames):
        if push is not None:
            push(n)
        metrics.advance()


# -- conservation --------------------------------------------------------


def test_totals_are_conserved_across_all_levels(metrics):
    """Every pushed unit is somewhere, and nowhere twice.

    The levels are DISJOINT -- level 0 holds the recent frames, level 1 the
    period before it, level 2 the period before that -- so a session total
    is their sum plus the accumulators still open. An overlapping cascade
    (folding a second into its minute when it CLOSES rather than when it is
    EVICTED) passes every shorter test and doubles the total here.
    """
    counter = metrics.counter("test.units", "One unit per frame")
    # Enough to fill level 0, fill level 1, and close at least one minute.
    frames = FRAMES + (SECONDS + SECONDS_PER_MINUTE + 1) * FPS
    _run(metrics, frames, lambda n: counter.push(1.0))

    summary = metrics.report().summarise("test.units")
    assert summary.total == pytest.approx(float(frames))


def test_counter_sums_several_pushes_in_one_frame(metrics):
    """Several hot paths may report into one counter per frame."""
    counter = metrics.counter("test.blits", "Blits per frame")

    def push(n):
        counter.push(10.0)
        counter.push(5.0)

    _run(metrics, 100, push)
    summary = metrics.report().summarise("test.blits")
    assert summary.total == pytest.approx(1500.0)
    assert summary.maximum == pytest.approx(15.0)


def test_gauge_keeps_min_max_and_count_within_a_frame(metrics):
    """A gauge observed several times in one frame keeps all of them.

    This is the presentation cadence: on a 144Hz display a 60fps simulation
    presents 2 or 3 times per game frame, and that per-frame count is the
    cadence itself rather than a derived statistic.
    """
    gauge = metrics.gauge("test.swap", "Swap duration")
    gauge.push(2.0)
    gauge.push(8.0)
    gauge.push(5.0)
    metrics.advance()

    minimums, maximums, sums, counts = gauge_levels(metrics, "test.swap")[0]
    slot = frame_slot(0)
    assert minimums[slot] == 2.0
    assert maximums[slot] == 8.0
    assert sums[slot] == 15.0
    assert counts[slot] == 3.0


def gauge_levels(metrics, name):
    index = metrics.names.index(name)
    return metrics._streams[index].levels


# -- absence is not zero -------------------------------------------------


def test_unreported_frames_read_as_nan_not_zero(metrics):
    """A frame nobody measured is unknown, not zero.

    The distinction is load-bearing: `np.nansum` of a sparse ring is the
    answer, a zero-filled ring's sum is a plausible lie, and a plain `.sum()`
    of the sparse ring is NaN -- which fails loudly instead.
    """
    gauge = metrics.gauge("test.sparse", "Measured every third frame")
    _run(metrics, 300, lambda n: gauge.push(4.0) if n % 3 == 0 else None)

    minimums = np.frombuffer(gauge_levels(metrics, "test.sparse")[0][0], dtype=np.float64)
    measured = ~np.isnan(minimums)
    assert measured.sum() == 100
    assert np.isnan(minimums).sum() == FRAMES - 100
    assert np.nanmean(minimums) == pytest.approx(4.0)


def test_rebase_discards_the_open_frame(metrics):
    """Wall time nothing should be charged for leaves no measurement.

    Resuming from suspension is the case: the frame spanning the pause
    describes the pause, not the game. It must read as unmeasured rather
    than as one catastrophically slow frame.
    """
    gauge = metrics.gauge("test.frame", "Frame duration")
    gauge.push(16.6)
    metrics.advance()
    gauge.push(30_000.0)      # a frame that spanned a suspension
    metrics.rebase()
    metrics.advance()

    minimums = np.frombuffer(gauge_levels(metrics, "test.frame")[0][0], dtype=np.float64)
    assert minimums[frame_slot(0)] == pytest.approx(16.6)
    assert np.isnan(minimums[frame_slot(1)])
    assert np.nanmax(minimums) == pytest.approx(16.6)


def test_statistics_skip_gaps_while_carry_forward_fills_them(metrics):
    """Both readings come out of the same ring, and they differ."""
    values = np.full(10, np.nan)
    values[2] = 5.0
    values[7] = 9.0

    assert np.nanmean(values) == pytest.approx(7.0)      # over 2 measured
    filled = carry_forward(values)
    assert filled[6] == 5.0 and filled[9] == 9.0
    assert np.isnan(filled[0])                           # nothing to carry yet
    assert np.nanmean(filled) == pytest.approx(6.5)      # a different number


# -- signs and types -----------------------------------------------------


def test_gauges_keep_negative_values(metrics):
    """`display.deadline.ms` is negative when a frame presents EARLY.

    Emptiness is flagged by `count`, never by a sentinel in `min`, so a
    metric reporting only negative values reports them rather than pinning
    its minimum at zero.
    """
    gauge = metrics.gauge("test.deadline", "Signed deadline error")
    for value in (-0.5, -0.2, -0.9):
        gauge.push(value)
    metrics.advance()

    summary = metrics.report().summarise("test.deadline")
    assert summary.minimum == pytest.approx(-0.9)
    assert summary.maximum == pytest.approx(-0.2)


def test_counters_reject_negative_deltas(metrics):
    counter = metrics.counter("test.tally", "Things that happened")
    with pytest.raises(ValueError, match="cannot push a negative delta"):
        counter.push(-1.0)


def test_redeclaring_a_name_with_another_type_raises(metrics):
    metrics.counter("test.thing", "A tally")
    assert metrics.counter("test.thing", "A tally") is metrics["test.thing"]
    with pytest.raises(TypeError, match="already declared"):
        metrics.gauge("test.thing", "A level")


# -- the cascade ---------------------------------------------------------


def test_nothing_is_evicted_until_the_ring_has_filled(metrics):
    """Level 1 covers the window BEFORE level 0, so it stays empty until
    frames start ageing out. Counting seconds from the first advance would
    close ~17 seconds of empty accumulators and report them as measured."""
    gauge = metrics.gauge("test.early", "Measured every frame")
    _run(metrics, FRAMES - 1, lambda n: gauge.push(1.0))

    stream = metrics._streams[metrics.names.index("test.early")]
    assert stream.closed == (0, 0)


def test_seconds_close_on_eviction_at_the_frame_rate(metrics):
    gauge = metrics.gauge("test.folding", "Measured every frame")
    _run(metrics, FRAMES + 5 * FPS, lambda n: gauge.push(2.0))

    stream = metrics._streams[metrics.names.index("test.folding")]
    seconds, minutes = stream.closed
    assert (seconds, minutes) == (5, 0)

    counts = np.frombuffer(stream.levels[1][3], dtype=np.float64)
    assert list(counts[:5]) == [float(FPS)] * 5


def test_max_folds_losslessly_into_the_session_worst(metrics):
    """The distribution decays with the cascade; the tail does not.

    A spike that has aged out of the 1024-frame window is gone from the
    percentiles but still exact in `session_worst`, because `max` folds
    without loss at every level.
    """
    gauge = metrics.gauge("test.spike", "One early spike")
    _run(metrics, 10, lambda n: gauge.push(99.0 if n == 3 else 1.0))
    _run(metrics, FRAMES + FPS, lambda n: gauge.push(1.0))

    summary = metrics.report().summarise("test.spike")
    assert summary.maximum == pytest.approx(1.0)          # aged out of level 0
    assert summary.session_worst == pytest.approx(99.0)   # still exact


def test_ring_sizes_need_not_divide_by_the_fold_ratio(metrics):
    """Folds are driven by eviction COUNT, not ring capacity, so nobody
    should 'fix' 1024 to 960 to make it divide by 60."""
    assert FRAMES % FPS != 0
    gauge = metrics.gauge("test.ratio", "Measured every frame")
    _run(metrics, FRAMES + 3 * FPS, lambda n: gauge.push(1.0))
    stream = metrics._streams[metrics.names.index("test.ratio")]
    assert stream.closed[0] == 3


# -- the staging buffer --------------------------------------------------


def test_buffer_bounds_a_runaway_and_counts_what_it_dropped(metrics):
    """A fixed buffer refuses to grow inside a frame and says so."""
    SingletonMeta._instances.pop(Metrics, None)
    small = Metrics(framerate=FPS, capacity=8)
    gauge = small.gauge("test.flood", "Pushed far too often")
    for _ in range(20):
        gauge.push(1.0)

    assert small.observations_dropped == 12   # 20 pushed, 8 staged
    small.advance()
    # What was staged still landed; only the overflow was lost.
    assert small.report().summarise("test.flood").observed == 1


def test_frame_counter_is_shared_so_slots_align(metrics):
    """Slot N is the same frame in every metric -- which is what makes a
    report able to correlate a gameloop spike with the swap beside it."""
    dense = metrics.gauge("test.dense", "Every frame")
    sparse = metrics.gauge("test.sparse2", "Every fifth frame")
    _run(
        metrics,
        50,
        lambda n: (dense.push(float(n)), sparse.push(float(n)) if n % 5 == 0 else None),
    )

    dense_values = np.frombuffer(gauge_levels(metrics, "test.dense")[0][0], dtype=np.float64)
    sparse_values = np.frombuffer(gauge_levels(metrics, "test.sparse2")[0][0], dtype=np.float64)
    measured = np.where(~np.isnan(sparse_values[:50]))[0]
    assert list(measured) == list(range(0, 50, 5))
    assert list(dense_values[measured]) == [float(i) for i in measured]


# -- the long run --------------------------------------------------------


def test_level_two_wraps_without_drift_or_corruption():
    """Past the point where the oldest level-2 slots start being discarded.

    Level 2 fills after 512 minutes -- about 8.7 hours at 60fps -- and then
    overwrites its oldest minute on every close. This drives a stream well
    past that with `framerate=1`, so a "second" is one frame and the whole
    cascade is exercised in ~65,000 frames rather than ~3 million.

    What must hold afterwards: sums exact (no float drift accumulating
    across a million folds), no NaN where a slot was written, indices still
    in range, and the retained total equal to the window rather than to
    everything ever pushed. Nothing here keeps a whole session -- that is
    the same promise as unbounded memory -- so the correct behaviour is to
    forget the oldest, exactly and quietly.
    """
    SingletonMeta._instances.pop(Metrics, None)
    metrics = Metrics(framerate=1)
    try:
        counter = metrics.counter("test.long", "One unit per frame")
        # 512 minutes need 512*60 second-evictions, and those only begin
        # once level 1 has filled. Double it to wrap level 2 outright.
        frames = 2 * (FRAMES + (SECONDS + MINUTES * SECONDS_PER_MINUTE))
        _run(metrics, frames, lambda n: counter.push(1.0))

        stream = metrics._streams[metrics.names.index("test.long")]
        seconds, minutes = stream.closed
        assert minutes > MINUTES, "level 2 never wrapped; test is not testing"

        # Every closed second folded exactly one frame (framerate=1), and
        # every closed minute exactly 60 seconds. Drift would show here.
        second_sums = np.frombuffer(stream.levels[1][0], dtype=np.float64)
        minute_sums = np.frombuffer(stream.levels[2][0], dtype=np.float64)
        assert not np.isnan(second_sums).any()
        assert not np.isnan(minute_sums).any()
        assert second_sums.min() == second_sums.max() == 1.0
        assert minute_sums.min() == minute_sums.max() == float(SECONDS_PER_MINUTE)

        # The total is the RETAINED window, not the whole run, and it is
        # short by exactly one frame: the slot the final advance opened.
        (pending_second, _), (pending_minute, _) = stream.pending
        window = (
            FRAMES
            + min(seconds, SECONDS)
            + min(minutes, MINUTES) * SECONDS_PER_MINUTE
        )
        summary = metrics.report().summarise("test.long")
        assert summary.total == pytest.approx(
            window + pending_second + pending_minute - 1
        )
        assert summary.total < frames, "a finite window cannot hold the run"
    finally:
        SingletonMeta._instances.pop(Metrics, None)
