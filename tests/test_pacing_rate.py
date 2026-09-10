"""The pacer re-times its second so ticks and slots stay commensurate.

`accepted_rate.py` decides the rate; this is the wiring, and the property that
matters is the one at the bottom -- deadlines landing on slot boundaries.
"""

from fractions import Fraction
from types import SimpleNamespace

import pytest

from blitspersecond.internal.pacing import session as session_module
from blitspersecond.internal.pacing.session import PresentationSession

from .test_dwm_pacing import FakeDisplay, FakeLogger

AUTHORED_STEP = 1.0 / 60.0


def make_session(monkeypatch, refresh_rate, *, path="x11"):
    monkeypatch.setattr(
        session_module,
        "detect_presentation_path",
        lambda: SimpleNamespace(name=path, description="Xlib/GLX -> X11"),
    )
    monkeypatch.setattr(session_module, "_query_dwm_clock", lambda name: None)
    logger = FakeLogger()
    session = PresentationSession(
        FakeDisplay(refresh_rate=refresh_rate),
        target_rate=60,
        step=AUTHORED_STEP,
        logger=logger,
        frame_pacing_factory=lambda **values: None,
    )
    return session, logger


def test_a_commensurate_panel_is_left_completely_alone(monkeypatch):
    """144Hz already closes at 5/12. Nothing about it changes."""
    session, logger = make_session(monkeypatch, 144.0)
    assert session._step == AUTHORED_STEP
    assert session.accepted.exact
    assert not [line for line in logger.infos if "commensurate" in line]


def test_a_non_nominal_panel_re_times_the_second(monkeypatch):
    """59.94Hz: one tick per slot, forever, instead of a beat every 17s."""
    session, _ = make_session(monkeypatch, 59.94)
    assert session.accepted.ratio == Fraction(1, 1)
    assert session._step == pytest.approx(1.0 / 59.94)
    assert session._step > AUTHORED_STEP


def test_every_frame_waits_exactly_as_long_as_every_other(monkeypatch):
    """The end-to-end property, through the real `slot_for`. THE beat, gone.

    `origin` is a `perf_counter` reading, so deadlines never sit ON slots --
    the two grids share a period but not a phase. What matters is that the
    offset between them has no `frame` term: the wait from deadline to slot
    is the same on every frame, so the picture arrives on an unvarying
    cadence and no frame is ever the odd one out.
    """
    session, _ = make_session(monkeypatch, 59.94)
    session.start()
    waits = {
        round(session.slot_for(frame) - session.deadline(frame), 6)
        for frame in range(1, 2000)
    }
    assert len(waits) == 1


def test_without_re_timing_the_wait_walks_and_that_is_the_beat(monkeypatch):
    """The contrast, with the authored step forced back on a 59.94 panel.

    The wait now takes many values and creeps across a whole slot, which is
    the frame that periodically has nowhere to go.
    """
    session, _ = make_session(monkeypatch, 59.94)
    session._step = AUTHORED_STEP
    session.start()
    waits = [
        session.slot_for(frame) - session.deadline(frame)
        for frame in range(1, 2000)
    ]
    assert len(set(round(wait, 6) for wait in waits)) > 500
    assert max(waits) - min(waits) > session._period * 0.9


def test_a_commensurate_panel_keeps_its_uneven_slot_pattern(monkeypatch):
    """60 on 144 is already fine and is NOT flattened. 5 frames, 12 slots."""
    session, _ = make_session(monkeypatch, 144.0)
    session.start()
    waits = {
        round(session.slot_for(frame) - session.deadline(frame), 6)
        for frame in range(1, 300)
    }
    assert len(waits) == 5


def test_a_rate_no_panel_can_carry_falls_back_to_the_authored_step(monkeypatch):
    """Strictly non-regressive: what this cannot help, it does not touch."""
    session, logger = make_session(monkeypatch, 30.4)
    assert not session.accepted.within_tolerance
    assert session._step == AUTHORED_STEP
    # Reported, and the wording is not the contract -- that it names the panel
    # and the rate actually being paced is.
    assert len(logger.warnings) == 1
    assert "30.400" in logger.warnings[0]
    assert "60fps" in logger.warnings[0]


def test_a_refused_rate_is_never_reported_as_the_one_being_ticked(monkeypatch):
    """The startup line must not name a rate nothing is ticking at.

    A rate outside tolerance is refused and the authored step stands, so the
    grid line has to stay silent about it -- the warning is what reports it.
    """
    _, logger = make_session(monkeypatch, 30.4)
    grid = [line for line in logger.infos if "slot grid" in line]
    assert len(grid) == 1
    assert "ticking at" not in grid[0]
    assert "60.800" not in grid[0]


def test_an_adopted_rate_rides_on_the_grid_line(monkeypatch):
    """One line, not two. The rate is a clause on the line that already
    reports the grid, and an exact rate adds nothing at all."""
    _, retimed = make_session(monkeypatch, 59.94)
    line = [l for l in retimed.infos if "slot grid" in l][0]
    assert "ticking at 59.940fps" in line
    assert len([l for l in retimed.infos if "Pacing" in l]) == 1

    _, exact = make_session(monkeypatch, 144.0)
    assert "ticking at" not in [l for l in exact.infos if "slot grid" in l][0]


def test_the_adjustment_is_reported_once_not_per_call(monkeypatch):
    session, logger = make_session(monkeypatch, 59.94)
    before = len(logger.infos)
    for _ in range(5):
        session._accept_rate()
    assert len(logger.infos) == before


def test_a_window_moving_to_another_output_re_times(monkeypatch):
    session, _ = make_session(monkeypatch, 144.0)
    assert session._step == AUTHORED_STEP
    session._synchronise_grid(59.94)
    assert session._step == pytest.approx(1.0 / 59.94)
    session._synchronise_grid(144.0)
    assert session._step == AUTHORED_STEP


def test_the_reserve_ceiling_follows_the_accepted_step(monkeypatch):
    """The reserve is half a step, and the step is now the accepted one."""
    session, _ = make_session(monkeypatch, 59.94)
    session._recent_work.extend([1.0])
    assert session.reserve == pytest.approx(session._step * 0.5)
