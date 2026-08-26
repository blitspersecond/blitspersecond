"""Presentation path classification, and the diagnostic deadline override.

`detect_presentation_path` is a pure function of the environment, which is
the whole point: it decides two things nothing downstream can recover --
whether to ask the window system to pace swaps, and whether to start on a
deadline grid -- and it must do so from launch facts alone, without opening
a second display to look.

NOTE (2026-08-25): these encode what the reference implementation decided,
which is reference-code history rather than a re-verified fact. The
classification is due a retest against a real window now that one opens
again; see claude.memories/bps-multi-display-pacing.md.
"""

import pytest

from blitspersecond.display.internal.pacing.path import (
    ENV_START_DEADLINE,
    detect_presentation_path,
    start_deadline_from_environment,
)


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


class TestPacingConsequences:
    """The two properties the rest of `pacing` actually branches on."""

    @pytest.mark.parametrize(
        "environment, platform, expected",
        [
            ({"GAMESCOPE_WAYLAND_DISPLAY": "gamescope-0"}, "linux", True),
            ({"XDG_SESSION_TYPE": "wayland"}, "linux", True),
            ({}, "win32", True),
            ({"XDG_SESSION_TYPE": "x11"}, "linux", False),
        ],
    )
    def test_which_paths_start_on_the_deadline_grid(
        self, environment, platform, expected
    ):
        """Gamescope, XWayland and Win32 have a known non-blocking swap, so
        BPS owns the deadline from frame one. Plain X11 does not -- it starts
        trusting the blocking swap, and only moves onto the grid if the
        health monitor MEASURES the swap failing to pace."""
        path = detect_presentation_path(environment, platform=platform)
        assert path.starts_on_deadline_grid is expected

    def test_only_xwayland_declines_to_request_vsync(self):
        """Mutter's XWayland/GLX path charges a whole extra refresh when a
        prepared frame narrowly misses vblank, so BPS does not ask it to
        pace at all. Gamescope keeps the request: its nested swap is already
        non-blocking and the marker starts the same deadline policy."""
        xwayland = detect_presentation_path(
            {"XDG_SESSION_TYPE": "wayland"}, platform="linux"
        )
        gamescope = detect_presentation_path(
            {"GAMESCOPE_WAYLAND_DISPLAY": "gamescope-0"}, platform="linux"
        )
        x11 = detect_presentation_path({"XDG_SESSION_TYPE": "x11"}, platform="linux")

        assert xwayland.requests_vsync is False
        assert gamescope.requests_vsync is True
        assert x11.requests_vsync is True


class TestDiagnosticOverride:
    """`BPS_FRAMEPACE_START_DEADLINE` forces the grid on a path that would
    not otherwise choose it -- the manual counterpart to
    `starts_on_deadline_grid`. It lives here rather than with the recorder
    because it CHANGES pacing rather than recording it."""

    def test_absent_means_no_override(self, monkeypatch):
        monkeypatch.delenv(ENV_START_DEADLINE, raising=False)
        assert start_deadline_from_environment() is False

    def test_it_is_read_strictly(self, monkeypatch):
        monkeypatch.setenv(ENV_START_DEADLINE, "yes")
        assert start_deadline_from_environment() is True

    def test_an_unparseable_value_names_the_variable(self, monkeypatch):
        """A silently-ignored typo here would produce a capture measuring
        the wrong pacing mode entirely, and nothing in the CSV would say so."""
        monkeypatch.setenv(ENV_START_DEADLINE, "perhaps")
        with pytest.raises(ValueError, match=ENV_START_DEADLINE):
            start_deadline_from_environment()
