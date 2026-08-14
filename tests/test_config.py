import pytest

from blitspersecond.system.config.config import _environment_flag


@pytest.mark.parametrize(("raw", "expected"), [("1", True), ("yes", True), ("0", False)])
def test_display_environment_flags_are_strict(monkeypatch, raw, expected):
    monkeypatch.setenv("BPS_FULLSCREEN", raw)

    assert _environment_flag("BPS_FULLSCREEN") is expected


def test_invalid_display_environment_flag_is_rejected(monkeypatch):
    monkeypatch.setenv("BPS_FULLSCREEN", "sometimes")

    with pytest.raises(ValueError, match="BPS_FULLSCREEN"):
        _environment_flag("BPS_FULLSCREEN")
