"""SpriteEngine + Sprite instance state, headless: no GL, no window.

Everything GL-free is proven here: construction, the instance factory,
per-instance state (assembly, position, flip, palette registers, colour
registers, visibility, collide intent) and its validation. The render
interior (staging, runs, uploads) needs a live context and is exercised by
sprite_test.py in the experiments repo -- the visual harness.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blitspersecond.common import Location
from blitspersecond.graphics import Sprite, SpriteEngine, SpriteSheet
from blitspersecond.resources import ResourceManager

TEST_ASSETS = Path(__file__).resolve().parent / "assets"
TEST_SHEET = TEST_ASSETS / "test.sprite.json"
BULLETS = TEST_ASSETS / "bullets.sprite.json"


@pytest.fixture(scope="module")
def engine() -> SpriteEngine:
    return SpriteEngine(SpriteSheet(ResourceManager().sprite_sheet(str(TEST_SHEET))))


def test_engine_binds_one_sheet(engine):
    assert set(engine.sheet.tilesets) == {"shapes", "marks"}
    with pytest.raises(TypeError):
        SpriteEngine({"not": "a sheet"})


def test_sprite_factory_and_order(engine):
    a = engine.sprite("shape-01")
    b = engine.sprite()
    c = Sprite(engine)  # direct construction also registers
    try:
        sprites = engine.sprites
        assert sprites.index(a) < sprites.index(b) < sprites.index(c)
    finally:
        engine.remove(a)
        engine.remove(b)
        engine.remove(c)
    assert a not in engine.sprites


def test_integer_planes_are_painter_order_not_creation_order(engine):
    front = engine.z(7).sprite()
    back = engine.z(-2).sprite()
    middle = engine.z(3).sprite()
    try:
        ordered = engine.sprites
        assert ordered.index(back) < ordered.index(middle) < ordered.index(front)
    finally:
        engine.remove(front)
        engine.remove(back)
        engine.remove(middle)


def test_dense_pool_exposes_renderer_owned_instance_arrays():
    engine = SpriteEngine(SpriteSheet.load(str(BULLETS)))
    pool = engine.z(4).pool(("pink", "blue", "pink"))

    assert pool.z == 4
    assert pool.positions.shape == (3, 2)
    assert pool.positions.dtype.name == "float32"
    assert pool.visible.tolist() == [True, True, True]

    engine._dirty = False
    with pool.edit():
        pool.positions[:] = ((10, 20), (30, 40), (50, 60))
        pool.visible[1] = False
        pool.flip_x[2] = True
        pool.rotation[2] = 1
    assert engine._dirty is True
    assert pool.count_visible == 2
    assert engine.collision_mask.any()
    assert engine._collision_dirty is False


def test_pool_rejects_multipart_or_mixed_material_assemblies(engine):
    multipart = next(
        name
        for name, assembly in engine.sheet.assemblies.items()
        if len(assembly.parts) > 1
    )
    with pytest.raises(ValueError, match="exactly one"):
        engine.z(0).pool((multipart,))


def test_assembly_selection(engine):
    s = engine.sprite()
    try:
        assert s.assembly is None  # presents nothing until told
        s.assembly = "shape-01"
        assert s.assembly == "shape-01"
        with pytest.raises(KeyError):
            s.assembly = "nope"
        s.assembly = "blank-01"  # a deliberate blank pose is selectable
    finally:
        engine.remove(s)


def test_position_and_facing(engine):
    s = engine.sprite("shape-01")
    try:
        s.position = (320.7, 300.2)
        assert s.position == Location(320, 300)  # floats held, floored out
        assert s.x == pytest.approx(320.7)
        s.present_at((100, 200), flip_x=True)
        assert s.position == Location(100, 200)
        assert s.flip_x is True
        s.present_at((101, 200))  # flip untouched when not passed
        assert s.flip_x is True
    finally:
        engine.remove(s)


def test_palette_registers(engine):
    # The mirror match: two instances on one engine, same pixels, different
    # per-instance palette selections -- the registers can't live on the
    # shared engine or sheet.
    red = engine.sprite("shape-01")
    blue = engine.sprite("shape-01")
    try:
        # Seeded from each tileset's default: shapes 0, marks 2.
        assert red.palettes["shapes"] == 0
        assert red.palettes["marks"] == 2
        blue.palettes["shapes"] = 3
        assert blue.palettes["shapes"] == 3
        assert red.palettes["shapes"] == 0  # untouched
        with pytest.raises(ValueError):
            blue.palettes["shapes"] = 4  # only 4 tables, 0..3
        with pytest.raises(KeyError):
            blue.palettes["nope"] = 0
    finally:
        engine.remove(red)
        engine.remove(blue)


def test_colour_registers_clamp(engine):
    s = engine.sprite()
    try:
        s.alpha = 1.7
        assert s.alpha == 1.0
        s.red = -3
        assert s.red == 0.0
        s.green = 0.25
        assert s.green == 0.25
    finally:
        engine.remove(s)


def test_visibility_and_collide_intent(engine):
    ghost = engine.sprite("shape-01")
    try:
        assert ghost.visible is True
        assert ghost.collide is True
        ghost.collide = False  # an afterimage: display-only
        ghost.visible = False
        assert ghost.collide is False
        assert ghost.visible is False
    finally:
        engine.remove(ghost)


def test_setters_mark_engine_dirty(engine):
    s = engine.sprite("shape-01")
    try:
        for write in (
            lambda: setattr(s, "position", (5, 5)),
            lambda: setattr(s, "x", 6),
            lambda: setattr(s, "flip_x", True),
            lambda: setattr(s, "visible", False),
            lambda: setattr(s, "alpha", 0.5),
            lambda: s.palettes.__setitem__("shapes", 1),
            lambda: setattr(s, "assembly", "blank-01"),
        ):
            engine._dirty = False
            write()
            assert engine._dirty is True
    finally:
        engine.remove(s)


def test_drawable_metadata_face(engine):
    # The primary (first) tileset is the engine's face for the compositor.
    assert engine.indexed is True
    assert engine.transparency_index == 0
    assert engine.size == (128, 128)
