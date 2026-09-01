"""Clips bound to the game tick.

A `Clip` is DATA on the sheet -- `(assembly, duration_ticks)` pairs, authored
and immutable. An `Animation` is a CURSOR into one. The tests split along that
line, because conflating them is how a reader stops being sure which advances.

The claims worth pinning are the ones a running game would not obviously
reveal:

**`play()` is four verbs.** Start, cancel, interrupt and reissue. Reissuing
the frame you are on restarts its hold, which is hitstop -- and is a no-op if
the hold is not reset.

**Writing a pose by hand cancels the clip.** Two things writing one pose every
tick is a fight, and the explicit write has to win, or a game that decided
what to show gets overwritten on a frame boundary, invisibly.

**A finished one-shot HOLDS.** Blanking would put a hole in the screen on the
frame after every animation.
"""

from __future__ import annotations

import pytest

from blitspersecond.common import PACKAGE_ROOT
from blitspersecond.graphics.sprite import Animation, Sprite, SpriteSheet

ASSETS = PACKAGE_ROOT.parent / "assets"


@pytest.fixture
def sheet() -> SpriteSheet:
    """Fumiko: 32 assemblies and eight clips, `walk` among them."""
    return SpriteSheet(str(ASSETS / "fumiko" / "fumiko.sprite.json"))


@pytest.fixture
def fumiko(sheet: SpriteSheet) -> Sprite:
    return Sprite(sheet)


def _hold(sheet: SpriteSheet, clip: str, frame: int = 0) -> int:
    return sheet.clips[clip].frames[frame].duration_ticks


# -- the cursor, unbound ------------------------------------------------


def test_an_animation_can_be_built_and_driven_by_hand(sheet: SpriteSheet) -> None:
    """The unbound road is deliberate, not a leftover: a game driving several
    cursors over one sheet wants the cursor without a layer per cursor."""
    anim = Animation(sheet)
    anim.play("walk")
    assert anim.assembly == sheet.clips["walk"].frames[0].assembly
    for _ in range(_hold(sheet, "walk")):
        anim.update()
    assert anim.assembly == sheet.clips["walk"].frames[1].assembly


def test_a_frame_is_held_for_its_authored_ticks(sheet: SpriteSheet) -> None:
    """Integer ticks are the only unit. The milliseconds an authoring tool
    exports stop at the loader."""
    anim = Animation(sheet)
    anim.play("walk")
    ticks = _hold(sheet, "walk")
    for _ in range(ticks - 1):
        anim.update()
    assert anim.frame == 0
    anim.update()
    assert anim.frame == 1


def test_an_unknown_clip_says_what_the_sheet_carries(sheet: SpriteSheet) -> None:
    anim = Animation(sheet)
    with pytest.raises(KeyError) as excinfo:
        anim.play("moonwalk")
    assert "moonwalk" in str(excinfo.value)
    assert "walk" in str(excinfo.value)


# -- looping and one-shots ----------------------------------------------


def test_a_looping_clip_comes_round_and_is_never_done(sheet: SpriteSheet) -> None:
    anim = Animation(sheet)
    anim.play("walk", loop=True)
    frames = len(sheet.clips["walk"].frames)
    for _ in range(sum(f.duration_ticks for f in sheet.clips["walk"].frames)):
        anim.update()
    assert anim.frame == 0
    assert anim.done is False
    assert frames > 1


def test_a_one_shot_finishes_and_holds_its_last_frame(sheet: SpriteSheet) -> None:
    """Holding matters: a character whose attack ended is still standing in
    the last pose of it. Blanking would flash a hole every time."""
    clip = sheet.clips["attack"]
    anim = Animation(sheet)
    anim.play("attack", loop=False)
    for _ in range(sum(f.duration_ticks for f in clip.frames) + 20):
        anim.update()
    assert anim.done is True
    assert anim.frame == len(clip.frames) - 1
    assert anim.assembly == clip.frames[-1].assembly


def test_a_done_animation_stops_advancing(sheet: SpriteSheet) -> None:
    anim = Animation(sheet)
    anim.play("attack", loop=False)
    for _ in range(500):
        anim.update()
    frame = anim.frame
    anim.update()
    assert anim.frame == frame


# -- play() is four verbs -----------------------------------------------


def test_play_reissues_and_restarts(sheet: SpriteSheet) -> None:
    anim = Animation(sheet)
    anim.play("walk")
    for _ in range(_hold(sheet, "walk")):
        anim.update()
    assert anim.frame == 1
    anim.play("walk")
    assert anim.frame == 0


def test_reissuing_the_current_frame_restarts_its_hold(sheet: SpriteSheet) -> None:
    """This IS hitstop. If the hold were not reset it would be a no-op, and
    the mechanism would silently not exist."""
    ticks = _hold(sheet, "walk")
    anim = Animation(sheet)
    anim.play("walk")
    for _ in range(ticks - 1):
        anim.update()
    anim.play("walk", frame=0)  # reissue: the hold starts over
    for _ in range(ticks - 1):
        anim.update()
    assert anim.frame == 0
    anim.update()
    assert anim.frame == 1


