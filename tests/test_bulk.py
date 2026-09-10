"""The bulk array surface: how a swarm is actually driven.

`Presentation` is a fine surface for a handful. It is not one for 25,000 --
reaching each through its own property setter costs 21.8 ms a frame against
3.1 ms writing the arrays, which is 1.3 frames against a fifth of one. So
`Presentations` hands out NumPy views and `edit()` marks the sprite dirty once
around a batch.

That buys speed by removing the guard rails, so what needs pinning is the
places they were kept:

**Views, or an error.** A view means writes land. A fancy index would return a
COPY -- every write silently swallowed, which is the exact hazard reference's
own `SpritePool.orientation` docstring names from the inside. A fragmented
batch raises rather than handing back something that looks like it works.

**`assembly_id` validates the NAME.** One check for a 25,000-element
broadcast, so the bulk door is as safe as the setter without paying per
element.

**`edit()` is what tells the sprite anything happened.** A view write goes
past no setter, so without it the frame plan would never rebuild -- and the
symptom would be a scene that quietly froze rather than anything raising.
"""

from __future__ import annotations

import numpy as np
import pytest

from blitspersecond.common import PACKAGE_ROOT
from blitspersecond.graphics.orientation import Orientation
from blitspersecond.graphics.sprite import Sprite, SpriteSheet

ASSETS = PACKAGE_ROOT.parent / "assets"


@pytest.fixture
def bullets() -> Sprite:
    return Sprite(SpriteSheet(str(ASSETS / "bullets" / "bullets.sprite.json")))


# -- the views are views ------------------------------------------------


def test_every_view_writes_through_to_the_state(bullets: Sprite) -> None:
    """The whole point. A copy would pass every test that only reads back
    through the same property, so each is checked against `Presentation`,
    which reads the state by another road."""
    swarm = bullets.after.extend(8)
    swarm.positions[:, 0] = np.arange(8) * 3
    swarm.orientation[:] = int(Orientation.FLIP_H)
    swarm.visible[:] = True
    swarm.collide[3] = True
    swarm.palette_index[:] = 0
    swarm.assemblies[:] = bullets.assembly_id("pink")

    assert swarm[5].x == 15
    assert swarm[5].orientation is Orientation.FLIP_H
    assert swarm[5].visible is True
    assert swarm[3].collide is True and swarm[4].collide is False
    assert swarm[5].palette_index[bullets.sheet.primary_tileset] == 0
    assert swarm[5].assembly == "pink"


def test_a_view_sees_writes_made_through_a_presentation(bullets: Sprite) -> None:
    """And the other direction, which a copy taken once would fail."""
    swarm = bullets.after.extend(4)
    swarm[2].position = (11, 22)
    assert list(swarm.positions[2]) == [11, 22]


def test_the_views_are_numpy_views_not_copies(bullets: Sprite) -> None:
    """Stated directly rather than only inferred, because `base is not None`
    is the property that makes every other test here mean what it says."""
    swarm = bullets.after.extend(4)
    for view in (
        swarm.positions,
        swarm.orientation,
        swarm.assemblies,
        swarm.visible,
        swarm.collide,
        swarm.palette_index,
    ):
        assert view.base is not None


def test_there_is_no_colour_view_and_it_says_where_to_go(bullets: Sprite) -> None:
    """Removed 2026-09-02 rather than left returning the sprite's one value
    broadcast, which would have read as a per-presentation array and silently
    swallowed every write to it -- the same failure `_span` raises over."""
    swarm = bullets.after.extend(4)
    with pytest.raises(AttributeError, match="vary `palette_index`"):
        swarm.color


def test_a_view_covers_only_this_batch(bullets: Sprite) -> None:
    """It must not reach the primary or another batch -- a swarm writing over
    the sprite's own state would be a very confusing bug."""
    first = bullets.after.extend(3)
    second = bullets.after.extend(3)
    bullets.position = (99, 99)
    first.positions[:] = 1
    assert len(second.positions) == 3
    assert not (second.positions == 1).any()
    assert bullets.position == (99, 99)


