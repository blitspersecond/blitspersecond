"""TileEngine's GL-free construction and atlas invariants."""

from types import SimpleNamespace

import numpy as np
import pytest

from blitspersecond.graphics import PixelBuffer, TileAtlas, TileEngine, TileFlags
from blitspersecond.graphics.tile.collision import (
    CollisionMask,
    _PATTERNS,
    _patterns,
)
from blitspersecond.graphics.tile.batch import TileBatch
from blitspersecond.graphics.tile.tile_layer import _TILE_DTYPE
from blitspersecond.resources import ImageSpec


def _buffer(size=(32, 32)) -> PixelBuffer:
    return PixelBuffer(ImageSpec(size=size, mode="P"))


def test_accepts_rectangular_atlas_tiles():
    atlas = TileAtlas(_buffer(), tile_size=(8, 4))

    engine = TileEngine(atlas)

    assert engine.tile_size == (8, 4)


def test_direct_tile_must_match_engine_atlas_size():
    buffer = _buffer()
    engine = TileEngine(TileAtlas(buffer, tile_size=(8, 8)))
    tile_from_another_grid = TileAtlas(buffer, tile_size=(4, 4))[0]

    with pytest.raises(ValueError, match="must match its atlas tile_size"):
        engine._resolve(tile_from_another_grid)


def _asymmetric_atlas() -> TileAtlas:
    pixels = np.zeros((8, 8), dtype=np.uint8)
    pixels[0, 1] = 1
    pixels[2, 6] = 1
    pixels[5, 3:5] = 1
    pixels[7, 0] = 1
    return TileAtlas(
        PixelBuffer(ImageSpec(size=(8, 8), mode="P", data=pixels)),
        tile_size=(8, 8),
    )


def _unpack(patterns: np.ndarray, width: int) -> np.ndarray:
    packed = patterns.astype(np.uint32).view(np.uint8)
    return np.unpackbits(
        packed, axis=1, bitorder="little", count=width
    ).astype(bool)


def _rendered_orientation(source: np.ndarray, flags: TileFlags) -> np.ndarray:
    """Reference from TileBatch's inverse UV mapping, expressed per pixel."""
    source_h, source_w = source.shape
    if flags & TileFlags.TRANSPOSE:
        output_w, output_h = source_h, source_w
    else:
        output_w, output_h = source_w, source_h
    expected = np.zeros((output_h, output_w), dtype=source.dtype)
    for y in range(output_h):
        for x in range(output_w):
            ox, oy = x, y
            if flags & TileFlags.FLIP_V:
                oy = output_h - 1 - oy
            if flags & TileFlags.FLIP_H:
                ox = output_w - 1 - ox
            if flags & TileFlags.TRANSPOSE:
                ox, oy = oy, ox
            expected[y, x] = source[oy, ox]
    return expected


@pytest.mark.parametrize("flags", [TileFlags(i) for i in range(8)])
def test_collision_patterns_match_every_rendered_orientation(flags):
    atlas = _asymmetric_atlas()

    actual = _unpack(_patterns(atlas, 0, 0, 8, 8, flags), 8)

    assert np.array_equal(
        actual, _rendered_orientation(atlas.buffer.mask, flags)
    )


def _asymmetric_rectangular_atlas() -> TileAtlas:
    pixels = np.zeros((4, 8), dtype=np.uint8)
    pixels[0, 1] = 1
    pixels[1, 6] = 1
    pixels[3, 2:5] = 1
    return TileAtlas(
        PixelBuffer(ImageSpec(size=(8, 4), mode="P", data=pixels)),
        tile_size=(8, 4),
    )


@pytest.mark.parametrize("flags", [TileFlags(i) for i in range(8)])
def test_rectangular_collision_patterns_match_every_orientation(flags):
    atlas = _asymmetric_rectangular_atlas()
    expected = _rendered_orientation(atlas.buffer.mask, flags)

    actual = _unpack(
        _patterns(atlas, 0, 0, 8, 4, flags),
        expected.shape[1],
    )

    assert np.array_equal(actual, expected)


