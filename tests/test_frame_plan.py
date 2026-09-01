"""The frame plan: presentations projected into quads and runs.

The plan is where this pillar's cost lives, and almost none of it is visible
in a picture -- a scene that draws correctly in five runs looks identical to
one that draws correctly in one. So these check the SHAPE of the work as much
as the numbers:

**Runs are discovered, not declared.** A uniform population must find zero
material boundaries and emit one run, reaching by diff what reference reached
by holding its material once per pool.

**Order is never rearranged.** Coalescing merges only adjacent quads, because
a material boundary is exactly where transparency stops commuting.

**One orientation formula.** `local_rect` will be shared with collision
stamping, so it is tested as a function in its own right rather than only
through the staging that calls it.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from blitspersecond.common import PACKAGE_ROOT, Location, Rect
from blitspersecond.graphics import PixelBuffer
from blitspersecond.graphics.orientation import Orientation
from blitspersecond.graphics.sprite import Sprite, SpriteSheet
from blitspersecond.graphics.sprite.frame import local_rect

ASSETS = PACKAGE_ROOT.parent / "assets"


@pytest.fixture
def bullets() -> Sprite:
    return Sprite(SpriteSheet(str(ASSETS / "bullets" / "bullets.sprite.json")))


# -- the one orientation formula ---------------------------------------


def test_the_identity_orientation_is_offset_minus_origin() -> None:
    """`drawn at = position + offset - origin`, which is the whole placement
    contract stated on `Sprite.origin`."""
    assert local_rect(
        Orientation.NONE, Location(10, 20), Rect(0, 0, 8, 12), Location(4, 6)
    ) == (6, 14, 8, 12)


def test_a_transposing_orientation_swaps_width_and_height() -> None:
    """The load-bearing distinction in the orientation vocabulary, and the
    reason a sprite may take all eight where a grid tile may not: a sprite
    has no cell to return to and no neighbours to overflow."""
    for turn in (
        Orientation.TRANSPOSE,
        Orientation.ROT_90,
        Orientation.ROT_270,
        Orientation.ANTI_TRANSPOSE,
    ):
        _, _, w, h = local_rect(
            turn, Location(0, 0), Rect(0, 0, 8, 12), Location(0, 0)
        )
        assert (w, h) == (12, 8), turn


def test_the_shape_preserving_four_leave_the_size_alone() -> None:
    for turn in (
        Orientation.NONE,
        Orientation.FLIP_H,
        Orientation.FLIP_V,
        Orientation.ROT_180,
    ):
        _, _, w, h = local_rect(
            turn, Location(0, 0), Rect(0, 0, 8, 12), Location(0, 0)
        )
        assert (w, h) == (8, 12), turn


def test_mirroring_is_about_the_origin_not_the_art() -> None:
    """Which is what makes two fighters' `x` values a real distance whatever
    their limbs are doing: turning Chun-Li round leaves her body where it was.

    A part sitting flush at the origin mirrors to sit flush on the other
    side, so `x` goes from 0 to -width.
    """
    at_origin = Rect(0, 0, 8, 8)
    assert local_rect(
        Orientation.FLIP_H, Location(0, 0), at_origin, Location(0, 0)
    ) == (-8, 0, 8, 8)
    assert local_rect(
        Orientation.FLIP_V, Location(0, 0), at_origin, Location(0, 0)
    ) == (0, -8, 8, 8)


def test_a_centred_part_is_unmoved_by_any_orientation() -> None:
    """A symmetric case with a known answer: art centred on the origin has
    nowhere to go under a mirror, and a square one has nowhere to go under a
    turn either. If the transform order were wrong this would still pass --
    which is why the rectangular cases above exist."""
    for turn in range(8):
        assert local_rect(
            turn, Location(-4, -4), Rect(0, 0, 8, 8), Location(0, 0)
        ) == (-4, -4, 8, 8)


def test_transpose_is_applied_before_the_mirrors() -> None:
    """The order the flag bits document, and the order the tile pillar
    speaks. Backwards, this is invisible on a square symmetric source and
    wrong on everything else -- so it is checked on a rectangle offset off
    the origin, where the two orders genuinely differ."""
    got = local_rect(
        Orientation.ROT_90, Location(3, 1), Rect(0, 0, 8, 2), Location(0, 0)
    )
    # transpose (3,1,8,2) -> (1,3,2,8); then FLIP_H -> x = -(1+2) = -3
    assert got == (-3, 3, 2, 8)


# -- one run when everything matches -----------------------------------


def test_a_uniform_population_is_one_run(bullets: Sprite) -> None:
    """The claim the whole collapse rests on: reference got this by holding
    its material once per pool, and it must fall out here from a diff that
    finds no boundaries."""
    swarm = bullets.after.extend(1_000)
    for p in swarm:
        p.assembly = "blue"
        p.visible = True
    bullets.assembly = "blue"
    bullets._sync()
    assert bullets._plan.quads == 1_001
    assert len(bullets._plan.runs) == 1
    assert bullets._plan.runs[0][3:] == (0, 1_001)


def test_two_assemblies_in_one_tileset_stay_one_run(bullets: Sprite) -> None:
    """Blue and pink bullets are two ASSEMBLIES, not two materials -- which
    is why per-slot assembly is free and expressing colour variety as tiles
    keeps a population at one bind."""
    swarm = bullets.after.extend(100)
    for n, p in enumerate(swarm):
        p.assembly = "pink" if n % 2 else "blue"
        p.visible = True
    bullets._sync()
    assert len(bullets._plan.runs) == 1


def test_25000_presentations_collapse_to_one_run(bullets: Sprite) -> None:
    """The scale case, stated as the card's acceptance criterion."""
    swarm = bullets.after.extend(25_000)
    ids = swarm.slots
    bullets._slots.assembly[ids] = bullets._assembly_ids["blue"]
    bullets._slots.visible[ids] = True
    bullets.assembly = "blue"
    bullets._touch()
    bullets._sync()
    assert bullets._plan.quads == 25_001
    assert len(bullets._plan.runs) == 1


