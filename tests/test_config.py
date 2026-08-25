"""What Config is left with after the 2026-08-25 audit: one field,
`fullscreen`, read from `BPS_FULLSCREEN` at construction.

Everything else that used to live here -- the fixed 640x360@60 shape,
window scale, tick rate, tile sizing -- was either a constant misfiled as
config, a value that gets MEASURED rather than set, or state belonging to a
pillar that has not been ported back yet. See the comment block at the top
of blitspersecond/system/config/config.py for the full audit.
"""

import pytest

from blitspersecond.system import Config
from blitspersecond.system.config.config import _environment_flag


class TestEnvironmentFlag:
    """`_environment_flag` is tested directly, not just through Config,
    because `Config` reads it once at construction -- these edge cases (an
    absent var, a strict spelling, a rejected typo) would otherwise only be
    reachable by setting the environment before constructing one."""

    def test_absent_variable_is_the_default(self, monkeypatch):
        monkeypatch.delenv("BPS_PROBE", raising=False)
        assert _environment_flag("BPS_PROBE") is False
        assert _environment_flag("BPS_PROBE", default=True) is True

    @pytest.mark.parametrize("raw", ["1", "true", "True", "yes", "on"])
    def test_truthy_spellings(self, monkeypatch, raw):
        monkeypatch.setenv("BPS_PROBE", raw)
        assert _environment_flag("BPS_PROBE") is True

    @pytest.mark.parametrize("raw", ["0", "false", "False", "no", "off"])
    def test_falsy_spellings(self, monkeypatch, raw):
        monkeypatch.setenv("BPS_PROBE", raw)
        assert _environment_flag("BPS_PROBE") is False

    def test_empty_string_is_the_default_not_an_error(self, monkeypatch):
        """`os.environ.get` and "variable unset" both hand back falsy
        values, so an exported-but-empty var must not raise."""
        monkeypatch.setenv("BPS_PROBE", "")
        assert _environment_flag("BPS_PROBE") is False

    def test_an_unrecognised_spelling_is_rejected(self, monkeypatch):
        """Strict on purpose: a flag silently defaulting on a typo is a
        harder bug to notice than one that refuses to start."""
        monkeypatch.setenv("BPS_PROBE", "sometimes")
        with pytest.raises(ValueError, match="BPS_PROBE"):
            _environment_flag("BPS_PROBE")


class TestConfig:
    """Config is a plain class -- `BlitsPerSecond` is the singleton, and
    `bps.config` is the intended way to reach one (see test_engine.py). A
    bare `Config()` here is just a normal, freshly-constructed object."""

    def test_fullscreen_defaults_to_false(self, monkeypatch):
        monkeypatch.delenv("BPS_FULLSCREEN", raising=False)
        assert Config().fullscreen is False

    def test_fullscreen_reads_the_environment_at_construction(self, monkeypatch):
        monkeypatch.setenv("BPS_FULLSCREEN", "1")
        assert Config().fullscreen is True
