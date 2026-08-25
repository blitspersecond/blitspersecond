from blitspersecond.display.internal.presentation.path import (
    detect_presentation_path,
)


def test_gamescope_markers_take_precedence_over_reported_x11_session():
    path = detect_presentation_path(
        {
            "GAMESCOPE_WAYLAND_DISPLAY": "gamescope-0",
            "XDG_CURRENT_DESKTOP": "gamescope",
            "XDG_SESSION_TYPE": "x11",
            "BPS_GAMESCOPE_BACKEND": "wayland",
        },
        platform="linux",
    )

    assert path.name == "gamescope"
    assert path.client == "Xlib/GLX"
    assert path.outer_backend == "wayland"
    assert path.starts_on_deadline_grid
    assert path.requests_vsync
    assert path.description == "Xlib/GLX -> Gamescope (outer backend: wayland)"


def test_xlib_client_in_wayland_session_is_described_as_xwayland():
    path = detect_presentation_path(
        {
            "XDG_CURRENT_DESKTOP": "GNOME",
            "XDG_SESSION_TYPE": "wayland",
        },
        platform="linux",
    )

    assert path.name == "xwayland"
    assert path.compositor == "GNOME"
    assert path.starts_on_deadline_grid
    assert not path.requests_vsync


def test_native_x11_keeps_blocking_vsync_presentation():
    path = detect_presentation_path(
        {
            "XDG_CURRENT_DESKTOP": "i3",
            "XDG_SESSION_TYPE": "x11",
        },
        platform="linux",
    )

    assert path.name == "x11"
    assert not path.starts_on_deadline_grid
    assert path.requests_vsync


def test_win32_path_is_not_inferred_from_linux_environment():
    path = detect_presentation_path(
        {"XDG_SESSION_TYPE": "wayland"},
        platform="win32",
    )

    assert path.name == "win32"
    assert path.description == "Win32/WGL -> DWM"
    assert path.starts_on_deadline_grid
    assert path.requests_vsync
