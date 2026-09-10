"""`Animation`: the tick sequencer over a sheet's carried clip data.

    chun.play("walk")

A clip advances itself on the fixed tick from there, and nothing needs calling
per frame to keep it running.

**A `Clip` is DATA on the sheet** -- a named sequence of `(assembly,
duration_ticks)`, authored and immutable. **An `Animation` is a CURSOR** into
one: which frame, how long it has been held, whether it has finished. Calling
both "animation" is how a reader stops being sure which one advances.

`duration_ticks` is an integer count of fixed simulation ticks. Milliseconds
never enter -- a clip advances on the fixed-step loop or not at all.

Constructing one directly is a real road, not a leftover: it gives a cursor
nothing advances for you, which is what a game driving several over one sheet
wants without a layer per cursor.

Why `play()` is the only verb, what hitstop is, and where this runs in the
frame: `bps/reference/graphics/sprites.md`, "Animation".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    from .sheet import Frame, SpriteSheet


class Animation:
    """A cursor into one of a sheet's clips.

        anim = Animation(sheet)
        anim.play("walk")
        anim.update()               # one tick
        sprite.assembly = anim.assembly

    Bound to a sprite by `Sprite.play`, in which case the engine calls
    `update()` once per fixed tick and applies the result -- see this
    module's header for the unbound road.
    """

    __slots__ = ("_sheet", "_name", "_frames", "_frame", "_hold", "_loop", "_done")

    def __init__(self, sheet: "SpriteSheet", loop: bool = True) -> None:
        self._sheet = sheet
        self._loop = bool(loop)
        self._name = ""
        self._frames: Tuple["Frame", ...] = ()
        self._frame = 0
        self._hold = 0
        self._done = False

    def play(
        self, name: str, frame: int = 0, loop: Optional[bool] = None
    ) -> None:
        """Start a clip -- and cancel, interrupt or reissue whatever was
        playing, which are the same act.

        `frame` starts partway in; reissuing the frame you are already on
        restarts its hold, which is hitstop. `loop` defaults to whatever this
        cursor was last told.
        """
        clip = self._sheet.clips.get(name)
        if clip is None:
            known = sorted(self._sheet.clips)
            raise KeyError(
                f"no clip named {name!r} on this sheet -- it carries "
                f"{known if known else 'none at all'}"
            )
        if loop is not None:
            self._loop = bool(loop)
        self._name = name
        self._frames = clip.frames
        self._done = False
        self.frame = frame

    def update(self) -> None:
        """One tick of playback.

        A finished one-shot HOLDS its last frame rather than clearing: a
        character whose attack ended is still standing in the last pose of it
        until something says otherwise, and blanking here would put a hole in
        the screen on the frame after every animation.
        """
        if self._done or not self._frames:
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
    def assembly(self) -> Optional[str]:
        """The pose this cursor is currently on, or None if nothing is
        playing."""
        if not self._frames:
            return None
        return self._frames[self._frame].assembly

    @property
    def name(self) -> str:
        """The clip being played, or `""` if none is."""
        return self._name

    @property
    def frame(self) -> int:
        """Which frame of the clip, counting from 0."""
        return self._frame

    @frame.setter
    def frame(self, value: int) -> None:
        """Jump to a frame, restarting its hold and clearing `done`.

        Restarting the hold is what makes reissuing the current frame mean
        "stay here longer" rather than being a no-op.
        """
        value = int(value)
        if not 0 <= value < len(self._frames):
            raise IndexError(
                f"frame {value} is not in {self._name!r}, which has "
                f"{len(self._frames)}"
            )
        self._frame = value
        self._hold = 0
        self._done = False

    @property
    def loop(self) -> bool:
        """Whether this clip restarts when it reaches the end."""
        return self._loop

    @property
    def done(self) -> bool:
        """True once a one-shot has reached and finished its last frame.

        Never true for a looping clip, which has no end to reach.
        """
        return self._done

    def __repr__(self) -> str:
        where = f"{self._frame + 1}/{len(self._frames)}" if self._frames else "-"
        state = " done" if self._done else ""
        return f"<Animation {self._name or 'nothing'!r} {where}{state}>"
