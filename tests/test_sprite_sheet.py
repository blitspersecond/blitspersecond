"""The sprite package loader, headless: no GL, no window, pure pytest.

Two halves: the committed fixture (tests/assets/test.sprite.json -- two
tilesets over one 128x128 sheet, differing default palettes, a multipart
mixed-material assembly and a blank one) proves the loader end to end; a tiny
generated package proves every validation error fires, loudly and named.
"""

import json
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blitspersecond.common import Location, Rect
from blitspersecond.resources import (
    Palette,
    ResourceManager,
    SpriteSheetSpec,
    load_sprite_sheet_spec,
)

TEST_SHEET = Path(__file__).resolve().parent / "assets/test.sprite.json"


# -- the real fixture ---------------------------------------------------------


@pytest.fixture(scope="module")
def spec() -> SpriteSheetSpec:
    return load_sprite_sheet_spec(str(TEST_SHEET))


def test_resource_manager_memoises():
    first = ResourceManager().sprite_sheet(str(TEST_SHEET))
    second = ResourceManager().sprite_sheet(str(TEST_SHEET))
    assert first is second


# -- the tiny generated package ----------------------------------------------


def _write_sheet_png(path: Path, mode: str = "P") -> None:
    img = Image.new(mode, (16, 16), 0)
    if mode == "P":
        img.putpalette([0, 0, 0, 255, 0, 0, 0, 255, 0])
        img.save(path, transparency=0)
    else:
        img.save(path)


def _package(image: str = "sheet.png") -> dict:
    return {
        "version": 1,
        "tilesets": {
            "main": {
                "image": image,
                "transparent_index": 0,
                "default_palette": 0,
                "palettes": [["#00000000", "#ff0000ff", "#00ff00ff"]],
                "tiles": {
                    "a": {"source": [0, 0, 8, 8]},
                    "b": {"source": [8, 0, 8, 8]},
                },
            }
        },
        "assemblies": {
            "pose": {
                "parts": [
                    {"tileset": "main", "tile": "a", "offset": [-4, -8], "order": 0}
                ],
                "collision": {"hit": [[-4, -8, 8, 8]]},
            },
            "blank": {"parts": []},
        },
        "animations": {
            "idle": {"frames": [{"assembly": "pose", "duration_ticks": 2}]}
        },
    }


def _load(tmp_path: Path, data: dict, name: str = "pack.sprite.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return load_sprite_sheet_spec(str(path))


@pytest.fixture()
def sheet_dir(tmp_path: Path) -> Path:
    _write_sheet_png(tmp_path / "sheet.png")
    return tmp_path


def test_dummy_package_loads(sheet_dir):
    spec = _load(sheet_dir, _package())
    assert spec.tilesets["main"].tiles["b"] == Rect(8, 0, 8, 8)
    assert spec.assemblies["blank"].parts == ()
    assert spec.assemblies["pose"].collision["hit"] == (Rect(-4, -8, 8, 8),)
    assert len(spec.animations["idle"].frames) == 1
    assert spec.animations["idle"].frames[0].assembly == "pose"
    assert spec.animations["idle"].frames[0].duration_ticks == 2


def test_part_palette_override(sheet_dir):
    data = _package()
    data["tilesets"]["main"]["palettes"].append(
        ["#00000000", "#0000ffff", "#ffff00ff"]
    )
    data["assemblies"]["pose"]["parts"][0]["palette"] = 1
    spec = _load(sheet_dir, data)
    assert spec.assemblies["pose"].parts[0].palette == 1


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda d: d.update(version=2), "version"),
        (lambda d: d["tilesets"]["main"]["tiles"].pop("b") and None, None),
        (
            lambda d: d["assemblies"]["pose"]["parts"][0].update(tile="nope"),
            "unknown tile",
        ),
        (
            lambda d: d["assemblies"]["pose"]["parts"][0].update(tileset="nope"),
            "unknown tileset",
        ),
        (
            lambda d: d["tilesets"]["main"]["tiles"].update(
                oob={"source": [12, 0, 8, 8]}
            ),
            "exceeds image size",
        ),
        (
            lambda d: d["tilesets"]["main"]["tiles"].update(
                flat={"source": [0, 0, 8, 0]}
            ),
            "non-positive size",
        ),
        (
            lambda d: d["tilesets"]["main"]["tiles"].update(
                blank={"source": None}
            ),
            "expected \\[x, y, w, h\\]",
        ),
        (
            lambda d: d["animations"]["idle"]["frames"][0].update(
                assembly="nope"
            ),
            "unknown assembly",
        ),
        (
            lambda d: d["animations"]["idle"]["frames"][0].update(
                duration_ticks=0
            ),
            "positive",
        ),
        (
            lambda d: d["animations"]["idle"]["frames"][0].update(
                duration_ticks=2.5
            ),
            "positive",
        ),
        (lambda d: d["animations"]["idle"].update(frames=[]), "no frames"),
        (
            lambda d: d["tilesets"]["main"].update(transparent_index=1),
            "requires index 0",
        ),
        (
            lambda d: d["tilesets"]["main"]["palettes"][0].__setitem__(
                1, "#ff0000aa"
            ),
            "all-or-nothing",
        ),
        (
            lambda d: d["tilesets"]["main"]["palettes"][0].__setitem__(
                0, "#000000ff"
            ),
            "all-or-nothing",
        ),
        (lambda d: d["tilesets"]["main"].update(palettes=[]), "no palettes"),
        (
            lambda d: d["tilesets"]["main"].update(default_palette=5),
            "default_palette 5 out of range",
        ),
        (
            lambda d: d["assemblies"]["pose"]["parts"][0].update(palette=7),
            "palette 7 out of range",
        ),
        (lambda d: d.update(tilesets={}), "no tilesets"),
        (lambda d: d.update(assemblies={}), "no assemblies"),
    ],
    ids=[
        "bad-version",
        "removed-tile-ok",
        "dangling-tile",
        "dangling-tileset",
        "rect-out-of-bounds",
        "rect-zero-size",
        "null-source",
        "dangling-anim-assembly",
        "zero-duration",
        "float-duration",
        "empty-frames",
        "bad-transparent-index",
        "mid-table-alpha",
        "opaque-index-0",
        "no-palettes",
        "default-palette-range",
        "part-palette-range",
        "no-tilesets",
        "no-assemblies",
    ],
)
def test_validation(sheet_dir, mutate, match):
    data = _package()
    mutate(data)
    if match is None:  # the one mutation that stays legal
        _load(sheet_dir, data, "ok.sprite.json")
        return
    with pytest.raises(ValueError, match=match):
        _load(sheet_dir, data, "bad.sprite.json")


