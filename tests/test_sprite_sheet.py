"""`SpriteSheet`: the two roads in, and what the loader refuses.

Both fixtures live in the engine repo and are first-party, so the whole of
this runs from a clean checkout with no GL context:

    bps/assets/fumiko/     32 one-part poses, 4 palettes, 8 clips, no planes
    bps/assets/bullets/    2 poses, 1 palette, no clips, [-4, -4] baked in

Chun-Li is Capcom's and lives in the `testsuite` repo; the 3198-assembly and
six-plane assertions belong there, not here.

The refusal tests are most of the file on purpose. The loader's whole job is
that a bad export fails HERE, with the offending tileset or assembly named,
rather than as garbage pixels three subsystems later -- so each documented
invariant gets a package built to break it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blitspersecond.common import PACKAGE_ROOT, Location, Rect
from blitspersecond.graphics import PaletteBank, PixelBuffer
from blitspersecond.graphics.sprite import GRID_TILESET, SpriteSheet

#: `bps/assets/` is repo furniture beside the package, not shipped inside it.
ASSETS = PACKAGE_ROOT.parent / "assets"
FUMIKO = str(ASSETS / "fumiko" / "fumiko.sprite.json")
BULLETS = str(ASSETS / "bullets" / "bullets.sprite.json")
BULLETS_PNG = str(ASSETS / "bullets" / "bullets.png")


@pytest.fixture(autouse=True)
def _drop_interned_sheets():
    """Each test gets a cold cache.

    `SpriteSheet` interns by path in a weak table, so a sheet another test
    still holds would be handed over as a cache hit -- which is the behaviour
    under test in one place and a confounder everywhere else.
    """
    yield
    SpriteSheet._instances.clear()


# -- the package road --------------------------------------------------


def test_fumiko_loads_whole() -> None:
    sheet = SpriteSheet(FUMIKO)
    assert set(sheet.tilesets) == {"character"}
    tileset = sheet.tilesets["character"]
    assert len(tileset.atlas) == 32
    # A bank is always 256 rows, so "four palettes" is no longer a length to
    # assert. What is still checkable, and is the actual claim, is that
    # fumiko's four LANDED: rows 0..3 differ from each other and from the
    # system palette every bank comes up with, and row 4 is untouched.
    bank = tileset.palette
    assert len(bank) == 256
    authored = {bank.texels[row].tobytes() for row in range(4)}
    assert len(authored) == 4, "the four tables are not four tables"
    assert tuple(bank[4][1]) == (255, 255, 255, 255), "row 4 is not still WHITE"
    # The package's `default_palette` lands on the bank's own register --
    # a tileset has no second field for it.
    assert tileset.palette.active == 0
    assert len(sheet.assemblies) == 32
    assert all(len(a.parts) == 1 for a in sheet.assemblies.values())
    assert len(sheet.clips) == 8
    assert set(sheet.clips) == {
        "idle", "walk", "run", "jump", "f-attack", "attack", "blk", "super",
    }
    # No authored planes: the silhouette is all there is. A real, exercised
    # case rather than a degenerate one.
    assert sheet.planes == frozenset()
    assert sheet.grid is None


def test_bullets_keep_their_baked_offset() -> None:
    """`[-4, -4]` on an 8x8 tile is the package saying "position IS the
    centre" -- what danmaku spawn maths and a small central hitbox both
    want."""
    sheet = SpriteSheet(BULLETS)
    assert set(sheet.assemblies) == {"blue", "pink"}
    for name in ("blue", "pink"):
        (part,) = sheet.assemblies[name].parts
        assert part.offset == Location(-4, -4)
        assert sheet.tilesets[part.tileset].atlas.rect(part.tile).width == 8
    assert sheet.clips == {}


def test_clips_carry_integer_tick_holds() -> None:
    """Ticks are the only time unit; milliseconds never enter."""
    idle = SpriteSheet(FUMIKO).clips["idle"]
    assert len(idle.frames) == 3
    for frame in idle.frames:
        assert isinstance(frame.duration_ticks, int)
        assert frame.duration_ticks > 0


def test_an_assembly_need_not_belong_to_any_clip() -> None:
    """Clips reference assemblies, never the reverse -- so a pose presented
    directly (`sprite.assembly = ...`) and never played is legal.

    Not a hypothetical: fumiko has exactly one. Aseprite will not let you tag
    a single frame that is not part of a loop, so a standalone pose comes out
    of the compiler untagged and named `frame-<n>` instead of `<tag>-<n>`,
    belonging to no clip at all. Pinned because it is easy to assume the
    assembly set and the clip set are the same set, and a loader that
    "tidied up" an orphan would silently delete a usable pose.
    """
    sheet = SpriteSheet(FUMIKO)
    played = {f.assembly for clip in sheet.clips.values() for f in clip.frames}
    orphans = set(sheet.assemblies) - played
    assert orphans == {"frame-0"}
    assert sheet.assembly("frame-0").parts  # and it is a real, drawable pose


def test_every_clip_frame_names_a_real_assembly() -> None:
    sheet = SpriteSheet(FUMIKO)
    for clip in sheet.clips.values():
        for frame in clip.frames:
            assert frame.assembly in sheet.assemblies


def test_parts_resolve_through_the_atlas() -> None:
    """A part names a tileset and a tile; the tileset's atlas turns that into
    a rect and pixels. That is the whole draw-side lookup."""
    sheet = SpriteSheet(FUMIKO)
    (part,) = sheet.assemblies["idle-0"].parts
    atlas = sheet.tilesets[part.tileset].atlas
    rect = atlas.rect(part.tile)
    assert (rect.width, rect.height) == (24, 32)
    assert atlas[part.tile].shape == (32, 24)


# -- interning ---------------------------------------------------------


def test_the_same_package_gives_the_same_sheet() -> None:
    """One parse, one set of palette tables, one texture. A mirror match is
    two sprites binding THIS sheet."""
    assert SpriteSheet(FUMIKO) is SpriteSheet(FUMIKO)


def test_a_tileset_holds_its_palettes_as_one_bank() -> None:
    """The bank is the unit the GPU stores -- one 256x256 table with a row
    per palette, which is what keeps `sprite.palette_index[...] = 3` a row NUMBER
    rather than a bind. Every bank is that shape, so a row number is always
    valid and no caller has to ask how many there are."""
    bank = SpriteSheet(FUMIKO).tilesets["character"].palette
    assert isinstance(bank, PaletteBank)
    assert len(bank) == 256
    assert bank.texels.shape == (256, 256, 4)
    # Rows are distinct tables, not one table seen four times.
    assert len({bank.texels[row].tobytes() for row in range(4)}) == 4
    with pytest.raises(IndexError):
        bank[256]


def test_copy_owns_its_palettes_but_shares_its_pixels() -> None:
    """The escape from interning, and it is colour-shaped: a super-flash must
    not reach the other holder, but pixels are shared by `PixelBuffer`
    regardless and copying them is a different request."""
    sheet = SpriteSheet(FUMIKO)
    twin = sheet.copy()
    assert twin is not sheet
    assert twin.tilesets["character"].atlas is sheet.tilesets["character"].atlas

    original = sheet.tilesets["character"].palette[0]
    copied = twin.tilesets["character"].palette[0]
    assert copied is not original
    # A new bank as well as new tables: a bank owns its GPU table, so a twin
    # sharing one would share the upload too.
    assert twin.tilesets["character"].palette is not sheet.tilesets[
        "character"
    ].palette
    copied[5] = (255, 0, 255)
    assert original[5] != copied[5]


def test_a_grid_sheet_is_keyed_by_how_it_was_cut() -> None:
    """Two cell sizes over one image are two different sets of assemblies,
    and neither is the other's."""
    eights = SpriteSheet(BULLETS_PNG, 8, 8)
    assert SpriteSheet(BULLETS_PNG, 8, 8) is eights
    assert SpriteSheet(BULLETS_PNG, 8, 8, -4, -4) is not eights
    assert SpriteSheet(BULLETS_PNG, 16, 8) is not eights


