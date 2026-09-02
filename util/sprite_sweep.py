#!/usr/bin/env python3
"""Find where the sprite pillar's per-frame cost stops being flat.

    util/sprite_sweep.py
    util/sprite_sweep.py --counts 10000,25000,50000 --ticks 900
    util/sprite_sweep.py --run 25000 --ticks 600     # one worker, JSON out

`examples/fountain.py` proves the pillar draws 25,000 presentations. It does
not say whether 25,000 is a limit, a comfortable cruise, or a number chosen
because the prototype it came from was doing collision as well. This answers
that: the same workload at a sweep of populations, with the engine's own
metrics read at each, so the target comes off a curve rather than a guess.

## Both engines, same workload

`--engine reference` runs the identical sweep against the pre-rework tree at
`bps/blitspersecond.reference`, so "is the rebuild faster" is a measurement
rather than a recollection. It is a fair comparison because the parts that
would make it unfair are held level: the bullet art is byte-identical, the two
sprite packages are the same document, both engines compose into a 640x360
framebuffer, and both quote a compose figure that spans every layer's draw and
excludes the present wait.

**Collision is off on the reference**, which is the whole point of the
exercise: its pools stamp collision planes by default and the rebuilt pillar
has no collision at all yet, so leaving it on would bill the old engine for
work the new one has not been asked to do. `--collide` puts it back, and the
difference between the two runs is what that stamping costs.

The simulation is written the way each engine wants it -- the reference takes
float positions, the rebuild takes whole pixels and floors on the way in -- so
`sim` is not identical between them. It is reported separately and is a
fraction of a millisecond either way.

## Why a subprocess per population

`BlitsPerSecond` and `Metrics` are both singletons and the loop leaves the
process when it stops -- `Loop.run` does not return. So one population per
process is not caution, it is the only shape available. It also isolates
driver and GL state, which a sweep in one process would carry from the small
runs into the large ones and quietly flatter them.

## What is actually being measured

**Every presentation is alive and drawn on every frame.** The fountain
example's population is set by its spawn rate, not by its slot count -- at
`PER_TICK=190` and a two-second life it settles near 23,000 whatever the
array is sized to, so sweeping `COUNT` there would sweep the allocation and
leave the workload flat. Here the spawn refills every dead slot each tick, so
`alive == count` from the first frame, and the population is seeded with a
random age so there is no synchronised wave of deaths to ride out.

The three numbers are separated on purpose:

- **sim** is this file's own NumPy, and it is NOT the engine's cost. It is
  here because it shares the frame budget, so a target that ignores it is a
  target for an engine with no game attached.
- **compose** is the engine drawing, which at this scale is the staging of
  N quads and the upload of them. This is the number the sweep is for.
- **pace** is the verdict: the loop is locked to the display, so it reads
  ~100% until the frame's work stops fitting in the period, and then falls.

Under a display lock `frame` sits on the authored period until saturation, so
it confirms the knee rather than finding it -- compose moves first.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from time import perf_counter

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SHEET = ROOT / "assets/bullets/bullets.sprite.json"

#: The line prefix a worker's result is emitted behind. The engine logs to
#: stdout too, so the driver needs something to pick out that is not a log
#: line and not English.
MARKER = "SWEEP_JSON "

DEFAULT_COUNTS = (10_000, 25_000, 50_000, 100_000, 150_000, 200_000, 250_000)

#: The engine's per-frame metric ring: a report covers the last this-many
#: frames and there is no way to ask it for a narrower span (`rebase()`
#: discards the OPEN frame only). Imported rather than written as 1024 so a
#: change to it cannot leave this quoting a window that no longer exists.
try:
    from blitspersecond.system.metrics.stream import FRAMES
except ImportError:  # the engine is not importable from a driver-only run
    FRAMES = 1024

#: Frames run before the measured span, chosen so warm-up falls off the back
#: of the ring rather than into the average. The first frames of any run pay
#: for shader compilation, the first texture upload and the first buffer
#: sizing -- real costs, but not per-frame ones, and at 250,000 the first
#: compose is an order of magnitude above the steady state.
WARMUP = 120
DEFAULT_TICKS = FRAMES + WARMUP

#: 60fps. The whole frame -- sim, compose, present -- has to fit in this.
BUDGET_MS = 1000.0 / 60.0

GRAVITY = 0.12
SPREAD = 2.2
LIFT = (-7.6, -5.6)
#: Ticks from launch to falling off the bottom, near enough. Only used to
#: spread the initial population's age, so it wants to be about right rather
#: than exact: 2*|vy|/g to come back down, plus the run out of frame.
LIFETIME = 115


#: The 640x360 internal screen both engines compose into.
SCREEN = (640, 360)


def bind_reference() -> None:
    """Make `blitspersecond` mean the pre-rework tree, for this process.

    The same binding `experiments/sprite_purist.py` uses, and for the same
    reason: the reference imports itself absolutely, so it only works under
    that exact name, and `blitspersecond.reference` is a directory name no
    import statement can spell. Registered before it executes, because its
    submodules import `blitspersecond.*` while it is still initialising.
    """
    reference = ROOT / "blitspersecond.reference"
    if not reference.is_dir():
        raise SystemExit(f"no reference engine at {reference}")
    # Evict the rebuilt package first. This module reads `FRAMES` off it at
    # import, which pulls `blitspersecond.system.*` into sys.modules; rebinding
    # only the top-level name would leave those submodules pointing at the new
    # tree, and the reference would half-import against them -- which surfaces
    # as `cannot import name ... (unknown location)` rather than as anything
    # that names the real problem. FRAMES is already an int by now.
    for name in [
        name
        for name in sys.modules
        if name == "blitspersecond" or name.startswith("blitspersecond.")
    ]:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        "blitspersecond",
        reference / "__init__.py",
        submodule_search_locations=[str(reference)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["blitspersecond"] = module
    spec.loader.exec_module(module)


class ReferenceSwarm:
    """The same fountain, on the pre-rework engine.

    Deliberately a separate class rather than a branch inside `Swarm`. The two
    engines do not share an API -- `layers.sprite(...).z(0).pool(...)` against
    `Sprite(...); sprite.after.extend(...)` -- and threading both through one
    class would bury the comparison in `if` statements. The DYNAMICS are the
    thing that has to match, and those are duplicated in full below where they
    can be read against the other class line by line.
    """

    def __init__(self, bps, count: int, offscreen: bool = False,
                 collide: bool = False) -> None:
        from blitspersecond.graphics import SpriteSheet

        self.count = count
        self._offset = 2000 if offscreen else 0
        sheet = SpriteSheet.load(str(SHEET))
        self.layer = bps.layers.sprite(sheet, tag="sweep")
        self.pool = self.layer.z(0).pool(
            "blue" if index & 1 else "pink" for index in range(count)
        )
        self.positions = self.pool.positions
        self.alive = self.pool.visible
        # Off unless asked for. A reference pool stamps collision planes by
        # default and the rebuilt pillar has none, so leaving this on would
        # charge the old engine for work the new one is not doing.
        self.pool.collide[:] = collide

        self.fx = np.zeros(count, dtype=np.float32)
        self.fy = np.zeros(count, dtype=np.float32)
        self.vx = np.zeros(count, dtype=np.float32)
        self.vy = np.zeros(count, dtype=np.float32)
        self.rng = np.random.default_rng(0xB011E7)

        self.origin = (SCREEN[0] // 2, SCREEN[1] - 24)
        self.bounds = (-8.0, float(SCREEN[0] + 8), -8.0, float(SCREEN[1] + 8))
        self.sim_ms = 0.0
        self._seed()

    def _seed(self) -> None:
        count = self.count
        self.fx[:] = self.origin[0]
        self.fy[:] = self.origin[1]
        self.vx[:] = self.rng.uniform(-SPREAD, SPREAD, count)
        self.vy[:] = self.rng.uniform(*LIFT, count)
        age = self.rng.uniform(0.0, LIFETIME, count).astype(np.float32)
        self.fx += self.vx * age
        self.fy += self.vy * age + 0.5 * GRAVITY * age * age
        self.vy += GRAVITY * age
        self.alive[:] = True
        with self.pool.edit():
            self.positions[:, 0] = self.fx
            self.positions[:, 1] = self.fy + self._offset

    def tick(self) -> None:
        started = perf_counter()
        self._step()
        self.sim_ms = (perf_counter() - started) * 1000.0

    def _step(self) -> None:
        with self.pool.edit():
            self._spawn()
            live = self.alive
            self.vy[live] += GRAVITY
            self.fx[live] += self.vx[live]
            self.fy[live] += self.vy[live]

            left, right, top, bottom = self.bounds
            gone = live & (
                (self.fx < left)
                | (self.fx > right)
                | (self.fy < top)
                | (self.fy > bottom)
            )
            self.alive[gone] = False
            # No floor: the reference takes float positions. That difference
            # is the rule the rebuild introduced, not an unfairness -- each
            # engine is being driven the way it asks to be.
            self.positions[:, 0] = self.fx
            self.positions[:, 1] = self.fy + self._offset

    def _spawn(self) -> None:
        free = np.flatnonzero(~self.alive)
        count = free.size
        if not count:
            return
        self.fx[free] = self.origin[0]
        self.fy[free] = self.origin[1]
        self.vx[free] = self.rng.uniform(-SPREAD, SPREAD, count)
        self.vy[free] = self.rng.uniform(*LIFT, count)
        self.alive[free] = True


class Swarm:
    """The fountain, held at a fixed population.

    A trimmed `examples/fountain.py`: same sheet, same dynamics, same integer
    positions floored on the way in. What differs is the spawn -- it refills
    every dead slot rather than a fixed number -- because the sweep needs the
    drawn population to BE the count it was asked for.
    """

    def __init__(self, bps, count: int, offscreen: bool = False) -> None:
        from blitspersecond.graphics.sprite import Sprite, SpriteSheet

        self.count = count
        # Parks the whole population outside the viewport. Every CPU cost is
        # unchanged -- same arrays, same floor, same writes, same staging and
        # the same upload, because the frame plan filters on `visible` and a
        # valid id and culls nothing by geometry. Only the rasteriser's work
        # changes. Running the sweep both ways is what separates "the cost is
        # building and uploading the quads" from "the cost is filling them".
        self._offset = 2000 if offscreen else 0
        self.sprite = Sprite(SpriteSheet(str(SHEET)), tag="sweep")
        bps.layers.add(self.sprite)
        self.sprite.assembly = None

        self.swarm = self.sprite.after.extend(count)
        self.swarm.assemblies[0::2] = self.sprite.assembly_id("blue")
        self.swarm.assemblies[1::2] = self.sprite.assembly_id("pink")
        self.alive = self.swarm.visible

        self.fx = np.zeros(count, dtype=np.float32)
        self.fy = np.zeros(count, dtype=np.float32)
        self.vx = np.zeros(count, dtype=np.float32)
        self.vy = np.zeros(count, dtype=np.float32)
        self.rng = np.random.default_rng(0xB011E7)

        self.origin = (bps.width // 2, bps.height - 24)
        self.bounds = (-8.0, float(bps.width + 8), -8.0, float(bps.height + 8))
        self.sim_ms = 0.0
        self._seed()

    def _seed(self) -> None:
        """Fill every slot, each at a random point along its own arc.

        Spawning them all on frame one instead would put the whole population
        on the same trajectory and kill it in the same tick, so the run would
        measure a pulsing population rather than a held one. Integrating each
        forward by a random age analytically costs one pass and starts the
        measurement already in steady state.
        """
        count = self.count
        self.fx[:] = self.origin[0]
        self.fy[:] = self.origin[1]
        self.vx[:] = self.rng.uniform(-SPREAD, SPREAD, count)
        self.vy[:] = self.rng.uniform(*LIFT, count)
        age = self.rng.uniform(0.0, LIFETIME, count).astype(np.float32)
        self.fx += self.vx * age
        self.fy += self.vy * age + 0.5 * GRAVITY * age * age
        self.vy += GRAVITY * age
        self.alive[:] = True
        with self.swarm.edit():
            self.swarm.positions[:, 0] = np.floor(self.fx)
            self.swarm.positions[:, 1] = np.floor(self.fy) + self._offset

    def tick(self) -> None:
        started = perf_counter()
        self._step()
        self.sim_ms = (perf_counter() - started) * 1000.0

    def _step(self) -> None:
        with self.swarm.edit():
            self._spawn()
            live = self.alive
            self.vy[live] += GRAVITY
            self.fx[live] += self.vx[live]
            self.fy[live] += self.vy[live]

            left, right, top, bottom = self.bounds
            gone = live & (
                (self.fx < left)
                | (self.fx > right)
                | (self.fy < top)
                | (self.fy > bottom)
            )
            self.alive[gone] = False
            self.swarm.positions[:, 0] = np.floor(self.fx)
            self.swarm.positions[:, 1] = np.floor(self.fy) + self._offset

    def _spawn(self) -> None:
        """Refill every dead slot. Uncapped, so the population holds."""
        free = np.flatnonzero(~self.alive)
        count = free.size
        if not count:
            return
        self.fx[free] = self.origin[0]
        self.fy[free] = self.origin[1]
        self.vx[free] = self.rng.uniform(-SPREAD, SPREAD, count)
        self.vy[free] = self.rng.uniform(*LIFT, count)
        self.alive[free] = True


def _window(samples: list) -> dict | None:
    """Mean, p99 and max over the same last-`FRAMES` span the engine quotes."""
    if not samples:
        return None
    recent = np.asarray(samples[-FRAMES:], dtype=np.float64)
    return {
        "mean": float(recent.mean()),
        "p99": float(np.percentile(recent, 99)),
        "max": float(recent.max()),
        "observed": int(recent.size),
    }


def _summary(report, name: str) -> dict | None:
    summary = report.summarise(name)
    if summary is None or not summary.observed:
        return None
    p50, p90, p99 = summary.percentiles
    return {
        "mean": summary.mean,
        "p50": p50,
        "p90": p90,
        "p99": p99,
        "max": summary.maximum,
        "observed": summary.observed,
    }


def run_reference(
    count: int, ticks: int, offscreen: bool = False, collide: bool = False
) -> int:
    """One population on the pre-rework engine.

    Its compose figure is `metrics.render`, which is the same scope as the
    rebuild's `display.compose.ms`: every layer's draw into the framebuffer,
    excluding the present wait.
    """
    bind_reference()
    from blitspersecond import BlitsPerSecond

    bps = BlitsPerSecond()
    swarm = ReferenceSwarm(bps, count, offscreen=offscreen, collide=collide)
    state = {"tick": 0, "sim": [], "compose": [], "started": perf_counter()}

    def loop(engine) -> None:
        swarm.tick()
        state["tick"] += 1
        if state["tick"] == WARMUP:
            state["started"] = perf_counter()
        state["sim"].append(swarm.sim_ms)
        # `render.average` is a rolling mean over the last second, so reading
        # it every tick would give a mean of means and no honest percentile.
        # The raw sample is the newest entry of the window behind it.
        queue = getattr(engine.metrics.render, "_queue", None)
        if queue:
            state["compose"].append(queue[-1] * 1000.0)
        if state["tick"] < ticks:
            return
        result = {
            "count": count,
            "ticks": ticks,
            "engine": "reference",
            "offscreen": offscreen,
            "collide": collide,
            "alive": int(swarm.alive.sum()),
            "pace": _delivered(state["tick"] - WARMUP, state["started"]),
            "sim_ms": _window(state["sim"]),
            "sync_ms": None,
            "compose_ms": _window(state["compose"]),
            "frame_ms": None,
            "gameloop_ms": None,
            "display_frame_ms": None,
        }
        print(MARKER + json.dumps(result), flush=True)
        engine.stop()

    bps.run(loop)
    return 0


def _delivered(ticks: int, started: float) -> float:
    """Game time delivered per unit of wall time, computed here.

    Computed rather than read off either engine's own pace instrument, because
    the two implement it differently and a comparison between two definitions
    of the same word is not a comparison. Ticks are a fixed 60Hz on both.

    Timed from the end of warm-up, like everything else here. Starting the
    clock at construction instead would charge the run for window creation and
    the first compose, which is how a population that never missed a frame
    comes to report 97%.
    """
    wall = perf_counter() - started
    return (ticks / 60.0) / wall if wall > 0 else 0.0


def run_one(
    count: int, ticks: int, offscreen: bool = False, split: bool = False,
    stage: str = "current",
) -> int:
    """One population, in this process. Emits its result and stops.

    The result is printed rather than returned because the loop leaves the
    process rather than handing control back, so there is nothing to return
    it to.

    `stage` selects which staging path the frame plan uses. `util/ab_stage.py`
    A/B's the candidates on their own, with an equivalence check; this runs
    the chosen one through the whole engine, on the same instrument that
    produced the shipped path's numbers, so the two rows compare.
    """
    if stage != "current":
        from ab_stage import install_bulk

        install_bulk()
    from blitspersecond import BlitsPerSecond

    bps = BlitsPerSecond()
    bps.background = (0.02, 0.02, 0.06)
    swarm = Swarm(bps, count, offscreen=offscreen)

    state = {"tick": 0, "sim": [], "sync": [], "started": perf_counter()}

    def loop() -> None:
        swarm.tick()
        if state["tick"] == WARMUP:
            state["started"] = perf_counter()
        if split:
            # `_composite` is `_sync()` then an unconditional `upload()` then
            # the draws. Syncing HERE, from the tick, leaves the sprite clean
            # by the time the compositor reaches it, so its `_sync` returns on
            # the dirty gate and `display.compose.ms` measures upload and draw
            # alone -- while this measures the plan rebuild. Reaching for a
            # private method to do it, which is the price of splitting a
            # number the engine reports as one.
            started = perf_counter()
            swarm.sprite._sync()
            state["sync"].append((perf_counter() - started) * 1000.0)
        state["tick"] += 1
        state["sim"].append(swarm.sim_ms)
        if state["tick"] < ticks:
            return
        # Read BEFORE stopping. `stop()` is deferred to a loop boundary and
        # the epilogue exits the process, so there is no later moment.
        report = bps.metrics.report()
        # Trimmed to the ring, so this file's own timing covers the same
        # span the engine's numbers do. Quoting a whole-run sim mean against
        # a last-1024-frames compose mean would put the two halves of the
        # frame budget on different clocks.
        recent = np.asarray(state["sim"][-FRAMES:], dtype=np.float64)
        result = {
            "count": count,
            "ticks": ticks,
            "offscreen": offscreen,
            # Slightly under `count`: a bullet that left the screen this
            # tick is refilled at the top of the NEXT one, so the reading
            # catches one tick's worth of dead slots. It is the drawn
            # population, which is the honest thing to report.
            "alive": int(swarm.alive.sum()),
            "engine": "new",
            "stage": stage,
            "pace": _delivered(state["tick"] - WARMUP, state["started"]),
            "engine_pace": report.pace(),
            "sim_ms": _window(state["sim"]),
            "sync_ms": _window(state["sync"]) if split else None,
            "compose_ms": _summary(report, "display.compose.ms"),
            "frame_ms": _summary(report, "engine.frame.ms"),
            "gameloop_ms": _summary(report, "engine.gameloop.ms"),
            "display_frame_ms": _summary(report, "display.frame.ms"),
        }
        print(MARKER + json.dumps(result), flush=True)
        bps.stop()

    bps.start(loop)
    return 0


def _worker(
    count: int,
    ticks: int,
    offscreen: bool = False,
    split: bool = False,
    engine: str = "new",
    collide: bool = False,
    stage: str = "current",
) -> dict | None:
    """Run one population in a child and bring back its result."""
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--run",
        str(count),
        "--ticks",
        str(ticks),
    ]
    if offscreen:
        command.append("--offscreen")
    if split:
        command.append("--split")
    if engine != "new":
        command += ["--engine", engine]
    if collide:
        command.append("--collide")
    if stage != "current":
        command += ["--stage", stage]
    completed = subprocess.run(
        command, capture_output=True, text=True, env=os.environ.copy()
    )
    for line in completed.stdout.splitlines():
        if line.startswith(MARKER):
            return json.loads(line[len(MARKER):])
    sys.stderr.write(
        f"\n  no result for {count:,} (exit {completed.returncode})\n"
        + "".join(f"    {line}\n" for line in completed.stderr.splitlines()[-8:])
    )
    return None


def _table(results: list[dict]) -> None:
    split = any(row.get("sync_ms") for row in results)
    header = (
        f"{'count':>9}  {'alive':>9}  "
        + (f"{'plan':>9}  {'upload':>9}  " if split else f"{'compose':>9}  ")
        + f"{'p99':>8}  {'sim':>8}  {'frame':>8}  {'pace':>7}  "
        f"{'budget':>7}  {'us/1k':>7}"
    )
    print("\n" + header)
    print("-" * len(header))
    for row in results:
        compose = row["compose_ms"] or {}
        frame = row.get("frame_ms") or {}
        mean = compose.get("mean", float("nan"))
        sim = row["sim_ms"]["mean"]
        used = (mean + sim) / BUDGET_MS * 100.0
        per_1k = mean / (row["count"] / 1000.0) * 1000.0
        pace = row["pace"]
        sync = (row.get("sync_ms") or {}).get("mean")
        cost = f"{sync:>8.2f}m  {mean:>8.2f}m  " if split else f"{mean:>8.2f}m  "
        if split:
            # The plan rebuild is the other half of the same per-frame cost,
            # so both the budget and the per-thousand figure have to count it
            # or the split would read as a saving.
            used = (mean + sync + sim) / BUDGET_MS * 100.0
            per_1k = (mean + sync) / (row["count"] / 1000.0) * 1000.0
        print(
            f"{row['count']:>9,}  {row['alive']:>9,}  {cost}"
            f"{compose.get('p99', float('nan')):>7.2f}m  {sim:>7.2f}m  "
            + (f"{frame['mean']:>7.2f}m  " if frame.get("mean") else f"{'-':>8}  ")
            +
            f"{(pace * 100 if pace else float('nan')):>6.1f}%  "
            f"{used:>6.1f}%  {per_1k:>7.1f}"
        )
    work = "plan+upload" if split else "compose"
    print(
        f"\nall times are per-frame means in ms; budget is ({work}+sim) "
        f"against {BUDGET_MS:.2f}ms.\n"
        f"us/1k is {work} per thousand presentations. Fixed per-frame cost "
        "makes the\nsmallest population look dearest per unit, so a KNEE is "
        "the figure turning back UP\nas the count grows, not merely being "
        "above the minimum."
    )


def _verdict(results: list[dict]) -> None:
    """Say where it stopped being free, and what to target."""
    usable = [
        row
        for row in results
        if row["compose_ms"] and row["pace"] and row["pace"] > 0.99
    ]
    if not usable:
        print("\nnothing held pace; no target to give.")
        return
    held = max(usable, key=lambda row: row["count"])
    # Half the budget, because a benchmark is not a game: the target has to
    # leave room for the game that will be running as well as this.
    comfortable = [
        row
        for row in usable
        if (row["compose_ms"]["mean"] + row["sim_ms"]["mean"]) < BUDGET_MS / 2
    ]
    print(f"\nheld 60fps up to      {held['count']:,} presentations")
    if comfortable:
        best = max(comfortable, key=lambda row: row["count"])
        print(f"inside half a budget  {best['count']:,}")
    # Per-unit cost against the whole per-frame cost, not just the half the
    # engine happens to report as `compose`: under --split the plan rebuild
    # is the other 96% of it, and reading the ratio off upload alone would
    # find a knee in a number nothing is bound by.
    ratios = []
    for row in results:
        if not row["compose_ms"]:
            continue
        cost = row["compose_ms"]["mean"] + (row.get("sync_ms") or {}).get("mean", 0.0)
        # us per thousand, matching the table's column: ms/1k * 1000.
        ratios.append((row["count"], cost / (row["count"] / 1000.0) * 1000.0))

    # A knee is the per-unit figure turning back UP as the population grows.
    # Comparing every point against the global minimum instead would flag the
    # SMALLEST run every time, because fixed per-frame cost is spread over
    # fewer presentations there -- which is amortisation, the opposite of the
    # thing being looked for.
    ratios.sort()
    knee = None
    best = float("inf")
    for count, cost in ratios:
        if cost > best * 1.25:
            knee = count
            break
        best = min(best, cost)
    if len(ratios) > 1:
        if knee is not None:
            print(f"cost per presentation rises from {knee:,}")
        else:
            first, last = ratios[0][1], ratios[-1][1]
            print(
                f"no knee: {first:.0f}us/1k at {ratios[0][0]:,} -> "
                f"{last:.0f}us/1k at {ratios[-1][0]:,}, so the ceiling is a "
                "budget, not a cliff"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=int, help="worker: one population, JSON out")
    parser.add_argument("--ticks", type=int, default=DEFAULT_TICKS)
    parser.add_argument(
        "--counts",
        default=",".join(str(n) for n in DEFAULT_COUNTS),
        help="comma-separated populations to sweep",
    )
    parser.add_argument(
        "--offscreen",
        action="store_true",
        help="park the population outside the viewport: same staging and "
        "upload, no fill. The difference against a normal run is the "
        "rasteriser's share of compose.",
    )
    parser.add_argument(
        "--split",
        action="store_true",
        help="time the frame-plan rebuild separately, leaving compose as "
        "upload and draw alone.",
    )
    parser.add_argument(
        "--engine",
        choices=("new", "reference"),
        default="new",
        help="which engine to measure (default: new)",
    )
    parser.add_argument(
        "--collide",
        action="store_true",
        help="reference only: leave collision stamping on. Off by default so "
        "the comparison is like for like against a pillar that has none.",
    )
    parser.add_argument(
        "--stage",
        choices=("current", "bulk"),
        default="current",
        help="new engine only: which staging path the frame plan uses. `bulk` "
        "installs the candidate from util/ab_stage.py, so the two runs are "
        "the same measurement of two implementations.",
    )
    parser.add_argument("--json", type=Path, help="also write raw results here")
    args = parser.parse_args()

    if args.run:
        if args.engine == "reference":
            return run_reference(
                args.run, args.ticks, args.offscreen, args.collide
            )
        return run_one(
            args.run, args.ticks, args.offscreen, args.split, args.stage
        )

    counts = [int(n) for n in args.counts.split(",") if n.strip()]
    label = (
        args.engine
        + (" +collision" if args.collide else "")
        + (f" [{args.stage}]" if args.stage != "current" else "")
    )
    print(f"sweeping {label} over {args.ticks} ticks")
    results = []
    for count in counts:
        print(f"  {count:>9,} ...", end="", flush=True)
        started = perf_counter()
        row = _worker(
            count,
            args.ticks,
            args.offscreen,
            args.split,
            args.engine,
            args.collide,
            args.stage,
        )
        if row is None:
            continue
        results.append(row)
        compose = row["compose_ms"]["mean"] if row["compose_ms"] else float("nan")
        print(
            f" compose {compose:6.2f}ms  pace {(row['pace'] or 0) * 100:5.1f}%"
            f"  ({perf_counter() - started:.0f}s)"
        )
    if not results:
        return 1
    _table(results)
    _verdict(results)
    if args.json:
        args.json.write_text(json.dumps(results, indent=2))
        print(f"\nraw results -> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