def test_unsorted_parts_rejected(sheet_dir):
    data = _package()
    data["assemblies"]["pose"]["parts"] = [
        {"tileset": "main", "tile": "a", "offset": [0, 0], "order": 1},
        {"tileset": "main", "tile": "b", "offset": [0, 0], "order": 0},
    ]
    with pytest.raises(ValueError, match="non-decreasing"):
        _load(sheet_dir, data)


def test_rgb_image_rejected(tmp_path):
    _write_sheet_png(tmp_path / "rgb.png", mode="RGB")
    with pytest.raises(ValueError, match="indexed"):
        _load(tmp_path, _package(image="rgb.png"))


# -- the runtime SpriteSheet (graphics-side, still GL-free) -------------------


@pytest.fixture(scope="module")
def sheet(spec):
    from blitspersecond.graphics import SpriteSheet

    return SpriteSheet(spec)


def test_sheet_tilesets(sheet):
    from blitspersecond.graphics import PixelBuffer

    assert set(sheet.tilesets) == {"shapes", "marks"}
    character = sheet.tilesets["shapes"]
    assert isinstance(character.buffer, PixelBuffer)
    assert character.buffer.indexed
    assert character.buffer.size == (128, 128)
    assert character.default_palette == 0
    assert sheet.tilesets["marks"].default_palette == 2
    assert character.tiles["shape-0"] == Rect(0, 0, 32, 32)
    assert len(character.palettes) == 4


def test_sheet_carries_spec_records(spec, sheet):
    # Assemblies and animations pass through untouched: the runtime reading
    # is the same immutable records the spec validated.
    assert sheet.assemblies is spec.assemblies
    assert sheet.animations is spec.animations


def test_sheet_palettes_are_copies(spec, sheet):
    # The sheet's palettes are ITS mutable registers; the shared spec in the
    # ResourceManager cache must stay pristine (the PixelBuffer/ImageSpec law).
    spec_pal = spec.tilesets["shapes"].palettes[0]
    sheet_pal = sheet.tilesets["shapes"].palettes[0]
    assert sheet_pal is not spec_pal
    assert tuple(sheet_pal[1]) == tuple(spec_pal[1])
    before = tuple(spec_pal[1])
    sheet_pal[1] = (10, 20, 30, 255)  # super-flash writes go here
    assert tuple(spec_pal[1]) == before


def test_two_sheets_are_independent(spec, sheet):
    from blitspersecond.graphics import SpriteSheet

    other = SpriteSheet(spec)
    assert (
        other.tilesets["shapes"].palettes[0]
        is not sheet.tilesets["shapes"].palettes[0]
    )
    assert other.tilesets["shapes"].buffer is not sheet.tilesets["shapes"].buffer