@pytest.mark.parametrize("flags", [TileFlags(i) for i in range(8)])
def test_rectangular_scalar_stamp_matches_every_orientation(flags):
    atlas = _asymmetric_rectangular_atlas()
    expected = _rendered_orientation(atlas.buffer.mask, flags)
    collision = CollisionMask(atlas)

    collision.stamp(13, 17, (0, 0), (8, 4), flags)

    eh, ew = expected.shape
    assert np.array_equal(collision.unpacked[17 : 17 + eh, 13 : 13 + ew], expected)
    assert collision.unpacked.sum() == expected.sum()


@pytest.mark.parametrize("flags", [TileFlags(i) for i in range(8)])
def test_rectangular_bulk_restamp_matches_every_orientation(flags):
    atlas = _asymmetric_rectangular_atlas()
    expected = _rendered_orientation(atlas.buffer.mask, flags)
    tiles = np.zeros(1, dtype=_TILE_DTYPE)
    tiles["target"][0] = (13, 17)
    tiles["source"][0] = (0, 0)
    tiles["size"][0] = (expected.shape[1], expected.shape[0])
    tiles["flags"][0] = int(flags)
    collision = CollisionMask(atlas)

    collision.restamp(tiles, (0, 0))

    eh, ew = expected.shape
    assert np.array_equal(collision.unpacked[17 : 17 + eh, 13 : 13 + ew], expected)
    assert collision.unpacked.sum() == expected.sum()


def test_oriented_patterns_are_lazy_cached_and_buffer_edits_invalidate():
    atlas = _asymmetric_atlas()

    first = _patterns(atlas, 0, 0, 8, 8, TileFlags.ROT_90)
    again = _patterns(atlas, 0, 0, 8, 8, TileFlags.ROT_90)
    assert again is first
    assert len(_PATTERNS[atlas][1]) == 1

    flipped = _patterns(atlas, 0, 0, 8, 8, TileFlags.FLIP_H)
    assert flipped is not first
    assert len(_PATTERNS[atlas][1]) == 2

    with atlas.buffer.edit() as pixels:
        pixels[0, 0] = 1
    rebuilt = _patterns(atlas, 0, 0, 8, 8, TileFlags.ROT_90)
    assert rebuilt is not first
    assert len(_PATTERNS[atlas][1]) == 1


@pytest.mark.parametrize("flags", [TileFlags(i) for i in range(8)])
def test_bulk_restamp_uses_each_placement_orientation(flags):
    atlas = _asymmetric_atlas()
    tiles = np.zeros(1, dtype=_TILE_DTYPE)
    tiles["target"][0] = (13, 17)
    tiles["source"][0] = (0, 0)
    tiles["size"][0] = (8, 8)
    tiles["flags"][0] = int(flags)
    collision = CollisionMask(atlas)

    collision.restamp(tiles, (0, 0))

    expected = _rendered_orientation(atlas.buffer.mask, flags)
    assert np.array_equal(collision.unpacked[17:25, 13:21], expected)
    assert collision.unpacked.sum() == expected.sum()


@pytest.mark.parametrize("flags", [TileFlags(i) for i in range(8)])
def test_scalar_stamp_uses_each_placement_orientation(flags):
    atlas = _asymmetric_atlas()
    collision = CollisionMask(atlas)

    collision.stamp(13, 17, (0, 0), (8, 8), flags)

    expected = _rendered_orientation(atlas.buffer.mask, flags)
    assert np.array_equal(collision.unpacked[17:25, 13:21], expected)
    assert collision.unpacked.sum() == expected.sum()


