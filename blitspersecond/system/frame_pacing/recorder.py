"""The optional frame-pacing capture: one row per submitted frame.

Off is the normal case. `BPS_FRAMEPACE_CSV` switches it on, and while it is
off the loop pays a predictable `None` check at each instrumentation point and
nothing else. Presentation reaches this through the `FramePacingSink` protocol
in `internal/pacing/sink.py`, so nothing under `internal/pacing/` imports a
recorder -- `internal/display.py` hands `from_environment` in as a factory.

**Preallocated, never grown.** A 150,000-row buffer is claimed up front --
about 40 minutes at 60fps -- so that recording a frame cannot allocate inside
the loop it is measuring. `BPS_FRAMEPACE_CAPACITY` moves the bound. Rows past
it are COUNTED and reported rather than growing the buffer, because a capture
that quietly reallocates has measured its own allocator.

`BPS_FRAMEPACE_DURABLE` adds a memory-mapped sidecar, which is what makes a
capture survive a compositor killing the process before it can shut down
cleanly: `util/capture_frame_pacing.py` recovers the completed rows into CSV
after the child exits. It adds no per-frame CSV formatting or append write to
the measured path.

`FORMAT` is written into the header, and every row repeats `target_fps`,
`refresh_hz`, `cadence` and the presentation path -- a capture is comparable
against another only if it says what produced it, and it must stay
self-describing once moved off that machine.

`util/frame_pacing.py` reads this and documents what each column means.
`bps/reference/engine/pacing.md` says what the numbers have to be.

**An append buffer, not a ring.** Capacity exhaustion is explicit because the
beginning of a run is the part worth keeping -- startup is where priming and
first-frame faults show -- and a ring would silently overwrite exactly that.
Each complete row is published in one assignment, then its recoverable count,
so a recovered sidecar never holds a half-written row.

**A background writer is deliberately not the default.** Measured over 100,000
rows: about 2.15us per frame in memory, 2.28us with the durable mmap -- roughly
0.027% of a 120Hz period. A worker would still leave timestamp capture on the
presentation thread while adding queue synchronisation, GIL wakeups, and a
crash-recovery gap for undrained events. The sink boundary leaves room for a
streaming backend later without `PresentationSession` knowing.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from time import perf_counter_ns

import numpy as np

from blitspersecond.common import environment_flag


FORMAT = "bps-frame-pacing-v1"
ENV_PATH = "BPS_FRAMEPACE_CSV"
ENV_CAPACITY = "BPS_FRAMEPACE_CAPACITY"
ENV_DURABLE = "BPS_FRAMEPACE_DURABLE"

_DTYPE = np.dtype(
    [
        ("frame", "u8"),
        ("elapsed_ns", "u8"),
        ("frame_interval_ns", "u8"),
        ("present_interval_ns", "u8"),
        ("ticks", "u4"),
        ("recomposed", "?"),
        ("dispatch_ns", "u8"),
        ("simulation_ns", "u8"),
        ("prepare_ns", "u8"),
        ("wait_ns", "u8"),
        ("swap_ns", "u8"),
        ("deadline_error_ns", "i8"),
        ("deadline_valid", "?"),
        ("pacing_mode", "U8"),
        ("flip_health", "U12"),
    ]
)


class FramePacingRecorder:
    """Record one bounded row per submitted frame."""

    def __init__(
        self,
        path: str | Path,
        *,
        target_fps: float,
        refresh_hz: float | None,
        cadence: str,
        presentation_path: str = "unknown",
        capacity: int = 150_000,
        durable: bool = False,
    ) -> None:
        if capacity < 1:
            raise ValueError("frame-pacing capacity must be positive")
        self.path = Path(path)
        self.target_fps = target_fps
        self.refresh_hz = refresh_hz
        self.cadence = cadence
        self.presentation_path = presentation_path
        self._durable = durable
        self._partial_data = Path(f"{self.path}.partial.npy")
        self._partial_count = Path(f"{self.path}.partial.count")
        self._partial_metadata = Path(f"{self.path}.partial.json")
        self._count_map: np.memmap | None = None
        if durable:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._partial_metadata.write_text(
                json.dumps(
                    {
                        "format": FORMAT,
                        "target_fps": target_fps,
                        "refresh_hz": refresh_hz,
                        "cadence": cadence,
                        "presentation_path": presentation_path,
                        "capacity": capacity,
                    }
                ),
                encoding="utf-8",
            )
            self._rows = np.lib.format.open_memmap(
                self._partial_data,
                mode="w+",
                dtype=_DTYPE,
                shape=(capacity,),
            )
            count_map = np.memmap(
                self._partial_count,
                mode="w+",
                dtype=np.uint64,
                shape=(2,),
            )
            count_map[:] = 0
            count_map.flush()
            self._count_map = count_map
        else:
            self._rows = np.zeros(capacity, dtype=_DTYPE)
        self._count = 0
        self._dropped = 0
        self._origin_ns = perf_counter_ns()
        self._iteration_ns = self._origin_ns
        self._dispatch_ns = self._origin_ns
        self._simulation_ns = self._origin_ns
        self._prepare_ns = self._origin_ns
        self._last_iteration_ns = 0
        self._last_present_ns = 0

    @classmethod
    def from_environment(
        cls,
        *,
        target_fps: float,
        refresh_hz: float | None,
        cadence: str,
        presentation_path: str = "unknown",
    ) -> FramePacingRecorder | None:
        path = os.environ.get(ENV_PATH)
        if not path:
            return None
        raw_capacity = os.environ.get(ENV_CAPACITY, "150000")
        try:
            capacity = int(raw_capacity)
        except ValueError as error:
            raise ValueError(f"{ENV_CAPACITY} must be an integer") from error
        durable = environment_flag(ENV_DURABLE)
        return cls(
            path,
            target_fps=target_fps,
            refresh_hz=refresh_hz,
            cadence=cadence,
            presentation_path=presentation_path,
            capacity=capacity,
            durable=durable,
        )

    def begin_iteration(self) -> None:
        self._iteration_ns = perf_counter_ns()

    def after_dispatch(self) -> None:
        self._dispatch_ns = perf_counter_ns()

    def after_simulation(self) -> None:
        self._simulation_ns = perf_counter_ns()

    def after_prepare(self) -> None:
        self._prepare_ns = perf_counter_ns()

    def record_present(
        self,
        *,
        presented_ns: int,
        ticks: int,
        recomposed: bool,
        swap_seconds: float,
        deadline_seconds: float | None,
        pacing_mode: str,
        flip_health: str,
    ) -> None:
        if self._count >= len(self._rows):
            self._dropped += 1
            if self._count_map is not None:
                self._count_map[1] = self._dropped
            self._last_iteration_ns = self._iteration_ns
            self._last_present_ns = presented_ns
            return

        swap_ns = round(swap_seconds * 1e9)
        deadline_valid = deadline_seconds is not None
        # Publish the structured row in one assignment. Besides being cheaper
        # than fifteen field-level NumPy scalar writes, this keeps the durable
        # invariant obvious: the count below advances only after a complete
        # row has been installed.
        self._rows[self._count] = (
            self._count,
            self._iteration_ns - self._origin_ns,
            (
                self._iteration_ns - self._last_iteration_ns
                if self._last_iteration_ns
                else 0
            ),
            (
                presented_ns - self._last_present_ns
                if self._last_present_ns
                else 0
            ),
            ticks,
            recomposed,
            self._dispatch_ns - self._iteration_ns,
            self._simulation_ns - self._dispatch_ns,
            self._prepare_ns - self._simulation_ns,
            max(0, presented_ns - self._prepare_ns - swap_ns),
            swap_ns,
            round(deadline_seconds * 1e9) if deadline_valid else 0,
            deadline_valid,
            pacing_mode,
            flip_health,
        )

        self._count += 1
        if self._count_map is not None:
            # Publish the count only after the row is complete. If Gamescope
            # kills the child, recovery never treats a half-written row as
            # valid.
            self._count_map[0] = self._count
        self._last_iteration_ns = self._iteration_ns
        self._last_present_ns = presented_ns

    @property
    def count(self) -> int:
        return self._count

    @property
    def dropped(self) -> int:
        return self._dropped

    def save(self) -> None:
        if isinstance(self._rows, np.memmap):
            self._rows.flush()
        if self._count_map is not None:
            self._count_map.flush()
        self._write_csv(
            self.path,
            self._rows,
            self._count,
            target_fps=self.target_fps,
            refresh_hz=self.refresh_hz,
            cadence=self.cadence,
            presentation_path=self.presentation_path,
        )
        self._discard_partial()

    @classmethod
    def recover(cls, path: str | Path) -> tuple[int, int] | None:
        """Recover a durable sidecar left by an abruptly terminated process."""
        path = Path(path)
        partial_data = Path(f"{path}.partial.npy")
        partial_count = Path(f"{path}.partial.count")
        partial_metadata = Path(f"{path}.partial.json")
        if not all(
            candidate.exists()
            for candidate in (partial_data, partial_count, partial_metadata)
        ):
            return None

        metadata = json.loads(partial_metadata.read_text(encoding="utf-8"))
        if metadata.get("format") != FORMAT:
            raise ValueError("durable frame-pacing sidecar has unknown format")
        counts = np.memmap(
            partial_count,
            mode="r",
            dtype=np.uint64,
            shape=(2,),
        )
        count, dropped = (int(value) for value in counts)
        rows = np.load(partial_data, mmap_mode="r", allow_pickle=False)
        if rows.dtype != _DTYPE:
            raise ValueError("durable frame-pacing sidecar has incompatible rows")
        if count > len(rows):
            raise ValueError("durable frame-pacing sidecar count exceeds capacity")

        cls._write_csv(
            path,
            rows,
            count,
            target_fps=float(metadata["target_fps"]),
            refresh_hz=metadata["refresh_hz"],
            cadence=str(metadata["cadence"]),
            presentation_path=str(
                metadata.get("presentation_path", "unknown")
            ),
        )
        del rows
        del counts
        for candidate in (partial_data, partial_count, partial_metadata):
            candidate.unlink()
        return count, dropped

    @staticmethod
    def _write_csv(
        path: Path,
        rows: np.ndarray,
        count: int,
        *,
        target_fps: float,
        refresh_hz: float | None,
        cadence: str,
        presentation_path: str,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fields = [
            "format",
            "frame",
            "elapsed_ms",
            "frame_interval_ms",
            "present_interval_ms",
            "ticks",
            "recomposed",
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
        ]
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(fields)
            for row in rows[:count]:
                def milliseconds(field: str, *, blank_zero: bool = False):
                    value = int(row[field])
                    return "" if blank_zero and value == 0 else value / 1e6

                writer.writerow(
                    [
                        FORMAT,
                        int(row["frame"]),
                        milliseconds("elapsed_ns"),
                        milliseconds("frame_interval_ns", blank_zero=True),
                        milliseconds("present_interval_ns", blank_zero=True),
                        int(row["ticks"]),
                        int(row["recomposed"]),
                        milliseconds("dispatch_ns"),
                        milliseconds("simulation_ns"),
                        milliseconds("prepare_ns"),
                        milliseconds("wait_ns"),
                        milliseconds("swap_ns"),
                        (
                            milliseconds("deadline_error_ns")
                            if row["deadline_valid"]
                            else ""
                        ),
                        row["pacing_mode"],
                        row["flip_health"],
                        target_fps,
                        "" if refresh_hz is None else refresh_hz,
                        cadence,
                        presentation_path,
                    ]
                )

    def _discard_partial(self) -> None:
        if not self._durable:
            return
        rows = self._rows
        counts = self._count_map
        self._rows = np.empty(0, dtype=_DTYPE)
        self._count_map = None
        if isinstance(rows, np.memmap):
            mapping = getattr(rows, "_mmap", None)
            if mapping is not None:
                mapping.close()
        if isinstance(counts, np.memmap):
            mapping = getattr(counts, "_mmap", None)
            if mapping is not None:
                mapping.close()
        for candidate in (
            self._partial_data,
            self._partial_count,
            self._partial_metadata,
        ):
            candidate.unlink(missing_ok=True)
