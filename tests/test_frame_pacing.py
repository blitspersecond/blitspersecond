"""The frame-pacing recorder: bounded, self-describing, and recoverable.

Ported from tests.reference/test_frame_pacing_capture.py. The one behaviour
that moved out is the BPS_FRAMEPACE_START_DEADLINE flag, which is a pacing
override rather than instrumentation and is now tested in test_pacing_path.py
alongside the code that reads it.

Timing is driven by a scripted `perf_counter_ns`, so the millisecond columns
below are exact rather than approximate -- these assert the CSV's arithmetic,
not the machine's speed.
"""

import csv
import gc

import pytest

from blitspersecond.system.frame_pacing import recorder as frame_pacing


@pytest.fixture
def scripted_clock(monkeypatch):
    """Install a fixed sequence of perf_counter_ns readings."""

    def install(*readings: int):
        times = iter(readings)
        monkeypatch.setattr(frame_pacing, "perf_counter_ns", lambda: next(times))

    return install


def _one_frame(recorder, **overrides):
    """Walk a recorder through the phases of a single frame."""
    recorder.begin_iteration()
    recorder.after_dispatch()
    recorder.after_simulation()
    recorder.after_prepare()
    present = {
        "presented_ns": 1_010_000_000,
        "ticks": 1,
        "recomposed": True,
        "swap_seconds": 0.002,
        "deadline_seconds": 0.00025,
        "pacing_mode": "deadline",
        "flip_health": "unthrottled",
    }
    recorder.record_present(**(present | overrides))


class TestCapture:
    def test_writes_a_self_describing_csv(self, tmp_path, scripted_clock):
        """Every row carries the format tag and the run's metadata. A capture
        is only comparable against another if it says what it was capturing,
        and these files outlive the process that wrote them."""
        scripted_clock(
            1_000_000_000,  # construction origin
            1_001_000_000,  # iteration
            1_001_100_000,  # dispatch
            1_001_400_000,  # simulation
            1_001_800_000,  # prepare
        )
        path = tmp_path / "capture.csv"
        recorder = frame_pacing.FramePacingRecorder(
            path,
            target_fps=60,
            refresh_hz=120,
            cadence="1/2",
            presentation_path="Xlib/GLX -> Gamescope (outer backend: wayland)",
            capacity=1,
        )
        _one_frame(recorder)
        recorder.save()

        with path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))

        assert rows[0]["format"] == frame_pacing.FORMAT
        assert rows[0]["cadence"] == "1/2"
        assert rows[0]["presentation_path"] == (
            "Xlib/GLX -> Gamescope (outer backend: wayland)"
        )

    def test_phase_durations_are_the_gaps_between_the_marks(
        self, tmp_path, scripted_clock
    ):
        """dispatch/simulation/prepare are differences between successive
        marks, and `wait` is what is left of the present after the swap --
        which is the number the deadline grid is actually judged on."""
        scripted_clock(
            1_000_000_000, 1_001_000_000, 1_001_100_000, 1_001_400_000, 1_001_800_000
        )
        path = tmp_path / "capture.csv"
        recorder = frame_pacing.FramePacingRecorder(
            path, target_fps=60, refresh_hz=120, cadence="1/2", capacity=1
        )
        _one_frame(recorder)
        recorder.save()

        with path.open(newline="", encoding="utf-8") as stream:
            row = next(csv.DictReader(stream))

        assert row["simulation_ms"] == "0.3"   # 1_001_400 - 1_001_100
        assert row["prepare_ms"] == "0.4"      # 1_001_800 - 1_001_400
        assert row["swap_ms"] == "2.0"
        assert row["wait_ms"] == "6.2"         # present - prepare - swap
        assert row["deadline_error_ms"] == "0.25"

    def test_an_absent_deadline_is_blank_not_zero(self, tmp_path, scripted_clock):
        """A vsync-paced frame has no deadline to miss. Zero would read as a
        perfectly-hit deadline, so the column has to be empty instead."""
        scripted_clock(
            1_000_000_000, 1_001_000_000, 1_001_100_000, 1_001_400_000, 1_001_800_000
        )
        path = tmp_path / "capture.csv"
        recorder = frame_pacing.FramePacingRecorder(
            path, target_fps=60, refresh_hz=120, cadence="1/2", capacity=1
        )
        _one_frame(
            recorder, deadline_seconds=None, pacing_mode="vsync", flip_health="ok"
        )
        recorder.save()

        with path.open(newline="", encoding="utf-8") as stream:
            row = next(csv.DictReader(stream))
        assert row["deadline_error_ms"] == ""