# -- runs are cut where they must be -----------------------------------


def test_differing_colours_cut_runs(bullets: Sprite) -> None:
    """A trail's fade is per presentation, and colour is part of a run's
    material, so four differently-faded ghosts are four runs. They share one
    texture bind between them, which is the expensive part."""
    ghosts = bullets.before.extend(4)
    for n, ghost in enumerate(ghosts):
        ghost.assembly = "blue"
        ghost.alpha = 0.2 + n * 0.2
        ghost.visible = True
    bullets.assembly = "blue"
    bullets._sync()
    assert len(bullets._plan.runs) == 5  # four fades, then the primary


def test_adjacent_equal_colours_merge(bullets: Sprite) -> None:
    ghosts = bullets.before.extend(4)
    for ghost in ghosts:
        ghost.assembly = "blue"
        ghost.alpha = 0.5
        ghost.visible = True
    bullets.assembly = "blue"
    bullets._sync()
    # the four ghosts are one run; the primary's alpha is 1.0, so it is another
    assert len(bullets._plan.runs) == 2
    assert bullets._plan.runs[0][3:] == (0, 4)


def test_a_material_is_never_merged_across_a_gap(bullets: Sprite) -> None:
    """Order-preserving means ADJACENT-only. Two stretches of one material
    with another between them stay two runs -- merging them would move quads
    past a boundary, and transparency does not commute across one."""
    ghosts = bullets.before.extend(3)
    for ghost in ghosts:
        ghost.assembly = "blue"
        ghost.visible = True
    ghosts[0].alpha = 1.0
    ghosts[1].alpha = 0.5
    ghosts[2].alpha = 1.0
    bullets._sync()
    runs = bullets._plan.runs
    assert [(r[3], r[4]) for r in runs][:3] == [(0, 1), (1, 1), (2, 1)]
    assert runs[0][:3] == runs[2][:3]  # same material, still two runs


