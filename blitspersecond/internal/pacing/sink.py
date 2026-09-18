"""What `pacing` needs from a frame-pacing recorder, stated as a Protocol.

This is a dependency inversion, and it is deliberate. `pacing` declares the
shape of the sink it will call; `system.frame_pacing.FramePacingRecorder`
happens to satisfy it; `internal/display.py` wires the two together. So
nothing under `internal/pacing/` imports the recorder, and the engine can run
with no sink at all -- the capture is opt-in, and `None` is the normal case.

Kept out of `session.py` because it is an interface to something else, not
part of running a frame. `session.py` is already the biggest thing in this
package and every non-pacing concern that sits in it makes the pacing
harder to read.
"""

from pathlib import Path
from typing import Protocol


class FramePacingSink(Protocol):
    """Completed-frame event sink used by presentation instrumentation."""

    path: Path

    @property
    def count(self) -> int: ...

    @property
    def dropped(self) -> int: ...

    def begin_iteration(self) -> None: ...

    def after_dispatch(self) -> None: ...

    def after_simulation(self) -> None: ...

    def after_prepare(self) -> None: ...

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
    ) -> None: ...

    def save(self) -> None: ...


class FramePacingSinkFactory(Protocol):
    """Construct an optional sink once presentation metadata is known.

    Metadata-first because the recorder writes it into the CSV header: a
    capture is only comparable against another if it says what it was
    capturing -- target rate, display rate, cadence, and which presentation
    path produced it.
    """

    def __call__(
        self,
        *,
        target_fps: float,
        refresh_hz: float | None,
        cadence: str,
        presentation_path: str,
    ) -> FramePacingSink | None: ...
