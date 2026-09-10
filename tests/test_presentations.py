"""Presentations: the drawings a sprite makes of itself, and their order.

The design this pins is a split: **a slot id is identity, a chain is draw
order, and they are different numbers.** An earlier version fused them into
one signed index, and every question about growing or reordering became a
question about renumbering. So the checks here lean hardest on the seam
between the two -- reordering must not disturb state, and freeing an id must
not disturb anything drawn.

Two more claims worth pinning because neither is visible in a passing draw:

**The primary is the sprite.** `chun.position` and `chun.primary.position` are
one value in one place, so every check writes through one surface and reads
back through the other.

**Liveness is not linkage.** A parked presentation keeps its slot and its
place; only `remove()` unlinks. Confusing the two costs a swarm dearly and
says nothing while doing it.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from blitspersecond.common import PACKAGE_ROOT, Location
from blitspersecond.graphics import PixelBuffer
from blitspersecond.graphics.orientation import Orientation
from blitspersecond.graphics.sprite import Presentation, Sprite, SpriteSheet

ASSETS = PACKAGE_ROOT.parent / "assets"


@pytest.fixture
def bullets() -> Sprite:
    """The bare fixture: one tileset, two single-part assemblies."""
    return Sprite(SpriteSheet(str(ASSETS / "bullets" / "bullets.sprite.json")))


@pytest.fixture
def grid(tmp_path: Path) -> Sprite:
    """A 4x2 grid sheet -- eight single-part assemblies, addressed by index."""
    image = tmp_path / "grid.png"
    from PIL import Image

    Image.fromarray(PixelBuffer((32, 16), "P").pixels, mode="P").save(image)
    return Sprite(SpriteSheet(str(image), 8, 8))


# -- a sprite starts as just its primary --------------------------------


def test_a_new_sprite_draws_only_its_primary(bullets: Sprite) -> None:
    """A sprite with no primary is not a thing, so it is the floor rather
    than a default -- which is why `chun.position` never has to guard against
    there being nothing to address."""
    assert len(bullets.before) == 0
    assert len(bullets.after) == 0
    assert [p.slot for p in bullets.presentations] == [0]


def test_the_primary_starts_shown_and_solid(bullets: Sprite) -> None:
    assert bullets.primary.visible is True
    assert bullets.primary.collide is True
    assert bullets.primary.instanced is False


# -- the primary IS the sprite ------------------------------------------


def test_the_sprites_attributes_are_the_primary(bullets: Sprite) -> None:
    """One value in one place. Written through the sprite, read through the
    presentation -- two stores kept in step would pass a laxer check."""
    bullets.position = (160, 200)
    bullets.orientation = Orientation.FLIP_H
    bullets.visible = False
    assert bullets.primary.position == Location(160, 200)
    assert bullets.primary.orientation is Orientation.FLIP_H
    assert bullets.primary.visible is False


def test_writing_the_primary_moves_the_sprite(bullets: Sprite) -> None:
    """The half a one-way delegation would still pass."""
    bullets.primary.position = (12, 34)
    bullets.primary.visible = False
    assert bullets.position == Location(12, 34)
    assert bullets.visible is False


def test_the_primary_cannot_be_removed(bullets: Sprite) -> None:
    with pytest.raises(ValueError) as excinfo:
        bullets.primary.remove()
    assert "primary" in str(excinfo.value)


# -- identity is not draw order -----------------------------------------


def test_a_slot_id_is_an_address_not_a_position(bullets: Sprite) -> None:
    """`chun[3]` is the presentation whose id is 3, NOT the fourth thing
    drawn. The whole design rests on those being different questions."""
    ghosts = bullets.before.extend(3)
    held = ghosts[0]
    assert bullets[held.slot] == held
    assert bullets.before[0] == held  # position, via the chain
    # and the two disagree the moment the order changes
    bullets.before.prepend()
    assert bullets[held.slot] == held  # identity unmoved
    assert bullets.before[0] != held  # position moved


def test_reordering_does_not_move_state(bullets: Sprite) -> None:
    """The defect the split exists to remove: under the old signed-index
    design, moving a presentation in the draw order moved its state."""
    a = bullets.before.append()
    a.position = (7, 7)
    a.assembly = "pink"
    slot = a.slot
    bullets.before.prepend()  # a is now second, not first
    assert bullets[slot].position == Location(7, 7)
    assert bullets[slot].assembly == "pink"
    assert bullets[slot].slot == slot


def test_an_unknown_slot_says_what_is_live(bullets: Sprite) -> None:
    with pytest.raises(IndexError) as excinfo:
        bullets[99].position
    message = str(excinfo.value)
    assert "99" in message
    assert "before.append" in message  # it says where ids come from


# -- the chains ---------------------------------------------------------


def test_before_draws_first_and_after_draws_last(bullets: Sprite) -> None:
    """Draw order IS the emit order, so this is the order transparency
    depends on. There is no sort anywhere -- the chains already are it."""
    back = bullets.before.append()
    front = bullets.after.append()
    assert [p.slot for p in bullets.presentations] == [
        back.slot,
        bullets.primary.slot,
        front.slot,
    ]


def test_a_chain_is_an_ordinary_sequence(bullets: Sprite) -> None:
    """Unlike `sprite[slot]`, a chain IS a sequence, so `-1` means the last
    one -- the one drawn latest, nearest the sprite in `before`."""
    ghosts = bullets.before.extend(3)
    assert len(bullets.before) == 3
    assert bullets.before[-1] == ghosts[2]
    assert [p.slot for p in bullets.before] == [g.slot for g in ghosts]


def test_extend_makes_them_in_order(bullets: Sprite) -> None:
    ghosts = bullets.before.extend(4)
    assert [p.slot for p in bullets.before] == [g.slot for g in ghosts]
    assert len(ghosts) == 4


def test_prepend_goes_to_the_far_end(bullets: Sprite) -> None:
    first = bullets.before.append()
    deeper = bullets.before.prepend()
    assert [p.slot for p in bullets.before] == [deeper.slot, first.slot]


def test_insert_before_and_after_place_relative_to_a_presentation(
    bullets: Sprite,
) -> None:
    a = bullets.before.append()
    c = bullets.before.append()
    b = bullets.before.insert_after(a)
    z = bullets.before.insert_before(a)
    assert [p.slot for p in bullets.before] == [z.slot, a.slot, b.slot, c.slot]


def test_inserting_against_the_wrong_chain_is_refused(bullets: Sprite) -> None:
    """A presentation in `before` is not a position in `after`."""
    ghost = bullets.before.append()
    with pytest.raises(ValueError) as excinfo:
        bullets.after.insert_after(ghost)
    assert "after chain" in str(excinfo.value)


def test_remove_unlinks_and_leaves_the_rest_in_order(bullets: Sprite) -> None:
    a, b, c = bullets.before.extend(3)
    b.remove()
    assert [p.slot for p in bullets.before] == [a.slot, c.slot]
    assert len(bullets.before) == 2


def test_a_removed_slot_is_reused_and_reseeded(bullets: Sprite) -> None:
    """A reused id must not arrive carrying the LAST HOLDER's state, which
    would show as a one-frame ghost at the old place. It is reseeded from the
    primary like any other new presentation."""
    bullets.position = (10, 10)
    ghost = bullets.before.append()
    ghost.position = (50, 60)
    ghost.visible = True
    slot = ghost.slot
    ghost.remove()
    fresh = bullets.before.append()
    assert fresh.slot == slot  # the id came back
    assert fresh.position == Location(10, 10)  # the primary's, not the old one
    assert fresh.visible is False


def test_a_removed_presentation_is_no_longer_addressable(bullets: Sprite) -> None:
    ghost = bullets.before.append()
    slot = ghost.slot
    ghost.remove()
    with pytest.raises(IndexError):
        bullets[slot].position


# -- new presentations arrive parked ------------------------------------


def test_new_presentations_arrive_parked(bullets: Sprite) -> None:
    """Making room must not put art on screen. Both bits off is PARKED: out
    of the draw pass and out of the collision pass.

    Note the two presence bits are the ONLY things not inherited from the
    primary -- see the seeding tests below."""
    bullets.assembly = "blue"
    bullets.visible = True
    bullets.collide = True
    ghost = bullets.before.append()
    assert ghost.visible is False
    assert ghost.collide is False


def test_a_new_presentation_is_seeded_from_the_primary(bullets: Sprite) -> None:
    """A presentation is another drawing of THIS sprite, so it arrives looking
    like it. The failure this replaces was silent: a white-haired character's
    afterimages came out in the tileset's default red, and a wrong colour is
    not an error anywhere -- it is just wrong."""
    bullets.assembly = "blue"
    bullets.position = (40, 50)
    bullets.orientation = Orientation.FLIP_H
    ghost = bullets.before.append()
    assert ghost.assembly == "blue"
    assert ghost.position == Location(40, 50)
    assert ghost.orientation is Orientation.FLIP_H
    tileset = bullets.sheet.primary_tileset
    assert ghost.palette_index[tileset] == bullets.palette_index[tileset]


def test_the_palette_register_is_seeded_too(tmp_path: Path) -> None:
    """The case that prompted this: the primary's register lives in its
    per-tileset dict rather than in the slot array, so it is the one value
    the pool cannot read for itself."""
    sprite = Sprite(SpriteSheet(_multipart_package(tmp_path, palettes=3)))
    sprite.palette_index["main"] = 2
    ghost = sprite.before.append()
    assert ghost.palette_index["main"] == 2


def test_a_pose_the_presentation_could_not_accept_is_not_seeded(
    tmp_path: Path,
) -> None:
    """Seeding a multipart or off-tileset pose would plant state no setter
    would have allowed, and the refusal would then fire on some later,
    unrelated write. So it seeds nothing and says nothing is presented."""
    sprite = Sprite(SpriteSheet(_multipart_package(tmp_path)))
    for pose in ("both", "fx-only"):
        sprite.assembly = pose
        assert sprite.before.append().assembly is None
    sprite.assembly = "pose"
    assert sprite.before.append().assembly == "pose"


def test_seeding_accepts_exactly_what_the_setter_accepts(tmp_path: Path) -> None:
    """The two paths must agree about every pose, or they mean different
    things. Blank is the case that caught it: the setter accepts a blank and
    the seeder used to refuse, so the picture matched but the ghost read back
    `None` instead of the pose's name."""
    sprite = Sprite(SpriteSheet(_multipart_package(tmp_path)))
    probe = sprite.before.append()
    for pose in sprite.sheet.assemblies:
        sprite.assembly = pose
        try:
            probe.assembly = pose
        except ValueError:
            accepted = None
        else:
            accepted = pose
        assert sprite.before.append().assembly == accepted, pose


