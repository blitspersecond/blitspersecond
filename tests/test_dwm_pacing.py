"""Contracts for BPS's Win32/DWM presentation clock."""

import ctypes
from types import SimpleNamespace

import pytest

from blitspersecond.internal.pacing import session as session_module
from blitspersecond.internal.pacing.dwm import (
    DWM_SUBMIT_LEAD,
    DwmPresentationClock,
    _DwmTimingInfo,
    _clock_from_counts,
)
from blitspersecond.internal.pacing.session import PresentationSession


class FakeLogger:
    def __init__(self) -> None:
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def info(self, message: str) -> None:
        self.infos.append(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)

    def error(self, message: str) -> None:
        self.errors.append(message)


class FakeDisplay:
    def __init__(
        self,
        refresh_rate: float = 144.0,
        *,
        dwm_clock_eligible: bool = True,
    ) -> None:
        self.refresh_rate = refresh_rate
        self.dwm_clock_eligible = dwm_clock_eligible
        self.polled: list[bool] = []
        self.next_refresh_rate: float | None = refresh_rate

    def poll_refresh_rate(self, *, force: bool = False) -> float | None:
        self.polled.append(force)
        return self.next_refresh_rate


def clock(rate: float, phase: float = 100.0) -> DwmPresentationClock:
    return DwmPresentationClock(
        period=1.0 / rate,
        phase=phase,
        refresh_rate=rate,
    )


def make_session(
    monkeypatch,
    *,
    path: str = "win32",
    clocks=(None,),
    dwm_clock_eligible: bool = True,
):
    descriptions = {
        "win32": "Win32/WGL -> DWM",
        "x11": "Xlib/GLX -> X11",
    }
    monkeypatch.setattr(
        session_module,
        "detect_presentation_path",
        lambda: SimpleNamespace(name=path, description=descriptions[path]),
    )
    samples = iter(clocks)
    calls: list[str] = []

    def query(path_name: str):
        calls.append(path_name)
        return next(samples)

    monkeypatch.setattr(session_module, "_query_dwm_clock", query)
    metadata = {}

    def sink_factory(**values):
        metadata.update(values)
        return None

    display = FakeDisplay(dwm_clock_eligible=dwm_clock_eligible)
    logger = FakeLogger()
    session = PresentationSession(
        display,
        target_rate=60,
        step=1.0 / 60.0,
        logger=logger,
        frame_pacing_factory=sink_factory,
    )
    return session, display, logger, calls, metadata


def test_dwm_sdk_structure_has_the_required_packed_size():
    assert ctypes.sizeof(_DwmTimingInfo) == 292


def test_qpc_counts_become_an_absolute_early_submission_grid():
    result = _clock_from_counts(
        qpc_vblank=10_000_000,
        qpc_refresh_period=69_445,
        qpc_frequency=10_000_000,
    )

    assert result is not None
    assert result.period == pytest.approx(0.0069445)
    assert result.refresh_rate == pytest.approx(143.998847)
    assert result.phase == pytest.approx(1.0 - DWM_SUBMIT_LEAD)


@pytest.mark.parametrize(
    "values",
    [
        {"qpc_vblank": 0, "qpc_refresh_period": 69_445, "qpc_frequency": 10_000_000},
        {"qpc_vblank": 1, "qpc_refresh_period": 0, "qpc_frequency": 10_000_000},
        {"qpc_vblank": 1, "qpc_refresh_period": 69_445, "qpc_frequency": 0},
    ],
)
def test_invalid_dwm_counts_are_unavailable(values):
    assert _clock_from_counts(**values) is None


def test_matching_dwm_clock_replaces_the_configured_mode_grid(monkeypatch):
    dwm = clock(143.998848, phase=50.0)
    session, _display, _logger, calls, metadata = make_session(
        monkeypatch, clocks=(dwm,)
    )

    assert calls == ["win32"]
    assert session.refresh_rate == pytest.approx(dwm.refresh_rate)
    assert session._period == dwm.period
    assert session._phase == dwm.phase
    assert session._uses_dwm_clock
    assert metadata["refresh_hz"] == pytest.approx(dwm.refresh_rate)
    assert session.next_slot(dwm.phase + 0.1 * dwm.period) == pytest.approx(
        dwm.phase + dwm.period
    )


def test_mismatched_desktop_clock_cannot_pace_another_output(monkeypatch):
    session, _display, logger, _calls, _metadata = make_session(
        monkeypatch, clocks=(clock(60.0),)
    )

    assert session.refresh_rate == 144.0
    assert not session._uses_dwm_clock
    assert session._phase == 0.0
    assert any("does not match" in message for message in logger.warnings)


def test_repeated_move_events_do_not_repeat_the_same_mismatch_warning(monkeypatch):
    session, _display, logger, _calls, _metadata = make_session(
        monkeypatch,
        clocks=(clock(60.0), clock(60.0), clock(60.0)),
    )

    session._adopt(144.0)
    session._adopt(144.0)

    assert len(logger.warnings) == 1


def test_non_windows_path_never_imports_a_dwm_clock(monkeypatch):
    session, _display, _logger, calls, _metadata = make_session(
        monkeypatch, path="x11", clocks=(None,)
    )

    assert calls == ["x11"]
    assert not session._uses_dwm_clock


def test_secondary_output_never_queries_the_primary_dwm_clock(monkeypatch):
    session, _display, _logger, calls, metadata = make_session(
        monkeypatch,
        clocks=(clock(143.998848),),
        dwm_clock_eligible=False,
    )

    assert calls == []
    assert session.refresh_rate == 144.0
    assert not session._uses_dwm_clock
    assert metadata["refresh_hz"] == 144.0


