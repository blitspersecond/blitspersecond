"""Animation, headless: pure counter arithmetic over the sheet's carried
clip data, provable to the exact tick.

The fixture's "cycle" clip is solid(2) -> dot(1) -> blank(3): frame
boundaries at cumulative ticks 2 and 3, the loop/done boundary at 6.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from blitspersecond.graphics import Animation, SpriteSheet
from blitspersecond.resources import ResourceManager

from sprite_fixtures import build_known_sheet



@pytest.fixture()
def sheet(tmp_path):
    return build_known_sheet(tmp_path)


def test_initial_state(sheet):
    anim = Animation(sheet, "cycle")
    assert anim.name == "cycle"
    assert anim.frame == 0
    assert anim.assembly == "solid"
    assert anim.loop is True
    assert anim.done is False


def test_tick_exact_holds(sheet):
    anim = Animation(sheet, "cycle")
    seen = [anim.assembly]
    for _ in range(6):
        anim.update()
        seen.append(anim.assembly)
    # Reads at ticks 0..6: solid holds 2, dot 1, blank 3, then the wrap.
    assert seen == ["solid", "solid", "dot", "blank", "blank", "blank", "solid"]


def test_loop_forever(sheet):
    anim = Animation(sheet, "cycle")
    for _ in range(60):  # ten full cycles
        anim.update()
    assert anim.done is False
    assert anim.frame == 0  # 60 ticks = exactly 10 loops of 6


def test_one_shot_done_holds_last_frame(sheet):
    anim = Animation(sheet, "cycle", loop=False)
    for _ in range(6):
        anim.update()
    assert anim.done is True
    assert anim.assembly == "blank"  # holds the last frame
    frame = anim.frame
    anim.update()  # further ticks are no-ops
    anim.update()
    assert anim.frame == frame
    assert anim.assembly == "blank"


def test_done_fires_exactly_when_last_hold_expires(sheet):
    anim = Animation(sheet, "cycle", loop=False)
    for tick in range(1, 7):
        anim.update()
        assert anim.done is (tick >= 6), f"tick {tick}"


def test_set_frame_jumps_and_revives(sheet):
    anim = Animation(sheet, "cycle", loop=False)
    anim.frame = 2
    assert anim.assembly == "blank"
    for _ in range(3):
        anim.update()
    assert anim.done is True
    anim.frame = 0  # setting the frame revives a finished one-shot
    assert anim.done is False
    assert anim.assembly == "solid"
    with pytest.raises(IndexError):
        anim.frame = 3


def test_play_is_the_cancel_primitive(sheet):
    anim = Animation(sheet, "cycle")
    anim.update()  # mid-hold
    anim.play("cycle", frame=1, loop=False)  # abandon, restart at the dot
    assert anim.frame == 1
    assert anim.assembly == "dot"
    assert anim.loop is False
    anim.play("cycle")  # loop=None keeps the current (False) setting
    assert anim.loop is False
    with pytest.raises(KeyError):
        anim.play("nope")
    with pytest.raises(IndexError):
        anim.play("cycle", frame=99)


def test_loop_toggle_mid_flight(sheet):
    anim = Animation(sheet, "cycle", loop=False)
    for _ in range(6):
        anim.update()
    assert anim.done is True
    anim.loop = True  # turning looping on revives it...
    assert anim.done is False
    assert anim.frame == 2  # ...continuing FROM the held last frame,
    for _ in range(3):  # which serves a fresh full hold
        anim.update()
    assert anim.frame == 0  # before the wrap