# -- draw order ---------------------------------------------------------


def test_quads_are_staged_before_primary_after(bullets: Sprite) -> None:
    """The emit order IS the draw order, and there is no sort anywhere."""
    back = bullets.before.append()
    front = bullets.after.append()
    for p in (back, front):
        p.assembly = "blue"
        p.visible = True
    bullets.assembly = "blue"
    bullets.position = (100, 0)
    back.position = (0, 0)
    front.position = (200, 0)
    bullets._sync()
    xs = bullets._plan.verts[: bullets._plan.quads, 0, 0]
    assert list(xs) == sorted(xs)  # 0, 100, 200 -- back, primary, front


# -- what contributes nothing ------------------------------------------


def test_a_parked_presentation_stages_no_quad(bullets: Sprite) -> None:
    ghost = bullets.before.append()
    ghost.assembly = "blue"
    ghost.visible = False
    bullets.assembly = "blue"
    bullets._sync()
    assert bullets._plan.quads == 1


def test_presenting_nothing_stages_no_quad(bullets: Sprite) -> None:
    bullets.assembly = None
    bullets._sync()
    assert bullets._plan.quads == 0
    assert bullets._plan.runs == []


def test_a_blank_pose_contributes_nothing(tmp_path: Path) -> None:
    """Six of Chun-Li's 3198 assemblies have no parts at all. A blank has a
    valid id, so it survives the "presents nothing" filter -- and would stage
    a zero-area quad if nothing else caught it."""
    sprite = Sprite(SpriteSheet(_package(tmp_path)))
    ghost = sprite.before.append()
    ghost.assembly = "blank"
    ghost.visible = True
    sprite.assembly = "blank"
    sprite._sync()
    assert sprite._plan.quads == 0


# -- the primary's parts ------------------------------------------------


def test_a_multipart_primary_produces_one_run_per_part(tmp_path: Path) -> None:
    """`character` + `effects` are two tilesets, so two buffers, so a
    material boundary that cannot be merged away."""
    sprite = Sprite(SpriteSheet(_package(tmp_path)))
    sprite.assembly = "both"
    sprite._sync()
    assert sprite._plan.quads == 2
    assert len(sprite._plan.runs) == 2


def test_the_primarys_parts_stay_in_authored_order(tmp_path: Path) -> None:
    """`order` is what the artist said draws on top, and the plan must not
    reorder it -- the effects part is authored second and stages second."""
    sprite = Sprite(SpriteSheet(_package(tmp_path)))
    sprite.assembly = "both"
    sprite._sync()
    buffers = [run[0] for run in sprite._plan.runs]
    main = sprite.sheet.tilesets["main"].atlas.buffer
    assert buffers[0] is main
    assert buffers[1] is not main


def test_a_single_part_primary_merges_with_the_chain(tmp_path: Path) -> None:
    """The common case, and the payoff: when the primary draws the same
    material as the population around it, the whole layer is ONE run."""
    sprite = Sprite(SpriteSheet(_package(tmp_path)))
    ghosts = sprite.before.extend(3)
    for ghost in ghosts:
        ghost.assembly = "pose"
        ghost.visible = True
    sprite.assembly = "pose"
    sprite._sync()
    assert sprite._plan.quads == 4
    assert len(sprite._plan.runs) == 1


# -- retained staging ---------------------------------------------------


def test_a_still_scene_rebuilds_nothing(bullets: Sprite) -> None:
    """`_sync` is dirty-gated, so a paused frame costs a boolean check."""
    bullets.assembly = "blue"
    bullets._sync()
    before = bullets._plan.verts
    bullets._plan.verts = None  # type: ignore[assignment]
    bullets._sync()  # must not touch it
    assert bullets._plan.verts is None
    bullets._plan.verts = before
    bullets.position = (1, 1)  # now it is dirty again
    bullets._sync()
    assert bullets._plan.verts is before  # rewritten in place, not replaced


