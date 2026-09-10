#!/usr/bin/env python3
"""Run a BPS program with paired, recoverable frame-pacing capture.

The runbook. `frame_pacing.py` reads what this writes;
`bps/reference/engine/pacing.md` says what the numbers have to be, and which
panels and scenarios are worth exercising.

## Linux

`util/linux.sh` resolves the repository root, uses the repository virtualenv,
puts that root first on `PYTHONPATH`, and picks up Gamescope when it is
available -- so prefer it over selecting an interpreter and a presentation
wrapper by hand:

    util/linux.sh ../experiments/tile_test.py

Windowed is the default and deliberately omits Gamescope's `-b`. Nested
Gamescope is the production Linux path; direct XWayland is the ordinary
desktop one. Native Xorg is not a target -- path detection will describe it,
which is not a support promise.

`BPS_GAMESCOPE_BACKEND` records the outer backend, because a client inside
Gamescope cannot discover it and a capture is comparable only against another
that says what produced it.

## What a capture can and cannot prove

Four timelines, and they are NOT interchangeable:

    simulation      the fixed-rate game clock
    submission      when the app completed present()
    display change  when new content actually reached a display
    telemetry       MangoHud's sampled CPU/GPU load, clocks, power, temps

**Only PresentMon's `MsBetweenDisplayChange` observes the third**, and it is
Windows-only. `MsBetweenPresents` describes app submissions and will simply
agree with BPS's own numbers, which is not corroboration. On Linux nothing
available observes what the compositor did with a submission: MangoHud hooks
the same swaps BPS already times, and X Present feedback is a split result --
direct Mutter XWayland returned 123 completion events with two distinct
UST/MSC pairs in 1.035s and cannot grade delivery, while Gamescope's private
XWayland returned 176 distinct pairs for 176 events in 2.929s and may grade
inner delivery only.

So a Linux capture describes BPS's submission timeline. Read it as that.

## Windows pairing

Run PresentMon alongside, and pair on wall time rather than frame index -- the
two disagree about what a frame is. Remember that BPS's second is sixty ticks
and the wall clock's is not; they differ by the pace figure, which is the point
of that figure.

Worth having Windows for specifically: confirming frames land on distinct
vblanks in the expected pattern, and grading the phase detector, whose evidence
and correction are otherwise computed from the same submission timeline and so
cannot be told apart from a measurement artefact.

## Stopping a run

The metrics report prints on shutdown, so a capture wants a bounded run:
`bps.stop()` from inside a tick is how to end one after a fixed number of
frames.

Taking one:

    venv/bin/python util/capture_frame_pacing.py captures -- \
        venv/bin/python ../experiments/tile_test.py

That sets `BPS_FRAMEPACE_CSV`, creates a timestamped run directory holding
`bps-frame-pacing.csv`, and enables the durable sidecar -- so a compositor
killing BPS before clean shutdown still yields the completed rows. See
`blitspersecond/system/frame_pacing/recorder.py` for the format and its bounds.

Without the wrapper, `BPS_FRAMEPACE_CSV=/tmp/bps.csv` on the command gives an
in-memory capture written after the loop exits; `BPS_FRAMEPACE_DURABLE=1` adds
the sidecars, but nothing recovers them automatically.

## MangoHud

`--mangohud` requests one sample per frame and versioned logs, and
**deliberately leaves the overlay visible** -- MangoHud 0.8.4's `no_display`
path does not reliably trigger `autostart_log`. The overlay perturbs pacing, so
compare an internal-only control run against the paired run before attributing
any change to the game.

## Gamescope

    venv/bin/python util/capture_frame_pacing.py captures \
        --gamescope-stats -- \
        gamescope --backend wayland -w 2560 -h 1440 -W 2560 -H 1440 -r 120 -- \
            env BPS_GAMESCOPE_BACKEND=wayland \
            venv/bin/python ../experiments/tile_test.py

`--gamescope-stats` creates and drains the FIFO Gamescope's `-T` expects.
Despite that option's "write statistics to path" help text it does not create a
normal file. The stream carries one FPS average per 300 `paint_all()` calls and
the focused X11 application id -- no individual frame timestamps -- so it
corroborates Gamescope's coarse outer repaint rate and cannot locate a single
judder. Nested Gamescope's outer surface still passes through the desktop
compositor, so physical smoothness stays a human observation.

The sibling testsuite's `tools/xpresent_timing.c` observes Present
`CompleteNotify` on pyglet's GLX drawable read-only, issuing no Present request
and feeding nothing back. Path-specific instrumentation, not a Linux
PresentMon and not a pacing clock.
"""

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

from blitspersecond.system.frame_pacing import FramePacingRecorder


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
