"""`Sprite`: one drawn thing, one layer, one collision identity.

The shell. It holds one presentation's worth of state and does not draw --
adding one to a draw order is legal and shows nothing, exactly as `TileMap`
did between its shell and its draw.

Two things get most of the attention here. **The four `visible`/`collide`
states**, because they are four meanings over two bits and the pair is easy
to collapse into a gate-and-modifier by accident. And **the buffer chain**,
because `PixelBuffer` interns weakly: a sprite that held only a texture id
would draw from a freed texture name at some arbitrary later moment, with no
exception and no reliable reproduction.
"""

from __future__ import annotations

import gc
import json
from pathlib import Path

import pytest
from PIL import Image

from blitspersecond.common import PACKAGE_ROOT, Location
from blitspersecond.graphics import Orientation, PixelBuffer
from blitspersecond.graphics.internal import Layers
from blitspersecond.graphics.sprite import Sprite, SpriteSheet

ASSETS = PACKAGE_ROOT.parent / "assets"
FUMIKO = str(ASSETS / "fumiko" / "fumiko.sprite.json")
BULLETS_PNG = str(ASSETS / "bullets" / "bullets.png")


@pytest.fixture(autouse=True)
def _cold_cache():
    yield
    SpriteSheet._instances.clear()


@pytest.fixture
def sheet() -> SpriteSheet:
    return SpriteSheet(FUMIKO)


@pytest.fixture
def chun(sheet: SpriteSheet) -> Sprite:
    sprite = Sprite(sheet)
    sprite.assembly = "idle-0"
    return sprite


# -- it is a layer, and there is one per layer -------------------------


def test_it_constructs_with_no_gl_context(sheet: SpriteSheet) -> None:
    sprite = Sprite(sheet)
    assert sprite.sheet is sheet
    assert sprite.assembly is None  # presenting nothing is a state


def test_it_is_a_layer_and_is_placed_like_one(chun: Sprite) -> None:
    layers = Layers()
    assert chun.attached is False
    layers.add(chun)
    assert chun.attached is True
    assert chun in list(layers)


def test_two_sprites_over_one_sheet_are_two_identities(sheet: SpriteSheet) -> None:
    """The mirror match: shared pixels, one texture upload, separate
    identities -- and each with its own palette register."""
    p1, p2 = Sprite(sheet), Sprite(sheet)
    assert p1 is not p2
    assert p1.sheet is p2.sheet
    p1.palettes["character"] = 3
    assert p2.palettes["character"] == 0


def test_the_sheet_has_no_setter(chun: Sprite) -> None:
    with pytest.raises(AttributeError):
        chun.sheet = SpriteSheet(FUMIKO)  # type: ignore[misc]


def test_it_refuses_anything_that_is_not_a_sheet() -> None:
    with pytest.raises(TypeError, match="SpriteSheet"):
        Sprite("fumiko.sprite.json")  # type: ignore[arg-type]


# -- the buffer chain --------------------------------------------------


def test_a_sprite_holds_its_pixels_alive() -> None:
    """`PixelBuffer` interns WEAKLY, so every step from sprite to pixels has
    to be a strong reference: sheet -> tileset -> Atlas -> PixelBuffer.

    Invisible without this test, because normal code usually happens to hold
    a reference by accident -- and the symptom is a black layer appearing
    some arbitrary time after construction, with no exception.
    """
    sprite = Sprite(SpriteSheet(FUMIKO))
    sprite.assembly = "idle-0"
    buffer = sprite.sheet.tilesets["character"].atlas.buffer
    buffer_id = buffer.id

    SpriteSheet._instances.clear()  # drop the shared table's entry too
    del buffer
    gc.collect()

    # Still reachable, still the same buffer, still readable.
    live = sprite.sheet.tilesets["character"].atlas.buffer
    assert live.id == buffer_id
    assert live.pixels.shape[0] > 0


# -- the pose ----------------------------------------------------------


def test_the_pose_is_set_by_name(chun: Sprite) -> None:
    assert chun.assembly == "idle-0"
    chun.assembly = "walk-1"
    assert chun.assembly == "walk-1"


def test_an_unknown_pose_raises_with_the_vocabulary(chun: Sprite) -> None:
    with pytest.raises(KeyError, match="nope"):
        chun.assembly = "nope"
    assert chun.assembly == "idle-0"  # and the old pose survives


def test_a_grid_sprite_takes_index_and_cell_addresses() -> None:
    sprite = Sprite(SpriteSheet(BULLETS_PNG, 8, 8))
    sprite.assembly = 1
    assert sprite.assembly == "1"
    sprite.assembly = (0, 0)
    assert sprite.assembly == "0"


def test_a_package_sprite_refuses_an_index(chun: Sprite) -> None:
    with pytest.raises(TypeError, match="authored names"):
        chun.assembly = 3


# -- where it is -------------------------------------------------------