def test_the_arrays_grow_by_doubling_and_then_stop(bullets: Sprite) -> None:
    """A steady scene allocates nothing: growth is only ever reached from a
    rebuild that needs more room than last time."""
    swarm = bullets.after.extend(300)
    for p in swarm:
        p.assembly = "blue"
        p.visible = True
    bullets._sync()
    grown = bullets._plan.verts
    assert grown.shape[0] >= 301
    bullets.position = (5, 5)
    bullets._sync()
    assert bullets._plan.verts is grown


# -- the origin is inside the formula ----------------------------------


def test_moving_the_origin_rebuilds_the_tables(bullets: Sprite) -> None:
    """The origin is inside `local_rect`, so cached rects computed against
    the old one are wrong. Per assembly, not per presentation."""
    bullets.assembly = "blue"
    bullets._sync()
    first = bullets._plan.verts[0, 0].copy()
    bullets.origin = (5, 7)
    bullets._sync()
    moved = bullets._plan.verts[0, 0]
    assert (first[0] - moved[0], first[1] - moved[1]) == (5, 7)


# -- texture coordinates ------------------------------------------------


def test_uvs_are_normalised_into_the_image(bullets: Sprite) -> None:
    bullets.assembly = "blue"
    bullets._sync()
    uv = bullets._plan.uvs[0]
    assert uv.shape == (4, 3)
    assert np.all((uv[:, :2] >= 0.0) & (uv[:, :2] <= 1.0))


def test_turning_permutes_the_corners_rather_than_moving_them(
    bullets: Sprite,
) -> None:
    """A mirror or a quarter turn is the same four points in a different
    order. Keeping it a permutation is what stops half-texel drift creeping
    in as orientations compose."""
    bullets.assembly = "blue"
    bullets._sync()
    straight = {tuple(c) for c in bullets._plan.uvs[0][:, :2]}
    for turn in range(1, 8):
        bullets.orientation = Orientation(turn)
        bullets._sync()
        assert {tuple(c) for c in bullets._plan.uvs[0][:, :2]} == straight


def _package(tmp_path: Path) -> str:
    """Two tilesets over TWO images, and the pose shapes staging tells apart:
    single-part, multipart, and blank.

    Two files matter: `PixelBuffer` interns by path, so one image would make
    both tilesets the same buffer object and a test about material boundaries
    would be testing nothing."""
    from PIL import Image

    Image.fromarray(PixelBuffer((16, 8), "P").pixels, mode="P").save(
        tmp_path / "main.png"
    )
    Image.fromarray(PixelBuffer((16, 8), "P").pixels, mode="P").save(
        tmp_path / "fx.png"
    )
    palettes = [["#00000000"] + ["#112233ff"] * 3]
    data = {
        "version": 1,
        "tilesets": {
            "main": {
                "image": "main.png",
                "primary": True,
                "transparent_index": 0,
                "default_palette": 0,
                "palettes": palettes,
                "tiles": {"a": {"source": [0, 0, 8, 8]}},
            },
            "fx": {
                "image": "fx.png",
                "transparent_index": 0,
                "default_palette": 0,
                "palettes": palettes,
                "tiles": {"b": {"source": [8, 0, 8, 8]}},
            },
        },
        "assemblies": {
            "pose": {
                "parts": [
                    {"tileset": "main", "tile": "a", "offset": [0, 0], "order": 0}
                ]
            },
            "blank": {"parts": []},
            "both": {
                "parts": [
                    {"tileset": "main", "tile": "a", "offset": [0, 0], "order": 0},
                    {"tileset": "fx", "tile": "b", "offset": [0, 0], "order": 1},
                ]
            },
        },
    }
    out = tmp_path / "multi.sprite.json"
    out.write_text(json.dumps(data), encoding="utf-8")
    return str(out)

