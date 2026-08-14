"""PlaneOverlay, headless: the canvas is a numpy buffer and the surfaces
need no GL, so the painted pixels assert directly.

Uses the shared known-pixel package: "solid" (8x8 block, hit + hurt boxes)
and "dot" (one pixel, no named planes) make every painted byte predictable.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from blitspersecond.graphics.sprite import PlaneOverlay, SpriteEngine

from sprite_fixtures import build_known_sheet

@pytest.fixture()
def engine(tmp_path):
    return SpriteEngine(build_known_sheet(tmp_path))


def alpha(overlay, row, column):
    index = overlay.buffer.pixels[row, column]
    return int(overlay.buffer.palette[index][3])


def test_silhouette_tint(engine):
    dot = engine.sprite("dot-off")  # pixel at anchor+4: clear of the cross
    dot.position = (40, 40)
    overlay = PlaneOverlay(engine, size=(64, 64))
    overlay.update()
    px = overlay.buffer.pixels
    assert px[40, 44] != 0  # the lone pixel, tinted
    assert 0 < alpha(overlay, 40, 44) < 255
    assert px[40, 45] == 0  # its transparent neighbour: nothing


def test_named_planes_fill_and_edge(engine):
    s = engine.sprite("solid")
    s.position = (20, 20)
    overlay = PlaneOverlay(engine, size=(64, 64))
    overlay.update()
    px = overlay.buffer.pixels
    # hit box spans 20..28: top edge solid, interior translucent fill
    # (col 27 is the right EDGE; 26 is the last interior column).
    assert alpha(overlay, 20, 26) == 255
    assert 0 < alpha(overlay, 21, 26) < 255
    # hurt box (22..26) painted after hit (sorted order): it owns its region.
    assert 0 < alpha(overlay, 24, 24) < 255
    assert alpha(overlay, 22, 24) == 255
    # anchor cross painted last, at the instance position.
    assert px[20, 20] == 2


def test_flip_mirrors_the_painting(engine):
    dot = engine.sprite("dot-off")
    dot.position = (48, 40)
    # Unflipped the pixel sits at 48+4=52; flipped, local [4, 12) mirrors to
    # [-12, -4) and the pixel to column 43 -- clear of the cross both ways.
    overlay = PlaneOverlay(engine, size=(64, 64))
    overlay.update()
    px = overlay.buffer.pixels
    assert 0 < alpha(overlay, 40, 52) < 255
    dot.flip_x = True
    overlay.update()
    assert 0 < alpha(overlay, 40, 43) < 255
    assert px[40, 52] == 0


def test_collide_false_paints_no_geometry(engine):
    s = engine.sprite("solid")
    s.position = (20, 20)
    s.collide = False  # a ghost: invisible to collision, and to the overlay
    overlay = PlaneOverlay(engine, size=(64, 64))
    overlay.update()
    px = overlay.buffer.pixels
    assert not px[21:28, 21:28].any()  # no fill, no tint
    assert px[20, 20] == 2  # the anchor still shows


def test_update_repaints_from_scratch(engine):
    s = engine.sprite("solid")
    s.position = (20, 20)
    overlay = PlaneOverlay(engine, size=(64, 64))
    overlay.update()
    s.position = (40, 40)
    overlay.update()
    px = overlay.buffer.pixels
    assert not px[20:28, 20:28].any()  # old position cleared
    assert 0 < alpha(overlay, 41, 46) < 255  # new position painted (interior)


def test_offscreen_clipping(engine):
    s = engine.sprite("solid")
    s.position = (-4, -4)  # box straddles the canvas corner
    overlay = PlaneOverlay(engine, size=(64, 64))
    overlay.update()  # must not raise
    px = overlay.buffer.pixels
    assert px[0:4, 0:4].any()  # the surviving quadrant painted
    # The retained mask itself is clipped to screen space, so its visible
    # boundary is outlined at the screen edge as well as at the true edges.
    assert alpha(overlay, 0, 0) == 255
    assert alpha(overlay, 3, 2) == 255


def test_marks_buffer_dirty(engine):
    engine.sprite("solid").position = (10, 10)
    overlay = PlaneOverlay(engine, size=(64, 64))
    overlay.buffer.dirty = False
    overlay.update()
    assert overlay.buffer.dirty  # the edit() context flagged the re-upload
