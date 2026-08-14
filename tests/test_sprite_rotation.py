"""Sprite rotation: python -m pytest tests/test_sprite_rotation.py

Rotation inherits the tile flip bits' D-move per instance: UV corners
cycle, mask views turn, offsets ride the ONE orientation formula -- and
because representation is implementation, these tests assert through
collision surfaces: where the query says the pixel is IS where it draws.

Geometry ground truth (dot = one solid pixel at local (0, 0), anchor at
screen (100, 100)): each clockwise quarter-turn walks that pixel around
the anchor corner: r0 (100,100), r1 (99,100), r2 (99,99), r3 (100,99).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blitspersecond.graphics import SpriteEngine

from sprite_fixtures import build_known_sheet


@pytest.fixture()
def pair(tmp_path):
    sheet = build_known_sheet(tmp_path)
    return SpriteEngine(sheet), SpriteEngine(sheet)


def probe(engine, other, x, y):
    """Is any of `engine`'s silhouette on the single pixel (x, y)? The
    other engine's dot is the 1px probe the query needs a partner for."""
    dot = other.sprites[0] if other.sprites else other.sprite("dot")
    dot.position = (x, y)
    return engine.collides_with(other).at()


def test_rotation_walks_the_dot_around_the_anchor(pair):
    a, b = pair
    dot = a.sprite("dot")
    dot.position = (100, 100)
    expected = {0: (100, 100), 1: (99, 100), 2: (99, 99), 3: (100, 99)}
    for rot, (ex, ey) in expected.items():
        dot.rotation = rot
        assert probe(a, b, ex, ey), f"r{rot}: pixel not at {(ex, ey)}"
        for wrong_rot, (wx, wy) in expected.items():
            if wrong_rot != rot:
                assert not probe(a, b, wx, wy), (
                    f"r{rot}: pixel ALSO at r{wrong_rot}'s corner"
                )


def test_rotation_wraps_and_accepts_negatives(pair):
    a, _ = pair
    dot = a.sprite("dot")
    dot.rotation = 5
    assert dot.rotation == 1
    dot.rotation = -1
    assert dot.rotation == 3


def test_flip_then_rotate_composes(pair):
    a, b = pair
    dot = a.sprite("dot")
    dot.position = (100, 100)
    dot.flip_x = True  # mirror puts the pixel at (99, 100)
    assert probe(a, b, 99, 100)
    dot.rotation = 1  # ...then a quarter-turn carries it to (99, 99)
    assert probe(a, b, 99, 99)
    assert not probe(a, b, 99, 100)


def test_offset_part_orbits(pair):
    # dot-off's pixel sits at local (4, 0): rotation must move the PART,
    # not just spin pixels in place -- r1 carries local (4,0) to (-1, 4).
    a, b = pair
    dot = a.sprite("dot-off")
    dot.position = (100, 100)
    assert probe(a, b, 104, 100)
    dot.rotation = 1
    assert probe(a, b, 99, 104)
    assert not probe(a, b, 104, 100)


def test_named_rect_planes_rotate(pair):
    # solid's hurt plane is local (2, 2, 4, 4); a quarter-turn about the
    # anchor carries it to local (-6, 2, 4, 4) -> screen (94, 102).
    a, b = pair
    s = a.sprite("solid")
    s.position = (100, 100)
    s.rotation = 1
    partner = b.sprite("solid")
    partner.position = (90, 95)  # its hit plane spans (90..98, 95..103)
    q = a.collides_with(b, mine="hurt", theirs="hit")
    assert q.at(94, 102, 1, 1)  # inside the rotated hurt box
    assert not q.at(98, 102, 1, 1)  # one column past its right edge
    assert not q.at(94, 106, 1, 1)  # one row below its bottom edge


def test_rotation_marks_dirty(pair):
    a, _ = pair
    s = a.sprite("dot")
    a._dirty = False
    s.rotation = 2
    assert a._dirty


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
