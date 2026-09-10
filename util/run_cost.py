#!/usr/bin/env python3
"""What a run cut costs at the draw, priced on its own.

    util/run_cost.py
    util/run_cost.py --count 100000

`_emit_chain` cuts a run wherever the material changes, and a material is
`(buffer, palette bank, palette row)`. Those are usually spoken of as one
thing, and they are not one thing:

| part of a material | what the draw does with it | where it lives |
|---|---|---|
| buffer | `glBindTexture` on unit 0 | a texture |
| palette bank | `glBindTexture` on unit 1, **and unit 0 again** | a texture |
| palette row | `glUniform1f` | a uniform |

So "one material" is not "one texture unit" -- and in a chain, the two
texture halves are fixed by construction: instanced presentations are pinned
to the sheet's `primary_tileset`, so the buffer cannot cut a run there, and
since 2026-09-02 a tileset's bank is ONE 256x256 texture, so the bank cannot
either. **The palette row is the only thing left that can cut one**, and this
prices it by holding the population fixed and splitting it into K runs, so
the staging is near enough constant and the difference is the draw.

**There was a second arm, and the answer removed it.** This compared a
palette cut against a COLOUR cut, because both were generic vertex attributes
and a particle chain that had to give up per-presentation colour to stay in
one run was giving up a fade. At 100,000 presentations in 4,096 runs a
palette cut cost 8.3us against a colour cut's 6.4us, and the gap was not a
bind: a palette-cut run wrote the row and the colour, a colour-cut run wrote
only the colour, because `draw_run` tracks the row. Then the per-presentation
colour registers went entirely on 2026-09-09 -- recolouring is a palette job,
see `Presentation.red` -- so there is no colour in a material to cut on, and
the arm measured a thing that cannot happen. The figures survive in
`draw_run`, which is what they justify.

**Read this as a stress case and not a frame.** Cutting 4,096 palette runs
out of one population is this harness manufacturing switches to price them.
The register is not an effect that varies per presentation the way a fade
was -- it answers whether Chun-Li is in her 1P blue costume or her 2P red
one, which is one answer for the whole of her. A swarm has ONE.

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


def run_one(count: int, runs: int) -> int:
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
        rows = len(sprite.sheet.tilesets[sprite.sheet.primary_tileset].palette)
        swarm.palette_index[:] = block % rows

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


def _worker(count: int, runs: int) -> dict | None:
    command = [
        sys.executable, str(Path(__file__).resolve()),
        "--run", str(count), "--runs", str(runs),
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
    args = parser.parse_args()

    if args.run:
        return run_one(args.run, args.runs)

    print(f"\n{args.count:,} presentations, cut into runs by palette register\n")
    print(f"  {'runs':>7}  {'emitted':>8}  {'compose':>9}  {'p99':>9}"
          f"  {'vs 1 run':>9}  {'us per extra run':>17}")
    print("  " + "-" * 70)
    base = None
    for runs in DEFAULT_RUNS:
        row = _worker(args.count, runs)
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