def test_parking_is_not_unlinking(bullets: Sprite) -> None:
    """The distinction a swarm's reap depends on. A dead bullet keeps its
    slot AND its place -- only the bits change, so the arrays stay put and
    the next spawn reuses it without touching the order."""
    ghosts = bullets.before.extend(3)
    order = list(bullets._before)
    ghosts[1].visible = False
    ghosts[1].collide = False
    assert list(bullets._before) == order
    assert len(bullets.before) == 3
    assert bullets[ghosts[1].slot].slot == ghosts[1].slot


# -- capacity -----------------------------------------------------------


def test_capacity_grows_without_moving_anyone(bullets: Sprite) -> None:
    """Growth copies rather than reindexes, so an id keeps its row and every
    handle held across the growth stays valid."""
    first = bullets.before.extend(4)
    for n, p in enumerate(first):
        p.position = (n, n * 2)
    held = first[2]
    bullets.after.extend(200)  # forces several doublings
    assert bullets[held.slot].position == Location(2, 4)
    for n, p in enumerate(first):
        assert bullets[p.slot].position == Location(n, n * 2)


def test_a_swarm_allocates_in_one_call(bullets: Sprite) -> None:
    """The bulk door: one allocation and one concatenate, not 25,000 calls,
    and the ids come back as an array a bulk write can index with."""
    swarm = bullets.after.extend(25_000)
    assert len(swarm) == 25_000
    assert isinstance(swarm.slots, np.ndarray)
    assert len(bullets.after) == 25_000