def test_border_stamp_scrolls_destructively_into_screen():
    atlas = _asymmetric_atlas()
    collision = CollisionMask(atlas)
    screen_width = collision.unpacked.shape[1]
    collision.stamp(
        screen_width,
        10,
        (0, 0),
        (8, 8),
        TileFlags.ROT_90,
    )
    assert collision.unpacked.sum() == 0

    collision.translate(0, 0, 8, 0)

    expected = _rendered_orientation(atlas.buffer.mask, TileFlags.ROT_90)
    assert np.array_equal(
        collision.unpacked[10:18, screen_width - 8 : screen_width],
        expected,
    )
    assert collision.unpacked.sum() == expected.sum()


@pytest.mark.parametrize(("position", "scroll", "window"), [
    ((-15, 10), (-16, 0), (10, 18, 1, 9)),
    ((10, -15), (0, -16), (1, 9, 10, 18)),
])
def test_leading_border_plus_fine_offset_fits_collision_apron(
    position, scroll, window
):
    atlas = _asymmetric_atlas()
    collision = CollisionMask(atlas)
    collision.stamp(position[0], position[1], (0, 0), (8, 8))
    assert collision.unpacked.sum() == 0

    collision.translate(0, 0, *scroll)

    y0, y1, x0, x1 = window
    assert np.array_equal(
        collision.unpacked[y0:y1, x0:x1],
        atlas.buffer.mask,
    )


@pytest.mark.parametrize("scroll", [(1, 0), (-7, 0), (0, 3), (0, -5), (9, -6)])
def test_collision_translation_tracks_camera_in_both_axes(scroll):
    atlas = _asymmetric_atlas()
    collision = CollisionMask(atlas)
    collision.stamp(100, 100, (0, 0), (8, 8))

    collision.translate(0, 0, *scroll)

    sx, sy = scroll
    assert np.array_equal(
        collision.unpacked[100 - sy : 108 - sy, 100 - sx : 108 - sx],
        atlas.buffer.mask,
    )
    assert collision.unpacked.sum() == atlas.buffer.mask.sum()


def test_collision_content_cannot_resurrect_after_leaving_working_store():
    collision = CollisionMask(_asymmetric_atlas())
    collision.stamp(10, 10, (0, 0), (8, 8))

    collision.translate(0, 0, 10_000, 0)
    collision.translate(10_000, 0, 0, 0)

    assert collision.unpacked.sum() == 0


class _FakeBatch:
    def __init__(self):
        self.compactions = []

    def resize(self, capacity):
        pass

    def set_quad(self, index, tex_coords, flags):
        pass

    def compact(self, indices):
        self.compactions.append(indices.copy())


def test_tile_batch_compacts_uv_rows_without_allocating_a_new_mirror():
    batch = TileBatch.__new__(TileBatch)
    batch._uv = np.arange(8 * 12, dtype=np.float32).reshape(8, 4, 3)
    batch._uv_scratch = np.zeros_like(batch._uv)
    batch._uv_dirty = False
    source_id = id(batch._uv)
    scratch_id = id(batch._uv_scratch)

    batch.compact(np.array([1, 4, 7]))

    assert id(batch._uv) == scratch_id
    assert id(batch._uv_scratch) == source_id
    expected = np.arange(8 * 12, dtype=np.float32).reshape(8, 4, 3)[[1, 4, 7]]
    assert np.array_equal(batch._uv[:3], expected)
    assert batch._uv_dirty is True


class _FakeTexture:
    def get_region(self, *args):
        return SimpleNamespace(tex_coords=np.zeros(12, dtype=np.float32))


def _headless_engine(monkeypatch, atlas, *, collidable=False):
    engine = TileEngine(atlas, collidable=collidable)
    engine._shader = object()
    engine._tilebatch = _FakeBatch()
    atlas.buffer._texture = _FakeTexture()
    monkeypatch.setattr(engine, "_ensure_built", lambda: None)
    return engine


def test_blit_retains_orientation_for_collision(monkeypatch):
    buffer = _buffer((16, 8))
    engine = _headless_engine(
        monkeypatch,
        TileAtlas(buffer, tile_size=(8, 8)),
    )

    engine.blit(0, 4, 5, TileFlags.ROT_90)

    assert engine._tiles["flags"][0] == int(TileFlags.ROT_90)


