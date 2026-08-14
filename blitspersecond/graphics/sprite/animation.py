from typing import Optional

from .sprite_sheet import SpriteSheet


class Animation:
    """The tick sequencer: play one named animation from the sheet's carried
    data -- an internal frame counter, integer tick holds, loop never or
    forever. Nothing else.

        anim = Animation(sheet, "stance")            # loops by default
        ...
        anim.update()                                # once per game tick
        sprite.assembly = anim.assembly

    The whole per-tick contract is those two lines, and the split is
    deliberate: the Animation owns WHICH frame is current, the game owns
    what to do with it (and owns not calling update() at all -- hitstop is
    exactly that, no API needed). A cancel is `anim.play("hp")` -- abandon
    this sequence, start that one; a one-shot reports `done` and holds its
    last frame until told otherwise. Branching, transitions, predicates,
    root motion, effect spawning: game code, above this line, per
    docs/SPRITES.md. Time is integer ticks from the fixed-step loop --
    never wall-clock.
    """

    def __init__(
        self,
        sheet: SpriteSheet,
        name: str,
        frame: int = 0,
        loop: bool = True,
    ) -> None:
        self._animations = sheet.animations
        self._loop = bool(loop)
        self._name = ""
        self._frames = ()
        self._frame = 0
        self._hold = 0
        self._done = False
        self.play(name, frame)

    def play(self, name: str, frame: int = 0, loop: Optional[bool] = None) -> None:
        """Start a (possibly different) animation from `frame`. `loop`
        None keeps the current setting. This IS the cancel primitive:
        whatever was playing is simply abandoned."""
        frames = self._animations.get(name)
        if frames is None:
            raise KeyError(f"unknown animation {name!r}")
        self._name = name
        self._frames = frames.frames
        if loop is not None:
            self._loop = bool(loop)
        self.frame = frame

    def update(self) -> None:
        """Advance one game tick. A finished one-shot holds its last frame
        and ignores further ticks."""
        if self._done:
            return
        self._hold += 1
        if self._hold < self._frames[self._frame].duration_ticks:
            return
        self._hold = 0
        if self._frame + 1 < len(self._frames):
            self._frame += 1
        elif self._loop:
            self._frame = 0
        else:
            self._done = True

    @property
    def assembly(self) -> str:
        """The current frame's assembly name -- what the sprite presents."""
        return self._frames[self._frame].assembly

    @property
    def name(self) -> str:
        return self._name

    @property
    def frame(self) -> int:
        """The current frame index. Settable -- jumping resets the hold
        counter (and revives a finished one-shot)."""
        return self._frame

    @frame.setter
    def frame(self, value: int) -> None:
        value = int(value)
        if not 0 <= value < len(self._frames):
            raise IndexError(
                f"frame {value} out of range ({self._name!r} has "
                f"{len(self._frames)})"
            )
        self._frame = value
        self._hold = 0
        self._done = False

    @property
    def loop(self) -> bool:
        return self._loop

    @loop.setter
    def loop(self, value: bool) -> None:
        self._loop = bool(value)
        if self._loop:
            self._done = False

    @property
    def done(self) -> bool:
        """True once a non-looping run has served its last frame's full
        hold. A looping animation is never done."""
        return self._done