# -- the grid road -----------------------------------------------------


def test_a_bare_image_becomes_a_sheet() -> None:
    sheet = SpriteSheet(BULLETS_PNG, 8, 8)
    assert sheet.grid == (2, 1)
    assert set(sheet.tilesets) == {GRID_TILESET}
    assert set(sheet.assemblies) == {"0", "1"}
    assert sheet.clips == {} and sheet.planes == frozenset()
    (part,) = sheet.assemblies["0"].parts
    assert part.offset == Location(0, 0)


def test_the_grid_offset_is_the_formats_own() -> None:
    """`ox`/`oy` say what a package's `offset` says, with the same sign."""
    sheet = SpriteSheet(BULLETS_PNG, 8, 8, -4, -4)
    (part,) = sheet.assemblies["0"].parts
    assert part.offset == Location(-4, -4)


def test_a_grid_sheet_is_addressable_by_index_and_cell() -> None:
    sheet = SpriteSheet(BULLETS_PNG, 8, 8)
    assert sheet.assembly_name(1) == "1"
    assert sheet.assembly_name((1, 0)) == "1"
    assert sheet.assembly(0) is sheet.assemblies["0"]


def test_a_package_refuses_index_addressing() -> None:
    """Authored names are not an ordering, so cell 3 of a package is a
    coincidence rather than an address."""
    with pytest.raises(TypeError, match="authored names"):
        SpriteSheet(FUMIKO).assembly_name(3)