class TestBounded:
    def test_frames_past_capacity_are_counted_not_written(
        self, tmp_path, scripted_clock
    ):
        """The capture is bounded so a long run cannot grow without limit in
        the middle of the loop it is measuring. Overflow has to be VISIBLE,
        though -- silently truncating would make a capture look complete."""
        scripted_clock(
            1_000_000_000, 1_001_000_000, 1_001_100_000, 1_001_400_000, 1_001_800_000
        )
        path = tmp_path / "capture.csv"
        recorder = frame_pacing.FramePacingRecorder(
            path, target_fps=60, refresh_hz=120, cadence="1/2", capacity=1
        )
        _one_frame(recorder)
        recorder.record_present(
            presented_ns=1_020_000_000,
            ticks=0,
            recomposed=False,
            swap_seconds=0.001,
            deadline_seconds=None,
            pacing_mode="vsync",
            flip_health="ok",
        )
        recorder.save()

        assert recorder.count == 1
        assert recorder.dropped == 1
        with path.open(newline="", encoding="utf-8") as stream:
            assert len(list(csv.DictReader(stream))) == 1

    def test_capacity_must_be_positive(self, tmp_path):
        with pytest.raises(ValueError):
            frame_pacing.FramePacingRecorder(
                tmp_path / "capture.csv",
                target_fps=60,
                refresh_hz=120,
                cadence="1/2",
                capacity=0,
            )


class TestOptIn:
    def test_no_csv_path_means_no_recorder(self, monkeypatch):
        """The capture is off unless asked for, and `None` is the normal
        case -- the engine then holds no sink and pays nothing per frame."""
        monkeypatch.delenv(frame_pacing.ENV_PATH, raising=False)
        assert (
            frame_pacing.FramePacingRecorder.from_environment(
                target_fps=60, refresh_hz=120, cadence="1/2"
            )
            is None
        )

    def test_a_csv_path_builds_a_recorder(self, tmp_path, monkeypatch):
        monkeypatch.setenv(frame_pacing.ENV_PATH, str(tmp_path / "capture.csv"))
        monkeypatch.delenv(frame_pacing.ENV_DURABLE, raising=False)
        recorder = frame_pacing.FramePacingRecorder.from_environment(
            target_fps=60, refresh_hz=120, cadence="1/2"
        )
        assert recorder is not None

    def test_a_non_integer_capacity_is_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv(frame_pacing.ENV_PATH, str(tmp_path / "capture.csv"))
        monkeypatch.setenv(frame_pacing.ENV_CAPACITY, "loads")
        with pytest.raises(ValueError, match=frame_pacing.ENV_CAPACITY):
            frame_pacing.FramePacingRecorder.from_environment(
                target_fps=60, refresh_hz=120, cadence="1/2"
            )


class TestDurable:
    def test_a_killed_process_leaves_a_recoverable_capture(
        self, tmp_path, scripted_clock
    ):
        """Why the durable mode exists: Gamescope kills its child rather than
        letting it exit, so `save()` never runs. The memory-mapped sidecar
        survives that, and `recover` turns it into the CSV that would have
        been written -- then clears the sidecar so it cannot be read twice."""
        scripted_clock(
            1_000_000_000, 1_001_000_000, 1_001_100_000, 1_001_200_000, 1_001_300_000
        )
        path = tmp_path / "capture.csv"
        recorder = frame_pacing.FramePacingRecorder(
            path,
            target_fps=60,
            refresh_hz=120,
            cadence="1/2",
            capacity=2,
            durable=True,
        )
        _one_frame(recorder, swap_seconds=0.001, deadline_seconds=0.0001)

        del recorder  # no save() -- the process "died" here
        gc.collect()

        assert frame_pacing.FramePacingRecorder.recover(path) == (1, 0)
        with path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        assert len(rows) == 1
        assert rows[0]["pacing_mode"] == "deadline"
        assert not list(tmp_path.glob("*.partial.*"))

    def test_nothing_to_recover_is_not_an_error(self, tmp_path):
        """A run that exited cleanly leaves no sidecar. Recovery has to say
        'nothing here' rather than raise, since the caller cannot know."""
        assert frame_pacing.FramePacingRecorder.recover(tmp_path / "absent.csv") is None