# -- fragmentation ------------------------------------------------------


def test_a_fragmented_batch_refuses_to_hand_out_views(bullets: Sprite) -> None:
    """The failure this replaces is silent: a fancy index returns a copy, so
    every write lands somewhere that is thrown away and the population simply
    does not move."""
    ghosts = list(bullets.after.extend(4))
    ghosts[1].remove()          # frees an id in the middle
    ghosts[2].remove()
    later = bullets.after.extend(3)   # reuses the freed ids, plus a fresh one
    assert later.contiguous is False
    with pytest.raises(ValueError) as excinfo:
        later.positions
    message = str(excinfo.value)
    assert "silently lost" in message
    assert "one `extend()`" in message  # it says how to avoid it


def test_a_fresh_batch_is_contiguous(bullets: Sprite) -> None:
    """The mainline: ids come off an ascending free array, so a swarm
    allocated in one call gets a solid run and a real view."""
    assert bullets.after.extend(25_000).contiguous is True


def test_a_fragmented_batch_still_works_one_at_a_time(bullets: Sprite) -> None:
    """Refusing the view is not refusing the presentations -- they are fine,
    they just cannot be addressed as one array."""
    ghosts = list(bullets.after.extend(4))
    ghosts[1].remove()
    ghosts[2].remove()
    later = bullets.after.extend(3)
    assert not later.contiguous
    later[0].position = (5, 6)
    assert later[0].position == (5, 6)


# -- edit() -------------------------------------------------------------


def test_a_view_write_alone_does_not_rebuild(bullets: Sprite) -> None:
    """The reason `edit()` exists. A write through a view goes past no setter,
    so nothing tells the sprite it moved -- and the symptom is a scene that
    quietly freezes rather than anything raising."""
    swarm = bullets.after.extend(4)
    swarm.assemblies[:] = bullets.assembly_id("blue")
    swarm.visible[:] = True
    bullets._touch()
    bullets._sync()
    swarm.positions[:, 0] = 100          # no edit(), no touch
    bullets._sync()
    assert bullets._plan.data[0, 0] != 100


def test_edit_marks_the_sprite_dirty_once(bullets: Sprite) -> None:
    """Note the sync BEFORE the edit. Without it the sprite is still dirty
    from its own allocation and the rebuild would happen regardless -- the
    test passed with `edit()` gutted until a mutation run caught it."""
    swarm = bullets.after.extend(4)
    swarm.assemblies[:] = bullets.assembly_id("blue")
    swarm.visible[:] = True
    bullets._sync()
    assert bullets._dirty is False
    with swarm.edit():
        swarm.positions[:, 0] = 100
    assert bullets._dirty is True
    bullets._sync()
    assert bullets._plan.data[0, 0] == 100 - 4   # art anchored at -4


def test_edit_marks_dirty_even_if_the_body_raises(bullets: Sprite) -> None:
    """Half a batch of writes still landed, so the plan is stale either way.
    Swallowing the dirty mark on the way out would leave the screen showing
    something that is no longer true."""
    swarm = bullets.after.extend(4)
    bullets._touch()
    bullets._sync()
    assert bullets._dirty is False
    with pytest.raises(RuntimeError):
        with swarm.edit():
            swarm.positions[:, 0] = 7
            raise RuntimeError("the game blew up mid-batch")
    assert bullets._dirty is True


def test_edit_yields_the_batch(bullets: Sprite) -> None:
    swarm = bullets.after.extend(2)
    with swarm.edit() as same:
        assert same is swarm


# -- assembly_id --------------------------------------------------------


def test_assembly_id_broadcasts_across_a_population(bullets: Sprite) -> None:
    """Two tiles in one tileset: assembly varies per presentation for free,
    because it is a gather into the vertex stream rather than GPU state."""
    swarm = bullets.after.extend(1_000)
    swarm.assemblies[0::2] = bullets.assembly_id("blue")
    swarm.assemblies[1::2] = bullets.assembly_id("pink")
    swarm.visible[:] = True
    bullets._touch()
    bullets._sync()
    assert bullets._plan.quads == 1_000
    assert len(bullets._plan.runs) == 1   # still ONE run


