from types import SimpleNamespace

import pytest

from blitspersecond.display.internal import refresh_rate
from blitspersecond.display.internal.refresh_rate import win32


class FakeScreen:
    def __init__(self, mode_rate=144):
        self.mode_rate = mode_rate

    def get_display_id(self):
        return r"\\.\DISPLAY1"

    def get_mode(self):
        return SimpleNamespace(rate=self.mode_rate)


def test_non_windows_uses_pyglet_mode_without_importing_win32(monkeypatch):
    monkeypatch.setattr(refresh_rate.sys, "platform", "linux")

    def unexpected(_screen):
        raise AssertionError("Win32 query ran on a non-Windows platform")

    monkeypatch.setattr(refresh_rate, "_win32_refresh_rate", unexpected)

    assert refresh_rate.get_refresh_rate(FakeScreen(144)) == 144.0


def test_windows_prefers_rational_active_path_rate(monkeypatch):
    monkeypatch.setattr(refresh_rate.sys, "platform", "win32")
    monkeypatch.setattr(
        refresh_rate,
        "_win32_refresh_rate",
        lambda _screen: 120_000 / 1_001,
    )

    assert refresh_rate.get_refresh_rate(FakeScreen(119)) == pytest.approx(
        119.88011988011988
    )


@pytest.mark.parametrize("modern_rate", [None, 0, float("nan")])
def test_windows_falls_back_to_pyglet_mode(monkeypatch, modern_rate):
    monkeypatch.setattr(refresh_rate.sys, "platform", "win32")
    monkeypatch.setattr(
        refresh_rate,
        "_win32_refresh_rate",
        lambda _screen: modern_rate,
    )

    assert refresh_rate.get_refresh_rate(FakeScreen(119)) == 119.0


def test_windows_api_error_falls_back_to_pyglet_mode(monkeypatch):
    monkeypatch.setattr(refresh_rate.sys, "platform", "win32")

    def unavailable(_screen):
        raise OSError("QueryDisplayConfig unavailable")

    monkeypatch.setattr(refresh_rate, "_win32_refresh_rate", unavailable)

    assert refresh_rate.get_refresh_rate(FakeScreen(60)) == 60.0


@pytest.mark.parametrize("mode_rate", [None, 0, float("inf"), "invalid"])
def test_unusable_pyglet_mode_returns_none(monkeypatch, mode_rate):
    monkeypatch.setattr(refresh_rate.sys, "platform", "linux")

    assert refresh_rate.get_refresh_rate(FakeScreen(mode_rate)) is None


def test_rational_query_matches_the_requested_monitor():
    candidates = [
        (r"\\.\DISPLAY2", 144_000, 1_000),
        (r"\\.\DISPLAY1", 120_000, 1_001),
    ]

    assert win32.select_refresh_rate(
        r"\\.\display1",
        candidates,
    ) == pytest.approx(119.88011988011988)


@pytest.mark.parametrize(
    "candidates",
    [
        [],
        [(r"\\.\DISPLAY2", 120_000, 1_001)],
        [(r"\\.\DISPLAY1", 0, 1_001)],
        [(r"\\.\DISPLAY1", 120_000, 0)],
        [(r"\\.\DISPLAY1", 1, 2)],
    ],
)
def test_rational_query_rejects_missing_or_malformed_paths(candidates):
    assert (
        win32.select_refresh_rate(
            r"\\.\DISPLAY1",
            candidates,
        )
        is None
    )


def test_rational_api_failure_is_a_safe_none(monkeypatch):
    def unavailable():
        raise OSError("QueryDisplayConfig unavailable")

    monkeypatch.setattr(
        win32,
        "_query_active_refresh_rates",
        unavailable,
    )

    assert win32.query_refresh_rate(r"\\.\DISPLAY1") is None