@pytest.mark.parametrize("bad", [2, (2, 0), (0, 1)])
def test_a_grid_address_outside_the_grid_raises(bad) -> None:
    with pytest.raises(IndexError):
        SpriteSheet(BULLETS_PNG, 8, 8).assembly_name(bad)


def test_an_unknown_assembly_names_the_vocabulary() -> None:
    with pytest.raises(KeyError) as excinfo:
        SpriteSheet(FUMIKO).assembly("nope")
    message = str(excinfo.value)
    assert "nope" in message
    # It says what IS there, and how much of it it is not showing.
    assert "attack-0" in message
    assert "32 total" in message


def test_a_non_indexed_image_is_refused() -> None:
    """A sprite tileset is an index texture read through a bank; an RGB
    picture has no indices to read."""
    with pytest.raises(ValueError, match="indexed"):
        SpriteSheet(str(ASSETS / "splashscreen" / "splashscreen.png"), 8, 8)


# -- what the loader refuses -------------------------------------------


def _package(tmp_path: Path, **overrides) -> str:
    """A minimal valid package, with `overrides` merged over it."""
    image = tmp_path / "sheet.png"
    PixelBuffer(str(image)) if image.exists() else None
    buffer = PixelBuffer((16, 8), "P")
    from PIL import Image

    Image.fromarray(buffer.pixels, mode="P").save(image)
    data = {
        "version": 1,
        "tilesets": {
            "main": {
                "image": "sheet.png",
                "transparent_index": 0,
                "default_palette": 0,
                "palettes": [["#00000000"] + ["#112233ff"] * 3],
                "tiles": {"a": {"source": [0, 0, 8, 8]}},
            }
        },
        "assemblies": {
            "pose": {
                "parts": [
                    {"tileset": "main", "tile": "a", "offset": [0, 0], "order": 0}
                ]
            }
        },
        "animations": {"go": {"frames": [{"assembly": "pose", "duration_ticks": 2}]}},
    }
    for path, value in overrides.items():
        target = data
        *walk, leaf = path.split(".")
        for step in walk:
            target = target[step]
        if value is _DELETE:
            del target[leaf]
        else:
            target[leaf] = value
    out = tmp_path / "test.sprite.json"
    out.write_text(json.dumps(data), encoding="utf-8")
    return str(out)


_DELETE = object()