def test_assembly_id_refuses_what_a_presentation_would(tmp_path) -> None:
    """The bulk door has to be as safe as the setter -- and it is, by checking
    the NAME once instead of 25,000 elements."""
    sprite = Sprite(SpriteSheet(_package(tmp_path)))
    assert sprite.assembly_id("pose") >= 0
    with pytest.raises(ValueError, match="2 parts"):
        sprite.assembly_id("both")
    with pytest.raises(ValueError, match="primary tileset"):
        sprite.assembly_id("fx-only")


def test_assembly_id_says_it_is_for_instanced_use(tmp_path) -> None:
    """The message must name the actual mistake, not a slot the bulk path
    does not have."""
    sprite = Sprite(SpriteSheet(_package(tmp_path)))
    with pytest.raises(ValueError) as excinfo:
        sprite.assembly_id("both")
    assert "instanced use" in str(excinfo.value)
    assert "slot" not in str(excinfo.value)


# -- the swarm idiom ----------------------------------------------------


def test_spawn_and_reap_through_the_liveness_bit(bullets: Sprite) -> None:
    """The whole loop, in the shape the fountain uses it: nothing is created
    or destroyed, `flatnonzero(~visible)` is the free list, and the draw order
    never changes."""
    swarm = bullets.after.extend(100)
    swarm.assemblies[:] = bullets.assembly_id("blue")
    alive = swarm.visible
    order = list(bullets._after)

    with swarm.edit():
        free = np.flatnonzero(~alive)[:30]
        alive[free] = True
    assert int(alive.sum()) == 30

    with swarm.edit():
        alive[np.flatnonzero(alive)[:10]] = False
    assert int(alive.sum()) == 20

    with swarm.edit():
        alive[np.flatnonzero(~alive)[:5]] = True   # reuse the dead
    assert int(alive.sum()) == 25

    assert list(bullets._after) == order  # no relinking, ever
    bullets._sync()
    assert bullets._plan.quads == 25


def test_positions_are_integer_pixels(bullets: Sprite) -> None:
    """A simulation with sub-pixel velocity keeps its own float arrays and
    floors on the way in -- one vectorised `np.floor`, rather than the engine
    guessing what the fraction was for."""
    swarm = bullets.after.extend(4)
    assert swarm.positions.dtype == np.int32
    fy = np.array([10.7, 11.2, -0.5, 3.9], dtype=np.float32)
    swarm.positions[:, 1] = np.floor(fy)
    assert list(swarm.positions[:, 1]) == [10, 11, -1, 3]


def test_len_and_slots_describe_the_batch(bullets: Sprite) -> None:
    swarm = bullets.after.extend(5)
    assert len(swarm) == 5
    assert swarm.slots.dtype == np.int32
    assert [p.slot for p in swarm] == list(swarm.slots)


def _package(tmp_path) -> str:
    """Two tilesets over two images, so a multipart pose really does cross a
    texture boundary. `PixelBuffer` interns by path -- one file would make
    them the same buffer and the test would prove nothing."""
    import json

    from PIL import Image

    from blitspersecond.graphics import PixelBuffer

    for name in ("main", "fx"):
        Image.fromarray(PixelBuffer((16, 8), "P").pixels, mode="P").save(
            tmp_path / f"{name}.png"
        )
    tables = [["#00000000"] + ["#112233ff"] * 3]
    data = {
        "version": 1,
        "tilesets": {
            "main": {
                "image": "main.png",
                "primary": True,
                "transparent_index": 0,
                "default_palette": 0,
                "palettes": tables,
                "tiles": {"a": {"source": [0, 0, 8, 8]}},
            },
            "fx": {
                "image": "fx.png",
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
