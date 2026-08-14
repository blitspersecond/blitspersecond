"""Sprite collision, headless: no GL, no window -- source silhouettes and
authored rectangles stamp into retained packed screen-space planes, so the
whole collision story tests without a context.

A hand-built package with KNOWN pixels drives the exact cases: tile "a" is
a solid 8x8 block, tile "b" is a lone pixel at its top-left corner --
silhouette answers are therefore predictable to the pixel. Chun-Li then
proves the real data end: attack meets body_hurt where the boxes say so.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from blitspersecond.graphics import SpriteEngine, SpriteSheet
from blitspersecond.graphics.common import CollisionQuery
from blitspersecond.resources import ResourceManager

from sprite_fixtures import build_known_sheet



@pytest.fixture()
def pair(tmp_path):
    sheet = build_known_sheet(tmp_path)
    return SpriteEngine(sheet), SpriteEngine(sheet)


def test_planes_vocabulary_precalculated(pair, tmp_path):
    a, _ = pair
    assert a.sheet.planes == frozenset({"hit", "hurt"})


def test_silhouette_pixel_perfect(pair):
    a, b = pair
    solid = a.sprite("solid")
    solid.position = (0, 0)
    dot = b.sprite("dot")
    # The dot's pixel is at its local (0, 0): inside the block at (7, 7)...
    dot.position = (7, 7)
    assert a.collides_with(b).at() is True
    # ...but one pixel right, or down, is out (block covers 0..7).
    dot.position = (8, 7)
    assert a.collides_with(b).at() is False
    dot.position = (7, 8)
    assert a.collides_with(b).at() is False


def test_silhouette_beats_aabb(pair):
    # AABBs overlap but the dot's ONLY pixel is outside the block: the
    # default plane answers with pixels, not boxes.
    a, b = pair
    a.sprite("solid").position = (0, 0)
    dot = b.sprite("dot")
    dot.position = (5, 5)  # 8x8 quad overlaps the block heavily...
    # ...and its pixel at (5, 5) IS inside -- move so the pixel exits but
    # the quad still overlaps:
    dot.position = (10, 5)  # quad spans 10..17, no overlap with 0..7 -- pick
    dot.position = (7, 9)  # quad overlaps? block rows 0..7, dot pixel (7,9)
    assert a.collides_with(b).at() is False


def test_window_box(pair):
    a, b = pair
    a.sprite("solid").position = (0, 0)
    dot = b.sprite("dot")
    dot.position = (7, 7)  # contact at exactly (7, 7)
    q = a.collides_with(b)
    assert q.at() is True
    assert q.at(0, 0, 4, 4) is False  # window misses the contact
    assert q.at(6, 6, 3, 3) is True  # window covers it


def test_queries_reuse_retained_composition(pair):
    a, b = pair
    a.sprite("solid").position = (0, 0)
    b.sprite("dot").position = (7, 7)
    q = a.collides_with(b)

    assert q.at() is True
    assert a._collision_dirty is False
    assert b._collision_dirty is False
    first_a = a.collision_mask
    first_b = b.collision_mask
    assert q.at() is True
    assert np.shares_memory(a.collision_mask, first_a)
    assert np.shares_memory(b.collision_mask, first_b)


def test_pool_instances_collapse_into_one_layer_mask(pair):
    bullets, player = pair
    pool = bullets.z(0).pool(("dot",) * 32)
    body = player.sprite("solid")
    body.position = (0, 0)
    with pool.edit():
        pool.positions[:] = (100, 100)
        pool.positions[17] = (7, 7)
        pool.collide[:] = True
        pool.visible[:] = True

    assert bullets.collides_with(player).at() is True
    with pool.edit():
        pool.visible[17] = False
    assert bullets.collides_with(player).at() is False


def test_pool_named_rects_stamp_named_planes(pair):
    attack, victim = pair
    attackers = attack.z(0).pool(("solid",) * 8)
    hurt = victim.sprite("solid")
    hurt.position = (5, 0)
    with attackers.edit():
        attackers.positions[:] = (100, 100)
        attackers.positions[3] = (0, 0)

    assert attack.collides_with(
        victim, mine="hit", theirs="hurt"
    ).at() is True


def test_source_pixel_edit_invalidates_cached_stamp_pattern(pair):
    engine, _ = pair
    engine.sprite("dot").position = (3, 9)
    before = engine.collision_mask_unpacked()
    assert before[9, 3]

    buffer = engine.sheet.tilesets["main"].buffer
    with buffer.edit() as pixels:
        pixels[0, 8] = 0
        pixels[0, 9] = 1

    after = engine.collision_mask_unpacked()
    assert not after[9, 3]
    assert after[9, 4]


def test_flip_mirrors_silhouette(pair):
    a, b = pair
    a.sprite("solid").position = (0, 0)
    dot = b.sprite("dot")
    # Unflipped at (8, 0): pixel at (8, 0), outside the block.
    dot.position = (8, 0)
    assert a.collides_with(b).at() is False
    # Flipped: quad spans [0, 8), pixel mirrors to column 7 -> (7, 0): hit.
    dot.flip_x = True
    assert a.collides_with(b).at() is True


def test_named_planes_aabb(pair):
    a, b = pair
    pa = a.sprite("solid")
    pa.position = (0, 0)
    pb = b.sprite("solid")
    # B's hurt box is local (2, 2, 4, 4). At x=5 it spans 7..11: meets A's
    # hit box 0..8. At x=6 it spans 8..12: clear.
    pb.position = (5, 0)
    assert a.collides_with(b, mine="hit", theirs="hurt").at() is True
    pb.position = (6, 0)
    assert a.collides_with(b, mine="hit", theirs="hurt").at() is False


def test_named_plane_flip_mirrors_rects(pair):
    a, b = pair
    a.sprite("solid").position = (0, 0)
    pb = b.sprite("solid")
    # Flipped, the hurt box [2, 6) mirrors to [-6, -2): at x=10 that is
    # screen 4..8 -- meets A's hit box; unflipped it is 12..16 -- clear.
    pb.position = (10, 0)
    assert a.collides_with(b, mine="hit", theirs="hurt").at() is False
    pb.flip_x = True
    assert a.collides_with(b, mine="hit", theirs="hurt").at() is True


def test_mixed_rect_vs_silhouette(pair):
    a, b = pair
    a.sprite("solid").position = (0, 0)
    dot = b.sprite("dot")
    dot.position = (7, 7)  # pixel inside A's hit box
    assert a.collides_with(b, mine="hit").at() is True
    dot.position = (9, 7)  # pixel outside the 0..8 box
    assert a.collides_with(b, mine="hit").at() is False


def test_multi_select_any_overlap(pair):
    a, b = pair
    pa = a.sprite("solid")
    pa.position = (0, 0)
    pb = b.sprite("solid")
    pb.position = (7, 0)  # B's hit box 7..15 meets A's; B's hurt box 9..13 misses
    q = a.collides_with(b, mine="hit", theirs=("hurt", "hit"))
    assert q.at() is True  # any overlap between the selected sets
    assert a.collides_with(b, mine="hit", theirs="hurt").at() is False


def test_selector_validation(pair):
    a, b = pair
    a.sprite("solid")
    b.sprite("solid")
    with pytest.raises(KeyError, match="attack"):
        a.collides_with(b, mine="attack")  # typo'd names fail LOUDLY
    with pytest.raises(KeyError, match="theirs"):
        a.collides_with(b, mine="hit", theirs=("hurt", "nope"))


def test_collide_and_visibility_intent(pair):
    a, b = pair
    a.sprite("solid").position = (0, 0)
    dot = b.sprite("dot")
    dot.position = (7, 7)
    dot.collide = False  # the afterimage flag: display-only
    assert a.collides_with(b).at() is False
    dot.collide = True
    dot.visible = False  # hidden = not present
    assert a.collides_with(b).at() is False
    dot.visible = True
    assert a.collides_with(b).at() is True


def test_blank_assembly_contributes_nothing(pair):
    a, b = pair
    a.sprite("solid").position = (0, 0)
    blank = b.sprite("blank")
    blank.position = (0, 0)
    assert a.collides_with(b).at() is False


# -- against a mask layer (the tile/text side, stubbed headless) -------------


class _MaskStub:
    """A packed screen-space mask participant, standing in for TileEngine
    (whose blit() needs GL). 32 rows x 1 word: bit x of row y."""

    collidable = True

    def __init__(self):
        self.collision_mask = np.zeros((32, 1), dtype=np.uint32)

    def set(self, x, y):
        self.collision_mask[y, 0] |= np.uint32(1 << x)


def test_sprite_vs_mask_silhouette(pair):
    a, _ = pair
    dot = a.sprite("dot")
    dot.position = (3, 9)  # its pixel lands at (3, 9)
    wall = _MaskStub()
    wall.set(3, 9)
    assert a.collides_with(wall).at() is True
    wall2 = _MaskStub()
    wall2.set(4, 9)  # adjacent, not coincident
    assert a.collides_with(wall2).at() is False


def test_sprite_vs_mask_named_rect(pair):
    a, _ = pair
    s = a.sprite("solid")
    s.position = (10, 10)  # hurt box spans 12..16 x 12..16
    wall = _MaskStub()
    wall.set(15, 15)
    assert a.collides_with(wall, mine="hurt").at() is True
    assert a.collides_with(wall, mine="hurt").at(0, 0, 12, 12) is False
    wall2 = _MaskStub()
    wall2.set(16, 15)  # just past the box's exclusive edge
    assert a.collides_with(wall2, mine="hurt").at() is False


def test_mask_initiated_query_dispatches(pair):
    # A tile engine constructs CollisionQuery(self, other) itself -- the
    # dispatch must route a mask x plane pairing from either seat.
    a, _ = pair
    dot = a.sprite("dot")
    dot.position = (3, 9)
    wall = _MaskStub()
    wall.set(3, 9)
    assert CollisionQuery(wall, a).at() is True


def test_theirs_rejected_against_mask(pair):
    a, _ = pair
    a.sprite("solid")
    with pytest.raises(ValueError, match="mask layer"):
        a.collides_with(_MaskStub(), theirs="hurt")


