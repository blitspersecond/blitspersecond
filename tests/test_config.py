"""What Config is left with after the 2026-08-25 audit: one field,
`fullscreen`, read from `BPS_FULLSCREEN` at construction.

Everything else that used to live here -- the fixed 640x360@60 shape,
window scale, tick rate, tile sizing -- was either a constant misfiled as
config, a value that gets MEASURED rather than set, or state belonging to a
pillar that has not been ported back yet. See the comment block at the top
of blitspersecond/system/config/config.py for the full audit.

The strict parsing behind `fullscreen` is `common.environment_flag`, tested
on its own in test_environment.py.
"""

from blitspersecond.system import Config


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
