import csv

import numpy as np

from util import frame_pacing


def test_loads_bps_csv_and_keeps_submission_domain_explicit(tmp_path):
    path = tmp_path / "bps.csv"
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "format",
                "frame",
                "elapsed_ms",
                "present_interval_ms",
                "ticks",
                "dispatch_ms",
                "simulation_ms",
                "prepare_ms",
                "wait_ms",
                "swap_ms",
                "deadline_error_ms",
                "pacing_mode",
                "flip_health",
                "target_fps",
                "refresh_hz",
                "cadence",
                "presentation_path",
            ],
        )
        writer.writeheader()
        for frame, elapsed, interval, ticks in (
            (0, 0, "", 1),
            (1, 8.3, 8.3, 0),
            (2, 16.7, 8.4, 1),
        ):
            writer.writerow(
                {
                    "format": "bps-frame-pacing-v1",
                    "frame": frame,
                    "elapsed_ms": elapsed,
                    "present_interval_ms": interval,
                    "ticks": ticks,
                    "dispatch_ms": 0.1,
                    "simulation_ms": 0.2,
                    "prepare_ms": 0.3,
                    "wait_ms": 7,
                    "swap_ms": 0.1,
                    "pacing_mode": "deadline",
                    "flip_health": "unthrottled",
                    "target_fps": 60,
                    "refresh_hz": 120,
                    "cadence": "1/2",
                    "presentation_path": (
                        "Xlib/GLX -> Gamescope (outer backend: wayland)"
                    ),
                }
            )

    capture = frame_pacing.load(path)
    assert capture.kind == "BPS internal CSV"
    assert capture.series[0].domain == "app completion"
    np.testing.assert_allclose(capture.series[0].intervals_ms, [8.3, 8.4])
    np.testing.assert_allclose(capture.columns["tick_gap_ms"], [16.7])
    assert "presentation path: Xlib/GLX -> Gamescope" in capture.details[2]


def test_loads_versioned_mangohud_log_after_metadata(tmp_path):
    path = tmp_path / "mangohud.csv"
    path.write_text(
        "v1\n0.8.4\n---\nos,cpu,gpu\nLinux,cpu,gpu\n---\n"
        "fps,frametime,cpu_load,gpu_load,elapsed\n"
        "120,8.2,5,7,1000000\n119,8.5,6,8,9500000\n",
        encoding="utf-8",
    )
    capture = frame_pacing.load(path)
    assert capture.kind == "MangoHud CSV"
    assert capture.series[0].domain == "external frame hook"
    np.testing.assert_allclose(capture.series[0].intervals_ms, [8.2, 8.5])


def test_loads_gamescope_coarse_stats_without_calling_them_frame_times(tmp_path):
    path = tmp_path / "gamescope.log"
    path.write_text(
        "fps=0.01\nfocus=0\nfps=119.80\nfocus=0\n"
        "fps=120.10\nfocus=0\n",
        encoding="utf-8",
    )

    capture = frame_pacing.load(path)

    assert capture.kind == "Gamescope stats FIFO"
    assert capture.series == []
    np.testing.assert_allclose(
        capture.columns["gamescope_paint_fps"],
        [119.8, 120.1],
    )
    assert "300 Gamescope paint_all() calls" in capture.details[0]


def test_presentmon_separates_submissions_from_display_changes(tmp_path):
    path = tmp_path / "presentmon.csv"
    path.write_text(
        "Application,MsBetweenPresents,MsBetweenDisplayChange,PresentMode\n"
        "game.exe,4.0,NA,Hardware: Independent Flip\n"
        "game.exe,4.2,8.2,Hardware: Independent Flip\n",
        encoding="utf-8",
    )
    capture = frame_pacing.load(path)
    assert [series.domain for series in capture.series] == [
        "app/PresentMon",
        "displayed",
    ]
    np.testing.assert_allclose(capture.series[0].intervals_ms, [4.0, 4.2])
    np.testing.assert_allclose(capture.series[1].intervals_ms, [8.2])
    assert "without display change: 1" in capture.details[0]


def test_slot_report_classifies_refresh_intervals_without_stutter_score():
    capture = frame_pacing.Capture(
        path=frame_pacing.Path("synthetic.csv"),
        kind="synthetic",
        series=[
            frame_pacing.Series(
                "display changes",
                "displayed",
                np.asarray([8.33, 8.35, 16.66]),
            )
        ],
    )
    output = frame_pacing.report([capture], refresh=120)
    assert "120 Hz slots [1=2, 2=1]" in output
    assert "residual p50=" in output


def test_loads_legacy_bps_npz_without_archived_capture_assets(tmp_path):
    path = tmp_path / "legacy.npz"
    frames = np.zeros(
        3,
        dtype=[
            ("frame_interval_ns", "<u8"),
            ("surface_flip_ns", "<u8"),
            ("msc", "<u8"),
        ],
    )
    frames["frame_interval_ns"] = [0, 8_300_000, 8_400_000]
    frames["surface_flip_ns"] = [100_000, 110_000, 120_000]
    frames["msc"] = [10, 11, 12]
    np.savez(path, frames=frames)

    capture = frame_pacing.load(path)

    assert capture.kind == "legacy BPS NPZ"
    np.testing.assert_allclose(capture.series[0].intervals_ms, [8.3, 8.4])
    np.testing.assert_allclose(capture.columns["swap_ms"], [0.1, 0.11, 0.12])
    np.testing.assert_allclose(capture.columns["msc_delta"], [1, 1])
