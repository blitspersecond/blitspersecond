"""Presentation path classification.

`detect_presentation_path` is a pure function of the environment, which is
the whole point: it must classify from launch facts alone, without opening a
second display to look. In particular a client inside Gamescope cannot
discover Gamescope's outer backend, so the launcher records it.

It no longer DECIDES anything. It used to settle two things nothing
downstream could recover -- whether to request vsync, and whether to start on
a deadline grid -- and both went when every path moved to the deadline grid
with vsync off; see `docs/bps/DECOUPLED_PRESENTATION.md`. What it produces
now is description, for the log and for frame-pacing capture headers.

NOTE (2026-08-25): these encode what the reference implementation decided,
which is reference-code history rather than a re-verified fact. The
classification is due a retest against a real window now that one opens
again; see claude.memories/bps-multi-display-pacing.md.
"""

from blitspersecond.display.internal.pacing.path import detect_presentation_path


class TestClassification:
    def test_win32_is_named_without_consulting_the_environment(self):
        path = detect_presentation_path({}, platform="win32")
        assert path.name == "win32"
        assert path.compositor == "DWM"

    def test_gamescope_markers_win_over_the_session_type(self):
        """Gamescope supplies explicit markers to every child and takes
        precedence: inside it, the session type describes the host, not the
        thing actually pacing us."""
        path = detect_presentation_path(
            {"GAMESCOPE_WAYLAND_DISPLAY": "gamescope-0", "XDG_SESSION_TYPE": "wayland"},
            platform="linux",
        )
        assert path.name == "gamescope"

    def test_the_outer_backend_is_read_from_the_launcher_not_discovered(self):
        """A client inside Gamescope cannot see Gamescope's own backend, so
        the launcher records the choice it made."""
        path = detect_presentation_path(
            {
                "GAMESCOPE_WAYLAND_DISPLAY": "gamescope-0",
                "BPS_GAMESCOPE_BACKEND": "wayland",
            },
            platform="linux",
        )
        assert path.outer_backend == "wayland"
        assert "wayland" in path.description

    def test_a_wayland_session_without_gamescope_is_xwayland(self):
        path = detect_presentation_path(
            {"XDG_SESSION_TYPE": "wayland"}, platform="linux"
        )
        assert path.name == "xwayland"

    def test_an_x11_session_is_plain_x11(self):
        path = detect_presentation_path({"XDG_SESSION_TYPE": "x11"}, platform="linux")
        assert path.name == "x11"
