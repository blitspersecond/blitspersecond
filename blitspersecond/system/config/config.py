# Audited 2026-08-25: Display (width/height/scale), Input.poll_rate and Tiles
# are gone. 640x360 is the engine's fixed shape, not a setting -- README calls
# it "a fixed 640x360 internal screen", so a Config field implying it varies
# was always going to mislead a reader. `scale` is measured off the window
# once one exists, never chosen. `poll_rate` named the tick rate, which is not
# something you poll for. `Tiles` had no identified caller and no requirement
# behind it -- it belongs with the tile pillar's own port, not here ahead of
# it. `spin_pacing` is deferred rather than cut: whether it is still needed
# under the newer deadline pacing is undecided until the window/display loop
# is back, so it returns with that piece, not before.
#
# AMENDED 2026-08-27: `fps` was cut above as part of "the engine's fixed
# shape" and that half no longer holds. The tick rate is an AUTHORING choice
# -- 15, 20, 30, 40 or 60 -- because every tuned constant in a game is counted
# in frames, so it is the author's to set and the engine's to hold steady, not
# the engine's to fix at 60. It is currently a `_FRAMERATE` constant in
# `blitspersecond.py`, and Config is where it belongs. 640x360 is unaffected:
# that really is fixed. See the board cards "Let the game author declare its
# tick rate" and "Stand up file-backed config".
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
