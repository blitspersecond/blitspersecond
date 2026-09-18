"""Opt-in, bounded frame-pacing capture for the engine main loop.

Pure telemetry: the engine WRITES here and never reads back to decide
anything. `internal/pacing/` calls `begin_iteration`/`after_*`/
`record_present` as a frame goes by, and the only values ever read out
(`count`, `dropped`) are for one log line after the loop exits. That
one-directionality is the reason this lives under `system` rather than in
`internal/` -- instrumentation observes the engine, it does not steer it.

The hot path writes into a preallocated NumPy array; CSV formatting happens
once, after the loop exits. The capture wrapper (`util/capture_frame_pacing.py`)
can request a memory-mapped backing store (`BPS_FRAMEPACE_DURABLE`) so an
abruptly killed child -- Gamescope does this -- can still be recovered,
without adding per-frame formatting or writes to the loop being measured.

Off unless `BPS_FRAMEPACE_CSV` names a path: `from_environment` returns None
otherwise, and the engine then holds no sink at all.

Two things that used to live here left on 2026-08-25:

  * `_environment_flag` was a second private copy of the same strict parser
    `system.config` already had. Now `common.environment_flag`, shared, so
    the two cannot disagree about how a BPS_* flag is spelled.
  * `start_deadline_from_environment` (BPS_FRAMEPACE_START_DEADLINE) moved
    to `internal/pacing/path.py`, and has since been deleted outright.
    It forced deadline-grid pacing from frame one on a path that would not
    otherwise select it; every path starts there now, so it had nothing left
    to force.
"""

from .recorder import FORMAT, FramePacingRecorder

__all__ = [
    "FORMAT",
    "FramePacingRecorder",
]
