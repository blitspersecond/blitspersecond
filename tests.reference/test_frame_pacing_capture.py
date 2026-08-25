import csv
import gc

from blitspersecond.system.monitor import frame_pacing


def test_recorder_is_bounded_and_writes_self_describing_csv(tmp_path, monkeypatch):
    times = iter(
        [
            1_000_000_000,  # construction origin
            1_001_000_000,  # iteration
            1_001_100_000,  # dispatch
            1_001_400_000,  # simulation
            1_001_800_000,  # prepare
        ]
    )
    monkeypatch.setattr(frame_pacing, "perf_counter_ns", lambda: next(times))
    path = tmp_path / "capture.csv"
    recorder = frame_pacing.FramePacingRecorder(
        path,
        target_fps=60,
        refresh_hz=120,
        cadence="1/2",
        presentation_path="Xlib/GLX -> Gamescope (outer backend: wayland)",
        capacity=1,
    )
    recorder.begin_iteration()
    recorder.after_dispatch()
    recorder.after_simulation()
    recorder.after_prepare()
    recorder.record_present(
        presented_ns=1_010_000_000,
        ticks=1,
        recomposed=True,
        swap_seconds=0.002,
        deadline_seconds=0.00025,
        pacing_mode="deadline",
        flip_health="unthrottled",
    )
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
        rows = list(csv.DictReader(stream))
    assert rows[0]["format"] == frame_pacing.FORMAT
    assert rows[0]["simulation_ms"] == "0.3"
    assert rows[0]["prepare_ms"] == "0.4"
    assert rows[0]["wait_ms"] == "6.2"
    assert rows[0]["swap_ms"] == "2.0"
    assert rows[0]["deadline_error_ms"] == "0.25"
    assert rows[0]["cadence"] == "1/2"
    assert rows[0]["presentation_path"] == (
        "Xlib/GLX -> Gamescope (outer backend: wayland)"
    )


def test_recorder_environment_is_opt_in(monkeypatch):
    monkeypatch.delenv(frame_pacing.ENV_PATH, raising=False)
    assert (
        frame_pacing.FramePacingRecorder.from_environment(
            target_fps=60,
            refresh_hz=120,
            cadence="1/2",
        )
        is None
    )


def test_durable_recorder_can_be_recovered_after_abrupt_exit(tmp_path, monkeypatch):
    times = iter(
        [
            1_000_000_000,
            1_001_000_000,
            1_001_100_000,
            1_001_200_000,
            1_001_300_000,
        ]
    )
    monkeypatch.setattr(frame_pacing, "perf_counter_ns", lambda: next(times))
    path = tmp_path / "capture.csv"
    recorder = frame_pacing.FramePacingRecorder(
        path,
        target_fps=60,
        refresh_hz=120,
        cadence="1/2",
        capacity=2,
        durable=True,
    )
    recorder.begin_iteration()
    recorder.after_dispatch()
    recorder.after_simulation()
    recorder.after_prepare()
    recorder.record_present(
        presented_ns=1_010_000_000,
        ticks=1,
        recomposed=True,
        swap_seconds=0.001,
        deadline_seconds=0.0001,
        pacing_mode="deadline",
        flip_health="unthrottled",
    )
    del recorder
    gc.collect()

    assert frame_pacing.FramePacingRecorder.recover(path) == (1, 0)
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1
    assert rows[0]["pacing_mode"] == "deadline"
    assert not list(tmp_path.glob("*.partial.*"))


def test_deadline_start_environment_flag_is_strict(monkeypatch):
    monkeypatch.setenv(frame_pacing.ENV_START_DEADLINE, "yes")
    assert frame_pacing.start_deadline_from_environment()

    monkeypatch.setenv(frame_pacing.ENV_START_DEADLINE, "perhaps")
    try:
        frame_pacing.start_deadline_from_environment()
    except ValueError as error:
        assert frame_pacing.ENV_START_DEADLINE in str(error)
    else:
        raise AssertionError("invalid diagnostic flag was accepted")
