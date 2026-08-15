from types import SimpleNamespace

from blitspersecond.display.internal import surface as surface_module
from blitspersecond.display.internal.surface import Surface


def bare_surface(monkeypatch):
    surface = Surface.__new__(Surface)
    surface._tracks_window_output = True
    surface._refresh_check_needed = False
    surface._refresh_rate = 144.0
    screen = SimpleNamespace(name="secondary")
    setattr(surface, "get_window_screen", lambda: screen)
    monkeypatch.setattr(surface_module, "get_refresh_rate", lambda value: 60.0)
    return surface, screen


def test_win32_move_requeries_monitor_from_window(monkeypatch):
    surface, _screen = bare_surface(monkeypatch)

    surface.on_move(4000, 1200)

    assert surface.poll_refresh_rate() == 60.0
    assert surface.refresh_rate == 60.0
    assert surface.poll_refresh_rate() is None


def test_win32_display_change_marks_refresh_dirty(monkeypatch):
    surface, _screen = bare_surface(monkeypatch)

    surface._event_display_change(0x007E, 32, 0)

    assert surface.poll_refresh_rate() == 60.0


def test_foreground_check_can_force_refresh_without_move(monkeypatch):
    surface, _screen = bare_surface(monkeypatch)

    assert surface.poll_refresh_rate(force=True) == 60.0


def test_other_platform_paths_do_not_query_window_monitor(monkeypatch):
    surface, _screen = bare_surface(monkeypatch)
    surface._tracks_window_output = False

    def unexpected():
        raise AssertionError("MonitorFromWindow queried outside Win32")

    setattr(surface, "get_window_screen", unexpected)
    surface.on_move(100, 100)

    assert surface.poll_refresh_rate(force=True) is None