def test_the_minimal_package_is_valid(tmp_path: Path) -> None:
    """The control: every refusal below is one edit away from this."""
    sheet = SpriteSheet(_package(tmp_path))
    assert len(sheet.assemblies) == 1 and len(sheet.clips) == 1


@pytest.mark.parametrize(
    "overrides, names",
    [
        ({"version": 2}, ["version"]),
        ({"tilesets": {}}, ["no tilesets"]),
        ({"assemblies": {}}, ["no assemblies"]),
        ({"tilesets.main.transparent_index": 3}, ["tileset 'main'", "requires 0"]),
        ({"tilesets.main.palettes": []}, ["tileset 'main'", "no palettes"]),
        ({"tilesets.main.default_palette": 4}, ["tileset 'main'", "out of range"]),
        ({"tilesets.main.tiles": {}}, ["tileset 'main'", "no tiles"]),
        (
            {"tilesets.main.tiles": {"a": {"source": [0, 0, 99, 8]}}},
            ["tileset 'main'", "runs outside"],
        ),
        (
            {"tilesets.main.tiles": {"a": {"source": [0, 0, 0, 8]}}},
            ["tile 'a'", "non-positive"],
        ),
        (
            {"tilesets.main.tiles": {"a": {"source": [-1, 0, 8, 8]}}},
            ["tile 'a'", "negative origin"],
        ),
        (
            {"tilesets.main.palettes": [["#00000000", "#11223300"]]},
            ["palette 0", "all-or-nothing"],
        ),
        (
            {"tilesets.main.palettes": [["#112233ff"]]},
            ["palette 0", "all-or-nothing"],
        ),
        (
            {"tilesets.main.palettes": [["#00000000", "nope"]]},
            ["palette 0", "#rrggbbaa"],
        ),
    ],
)
def test_a_bad_tileset_is_named_in_the_error(
    tmp_path: Path, overrides: dict, names: list
) -> None:
    with pytest.raises(ValueError) as excinfo:
        SpriteSheet(_package(tmp_path, **overrides))
    message = str(excinfo.value)
    for fragment in names:
        assert fragment in message, f"{fragment!r} missing from {message!r}"


@pytest.mark.parametrize(
    "parts, names",
    [
        ([{"tileset": "nope", "tile": "a", "offset": [0, 0]}], ["unknown tileset"]),
        ([{"tileset": "main", "tile": "z", "offset": [0, 0]}], ["unknown tile 'z'"]),
        ([{"tileset": "main", "tile": "a", "offset": [0]}], ["offset"]),
        (
            [
                {"tileset": "main", "tile": "a", "offset": [0, 0], "order": 3},
                {"tileset": "main", "tile": "a", "offset": [0, 0], "order": 1},
            ],
            ["non-decreasing"],
        ),
        (
            [
                {
                    "tileset": "main",
                    "tile": "a",
                    "offset": [0, 0],
                    "palette": 1,
                }
            ],
            ["per-part 'palette' was removed"],
        ),
    ],
)
def test_a_bad_part_is_named_in_the_error(
    tmp_path: Path, parts: list, names: list
) -> None:
    with pytest.raises(ValueError) as excinfo:
        SpriteSheet(_package(tmp_path, **{"assemblies.pose.parts": parts}))
    message = str(excinfo.value)
    assert "assembly 'pose'" in message
    for fragment in names:
        assert fragment in message, f"{fragment!r} missing from {message!r}"


@pytest.mark.parametrize(
    "frames, fragment",
    [
        ([{"assembly": "nope", "duration_ticks": 1}], "unknown assembly"),
        ([{"assembly": "pose", "duration_ticks": 0}], "positive whole"),
        ([{"assembly": "pose", "duration_ticks": 1.5}], "positive whole"),
        ([{"assembly": "pose"}], "positive whole"),
        ([], "no frames"),
    ],
)
def test_a_bad_clip_is_named_in_the_error(
    tmp_path: Path, frames: list, fragment: str
) -> None:
    with pytest.raises(ValueError) as excinfo:
        SpriteSheet(_package(tmp_path, **{"animations.go.frames": frames}))
    message = str(excinfo.value)
    assert "animation 'go'" in message
    assert fragment in message