def test_sheet_load_convenience():
    from blitspersecond.graphics import SpriteSheet

    a = SpriteSheet.load(str(TEST_SHEET))
    b = SpriteSheet.load(str(TEST_SHEET))
    # One parse, two sheets: the spec memoises, the records share identity.
    assert a.assemblies is b.assemblies


def test_sheet_rejects_non_spec():
    from blitspersecond.graphics import SpriteSheet

    with pytest.raises(TypeError):
        SpriteSheet({"tilesets": {}})


# -- the bare-image path (no package file at all) -----------------------------


BAND = (
    Path(__file__).resolve().parents[1]
    / "assets/gorillaz/band.png"
)


@pytest.fixture(scope="module")
def band():
    from blitspersecond.resources import grid_sprite_sheet_spec

    return grid_sprite_sheet_spec(str(BAND), 12, 12)


def test_grid_synthesises_one_tileset(band):
    from blitspersecond.resources.sprite_sheet import GRID_TILESET

    assert list(band.tilesets) == [GRID_TILESET]
    tileset = band.tilesets[GRID_TILESET]
    assert tileset.transparent_index == 0
    assert tileset.default_palette == 0
    # The embedded table IS the table here -- there is no package to override
    # it, which is the one place the bare path inverts the format's law.
    assert len(tileset.palettes) == 1


def test_grid_names_cells_row_major(band):
    from blitspersecond.resources.sprite_sheet import GRID_TILESET

    assert band.grid == (2, 2)
    assert list(band.assemblies) == ["0", "1", "2", "3"]
    tiles = band.tilesets[GRID_TILESET].tiles
    assert tuple(tiles["0"]) == (0, 0, 12, 12)
    assert tuple(tiles["1"]) == (12, 0, 12, 12)
    assert tuple(tiles["2"]) == (0, 12, 12, 12)
    assert tuple(tiles["3"]) == (12, 12, 12, 12)


def test_grid_assemblies_are_one_part_at_the_origin(band):
    part, = band.assemblies["2"].parts
    assert part.tile == "2"
    assert part.offset == Location(0, 0)
    assert part.order == 0
    # No authored planes: the silhouette is all a grid sheet has.
    assert band.assemblies["2"].collision == {}
    assert band.planes == frozenset()
    assert band.animations == {}


def test_grid_rejects_a_non_indexed_image(tmp_path):
    from blitspersecond.resources import grid_sprite_sheet_spec

    path = tmp_path / "rgba.png"
    Image.new("RGBA", (16, 16)).save(path)
    with pytest.raises(ValueError, match="indexed"):
        grid_sprite_sheet_spec(str(path), 8, 8)


def test_grid_rejects_a_partial_cell(tmp_path):
    from blitspersecond.resources import grid_sprite_sheet_spec

    path = tmp_path / "odd.png"
    _write_sheet_png(path)  # 16x16
    with pytest.raises(ValueError, match="whole number"):
        grid_sprite_sheet_spec(str(path), 5, 8)


def test_grid_rejects_a_non_positive_cell(tmp_path):
    from blitspersecond.resources import grid_sprite_sheet_spec

    path = tmp_path / "zero.png"
    _write_sheet_png(path)
    with pytest.raises(ValueError, match="non-positive"):
        grid_sprite_sheet_spec(str(path), 0, 8)


def test_grid_sheet_addresses_by_index_and_cell():
    from blitspersecond.graphics import SpriteSheet

    sheet = SpriteSheet.grid(str(BAND), 12, 12)
    assert sheet.grid_size == (2, 2)
    assert sheet.assembly_name(2) == "2"
    assert sheet.assembly_name((1, 1)) == "3"
    assert sheet.assembly_name("0") == "0"
    with pytest.raises(IndexError):
        sheet.assembly_name(4)
    with pytest.raises(IndexError):
        sheet.assembly_name((2, 0))


def test_package_sheet_refuses_index_addressing():
    from blitspersecond.graphics import SpriteSheet

    sheet = SpriteSheet.load(str(TEST_SHEET))
    assert sheet.grid_size is None
    with pytest.raises(TypeError, match="authored names"):
        sheet.assembly_name(0)


def test_grid_offset_moves_every_anchor():
    from blitspersecond.resources import grid_sprite_sheet_spec

    # The bullets package authors [-4, -4] by hand for 8x8 tiles; ox/oy is
    # the same statement, with the same sign, for a bare sheet.
    spec = grid_sprite_sheet_spec(str(BAND), 12, 12, -6, -6)
    for assembly in spec.assemblies.values():
        part, = assembly.parts
        assert part.offset == Location(-6, -6)
    # Source rects are unmoved -- the offset places the quad, not the crop.
    from blitspersecond.resources.sprite_sheet import GRID_TILESET

    assert tuple(spec.tilesets[GRID_TILESET].tiles["3"]) == (12, 12, 12, 12)
