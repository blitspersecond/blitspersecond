#!/usr/bin/env python3
"""Run a BPS program with paired, recoverable frame-pacing capture."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
from threading import Thread
from time import strftime

from blitspersecond.system.monitor.frame_pacing import FramePacingRecorder


def _drain_fifo(fifo: Path, output: Path) -> None:
    with (
        fifo.open("r", encoding="utf-8", errors="replace") as source,
        output.open("w", encoding="utf-8") as destination,
    ):
        shutil.copyfileobj(source, destination)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" not in argv:
        command = []
        options = argv
    else:
        separator = argv.index("--")
        options = argv[:separator]
        command = argv[separator + 1 :]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "output",
        help="parent directory (a timestamped run directory is created inside)",
    )
    parser.add_argument(
        "--mangohud",
        action="store_true",
        help="wrap the command and log external frame/telemetry samples",
    )
    parser.add_argument(
        "--start-deadline",
        action="store_true",
        help="diagnostic: use BPS deadline pacing from the first frame",
    )
    parser.add_argument(
        "--gamescope-stats",
        action="store_true",
        help="add Gamescope -T output to a command beginning with gamescope",
    )
    args = parser.parse_args(options)
    if not command:
        parser.error("a command is required after --")

    run_dir = Path(args.output).expanduser() / strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True)
    environment = os.environ.copy()
    internal_path = (run_dir / "bps-frame-pacing.csv").resolve()
    environment["BPS_FRAMEPACE_CSV"] = str(internal_path)
    environment["BPS_FRAMEPACE_DURABLE"] = "1"
    if args.start_deadline:
        environment["BPS_FRAMEPACE_START_DEADLINE"] = "1"

    gamescope_fifo: Path | None = None
    gamescope_output: Path | None = None
    gamescope_reader: Thread | None = None
    if args.gamescope_stats:
        if Path(command[0]).name != "gamescope":
            parser.error("--gamescope-stats requires a command beginning with gamescope")
        gamescope_fifo = (run_dir / "gamescope-stats.pipe").resolve()
        gamescope_output = (run_dir / "gamescope-stats.log").resolve()
        os.mkfifo(gamescope_fifo)
        gamescope_reader = Thread(
            target=_drain_fifo,
            args=(gamescope_fifo, gamescope_output),
            name="gamescope-stats-reader",
            daemon=True,
        )
        gamescope_reader.start()
        command = [command[0], "-T", str(gamescope_fifo), *command[1:]]

    if args.mangohud:
        executable = shutil.which("mangohud")
        if executable is None:
            parser.error("--mangohud requested, but mangohud is not on PATH")
        # Do not add no_display: MangoHud 0.8.4 does not reach its autostart
        # check until the overlay/logging update path is active.
        environment["MANGOHUD_CONFIG"] = ",".join(
            [
                "autostart_log=1",
                "log_interval=0",
                f"output_folder={run_dir.resolve()}",
                "log_versioning",
            ]
        )
        command = [executable, *command]

    print(f"Capture directory: {run_dir}")
    print(f"Command: {shlex.join(command)}")
    try:
        completed = subprocess.run(command, env=environment, check=False)
        returncode = completed.returncode
    except KeyboardInterrupt:
        returncode = 130

    if gamescope_reader is not None:
        gamescope_reader.join(timeout=2)
    if gamescope_fifo is not None:
        gamescope_fifo.unlink(missing_ok=True)

    if not internal_path.exists():
        recovered = FramePacingRecorder.recover(internal_path)
        if recovered is not None:
            count, dropped = recovered
            suffix = f", {dropped} dropped" if dropped else ""
            print(
                f"Recovered interrupted BPS capture: {internal_path} "
                f"({count} rows{suffix})"
            )

    captures = [
        path
        for path in sorted(run_dir.iterdir())
        if path.suffix.lower() in (".csv", ".log", ".npz")
        and not path.stem.endswith("_summary")
    ]
    if captures:
        arguments = " ".join(shlex.quote(str(path)) for path in captures)
        print(f"Interpret with: {sys.executable} util/frame_pacing.py {arguments}")
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
