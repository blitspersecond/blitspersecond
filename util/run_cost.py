#!/usr/bin/env python3
"""What a material change actually costs at the draw, priced separately.

    util/run_cost.py
    util/run_cost.py --count 100000 --by palette

`_emit_chain` cuts a run wherever the material changes, and a material is
`(buffer, palette, colour registers)`. Those three are usually spoken of as
one thing, and they are not one thing:

| part of a material | what the draw does with it | where it lives |
|---|---|---|
| buffer | `glBindTexture` on unit 0 | a texture |
| palette | `glBindTexture` on unit 1, **and unit 0 again** | a texture |
| colour registers | `glVertexAttrib4f` | a generic vertex attribute |

So "one material" is not "one texture unit". It is two units and a constant,
and the atlas half of it is already fixed for a whole chain by construction --
instanced presentations are pinned to the sheet's `primary_tileset`, so the
buffer can never cut a run there. Only the other two can, and `draw_run`
treats them very differently: a palette change rebinds unit 1 AND forces unit
0 to rebind after it, because uploading palette RAM may disturb whatever unit
was active. A colour change sets one generic attribute.

This prices the two against each other by holding the population fixed and
splitting it into K runs -- once by colour, once by palette register -- so the
staging is near enough constant and the difference is the draw.

**Why it matters.** Particle mode gives up per-presentation colour AND
per-presentation palette in one move, because both cut runs. If a colour cut
is nearly free at the draw, that is one restriction too many: a particle chain
could keep its fades by emitting K runs and pin only the palette.

Fumiko rather than the bullets, because the bullet sheet carries a single
palette and there would be nothing to switch between.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from time import perf_counter

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SHEET = ROOT / "assets/fumiko/fumiko.sprite.json"
MARKER = "RUNCOST_JSON "

#: Short next to `sprite_sweep.py`'s 1024. This measures the DIFFERENCE
#: between conditions rather than a ceiling, so it wants many conditions more
#: than it wants a long window on each.
WARMUP = 90
TICKS = 420

DEFAULT_RUNS = (1, 2, 4, 16, 64, 256, 1024, 4096, 16_384)
SCREEN = (640, 360)


def _blocks(count: int, runs: int) -> np.ndarray:
    """Which of `runs` blocks each presentation falls in, contiguously.

    Contiguous because a run is a maximal stretch of ADJACENT quads sharing a
    material -- scattering the same number of distinct materials would produce
    a different (and much larger) run count from the same input, which would
    measure the scatter rather than the switch.
    """
    return (np.arange(count) * runs // max(count, 1)).astype(np.int32)


def run_one(count: int, runs: int, by: str) -> int:
    from blitspersecond import BlitsPerSecond
    from blitspersecond.graphics.sprite import Sprite, SpriteSheet

    bps = BlitsPerSecond()
    sprite = Sprite(SpriteSheet(str(SHEET)), tag="runcost")
    bps.layers.add(sprite)
    sprite.assembly = None

    swarm = sprite.after.extend(count)
    rng = np.random.default_rng(0x9E77)
    block = _blocks(count, runs)
    with swarm.edit():
        # A named pose, because this sheet came from a package and index
        # addressing exists only for a sheet read as a grid.
        swarm.assemblies[:] = sprite.assembly_id("idle-0")
        swarm.positions[:, 0] = rng.integers(0, SCREEN[0], count)
        swarm.positions[:, 1] = rng.integers(0, SCREEN[1], count)
        swarm.visible[:] = True
        if by == "color":
            # Alpha only, so every run still draws and the fill stays equal
            # across conditions. A run that draws nothing would flatter the
            # high-K end by removing fragments rather than binds.
            swarm.color[:, 3] = 0.4 + 0.6 * (block % 7) / 6.0
        else:
            palettes = len(sprite.sheet.tilesets[sprite.sheet.primary_tileset].palettes)
            swarm.palettes[:] = block % palettes

    state = {"tick": 0, "started": perf_counter()}

    def loop() -> None:
        # Nothing moves. This is a draw measurement, and a simulation would
        # only add noise to it -- the plan is rebuilt anyway on the first
        # frame and then stays clean, which is exactly what isolates the draw.
        state["tick"] += 1
        if state["tick"] == WARMUP:
            state["started"] = perf_counter()
        if state["tick"] < TICKS:
            return
        report = bps.metrics.report()
        summary = report.summarise("display.compose.ms")
        wall = perf_counter() - state["started"]
        print(
            MARKER
            + json.dumps(
                {
                    "count": count,
                    "runs": runs,
                    "by": by,
                    "emitted": len(sprite._plan.runs),
                    "compose": summary.mean if summary else None,
                    "p99": summary.percentiles[2] if summary else None,
                    "pace": ((state["tick"] - WARMUP) / 60.0) / wall,
                }
            ),
            flush=True,
        )
        bps.stop()

    bps.start(loop)
    return 0


def _worker(count: int, runs: int, by: str) -> dict | None:
    command = [
        sys.executable, str(Path(__file__).resolve()),
        "--run", str(count), "--runs", str(runs), "--by", by,
    ]
    done = subprocess.run(command, capture_output=True, text=True,
                          env=os.environ.copy())
    for line in done.stdout.splitlines():
        if line.startswith(MARKER):
            return json.loads(line[len(MARKER):])
    sys.stderr.write(
        f"\n  no result for {runs} runs (exit {done.returncode})\n"
        + "".join(f"    {x}\n" for x in done.stderr.splitlines()[-6:])
    )
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=int, help="worker: one condition")
    parser.add_argument("--count", type=int, default=100_000)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--by", choices=("color", "palette"), default="color")
    parser.add_argument("--only", choices=("color", "palette"))
    args = parser.parse_args()

    if args.run:
        return run_one(args.run, args.runs, args.by)

    modes = [args.only] if args.only else ["color", "palette"]
    for by in modes:
        thing = "colour register" if by == "color" else "palette register"
        print(f"\n{args.count:,} presentations, cut into runs by {thing}\n")
        print(f"  {'runs':>7}  {'emitted':>8}  {'compose':>9}  {'p99':>9}"
              f"  {'vs 1 run':>9}  {'us per extra run':>17}")
        print("  " + "-" * 70)
        base = None
        for runs in DEFAULT_RUNS:
            row = _worker(args.count, runs, by)
            if row is None:
                continue
            if base is None:
                base = row["compose"]
            delta = row["compose"] - base
            each = delta * 1000.0 / max(row["emitted"] - 1, 1)
            print(
                f"  {runs:>7,}  {row['emitted']:>8,}  {row['compose']:8.2f}m"
                f"  {row['p99']:8.2f}m  {delta:+8.2f}m"
                + (f"  {each:>16.2f}" if row["emitted"] > 1 else f"  {'--':>16}")
            )
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
