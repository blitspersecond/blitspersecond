# Audited 2026-08-25: Display (width/height/fps/scale), Input.poll_rate and
# Tiles are gone. 640x360@60 is the engine's fixed shape, not a setting --
# README calls it "a fixed 640x360 internal screen", so a Config field
# implying it varies was always going to mislead a reader. `scale` is
# measured off the window once one exists, never chosen. `poll_rate` named
# the tick rate, which is not something you poll for. `Tiles` had no
# identified caller and no requirement behind it -- it belongs with the tile
# pillar's own port, not here ahead of it. `spin_pacing` is deferred rather
# than cut: whether it is still needed under the newer deadline pacing is
# undecided until the window/display loop is back, so it returns with that
# piece, not before.
#
# `fullscreen` survives because it is the one field that is actually
# user-set input, self-contained (an env var, nothing measured or derived),
# and already has its own contract pinned in tests/test_config.py.

from blitspersecond.common import SingletonMeta, environment_flag


class Config(metaclass=SingletonMeta):
    def __init__(self):
        self._fullscreen = environment_flag("BPS_FULLSCREEN")

    @property
    def fullscreen(self) -> bool:
        """Borderless at the current desktop mode; never switches display
        modes. Presentation uses the largest centred integer scale that
        fits."""
        return self._fullscreen