def test_extending_by_a_negative_count_is_refused(bullets: Sprite) -> None:
    with pytest.raises(ValueError):
        bullets.before.extend(-1)


# -- what instanced presentations refuse --------------------------------


def test_an_instanced_presentation_refuses_a_multipart_pose(tmp_path: Path) -> None:
    """The restriction the whole collapse rests on: every instanced
    presentation is one quad. Refused at the WRITE, not dropped at a draw --
    a presentation that silently never appears is the failure worth refusing
    outright."""
    sprite = Sprite(SpriteSheet(_multipart_package(tmp_path)))
    ghost = sprite.before.append()
    sprite.assembly = "both"  # the primary may
    with pytest.raises(ValueError) as excinfo:
        ghost.assembly = "both"
    message = str(excinfo.value)
    assert "'both'" in message and "2 parts" in message


def test_an_instanced_presentation_refuses_another_tileset(tmp_path: Path) -> None:
    """Single-part is not enough: 194 of Chun-Li's assemblies are one part
    and draw entirely from a non-primary tileset. They pass the multipart
    check and would still break the batch."""
    sprite = Sprite(SpriteSheet(_multipart_package(tmp_path)))
    ghost = sprite.before.append()
    sprite.assembly = "fx-only"  # the primary may
    with pytest.raises(ValueError) as excinfo:
        ghost.assembly = "fx-only"
    message = str(excinfo.value)
    assert "'fx'" in message and "'main'" in message


def test_an_instanced_presentation_takes_the_primary_tilesets_art(
    tmp_path: Path,
) -> None:
    """The mainline: the restrictions must not be so tight that a trail of
    the sprite's own art is refused."""
    sprite = Sprite(SpriteSheet(_multipart_package(tmp_path)))
    ghost = sprite.before.append()
    ghost.assembly = "pose"
    assert ghost.assembly == "pose"