def test_rectangular_transpose_swaps_displayed_placement_size(monkeypatch):
    buffer = _buffer((16, 8))
    engine = _headless_engine(
        monkeypatch,
        TileAtlas(buffer, tile_size=(8, 4)),
    )

    engine.blit(0, 0, 0, TileFlags.FLIP_H | TileFlags.FLIP_V)
    engine.blit(0, 0, 0, TileFlags.ROT_90)

    assert tuple(engine._tiles["size"][0]) == (8, 4)
    assert tuple(engine._tiles["size"][1]) == (4, 8)


def test_stamp_is_bounded_to_viewport_plus_one_tile(monkeypatch):
    engine = _headless_engine(
        monkeypatch,
        TileAtlas(_buffer((8, 8)), tile_size=(8, 8)),
    )

    engine.blit(0, -8, -8)
    engine.blit(0, 640, 360)

    with pytest.raises(ValueError, match="outside the bounded drawable area"):
        engine.blit(0, -16, 0)
    with pytest.raises(ValueError, match="outside the bounded drawable area"):
        engine.blit(0, 648, 0)


def test_360_pixel_tiles_expose_four_by_three_cell_origins(monkeypatch):
    engine = _headless_engine(
        monkeypatch,
        TileAtlas(_buffer((360, 360)), tile_size=(360, 360)),
    )

    left, top, right, bottom = engine._drawable_bounds()

    assert len(range(left, right, 360)) == 4
    assert len(range(top, bottom, 360)) == 3


def test_fine_camera_motion_only_changes_gpu_offset(monkeypatch):
    engine = _headless_engine(
        monkeypatch,
        TileAtlas(_buffer((8, 8)), tile_size=(8, 8)),
    )
    engine.blit(0, 0, 0)
    before = engine._tiles["target"][: engine._ntiles].copy()
    engine._dirty_sync = False

    engine.scroll = (7.5, 3.25)

    assert np.array_equal(engine._tiles["target"][: engine._ntiles], before)
    assert engine._fine_scroll == (7.5, 3.25)
    assert engine._dirty_sync is False
    assert engine._tilebatch.compactions == []


def test_tile_boundary_broadcasts_translation_and_compacts(monkeypatch):
    engine = _headless_engine(
        monkeypatch,
        TileAtlas(_buffer((8, 8)), tile_size=(8, 8)),
    )
    engine.blit(0, -8, 0)  # departing left border
    engine.blit(0, 0, 0)  # survives as the new left border

    engine.scroll = (8, 0)

    assert engine._ntiles == 1
    assert tuple(engine._tiles["target"][0]) == (-8, 0)
    assert engine._fine_scroll == (0, 0)
    assert np.array_equal(engine._tilebatch.compactions[0], np.array([1]))


def test_vertical_tile_boundary_culls_top_border(monkeypatch):
    engine = _headless_engine(
        monkeypatch,
        TileAtlas(_buffer((8, 8)), tile_size=(8, 8)),
    )
    engine.blit(0, 0, -8)
    engine.blit(0, 0, 0)

    engine.scroll = (0, 8)

    assert engine._ntiles == 1
    assert tuple(engine._tiles["target"][0]) == (0, -8)
    assert np.array_equal(engine._tilebatch.compactions[0], np.array([1]))


def test_collidable_border_stamp_enters_with_camera(monkeypatch):
    atlas = _asymmetric_atlas()
    engine = _headless_engine(monkeypatch, atlas, collidable=True)
    engine.blit(0, 640, 10, TileFlags.ROT_90)
    assert engine.collision_mask_unpacked.sum() == 0

    engine.scroll = (8, 0)

    expected = _rendered_orientation(atlas.buffer.mask, TileFlags.ROT_90)
    assert np.array_equal(
        engine.collision_mask_unpacked[10:18, 632:640],
        expected,
    )