def test_position_is_whole_pixels(chun: Sprite) -> None:
    """A float is floored ON THE WAY IN, so what is read back is what is
    drawn. Sub-pixel positions are a GPU capability this engine declines."""
    chun.position = (160.7, 200.9)
    assert chun.position == Location(160, 200)
    assert (chun.x, chun.y) == (160, 200)
    assert isinstance(chun.x, int) and isinstance(chun.y, int)


def test_a_sub_pixel_nudge_does_not_accumulate(chun: Sprite) -> None:
    """The documented consequence, pinned so it cannot regress into a hidden
    float accumulator: the fraction is gone before the next write arrives, and
    the sprite stays where it was. Keeping it is the game's job."""
    chun.position = (10, 10)
    for _ in range(10):
        chun.x += 0.4
    assert chun.x == 10

    fx = 10.0
    for _ in range(10):
        fx += 0.4
        chun.x = fx
    assert chun.x == 14


def test_position_floors_rather_than_truncating(chun: Sprite) -> None:
    """`int()` truncates toward zero, which stalls a sprite for one pixel as
    it crosses the origin leftward. `floor` moves it at the same rate in both
    directions."""
    chun.position = (-0.5, -1.5)
    assert chun.position == Location(-1, -2)
    chun.origin = (-0.5, -1.5)
    assert chun.origin == Location(-1, -2)


def test_position_unpacks_like_a_tuple(chun: Sprite) -> None:
    chun.position = (10, 20)
    x, y = chun.position
    assert (x, y) == (10, 20)
    assert chun.position == (10, 20)


# -- origin and centre -------------------------------------------------


def test_origin_defaults_to_the_arts_own_corner(chun: Sprite) -> None:
    """No halfway point is invented: a default you cannot predict is worse
    than one you have to change."""
    assert chun.origin == Location(0, 0)


def test_centre_of_a_grid_cell_accounts_for_the_baked_offset() -> None:
    """The bullets bake `[-4, -4]` on 8x8 art, so the cell already straddles
    the origin and its centre IS the origin."""
    sprite = Sprite(SpriteSheet(str(ASSETS / "bullets" / "bullets.sprite.json")))
    sprite.assembly = "blue"
    assert sprite.centre == Location(0, 0)

    plain = Sprite(SpriteSheet(BULLETS_PNG, 8, 8))
    plain.assembly = 0
    assert plain.centre == Location(4, 4)


