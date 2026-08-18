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
from blitspersecond.resources import ImageSpec, PLANE_DTYPE


def _buffer(size=(32, 32)) -> PixelBuffer:
    return PixelBuffer(ImageSpec(size=size, mode="P"))


def test_accepts_rectangular_atlas_tiles():
    atlas = TileAtlas(_buffer(), 8, 4)

    engine = TileEngine(atlas)

    assert engine.tile_size == (8, 4)


def test_direct_tile_must_match_engine_atlas_size():
    buffer = _buffer()
    engine = TileEngine(TileAtlas(buffer, 8, 8))
    tile_from_another_grid = TileAtlas(buffer, 4, 4)[0]

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
        8, 8,
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
        8, 4,
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
        TileAtlas(buffer, 8, 8),
    )

    engine.blit(0, 4, 5, TileFlags.ROT_90)

    assert engine._tiles["flags"][0] == int(TileFlags.ROT_90)


def test_rectangular_transpose_swaps_displayed_placement_size(monkeypatch):
    buffer = _buffer((16, 8))
    engine = _headless_engine(
        monkeypatch,
        TileAtlas(buffer, 8, 4),
    )

    engine.blit(0, 0, 0, TileFlags.FLIP_H | TileFlags.FLIP_V)
    engine.blit(0, 0, 0, TileFlags.ROT_90)

    assert tuple(engine._tiles["size"][0]) == (8, 4)
    assert tuple(engine._tiles["size"][1]) == (4, 8)


def test_stamp_is_bounded_to_viewport_plus_one_tile(monkeypatch):
    engine = _headless_engine(
        monkeypatch,
        TileAtlas(_buffer((8, 8)), 8, 8),
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
        TileAtlas(_buffer((360, 360)), 360, 360),
    )

    left, top, right, bottom = engine._drawable_bounds()

    assert len(range(left, right, 360)) == 4
    assert len(range(top, bottom, 360)) == 3