def test_foregrounding_refreshes_dwm_phase(monkeypatch):
    first = clock(143.998848, phase=50.0)
    second = clock(143.998848, phase=80.0)
    session, display, _logger, calls, _metadata = make_session(
        monkeypatch, clocks=(first, second)
    )

    display.next_refresh_rate = 144.0
    session.foregrounded()

    assert display.polled == [True]
    assert calls == ["win32", "win32"]
    assert session._phase == second.phase


def test_failed_topology_query_detaches_an_adopted_dwm_clock(monkeypatch):
    dwm = clock(143.998848, phase=50.0)
    session, display, _logger, calls, _metadata = make_session(
        monkeypatch, clocks=(dwm,)
    )

    display.dwm_clock_eligible = False
    display.next_refresh_rate = None
    session.foregrounded()

    assert calls == ["win32"]
    assert session.refresh_rate == 144.0
    assert session._period == pytest.approx(1.0 / 144.0)
    assert session._phase == 0.0
    assert not session._uses_dwm_clock


def test_monitor_move_rejects_a_clock_from_a_different_rate(monkeypatch):
    session, _display, _logger, _calls, _metadata = make_session(
        monkeypatch,
        clocks=(clock(143.998848, phase=50.0), clock(143.998848, phase=80.0)),
    )

    session._adopt(60.0)

    assert session.refresh_rate == 60.0
    assert session._period == pytest.approx(1.0 / 60.0)
    assert session._phase == 0.0
    assert not session._uses_dwm_clock


def test_absolute_dwm_phase_is_never_nudged_by_interval_heuristic(monkeypatch):
    dwm = clock(143.998848, phase=50.0)
    session, _display, _logger, _calls, _metadata = make_session(
        monkeypatch, clocks=(dwm,)
    )

    for index in range(20):
        session._interval = session._period * 4
        session._watch_phase(index, overran=False)

    assert session._phase == dwm.phase
    assert session.phase_shifts == 0


def test_a_dwm_grid_re_reads_its_reference_and_follows_it(monkeypatch):
    """The other half of the contract above.

    `_watch_phase` refuses to move this grid on application-side evidence.
    Re-reading the reference itself is not that: DWM's reported period is
    quantised, so a session that sampled a tail value walks off the real
    vblank until it looks again.
    """
    first = clock(143.998848, phase=50.0)
    moved = clock(143.998848, phase=50.00025)
    session, _display, _logger, _calls, _metadata = make_session(
        monkeypatch, clocks=(first, moved)
    )
    assert session._phase == first.phase

    session._next_reanchor = 0.0
    session._reanchor()

    assert session._phase == pytest.approx(moved.phase)
    assert session.reanchors == 1
    assert session.reanchor_worst == pytest.approx(0.00025)


def test_re_anchoring_moves_the_phase_and_never_the_period(monkeypatch):
    """Re-timing mid-run would change slot spacing under a running game."""
    first = clock(143.998848, phase=50.0)
    faster = clock(144.002995, phase=50.001)
    session, _display, _logger, _calls, _metadata = make_session(
        monkeypatch, clocks=(first, faster)
    )
    period, rate = session._period, session._refresh_rate

    session._next_reanchor = 0.0
    session._reanchor()

    assert session._period == period
    assert session._refresh_rate == rate
    assert session._phase == pytest.approx(faster.phase)


def test_a_stale_dwm_prediction_is_the_same_grid(monkeypatch):
    """DWM retains a prediction while the desktop needs no new work.

    The vblank it then reports is whole periods in the past. Folding the
    correction to the nearest grid line makes that read as no move at all,
    which is what it is -- every phase congruent to the grid IS the grid.
    """
    first = clock(143.998848, phase=50.0)
    stale = clock(143.998848, phase=50.0 - 3.0 / 143.998848)
    session, _display, _logger, _calls, _metadata = make_session(
        monkeypatch, clocks=(first, stale)
    )

    session._next_reanchor = 0.0
    session._reanchor()

    assert session._phase == pytest.approx(first.phase)
    assert session.reanchor_worst == pytest.approx(0.0, abs=1e-9)


def test_the_reference_is_not_re_read_every_frame(monkeypatch):
    """A freshly anchored grid is already where it should be."""
    first = clock(143.998848, phase=50.0)
    session, _display, _logger, calls, _metadata = make_session(
        monkeypatch, clocks=(first, clock(143.998848, phase=99.0))
    )
    calls.clear()

    session._reanchor()

    assert calls == []
    assert session.reanchors == 0
    assert session._phase == first.phase


def test_a_silent_dwm_leaves_the_adopted_grid_where_it_is(monkeypatch):
    """A grid with a stale reference still beats no grid at all."""
    first = clock(143.998848, phase=50.0)
    session, _display, _logger, _calls, _metadata = make_session(
        monkeypatch, clocks=(first, None)
    )

    session._next_reanchor = 0.0
    session._reanchor()

    assert session._phase == first.phase
    assert session.reanchors == 0


def test_a_notional_grid_has_no_reference_to_re_read(monkeypatch):
    """Nothing to ask, and asking would import a Win32 probe off-platform."""
    session, _display, _logger, calls, _metadata = make_session(
        monkeypatch, path="x11", clocks=(None,)
    )
    calls.clear()
    session._next_reanchor = 0.0

    session.wait_for(0)

    assert calls == []
    assert session.reanchors == 0