def test_centre_spans_every_part_of_a_multipart_pose(tmp_path: Path) -> None:
    """The case fumiko cannot exercise -- 45 of Chun-Li's 3198 assemblies are
    two parts, and the centre has to be the bounding box of all of them."""
    image = tmp_path / "sheet.png"
    Image.fromarray(PixelBuffer((32, 32), "P").pixels, mode="P").save(image)
    package = tmp_path / "multi.sprite.json"
    package.write_text(
        json.dumps(
            {
                "version": 1,
                "tilesets": {
                    "main": {
                        "image": "sheet.png",
                        "palettes": [["#00000000", "#ffffffff"]],
                        "tiles": {
                            "a": {"source": [0, 0, 8, 8]},
                            "b": {"source": [8, 0, 4, 4]},
                        },
                    }
                },
                "assemblies": {
                    "pose": {
                        "parts": [
                            {"tileset": "main", "tile": "a", "offset": [0, 0]},
                            {"tileset": "main", "tile": "b", "offset": [10, 20]},
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    sprite = Sprite(SpriteSheet(str(package)))
    sprite.assembly = "pose"
    # x spans 0..14, y spans 0..24 -- the union, not the first part.
    assert sprite.centre == Location(7, 12)


def test_centre_needs_a_pose(sheet: SpriteSheet) -> None:
    with pytest.raises(ValueError, match="no pose is presented"):
        Sprite(sheet).centre


# -- orientation -------------------------------------------------------


@pytest.mark.parametrize("orientation", list(Orientation))
def test_every_orientation_round_trips(chun: Sprite, orientation) -> None:
    """Stored as an `Orientation`, not as a bool beside a turn count -- one
    vocabulary in one storage."""
    chun.orientation = orientation
    assert chun.orientation is orientation


def test_orientation_defaults_to_none(chun: Sprite) -> None:
    assert chun.orientation is Orientation.NONE


def test_a_sprite_turns_rectangular_art(chun: Sprite) -> None:
    """Fumiko's cells are 24x32. `applies_to` would forbid a quarter turn of
    those -- but its reasoning is about a cell returning to its grid and
    overflowing its neighbours, and a sprite has neither. See
    SPRITE_PORT.md F7; this pins the intended behaviour before the draw
    exists to demonstrate it."""
    chun.orientation = Orientation.ROT_90
    assert chun.orientation is Orientation.ROT_90


# -- palette registers -------------------------------------------------


def test_registers_seed_from_each_tilesets_default(chun: Sprite) -> None:
    assert chun.palettes["character"] == 0
    assert set(chun.palettes) == {"character"}
    assert "character" in chun.palettes and len(chun.palettes) == 1


def test_an_unknown_tileset_raises_naming_the_sheets(chun: Sprite) -> None:
    for action in (
        lambda: chun.palettes["nope"],
        lambda: chun.palettes.__setitem__("nope", 0),
    ):
        with pytest.raises(KeyError) as excinfo:
            action()
        assert "character" in str(excinfo.value)


@pytest.mark.parametrize("index", [-1, 4, 99])
def test_a_palette_out_of_range_raises(chun: Sprite, index: int) -> None:
    """Fumiko carries 4 tables. A register that silently did nothing would be
    art in the wrong colours with nothing reporting it."""
    with pytest.raises(ValueError, match="out of range"):
        chun.palettes["character"] = index


def test_registers_are_not_a_dict_subclass(chun: Sprite) -> None:
    """Reference subclassed `dict`, which left `update()` and friends as
    unguarded doors -- a register set through one would skip both the range
    check and the dirty mark."""
    assert not isinstance(chun.palettes, dict)
    assert not hasattr(chun.palettes, "update")


# -- colour registers --------------------------------------------------


@pytest.mark.parametrize("channel", ["red", "green", "blue"])
def test_colour_registers_default_to_one_and_clamp(
    chun: Sprite, channel: str
) -> None:
    assert getattr(chun, channel) == 1.0
    setattr(chun, channel, 0.25)
    assert getattr(chun, channel) == 0.25
    setattr(chun, channel, 5.0)
    assert getattr(chun, channel) == 1.0
    setattr(chun, channel, -3.0)
    assert getattr(chun, channel) == 0.0


def test_the_channels_are_independent(chun: Sprite) -> None:
    """`approx` because the registers are held at float32 -- the precision
    they are sent at. A register that read back as an exact Python float
    would be storing something the GPU never sees."""
    chun.red = 0.1
    chun.green = 0.2
    chun.blue = 0.3
    assert (chun.red, chun.green, chun.blue) == pytest.approx(
        (0.1, 0.2, 0.3), rel=1e-6
    )


def test_there_is_no_alpha_register(chun: Sprite) -> None:
    """Removed 2026-09-02. A sprite pixel is drawn or discarded, so a
    fractional alpha had nothing to mean -- and the refusal names the three
    things a caller might have wanted instead, because `ghost.alpha = 0.4`
    was the documented way to fade a trail."""
    for reach in (chun, chun.primary, chun.before.append()):
        with pytest.raises(AttributeError, match="drawn or it is not"):
            reach.alpha
        with pytest.raises(AttributeError, match="palette bank"):
            reach.alpha = 0.5


# -- being seen, and being there ---------------------------------------


def test_the_four_states_are_four_distinct_things(chun: Sprite) -> None:
    """Two independent bits over one presence, not a gate and a modifier."""
    assert (chun.visible, chun.collide) == (True, True)  # ordinary
    chun.collide = False
    assert (chun.visible, chun.collide) == (True, False)  # a ghost
    chun.visible, chun.collide = False, True
    assert (chun.visible, chun.collide) == (False, True)  # a cloak
    chun.collide = False
    assert (chun.visible, chun.collide) == (False, False)  # parked


def test_visible_dirties_staging_alone(chun: Sprite) -> None:
    """A blinking sprite must never restamp a plane it could not have
    changed."""
    chun._dirty = chun._collision_dirty = False
    chun.visible = False
    assert chun._dirty is True
    assert chun._collision_dirty is False


def test_collide_dirties_the_masks_alone(chun: Sprite) -> None:
    chun._dirty = chun._collision_dirty = False
    chun.collide = False
    assert chun._collision_dirty is True
    assert chun._dirty is False


@pytest.mark.parametrize(
    "write",
    [
        lambda s: setattr(s, "position", (1, 2)),
        lambda s: setattr(s, "x", 3.0),
        lambda s: setattr(s, "assembly", "walk-0"),
        lambda s: setattr(s, "origin", (2, 2)),
        lambda s: setattr(s, "orientation", Orientation.FLIP_H),
        lambda s: s.palettes.__setitem__("character", 1),
    ],
)
def test_moving_the_pose_dirties_both(chun: Sprite, write) -> None:
    """Anything that changes WHERE or WHAT is presented changes both the
    quad and the stamp."""
    chun._dirty = chun._collision_dirty = False
    write(chun)
    assert chun._dirty is True
    assert chun._collision_dirty is True


# -- it does not draw yet ----------------------------------------------


def test_compositing_is_a_no_op(chun: Sprite) -> None:
    """Inherited from `Layer`. Adding one to a draw order is legal and shows
    nothing, which is where `TileMap` sat between its shell and its draw."""
    assert chun.composite() is None