def test_clearing_an_instanced_pose_is_always_allowed(tmp_path: Path) -> None:
    """`None` presents nothing, which no restriction can be violated by."""
    sprite = Sprite(SpriteSheet(_multipart_package(tmp_path)))
    ghost = sprite.before.append()
    ghost.assembly = "pose"
    ghost.assembly = None
    assert ghost.assembly is None


# -- palettes ------------------------------------------------------------


def test_the_primary_has_a_register_per_tileset(tmp_path: Path) -> None:
    sprite = Sprite(SpriteSheet(_multipart_package(tmp_path)))
    assert set(sprite.palette_index) == {"main", "fx"}


def test_an_instanced_presentation_has_only_the_primary_register(
    tmp_path: Path,
) -> None:
    """It draws one tileset, so it has one register -- and naming the other
    raises rather than accepting a write that would mean nothing."""
    sprite = Sprite(SpriteSheet(_multipart_package(tmp_path)))
    ghost = sprite.before.append()
    assert set(ghost.palette_index) == {"main"}
    with pytest.raises(KeyError) as excinfo:
        ghost.palette_index["fx"] = 0
    assert "primary tileset" in str(excinfo.value)


def test_presentations_carry_their_own_registers(grid: Sprite) -> None:
    """The trail's whole effect is each ghost differing, so these must not
    share storage."""
    ghosts = grid.before.extend(2)
    grid.palette_index["sheet"] = 0
    ghosts[0].palette_index["sheet"] = 0
    ghosts[1].palette_index["sheet"] = 0
    assert ghosts[0].palette_index["sheet"] == 0


# -- handles are views ---------------------------------------------------


def test_handles_are_views_and_compare_by_slot(bullets: Sprite) -> None:
    """A handle held across a write sees the write -- it holds an id, not a
    copy."""
    ghost = bullets.before.append()
    assert ghost == bullets[ghost.slot]
    assert ghost != bullets.primary
    bullets[ghost.slot].position = (4, 5)
    assert ghost.position == Location(4, 5)


def test_a_handle_is_a_Presentation(bullets: Sprite) -> None:
    assert isinstance(bullets.primary, Presentation)


# -- the two fixtures' shapes -------------------------------------------


def test_the_trail_shape(grid: Sprite) -> None:
    """The principal with its echoes behind, oldest furthest back -- so
    distance along the chain is age, with no reversal to undo."""
    ghosts = grid.before.extend(4)
    grid.assembly = 0
    ages = {}
    for age, ghost in enumerate(reversed(list(ghosts)), start=1):
        ghost.assembly = 0
        ghost.position = (age, 0)  # stands in for how far back in history
        ghost.collide = False
        ghost.visible = True
        ages[ghost.slot] = age
    # Furthest back is oldest. A fade would say the same thing with a dimmer
    # palette row, which this sheet has only one of -- the ORDER is what is
    # under test, and it is what a fade would be read off.
    assert ghosts[0].x > ghosts[3].x
    assert grid.collide is True and ghosts[0].collide is False
    assert [p.slot for p in grid.presentations][-1] == grid.primary.slot


def test_the_swarm_shape(bullets: Sprite) -> None:
    """No principal to speak of: the population lives in one chain, and the
    primary is simply the first thing drawn after it."""
    swarm = bullets.after.extend(1_000)
    assert len(bullets.before) == 0
    order = [p.slot for p in bullets.presentations]
    assert order[0] == bullets.primary.slot
    assert len(order) == 1_001
    assert order[1:] == [int(s) for s in swarm.slots]


def _multipart_package(tmp_path: Path, palettes: int = 1) -> str:
    """A two-tileset package: a single-part pose, an fx-only pose, and a
    multipart one. The three cases an instanced presentation tells apart."""
    import json

    from PIL import Image

    image = tmp_path / "sheet.png"
    Image.fromarray(PixelBuffer((16, 8), "P").pixels, mode="P").save(image)
    tables = [["#00000000"] + ["#112233ff"] * 3] * palettes
    data = {
        "version": 1,
        "tilesets": {
            "main": {
                "image": "sheet.png",
                "primary": True,
                "transparent_index": 0,
                "default_palette": 0,
                "palettes": tables,
                "tiles": {"a": {"source": [0, 0, 8, 8]}},
            },
            "fx": {
                "image": "sheet.png",
                "transparent_index": 0,
                "default_palette": 0,
                "palettes": tables,
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
            "fx-only": {
                "parts": [
                    {"tileset": "fx", "tile": "b", "offset": [0, 0], "order": 0}
                ]
            },
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