def test_fine_camera_motion_only_changes_gpu_offset(monkeypatch):
    engine = _headless_engine(
        monkeypatch,
        TileAtlas(_buffer((8, 8)), 8, 8),
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
        TileAtlas(_buffer((8, 8)), 8, 8),
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
        TileAtlas(_buffer((8, 8)), 8, 8),
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


# -- atlas.region: a rectangular block of tiles as a blittable plane --------


def _region_atlas() -> TileAtlas:
    """10 x 5 tiles of 8x8."""
    return TileAtlas(_buffer(size=(80, 40)), 8, 8)


def test_region_returns_a_blittable_plane():
    plane = _region_atlas().region(0, 0, 10, 5)

    assert plane.dtype == PLANE_DTYPE
    assert plane.shape == (5, 10)  # numpy order: (rows, cols)


def test_region_carries_flat_row_major_tile_ids():
    plane = _region_atlas().region(0, 0, 10, 5)

    assert plane["tile"][0].tolist() == list(range(10))
    assert plane["tile"][1].tolist() == list(range(10, 20))


def test_region_offset_is_x_y_in_tiles():
    plane = _region_atlas().region(2, 1, 3, 2)

    assert plane["tile"].tolist() == [[12, 13, 14], [22, 23, 24]]


def test_region_has_no_orientation_and_is_writable():
    plane = _region_atlas().region(0, 0, 4, 2)

    assert not plane["flags"].any()
    assert plane.flags.writeable  # callers may set flags before blitting


@pytest.mark.parametrize(
    "rect", [(0, 0, 11, 5), (0, 0, 10, 6), (-1, 0, 2, 2), (9, 4, 2, 1)]
)
def test_region_out_of_bounds_raises_rather_than_clamping(rect):
    with pytest.raises(IndexError, match="does not fit an atlas"):
        _region_atlas().region(*rect)


@pytest.mark.parametrize("rect", [(0, 0, 0, 1), (0, 0, 1, 0), (0, 0, -1, 1)])
def test_region_rejects_a_non_positive_size(rect):
    with pytest.raises(ValueError, match="must be positive"):
        _region_atlas().region(*rect)


def test_atlas_does_not_slice():
    # Statically a type error too, which is the point -- the runtime raise is
    # the backstop for callers who aren't type-checked.
    with pytest.raises(TypeError, match="does not slice"):
        _region_atlas()[0:5]  # type: ignore[index]


def test_single_tile_access_still_interns():
    atlas = _region_atlas()

    assert atlas[3, 1] is atlas[13]


# -- TiledLayer carries the tileset the loader already proved ---------------


def _island() -> str:
    from pathlib import Path

    return str(
        Path(__file__).resolve().parents[1]
        / "blitspersecond/assets/gorillaz/island.tmx"
    )


def test_tiled_layer_names_its_tileset():
    """The loader rejects a layer spanning two tilesets, so the pairing is
    established at parse time -- it must not be re-derived downstream."""
    from blitspersecond.resources import load_tiled_file

    tiled = load_tiled_file(_island())

    assert tiled.layers, "island.tmx should have at least one layer"
    names = {ts.name for ts in tiled.tilesets}
    for entry in tiled.layers:
        assert entry.tileset in names
        assert entry.plane.shape == (tiled.rows, tiled.cols)


def test_tiled_file_speaks_bps_not_tmx():
    """cols/rows for cells, w/h for one tile in pixels -- Tiled's own
    attribute names stop at the parser."""
    from blitspersecond.resources import load_tiled_file

    tiled = load_tiled_file(_island())

    assert tiled.cols > 0 and tiled.rows > 0
    assert tiled.w > 0 and tiled.h > 0
    assert not hasattr(tiled, "tilewidth")
    assert not hasattr(tiled, "width")


def test_planes_are_read_only_and_maps_get_their_own_copy():
    from blitspersecond.resources import load_tiled_file

    a = load_tiled_file(_island())
    b = load_tiled_file(_island())

    # The cached spec stays pristine; TileMap copies before handing it on.
    assert not a.layers[0].plane.flags.writeable
    assert np.array_equal(a.layers[0].plane, b.layers[0].plane)


# -- TileLayer.clear: empties stamps, keeps the binding ---------------------


def test_clear_empties_stamps_but_keeps_the_atlas(monkeypatch):
    buffer = _buffer((16, 8))
    engine = _headless_engine(monkeypatch, TileAtlas(buffer, 8, 8))
    engine.blit(0, 0, 0)
    engine.blit(1, 8, 0)
    assert engine._ntiles == 2

    engine.clear()

    assert engine._ntiles == 0
    assert engine.atlas is not None  # binding survives, unlike unbind()
    engine.blit(0, 0, 0)
    assert engine._ntiles == 1  # refills from index 0


def test_clear_on_an_unbound_layer_raises():
    from blitspersecond.graphics import TileLayer

    layer = TileLayer(TileAtlas(_buffer(), 8, 8))
    layer.unbind()

    with pytest.raises(RuntimeError, match="unbound"):
        layer.clear()


# -- per-layer palette: recolour by swapping the table, not the pixels ------


def _indexed_pair():
    """Two layers over ONE shared atlas -- the red/blue island case."""
    from blitspersecond.colors import Palette
    from blitspersecond.graphics import PixelBuffer, TileLayer

    buffer = PixelBuffer(
        ImageSpec(size=(32, 32), mode="P", palette=Palette())
    )
    atlas = TileAtlas(buffer, 8, 8)
    return TileLayer(atlas), TileLayer(atlas), atlas


def test_layers_share_the_buffer_palette_until_one_is_assigned():
    blue, red, atlas = _indexed_pair()

    assert blue.palette is atlas.buffer.palette
    assert red.palette is atlas.buffer.palette


def test_assigning_a_palette_is_layer_local():
    from blitspersecond.colors import Palette

    blue, red, atlas = _indexed_pair()
    own = Palette(atlas.buffer.palette.raw())
    own[3] = (200, 40, 40, 255)

    red.palette = own

    assert red.palette is own
    assert red.palette[3] == (200, 40, 40, 255)
    # the shared atlas and the other layer are untouched -- one copy of the
    # index data, two tables
    assert blue.palette is atlas.buffer.palette
    assert atlas.buffer.palette[3] != (200, 40, 40, 255)


def test_assigned_palette_drives_the_background_too():
    from blitspersecond.colors import Palette

    _blue, red, atlas = _indexed_pair()
    own = Palette(atlas.buffer.palette.raw())
    own[7] = (1, 2, 3, 255)
    red.palette = own
    red.background = 7

    assert red._resolved_background() == pytest.approx(
        (1 / 255.0, 2 / 255.0, 3 / 255.0, 1.0)
    )


def test_palette_must_be_a_palette():
    _blue, red, _atlas = _indexed_pair()

    with pytest.raises(TypeError, match="expected Palette"):
        red.palette = object()  # type: ignore[assignment]


def test_unbind_clears_the_layer_palette():
    from blitspersecond.colors import Palette

    _blue, red, atlas = _indexed_pair()
    red.palette = Palette(atlas.buffer.palette.raw())

    red.unbind()
    red.bind(atlas)

    # a palette only means anything against the indices it was chosen for
    assert red.palette is atlas.buffer.palette


def test_direct_colour_layer_still_has_no_palette():
    from blitspersecond.graphics import PixelBuffer, TileLayer

    direct = TileLayer(
        TileAtlas(PixelBuffer(ImageSpec(size=(16, 16), mode="RGBA")), 8, 8)
    )

    with pytest.raises(ValueError, match="no palette"):
        direct.palette


# -- TileMap refills only what came into view -------------------------------


class _StubLayer:
    """Just enough TileLayer for TileMap's window arithmetic: no GL."""

    def __init__(self, tile):
        self.tile_size = tile
        self.scroll = (0.0, 0.0)

    def clear(self):
        pass


class _RecordingMap:
    """TileMap with _blit_cells replaced by a recorder, so the cell rectangles
    it decides to paint can be asserted without a context."""

    def __init__(self, cols=480, rows=34, tile=(8, 8)):
        from blitspersecond.graphics.tile.tile_map import TileMap

        self.painted = []
        self.tile = tile
        self.map = TileMap.__new__(TileMap)
        self.map._plane = np.zeros((rows, cols), dtype=PLANE_DTYPE)
        self.map._layer = _StubLayer(tile)
        self.map._tiled = None
        self.map._scroll = (0.0, 0.0)
        self.map._coarse = (0, 0)
        self.map._origin = (0, 0)
        self.map._window = (0, 0, 0, 0)
        self.map._blit_cells = self._record
        self.map._refill()

    def _record(self, x0, y0, x1, y1):
        if x1 > x0 and y1 > y0:
            self.painted.append((x0, y0, x1, y1))

    def cells(self):
        return sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in self.painted)

    def step(self, sx, sy=0.0):
        tw, th = self.tile
        self.painted.clear()
        coarse = (int(sx // tw), int(sy // th))
        if coarse != self.map._coarse:
            self.map._coarse = coarse
            self.map._refill()


def test_first_fill_paints_the_whole_window():
    m = _RecordingMap()

    assert m.cells() > 34, "a whole window, not one column"


def test_one_cell_step_paints_exactly_one_column():
    m = _RecordingMap()

    m.step(8.0)

    assert m.cells() == 34, f"one column of 34 rows, got {m.cells()}"


def test_sub_cell_step_paints_nothing():
    m = _RecordingMap()

    m.step(4.0)

    assert m.painted == []


def test_stepping_back_paints_the_other_edge():
    m = _RecordingMap()
    m.step(80.0)
    m.step(72.0)

    assert m.cells() == 34


def test_diagonal_step_never_stamps_a_cell_twice():
    m = _RecordingMap(rows=60)

    m.step(8.0, 8.0)

    seen = set()
    for x0, y0, x1, y1 in m.painted:
        for x in range(x0, x1):
            for y in range(y0, y1):
                assert (x, y) not in seen, f"cell {(x, y)} stamped twice"
                seen.add((x, y))
    assert seen, "a diagonal step should paint an L of new cells"


def test_window_is_clamped_to_the_plane():
    m = _RecordingMap(cols=4, rows=4)

    for x0, y0, x1, y1 in m.painted:
        assert 0 <= x0 < x1 <= 4
        assert 0 <= y0 < y1 <= 4
