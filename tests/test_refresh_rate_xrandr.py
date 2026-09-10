"""Reading a CRTC's unrounded rate on X11."""

from types import SimpleNamespace

import pytest

from blitspersecond.internal.pacing.refresh_rate import get_refresh_rate
from blitspersecond.internal.pacing.refresh_rate import xrandr as backend


def test_a_mode_line_divides_to_the_rate_xrandr_prints():
    """The measured DP-2 mode on the reference machine. xrandr says 59.92."""
    assert backend.refresh_from_mode(712000000, 5312, 2237) == pytest.approx(
        59.91781, abs=1e-5
    )
    assert backend.refresh_from_mode(173000000, 2576, 1120) == pytest.approx(
        59.96284, abs=1e-5
    )


def test_the_fraction_pyglet_discards_is_the_whole_point():
    """`round()` on that value reports a 59.918Hz panel as 60."""
    rate = backend.refresh_from_mode(712000000, 5312, 2237)
    assert round(rate) == 60
    assert rate != 60


@pytest.mark.parametrize(
    "dot_clock,h_total,v_total",
    [(0, 5312, 2237), (712000000, 0, 2237), (712000000, 5312, 0), (-1, 1, 1)],
)
def test_an_unusable_mode_line_is_unavailable(dot_clock, h_total, v_total):
    assert backend.refresh_from_mode(dot_clock, h_total, v_total) is None


def test_a_screen_without_a_crtc_is_unavailable():
    """Pyglet builds screens without XRandR too; that is not an error."""
    assert backend.query_refresh_rate(SimpleNamespace()) is None
    assert backend.query_refresh_rate(SimpleNamespace(crtc_id=0)) is None
    screen = SimpleNamespace(crtc_id=99, display=SimpleNamespace(_display=None))
    assert backend.query_refresh_rate(screen) is None


def test_linux_prefers_the_crtc_rate_over_the_rounded_mode(monkeypatch):
    monkeypatch.setattr(backend_platform := __import__(
        "blitspersecond.internal.pacing.refresh_rate",
        fromlist=["sys"]), "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(
        backend_platform, "_xrandr_refresh_rate", lambda screen: 59.91781
    )
    screen = SimpleNamespace(get_mode=lambda: SimpleNamespace(rate=60))
    assert get_refresh_rate(screen) == pytest.approx(59.91781)


def test_a_backend_failure_falls_back_to_the_rounded_mode(monkeypatch):
    """No worse than having no backend at all."""
    module = __import__(
        "blitspersecond.internal.pacing.refresh_rate", fromlist=["sys"]
    )
    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(module, "_xrandr_refresh_rate", lambda screen: None)
    screen = SimpleNamespace(get_mode=lambda: SimpleNamespace(rate=60))
    assert get_refresh_rate(screen) == 60.0
