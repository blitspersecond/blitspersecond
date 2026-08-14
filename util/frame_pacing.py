#!/usr/bin/env python3
"""Interpret BPS, MangoHud, PresentMon, and legacy BPS pacing captures."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from pathlib import Path
import sys

import numpy as np


@dataclass
class Series:
    name: str
    domain: str
    intervals_ms: np.ndarray


@dataclass
class Capture:
    path: Path
    kind: str
    series: list[Series]
    details: list[str] = field(default_factory=list)
    columns: dict[str, np.ndarray] = field(default_factory=dict)


def _numbers(rows: list[dict[str, str]], name: str) -> np.ndarray:
    values = []
    for row in rows:
        raw = row.get(name, "")
        if raw and raw.upper() != "NA":
            try:
                values.append(float(raw))
            except ValueError:
                pass
    return np.asarray(values, dtype=float)


def _read_csv_from_header(path: Path, required: str) -> list[dict[str, str]]:
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    header = next(
        (index for index, line in enumerate(lines) if required in line.split(",")),
        None,
    )
    if header is None:
        raise ValueError(f"could not find {required!r} header")
    return list(csv.DictReader(lines[header:]))


def load_bps_csv(path: Path) -> Capture:
    rows = _read_csv_from_header(path, "format")
    intervals = _numbers(rows, "present_interval_ms")
    columns = {
        name: _numbers(rows, name)
        for name in (
            "dispatch_ms",
            "simulation_ms",
            "prepare_ms",
            "wait_ms",
            "swap_ms",
            "deadline_error_ms",
        )
    }
    tick_times = np.asarray(
        [
            float(row["elapsed_ms"])
            for row in rows
            if row.get("ticks", "0") not in ("", "0")
        ],
        dtype=float,
    )
    if len(tick_times) > 1:
        columns["tick_gap_ms"] = np.diff(tick_times)
    modes: dict[str, int] = {}
    health: dict[str, int] = {}
    for row in rows:
        modes[row["pacing_mode"]] = modes.get(row["pacing_mode"], 0) + 1
        health[row["flip_health"]] = health.get(row["flip_health"], 0) + 1
    details = [
        f"pacing modes: {_counts(modes)}",
        f"flip health: {_counts(health)}",
    ]
    if rows:
        path = rows[0].get("presentation_path", "")
        if path:
            details.append(f"presentation path: {path}")
        details.append(
            "declared target/display/cadence: "
            f"{rows[0]['target_fps']} FPS / "
            f"{rows[0]['refresh_hz'] or 'unknown'} Hz / {rows[0]['cadence']}"
        )
    return Capture(
        path,
        "BPS internal CSV",
        [Series("submitted frames", "app completion", intervals)],
        details,
        columns,
    )


def load_presentmon(path: Path) -> Capture:
    rows = _read_csv_from_header(path, "Application")
    submitted = _numbers(rows, "MsBetweenPresents")
    displayed = _numbers(rows, "MsBetweenDisplayChange")
    undisplayed = sum(
        row.get("MsBetweenDisplayChange", "").upper() in ("", "NA") for row in rows
    )
    modes: dict[str, int] = {}
    for row in rows:
        mode = row.get("PresentMode", "") or "unknown"
        modes[mode] = modes.get(mode, 0) + 1
    details = [
        f"present events: {len(rows)}; without display change: {undisplayed}",
        f"present modes: {_counts(modes)}",
    ]
    columns = {
        name: _numbers(rows, name)
        for name in (
            "MsInPresentAPI",
            "MsRenderPresentLatency",
            "MsUntilDisplayed",
            "MsCPUBusy",
            "MsCPUWait",
            "MsGPULatency",
            "MsGPUTime",
            "MsGPUBusy",
            "MsGPUWait",
        )
    }
    return Capture(
        path,
        "PresentMon CSV",
        [
            Series("present submissions", "app/PresentMon", submitted),
            Series("display changes", "displayed", displayed),
        ],
        details,
        columns,
    )


def load_mangohud(path: Path) -> Capture:
    rows = _read_csv_from_header(path, "frametime")
    frametime = _numbers(rows, "frametime")
    columns = {
        "cpu_load_percent": _numbers(rows, "cpu_load"),
        "gpu_load_percent": _numbers(rows, "gpu_load"),
        "cpu_temp_c": _numbers(rows, "cpu_temp"),
        "gpu_temp_c": _numbers(rows, "gpu_temp"),
    }
    elapsed = _numbers(rows, "elapsed")
    details = []
    if len(elapsed) > 1:
        details.append(f"telemetry span: {(elapsed[-1] - elapsed[0]) / 1e9:.3f} s")
    return Capture(
        path,
        "MangoHud CSV",
        [Series("hooked frames", "external frame hook", frametime)],
        details,
        columns,
    )


def load_gamescope_stats(path: Path) -> Capture:
    """Load Gamescope's coarse -T FIFO protocol.

    Upstream emits one FPS value per 300 paint_all() calls. It is useful as an
    outer-compositor rate corroboration, but contains neither frame timestamps
    nor individual frame intervals.
    """
    values: list[float] = []
    discarded_origin_sample = False
    focus: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        name, separator, raw = line.partition("=")
        if not separator:
            continue
        if name == "fps":
            try:
                value = float(raw)
            except ValueError:
                pass
            else:
                if not discarded_origin_sample:
                    # Gamescope's first 300-paint sample divides by elapsed
                    # time from its zero-initialised lastSampledFrameTime, so
                    # it is effectively frames divided by system uptime.
                    discarded_origin_sample = True
                else:
                    values.append(value)
        elif name == "focus":
            focus[raw] = focus.get(raw, 0) + 1
    details = [
        "each FPS sample averages 300 Gamescope paint_all() calls; "
        "no per-frame timestamps",
        "discarded Gamescope's invalid first sample with zero time origin",
    ]
    if focus:
        details.append(f"focus reports: {_counts(focus)}")
    return Capture(
        path,
        "Gamescope stats FIFO",
        [],
        details,
        {"gamescope_paint_fps": np.asarray(values, dtype=float)},
    )


def load_legacy_npz(path: Path) -> Capture:
    with np.load(path, allow_pickle=False) as archive:
        frames = archive["frames"]
        names = frames.dtype.names or ()
        intervals = (
            frames["frame_interval_ns"].astype(float) / 1e6
            if "frame_interval_ns" in names
            else np.array([], dtype=float)
        )
        columns = {}
        for source, destination in (
            ("display_flip_ns", "prepare_through_present_ms"),
            ("surface_flip_ns", "swap_ms"),
            ("tick_work_ns", "tick_work_ms"),
            ("oml_query_ns", "oml_query_ms"),
        ):
            if source in names:
                columns[destination] = frames[source].astype(float) / 1e6
        if "msc" in names:
            msc = frames["msc"].astype(float)
            valid = msc > 0
            if valid.sum() > 1:
                columns["msc_delta"] = np.diff(msc[valid])
    return Capture(
        path,
        "legacy BPS NPZ",
        [Series("prepared frames", "app probe", intervals[intervals > 0])],
        ["legacy probe spans prepare through present; it includes any deadline wait"],
        columns,
    )


def load(path: str | Path) -> Capture:
    path = Path(path)
    if path.suffix.lower() == ".npz":
        return load_legacy_npz(path)
    if path.name == "gamescope-stats.log":
        return load_gamescope_stats(path)
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    preview = "\n".join(lines[:12])
    if "bps-frame-pacing-v1" in preview or (
        lines and "format" in lines[0].split(",")
    ):
        return load_bps_csv(path)
    if any("MsBetweenPresents" in line for line in lines[:12]):
        return load_presentmon(path)
    if any("frametime" in line.split(",") for line in lines[:12]):
        return load_mangohud(path)
    if any(line.startswith("fps=") for line in lines[:12]):
        return load_gamescope_stats(path)
    raise ValueError("unrecognised capture format")


def _counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{name or 'blank'}={count}" for name, count in counts.items())


def _trim(values: np.ndarray, start: float, end: float) -> np.ndarray:
    first = min(len(values), max(0, round(start)))
    last = max(first, len(values) - max(0, round(end)))
    return values[first:last]


def _stats(values: np.ndarray, *, unit: str = "", rate: bool = False) -> str:
    values = values[np.isfinite(values)]
    if not len(values):
        return "no samples"
    p50, p90, p99 = np.percentile(values, (50, 90, 99))
    suffix = f" {unit}" if unit else ""
    frequency = (
        f" ({1000 / values.mean():.3f} Hz)"
        if rate and values.mean() > 0
        else ""
    )
    span = f", span={values.sum() / 1000:.3f} s" if rate else ""
    return (
        f"n={len(values)}{span}, mean={values.mean():.3f}{suffix}{frequency}, "
        f"p50={p50:.3f}, p90={p90:.3f}, p99={p99:.3f}, "
        f"max={values.max():.3f}"
    )


def _slot_stats(values: np.ndarray, refresh: float) -> str:
    values = values[np.isfinite(values) & (values > 0)]
    if not len(values):
        return "no samples"
    period = 1000 / refresh
    slots = np.maximum(1, np.rint(values / period).astype(int))
    residual = values - slots * period
    unique, counts = np.unique(slots, return_counts=True)
    distribution = ", ".join(f"{slot}={count}" for slot, count in zip(unique, counts))
    return (
        f"{refresh:g} Hz slots [{distribution}]; residual "
        f"p50={np.percentile(np.abs(residual), 50):.3f} ms, "
        f"p99={np.percentile(np.abs(residual), 99):.3f} ms"
    )


def report(
    captures: list[Capture],
    *,
    refresh: float | None = None,
    trim_start: int = 0,
    trim_end: int = 0,
) -> str:
    output = []
    for capture in captures:
        output.append(f"{capture.path} — {capture.kind}")
        for detail in capture.details:
            output.append(f"  {detail}")
        for series in capture.series:
            values = _trim(series.intervals_ms, trim_start, trim_end)
            output.append(
                f"  {series.name} [{series.domain}]: "
                f"{_stats(values, unit='ms', rate=True)}"
            )
            if refresh is not None:
                output.append(f"    {_slot_stats(values, refresh)}")
        for name, values in capture.columns.items():
            finite = values[np.isfinite(values)]
            if len(finite):
                unit = (
                    "ms"
                    if name.endswith("_ms") or name.startswith("Ms")
                    else "%"
                    if name.endswith("_percent")
                    else "FPS"
                    if name.endswith("_fps")
                    else "C"
                    if name.endswith("_c")
                    else ""
                )
                output.append(f"  {name}: {_stats(finite, unit=unit)}")
        output.append("")
    return "\n".join(output).rstrip()


def write_normalized(captures: list[Capture], path: str | Path) -> None:
    """Write independent series in a common long format; rows are not aligned."""
    with Path(path).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "format",
                "source_file",
                "source_kind",
                "series",
                "domain",
                "sample",
                "interval_ms",
            ]
        )
        for capture in captures:
            for series in capture.series:
                for index, value in enumerate(series.intervals_ms):
                    if np.isfinite(value):
                        writer.writerow(
                            [
                                "bps-pacing-normalized-v1",
                                capture.path,
                                capture.kind,
                                series.name,
                                series.domain,
                                index,
                                value,
                            ]
                        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Interpret BPS, MangoHud, PresentMon, and legacy pacing captures."
    )
    parser.add_argument("captures", nargs="+", help="CSV or legacy internal NPZ")
    parser.add_argument(
        "--refresh",
        type=float,
        help="explicit display refresh for slot analysis",
    )
    parser.add_argument("--trim-start", type=int, default=0, metavar="SAMPLES")
    parser.add_argument("--trim-end", type=int, default=0, metavar="SAMPLES")
    parser.add_argument(
        "--normalized",
        metavar="CSV",
        help="write common long-form series",
    )
    args = parser.parse_args(argv)
    if args.refresh is not None and args.refresh <= 0:
        parser.error("--refresh must be positive")
    try:
        captures = [load(path) for path in args.captures]
    except (OSError, ValueError, KeyError) as error:
        print(f"frame_pacing: {error}", file=sys.stderr)
        return 2
    print(
        report(
            captures,
            refresh=args.refresh,
            trim_start=args.trim_start,
            trim_end=args.trim_end,
        )
    )
    if args.normalized:
        write_normalized(captures, args.normalized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