def test_play_can_start_partway_in(sheet: SpriteSheet) -> None:
    anim = Animation(sheet)
    anim.play("walk", frame=2)
    assert anim.frame == 2
    assert anim.assembly == sheet.clips["walk"].frames[2].assembly


def test_a_frame_outside_the_clip_is_refused(sheet: SpriteSheet) -> None:
    anim = Animation(sheet)
    with pytest.raises(IndexError, match="frame 99"):
        anim.play("walk", frame=99)


def test_playing_a_one_shot_again_clears_done(sheet: SpriteSheet) -> None:
    anim = Animation(sheet)
    anim.play("attack", loop=False)
    for _ in range(500):
        anim.update()
    anim.play("attack", loop=False)
    assert anim.done is False


# -- bound to a sprite ---------------------------------------------------


def test_play_applies_its_first_frame_immediately(fumiko: Sprite) -> None:
    """A `play()` issued in this tick's callback must show THIS tick, or a
    frame of the previous pose leaks through."""
    fumiko.play("walk")
    assert fumiko.assembly == fumiko.sheet.clips["walk"].frames[0].assembly


def test_a_clip_advances_on_the_tick(fumiko: Sprite, sheet: SpriteSheet) -> None:
    """`_advance` is the `Layer` hook the loop calls once per fixed tick."""
    fumiko.play("walk")
    for _ in range(_hold(sheet, "walk")):
        fumiko._advance()
    assert fumiko.assembly == sheet.clips["walk"].frames[1].assembly


def test_playing_reports_the_clip(fumiko: Sprite) -> None:
    assert fumiko.playing is None
    fumiko.play("walk")
    assert fumiko.playing == "walk"


def test_a_sprite_reports_a_finished_one_shot(fumiko: Sprite, sheet) -> None:
    fumiko.play("attack", loop=False)
    assert fumiko.done is False
    for _ in range(sum(f.duration_ticks for f in sheet.clips["attack"].frames)):
        fumiko._advance()
    assert fumiko.done is True


def test_writing_a_pose_by_hand_takes_the_sprite_off_its_clip(
    fumiko: Sprite, sheet: SpriteSheet
) -> None:
    """The explicit write wins. Otherwise a game that decided what to show
    gets overwritten by the sequencer a moment later, on a frame boundary."""
    fumiko.play("walk")
    fumiko.assembly = "idle-0"
    assert fumiko.playing is None
    for _ in range(50):
        fumiko._advance()
    assert fumiko.assembly == "idle-0"


def test_reaching_the_primary_through_the_index_cancels_too(
    fumiko: Sprite,
) -> None:
    """Same state, so the same rule -- `sprite[0]` must not be a way round
    it."""
    fumiko.play("walk")
    fumiko.primary.assembly = "idle-0"
    assert fumiko.playing is None


def test_clearing_the_pose_cancels_as_well(fumiko: Sprite) -> None:
    fumiko.play("walk")
    fumiko.assembly = None
    assert fumiko.playing is None


def test_advancing_does_not_cancel_itself(fumiko: Sprite) -> None:
    """The sequencer writes the pose every tick, and that write must NOT take
    the sprite off its own clip -- which is why `_apply` does not go through
    the setter."""
    fumiko.play("walk")
    for _ in range(200):
        fumiko._advance()
    assert fumiko.playing == "walk"


def test_a_sprite_with_no_clip_advances_for_free(fumiko: Sprite) -> None:
    """One `is None` test, which is what makes a swarm that animates nothing
    cost nothing."""
    fumiko.assembly = "idle-0"
    for _ in range(10):
        fumiko._advance()
    assert fumiko.assembly == "idle-0"
    assert fumiko.playing is None


def test_presentations_are_never_played(fumiko: Sprite, sheet) -> None:
    """The clip is the SPRITE's. A ghost presents a snapshot, and a snapshot
    that animated itself would not be one."""
    ghost = fumiko.before.append()
    assert not hasattr(ghost, "play")
    fumiko.play("walk")
    ghost.assembly = "idle-0"
    for _ in range(_hold(sheet, "walk") * 3):
        fumiko._advance()
    assert ghost.assembly == "idle-0"


# -- the layer hook ------------------------------------------------------


def test_every_layer_has_a_real_advance_method() -> None:
    """A real method, not a `getattr` probe -- so it is typed, greppable, and
    cannot be satisfied by accident by an unrelated attribute."""
    from blitspersecond.graphics.console import CharSet, Console
    from blitspersecond.graphics.internal.layer import Layer

    assert "_advance" in vars(Layer)
    console = Console(CharSet.ASCII8X12)
    console._advance()  # a no-op, and must not raise


def test_layers_advance_walks_every_layer(fumiko: Sprite, sheet) -> None:
    from blitspersecond.graphics.console import CharSet, Console
    from blitspersecond.graphics.internal.layers import Layers

    layers = Layers()
    layers.add(Console(CharSet.ASCII8X12))
    layers.add(fumiko)
    fumiko.play("walk")
    for _ in range(_hold(sheet, "walk")):
        layers._advance()
    assert fumiko.assembly == sheet.clips["walk"].frames[1].assembly