def test_a_blank_pose_is_allowed(tmp_path: Path) -> None:
    """Six of Chun-Li's 3198 assemblies have no parts. Deliberate, not a bad
    export -- presenting nothing is a real state."""
    sheet = SpriteSheet(_package(tmp_path, **{"assemblies.pose.parts": []}))
    assert sheet.assemblies["pose"].parts == ()


def test_collision_boxes_are_signed_and_collected_into_the_vocabulary(
    tmp_path: Path,
) -> None:
    """Plane rects are in the assembly's local frame, where (0, 0) is the
    anchor -- so negative origins are the normal case, not an error."""
    path = _package(
        tmp_path,
        **{
            "assemblies.pose.collision": {
                "hurt": [[-8, -16, 16, 32]],
                "attack": [[4, -12, 10, 6]],
            }
        },
    )
    sheet = SpriteSheet(path)
    assert sheet.assemblies["pose"].collision["hurt"] == (Rect(-8, -16, 16, 32),)
    assert sheet.planes == frozenset({"hurt", "attack"})


# -- the primary tileset ----------------------------------------------


def _two_tileset_package(tmp_path: Path, **overrides) -> str:
    """The minimal package with a second tileset, `fx`, authored after `main`."""
    fx = {
        "image": "sheet.png",
        "transparent_index": 0,
        "default_palette": 0,
        "palettes": [["#00000000"] + ["#445566ff"] * 3],
        "tiles": {"b": {"source": [8, 0, 8, 8]}},
    }
    merged = {"tilesets.fx": fx}
    merged.update(overrides)
    return _package(tmp_path, **merged)


def test_a_single_tileset_package_needs_no_declaration(tmp_path: Path) -> None:
    """The common case says nothing and gets the only answer there is."""
    sheet = SpriteSheet(_package(tmp_path))
    assert sheet.primary_tileset == "main"


def test_a_package_with_a_choice_must_make_it(tmp_path: Path) -> None:
    """Deliberately NOT "the first one wins". JSON object order is not a
    contract -- a prettifier or a `sort_keys=True` round-trip may reorder the
    file, and a silently different primary tileset shows up as art that
    stopped drawing rather than as an error."""
    with pytest.raises(ValueError) as excinfo:
        SpriteSheet(_two_tileset_package(tmp_path))
    message = str(excinfo.value)
    assert "2 tilesets" in message
    assert "main" in message and "fx" in message
    assert '"primary": true' in message  # it says what to write


def test_the_flag_is_what_decides(tmp_path: Path) -> None:
    """`fx` is written second and wins, because position is not the rule."""
    sheet = SpriteSheet(_two_tileset_package(tmp_path, **{"tilesets.fx.primary": True}))
    assert sheet.primary_tileset == "fx"


def test_two_tilesets_cannot_both_claim_it(tmp_path: Path) -> None:
    """The one error class the top-level-pointer encoding did not have, and the
    reason this flag is validated rather than just read: a flag ON each object
    cannot express "exactly one" structurally, so the loader must."""
    with pytest.raises(ValueError) as excinfo:
        SpriteSheet(
            _two_tileset_package(
                tmp_path,
                **{"tilesets.main.primary": True, "tilesets.fx.primary": True},
            )
        )
    message = str(excinfo.value)
    assert "main" in message and "fx" in message
    assert "ONE texture" in message


def test_marking_it_false_is_not_claiming_it(tmp_path: Path) -> None:
    """An exporter writing the flag on every tileset should not trip the
    two-claimants check by writing `false`."""
    sheet = SpriteSheet(
        _two_tileset_package(
            tmp_path,
            **{"tilesets.main.primary": False, "tilesets.fx.primary": True},
        )
    )
    assert sheet.primary_tileset == "fx"


def test_copy_carries_the_primary_tileset_flagged(tmp_path: Path) -> None:
    sheet = SpriteSheet(_two_tileset_package(tmp_path, **{"tilesets.fx.primary": True}))
    assert sheet.copy().primary_tileset == "fx"


def test_a_grid_sheet_is_its_one_synthetic_tileset(tmp_path: Path) -> None:
    """Nothing to choose, and nothing to author -- there is no package."""
    image = tmp_path / "grid.png"
    from PIL import Image

    Image.fromarray(PixelBuffer((16, 16), "P").pixels, mode="P").save(image)
    sheet = SpriteSheet(str(image), 8, 8)
    assert sheet.primary_tileset == GRID_TILESET
    assert list(sheet.tilesets) == [GRID_TILESET]


def test_the_first_party_fixtures_answer_it(tmp_path: Path) -> None:
    """Both shipped packages are single-tileset, so both take the default and
    neither had to be edited to gain this field."""
    assets = PACKAGE_ROOT.parent / "assets"
    bullets = SpriteSheet(str(assets / "bullets" / "bullets.sprite.json"))
    assert bullets.primary_tileset in bullets.tilesets
    assert len(bullets.tilesets) == 1


# -- the modify path ------------------------------------------------------
#
# `extended` and `shadowed` were tested here. Banks do not grow -- every one
# is 256 rows and a derived shade goes into a spare row the author names --
# so the extension tests have no subject. `shadowed` became two operators
# doing one job each, which is what these prove.


def _art() -> "PaletteBank":
    """A bank whose row 0 is a two-entry ramp: nearly black, nearly white."""
    bank = PaletteBank()
    bank[0][1] = (20, 20, 20)
    bank[0][2] = (200, 200, 200)
    return bank


def test_a_duotone_keeps_the_arts_light_and_shade() -> None:
    """A duotone, not a blend toward the colour. Lerping toward a bright
    signature drags DARK pixels up, so a white character's shadow comes out
    brighter than she is -- which is what this asserts cannot happen, and is
    why `duotone` and `blend` are two operators rather than one."""
    bank = _art()
    bank[40] = bank[0]
    bank[40].duotone((255, 255, 255), 1.0)
    assert bank[40][1][:3] < bank[40][2][:3], "the duotone lost the art's ordering"
    assert sum(bank[40][1][:3]) <= sum(bank[0][1][:3]) + 3, (
        "a dark pixel got BRIGHTER under a white signature"
    )


def test_a_blend_toward_a_flat_colour_is_the_thing_duotone_avoids() -> None:
    """The other half of the argument, stated as a test so the distinction
    cannot be quietly collapsed later: blending toward a flat white table
    DOES drag the dark entry up, which is exactly why the trail uses a
    duotone."""
    bank = _art()
    bank[9] = [(255, 255, 255)] * 256  # a flat white table to blend toward
    bank[40] = bank[0]
    bank[40].blend(bank[9], 1.0)
    assert sum(bank[40][1][:3]) > sum(bank[0][1][:3]), (
        "a lerp toward white left a dark entry alone -- it should raise it"
    )


def test_shades_darken_monotonically() -> None:
    """Each step of a trail is dimmer than the last."""
    steps = 6
    means = []
    for k in range(1, steps + 1):
        bank = PaletteBank()
        bank[0][9] = (180, 120, 60)
        bank[40] = bank[0]
        bank[40].duotone((200, 40, 40), k / steps).darken(0.8 * k / steps)
        means.append(sum(bank[40][9][:3]))
    assert means == sorted(means, reverse=True), means


def test_an_operator_keeps_the_transparent_index() -> None:
    """A shadow of a hole is still a hole. Alpha is decided by the index, and
    no operator touches it -- which is why the pin survives all of them
    without any of them knowing about it."""
    bank = PaletteBank()
    bank[0][1] = (90, 90, 90)
    bank[0].duotone((10, 200, 10), 1.0).darken(0.5).negate()
    assert bank[0][0][3] == 0
    assert bank[0][1][3] == 255
