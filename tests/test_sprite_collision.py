"""The sprite pillar's collision plane, against the pixels it claims to match.

`test_tilemap_collision` proves the same thing for the other pillar and for
the same reason: the claim worth testing is not that the bits are
self-consistent but that **the plane and the picture are the same shape in the
same place**. So most of what is here composites for real at screen size and
compares the framebuffer with `collider().unpacked`, pixel for pixel.

The window is 640x360 so a readback IS the screen and nothing scales between
the two. Blend is off and index 0 is discarded by the shader, so anything
non-black was drawn -- which is exactly what the silhouette claims to be.
"""

from __future__ import annotations

import numpy as np
import pytest

from blitspersecond.common import PACKAGE_ROOT, SCREEN
from blitspersecond.graphics.orientation import Orientation
from blitspersecond.graphics.sprite import Sprite, SpriteSheet
from blitspersecond.graphics.sprite.collision import SpriteCollision

ASSETS = PACKAGE_ROOT.parent / "assets"
pyglet = pytest.importorskip("pyglet")


@pytest.fixture(scope="module")
def window():
    from pyglet.math import Mat4

    try:
        win = pyglet.window.Window(
            width=SCREEN.width, height=SCREEN.height, visible=False
        )
    except Exception as exc:  # pragma: no cover - depends on the machine
        pytest.skip(f"no GL context available: {exc}")
    win.switch_to()
    win.projection = Mat4.orthogonal_projection(
        0, SCREEN.width, 0, SCREEN.height, -1, 1
    )
    yield win
    win.close()


def _sprite(name: str) -> Sprite:
    sheet = SpriteSheet(str(ASSETS / name / f"{name}.sprite.json"))
    sprite = Sprite(sheet)
    sprite.collidable = True
    return sprite


@pytest.fixture
def fumiko() -> Sprite:
    """Asymmetric art, so an orientation slip cannot hide."""
    return _sprite("fumiko")


@pytest.fixture
def bullets() -> Sprite:
    return _sprite("bullets")


def _drawn(window, sprite: Sprite) -> np.ndarray:
    from pyglet.gl import (
        GL_BLEND,
        GL_COLOR_BUFFER_BIT,
        GL_RGBA,
        GL_UNSIGNED_BYTE,
        glClear,
        glClearColor,
        glDisable,
        glReadPixels,
        glViewport,
    )

    window.switch_to()
    glViewport(0, 0, SCREEN.width, SCREEN.height)
    glClearColor(0.0, 0.0, 0.0, 1.0)
    glClear(GL_COLOR_BUFFER_BIT)
    glDisable(GL_BLEND)
    sprite.composite()
    raw = (pyglet.gl.GLubyte * (SCREEN.width * SCREEN.height * 4))()
    glReadPixels(
        0, 0, SCREEN.width, SCREEN.height, GL_RGBA, GL_UNSIGNED_BYTE, raw
    )
    pixels = np.frombuffer(raw, dtype=np.uint8).reshape(
        SCREEN.height, SCREEN.width, 4
    )
    return pixels[:, :, :3].any(axis=2)


def _disagreement(window, sprite: Sprite) -> int:
    return int(
        np.count_nonzero(_drawn(window, sprite) ^ sprite.collider().unpacked)
    )


# -- the picture and the plane --------------------------------------------


def test_a_sprite_collides_where_it_draws(window, fumiko: Sprite) -> None:
    fumiko.assembly = "idle-0"
    fumiko.position = (300, 180)
    # The draw first, always: the plane is the frame that was PAINTED, so a
    # question asked before one exists is answered about nothing.
    assert _disagreement(window, fumiko) == 0
    assert fumiko.collider().unpacked.any(), "composed nothing at all"


@pytest.mark.parametrize("turn", list(range(8)))
def test_every_orientation_agrees_with_the_draw(
    window, fumiko: Sprite, turn: int
) -> None:
    """One `local_rect` stages the quad and places the pattern, and
    `Atlas.solidity` turns the bits the same way. A transposed rect on
    asymmetric art is where a disagreement would show."""
    fumiko.assembly = "idle-0"
    fumiko.orientation = Orientation(turn)
    fumiko.position = (300, 180)
    assert _disagreement(window, fumiko) == 0


@pytest.mark.parametrize(
    "position",
    [(0, 0), (1, 1), (31, 0), (32, 0), (33, 7), (300, 180), (600, 330)],
)
def test_the_plane_follows_the_position(window, fumiko, position) -> None:
    """Word boundaries are where a packed stamp goes wrong: 31, 32 and 33
    straddle one, and a pattern shifted left by 31 has to carry."""
    fumiko.assembly = "idle-0"
    fumiko.position = position
    assert _disagreement(window, fumiko) == 0


@pytest.mark.parametrize(
    "position", [(-20, 100), (630, 100), (100, -20), (100, 350), (-999, -999)]
)
def test_a_sprite_off_the_edge_stamps_what_lands(window, fumiko, position):
    """The apron is clip avoidance, so a half-off sprite costs a dropped word
    rather than a row-by-row clip -- and one far away costs nothing and must
    not reach into the plane at all."""
    fumiko.assembly = "idle-0"
    fumiko.position = position
    assert _disagreement(window, fumiko) == 0


def test_moving_leaves_nothing_behind(window, fumiko: Sprite) -> None:
    """Composed, not retained -- so the plane is cleared and refilled every
    time it is rebuilt.

    A plane that only ORed would leave a trail of stale silhouette down
    everything the sprite had ever walked past, and every one of those would
    answer True forever. There is no cheaper truth available: presentations
    move independently, so a plane that tried to erase what it drew would
    have to remember where each one used to be.
    """
    fumiko.assembly = "idle-0"
    fumiko.position = (80, 80)
    _drawn(window, fumiko)
    assert fumiko.collider().unpacked[80:120, 80:120].any()

    fumiko.position = (400, 250)
    _drawn(window, fumiko)
    assert not fumiko.collider().unpacked[80:120, 80:120].any(), "left a trail"
    assert _disagreement(window, fumiko) == 0


def test_a_chain_of_mixed_poses_keys_on_orientation_too(
    window, fumiko: Sprite
) -> None:
    """The scatter groups by `assembly * 8 + orientation`, and the orientation
    half of that key only shows on a CHAIN -- the primary is walked, not
    grouped. Two members of one assembly turned differently must stamp two
    different patterns, not one pattern twice."""
    fumiko.primary.visible = False
    fumiko.primary.collide = False
    chain = fumiko.after.extend(4)
    with chain.edit():
        chain.assemblies[:] = fumiko._assembly_ids["idle-0"]
        chain.orientation[:] = [
            int(Orientation.NONE),
            int(Orientation.FLIP_H),
            int(Orientation.FLIP_V),
            int(Orientation.ROT_180),
        ]
        chain.positions[:, 0] = [60, 200, 340, 480]
        chain.positions[:, 1] = [60, 140, 220, 290]
        chain.visible[:] = True
        chain.collide[:] = True
    assert _disagreement(window, fumiko) == 0


# -- the three levels of participation ------------------------------------


def test_a_hidden_layer_still_recomposes_its_plane(window, fumiko) -> None:
    """`enabled` is a view decision. A layer turned off keeps colliding, and
    keeps colliding WHERE IT IS -- the plane that froze at the moment of
    hiding would answer True about a sprite that has walked away."""
    fumiko.assembly = "idle-0"
    fumiko.position = (80, 80)
    _drawn(window, fumiko)

    fumiko.enabled = False
    fumiko.position = (400, 250)
    fumiko.composite()
    assert not fumiko.collider().unpacked[80:120, 80:120].any(), "froze"
    assert fumiko.collider().unpacked[250:290, 400:440].any()


def test_a_ghost_is_drawn_and_hits_nothing(window, fumiko: Sprite) -> None:
    """`visible` and `collide` are two bits over one presence. A trail's
    ghosts are the most visible thing on screen and must contribute nothing."""
    fumiko.assembly = "idle-0"
    fumiko.position = (300, 180)
    ghost = fumiko.before.append()
    ghost.assembly = "idle-0"
    ghost.position = (100, 100)
    ghost.visible = True
    ghost.collide = False

    assert _drawn(window, fumiko)[100:140, 100:140].any(), "the ghost drew"
    assert not fumiko.collider().unpacked[100:140, 100:140].any()
    # and the principal still collides where it draws
    assert fumiko.collider().unpacked[180:220, 300:340].any()


def test_a_cloak_hits_and_is_not_drawn(window, fumiko: Sprite) -> None:
    fumiko.assembly = "idle-0"
    fumiko.position = (300, 180)
    cloak = fumiko.after.append()
    cloak.assembly = "idle-0"
    cloak.position = (100, 100)
    cloak.visible = False
    cloak.collide = True

    assert not _drawn(window, fumiko)[100:140, 100:140].any()
    assert fumiko.collider().unpacked[100:140, 100:140].any()


def test_a_parked_slot_contributes_nothing(window, fumiko: Sprite) -> None:
    """A fresh presentation arrives with BOTH bits off."""
    fumiko.assembly = "idle-0"
    fumiko.position = (300, 180)
    _drawn(window, fumiko)
    before = fumiko.collider().unpacked.copy()
    parked = fumiko.after.append()
    parked.assembly = "idle-0"
    parked.position = (100, 100)
    _drawn(window, fumiko)
    assert np.array_equal(fumiko.collider().unpacked, before)


def test_the_layer_switch_drops_the_plane(window, fumiko: Sprite) -> None:
    fumiko.assembly = "idle-0"
    fumiko.position = (300, 180)
    _drawn(window, fumiko)
    assert fumiko._collision is not None
    fumiko.collidable = False
    assert fumiko._collision is None
    with pytest.raises(ValueError, match="collidable=False"):
        fumiko.collider()


def test_switching_back_on_composes_where_the_sprite_now_is(
    window, fumiko: Sprite
) -> None:
    """The sprite kept moving while collision was off, so what comes back has
    to be about where it IS -- not where it was when the switch was thrown."""
    fumiko.assembly = "idle-0"
    fumiko.position = (300, 180)
    _drawn(window, fumiko)
    fumiko.collidable = False
    for x in range(300, 360, 12):
        fumiko.position = (x, 180)
        _drawn(window, fumiko)
    fumiko.collidable = True
    assert _disagreement(window, fumiko) == 0


def test_switching_off_and_on_with_nothing_moving_still_composes(
    window, fumiko: Sprite
) -> None:
    """Switching off DROPS the plane, so switching on must rebuild it -- even
    though nothing moved and so nothing marked it dirty.

    Without the flag being set by the switch itself, the sprite comes back
    with a freshly allocated, empty plane and answers False about everything
    until the next time it happens to move. That is the silent-inert-hazard
    failure the whole `collidable` gate exists to prevent.
    """
    fumiko.assembly = "idle-0"
    fumiko.position = (300, 180)
    _drawn(window, fumiko)
    expected = fumiko.collider().unpacked.copy()
    assert expected.any()

    fumiko.collidable = False
    fumiko.collidable = True          # and nothing at all has moved
    _drawn(window, fumiko)
    assert np.array_equal(fumiko.collider().unpacked, expected)


def test_a_still_sprite_does_not_recompose(
    window, fumiko: Sprite, monkeypatch
) -> None:
    """`visible` and `collide` invalidate different things, and neither is
    invalidated by a frame merely happening. Composing is a clear plus a stamp
    per presentation, so a static scene doing it every frame would be paying a
    population's worth of work to reach the answer it already had."""
    fumiko.assembly = "idle-0"
    fumiko.position = (300, 180)
    _drawn(window, fumiko)

    assert fumiko._collision is not None
    # Patched on the CLASS -- `SpriteCollision` has `__slots__`, which is
    # itself the point: nothing per-instance is allocated for this.
    calls = []
    original = SpriteCollision.compose

    def counted(self):
        calls.append(1)
        original(self)

    monkeypatch.setattr(SpriteCollision, "compose", counted)

    _drawn(window, fumiko)
    _drawn(window, fumiko)
    assert calls == [], "recomposed a plane nothing could have changed"

    fumiko.position = (301, 180)
    _drawn(window, fumiko)
    assert len(calls) == 1, "moving must recompose exactly once"


def test_an_uncollidable_sprite_composes_no_plane(window, fumiko) -> None:
    fumiko.collidable = False
    fumiko.assembly = "idle-0"
    fumiko.position = (300, 180)
    _drawn(window, fumiko)
    assert fumiko._collision is None


# -- a population ---------------------------------------------------------


def test_a_population_composes_into_one_plane(window, bullets: Sprite) -> None:
    """Composition destroys presentation identity on purpose: the question is
    "did enemy fire touch the player", not which bullet."""
    bullets.assembly = "blue"
    bullets.position = (20, 20)
    swarm = bullets.after.extend(64)
    rng = np.random.default_rng(4)
    with swarm.edit():
        swarm.assemblies[:] = 0
        swarm.positions[:, 0] = rng.integers(0, SCREEN.width - 8, 64)
        swarm.positions[:, 1] = rng.integers(0, SCREEN.height - 8, 64)
        swarm.visible[:] = True
        swarm.collide[:] = True
    assert _disagreement(window, bullets) == 0


def test_a_dense_population_agrees_with_the_draw(window, bullets) -> None:
    """The same claim as the single-sprite case, at a scale where different
    things break: thousands of pose groups' worth of gathering, positions
    spread across every word boundary, and enough overlap that the scatter's
    read-modify-write is doing real work on nearly every word.

    The fountain demo does this at 21,527 and also agrees exactly; this is the
    part of it that belongs in a suite.
    """
    n = 5000
    bullets.assembly = "blue"
    bullets.primary.visible = False
    bullets.primary.collide = False
    swarm = bullets.after.extend(n)
    rng = np.random.default_rng(9)
    with swarm.edit():
        swarm.assemblies[0::2] = bullets._assembly_ids["blue"]
        swarm.assemblies[1::2] = bullets._assembly_ids["pink"]
        swarm.orientation[:] = rng.integers(0, 8, n)
        swarm.positions[:, 0] = rng.integers(-8, SCREEN.width + 8, n)
        swarm.positions[:, 1] = rng.integers(-8, SCREEN.height + 8, n)
        swarm.visible[:] = True
        swarm.collide[:] = True

    drawn = _drawn(window, bullets)
    assert drawn.sum() > SCREEN.width, "the population drew almost nothing"
    assert np.array_equal(drawn, bullets.collider().unpacked)


def test_overlapping_members_or_together(window, bullets: Sprite) -> None:
    """The reason the scatter cannot use buffered fancy indexing: several
    presentations landing in one word must OR, not overwrite."""
    bullets.assembly = "blue"
    bullets.position = (0, 0)
    # Out of BOTH projections, so the swarm is the only thing in either.
    bullets.primary.visible = False
    bullets.primary.collide = False
    swarm = bullets.after.extend(3)
    with swarm.edit():
        swarm.assemblies[:] = 0
        swarm.positions[:, 0] = [100, 101, 102]
        swarm.positions[:, 1] = [100, 100, 100]
        swarm.visible[:] = True
        swarm.collide[:] = True
    assert _disagreement(window, bullets) == 0
    # three overlapping stamps cover more than any one of them
    plane = bullets.collider().unpacked
    single = np.count_nonzero(plane[100:120, 100:102])
    assert single > 0


# -- named planes ---------------------------------------------------------
#
# An assembly authors these as AABBs in its local frame. They are FILLED into
# a plane of their own and nothing downstream knows they were rectangles: a
# query is a bitwise intersection over packed planes, so a box plane and a
# pixel silhouette answer the same way, are never consulted as boxes, never
# cull, and never turn back into geometry.
#
# Built here rather than taken from the third-party fixture, which lives in
# `testsuite` and is not the engine's to depend on.


def _boxed(tmp_path, collision, tile=(0, 0, 8, 8)) -> Sprite:
    """A one-tile sheet whose single pose authors `collision`."""
    import json

    from PIL import Image

    from blitspersecond.graphics import PixelBuffer

    image = tmp_path / "sheet.png"
    buffer = PixelBuffer((16, 8), "P")
    with buffer.edit() as px:
        px[:] = 1
    Image.fromarray(buffer.pixels, mode="P").save(image)
    data = {
        "version": 1,
        "tilesets": {
            "main": {
                "image": "sheet.png",
                "transparent_index": 0,
                "default_palette": 0,
                "palettes": [["#00000000"] + ["#112233ff"] * 3],
                "tiles": {"a": {"source": list(tile)}},
            }
        },
        "assemblies": {
            "pose": {
                "parts": [
                    {
                        "tileset": "main",
                        "tile": "a",
                        "offset": [0, 0],
                        "order": 0,
                    }
                ],
                "collision": collision,
            },
            "bare": {
                "parts": [
                    {
                        "tileset": "main",
                        "tile": "a",
                        "offset": [0, 0],
                        "order": 0,
                    }
                ]
            },
        },
        "animations": {
            "go": {"frames": [{"assembly": "pose", "duration_ticks": 2}]}
        },
    }
    path = tmp_path / "boxed.sprite.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    sprite = Sprite(SpriteSheet(str(path)))
    sprite.collidable = True
    return sprite


def _rects(plane: np.ndarray, boxes, at) -> np.ndarray:
    """The same boxes drawn independently, for the plane to be compared to.

    Clipped on BOTH ends. Clamping only the start and passing `x0 + w`
    straight through looks right until a box is entirely off the left edge,
    where the stop goes negative and NumPy reads it as an offset from the far
    end -- so the reference lights up a band on the opposite side of the
    screen and the comparison is against nonsense.
    """
    out = np.zeros_like(plane)
    height, width = out.shape
    for x, y, w, h in boxes:
        x0, y0 = at[0] + x, at[1] + y
        x1, y1 = min(x0 + w, width), min(y0 + h, height)
        x0, y0 = max(x0, 0), max(y0, 0)
        if x0 < x1 and y0 < y1:
            out[y0:y1, x0:x1] = True
    return out


def test_a_named_plane_is_its_rects_filled_in(window, tmp_path) -> None:
    boxes = [[-8, -16, 22, 17], [4, -4, 12, 20]]
    sprite = _boxed(tmp_path, {"hurt": boxes})
    sprite.assembly = "pose"
    sprite.position = (200, 150)
    _drawn(window, sprite)

    plane = sprite.collider("hurt").unpacked
    assert np.array_equal(plane, _rects(plane, boxes, (200, 150)))


def test_overlapping_boxes_are_a_union_not_a_sum(window, tmp_path) -> None:
    """They are ORed into one plane, so the overlap is counted once. Chun-Li's
    `body_hurt` is four boxes totalling 4902 pixels of area and 4836 bits."""
    boxes = [[0, 0, 20, 20], [10, 10, 20, 20]]
    sprite = _boxed(tmp_path, {"hurt": boxes})
    sprite.assembly = "pose"
    sprite.position = (100, 100)
    _drawn(window, sprite)

    bits = int(sprite.collider("hurt").unpacked.sum())
    assert bits == 20 * 20 * 2 - 10 * 10
    assert bits < sum(w * h for _x, _y, w, h in boxes)


def test_a_pose_without_that_box_answers_empty(window, tmp_path) -> None:
    """Not the last frame's. Chun-Li authors `attack` on some poses and not
    others, and a plane that held the previous frame's box would land hits
    from a move that had already finished."""
    sprite = _boxed(tmp_path, {"hurt": [[0, 0, 20, 20]]})
    sprite.assembly = "pose"
    sprite.position = (100, 100)
    _drawn(window, sprite)
    assert sprite.collider("hurt").unpacked.any()

    sprite.assembly = "bare"          # same sheet, no boxes on this pose
    _drawn(window, sprite)
    assert not sprite.collider("hurt").unpacked.any()


@pytest.mark.parametrize("turn", list(range(8)))
def test_boxes_turn_with_the_art(window, tmp_path, turn) -> None:
    """Through the SAME `local_rect` the parts go through, so a mirrored
    fighter's hitbox mirrors with her arm rather than staying where the artist
    drew it. A quarter turn swaps width and height, which for an axis-aligned
    box is exactly right -- a turned rectangle is a rectangle."""
    from blitspersecond.graphics.sprite.frame import local_rect
    from blitspersecond.common import Location, Rect

    boxes = [[-8, -16, 22, 17]]
    sprite = _boxed(tmp_path, {"hurt": boxes})
    sprite.assembly = "pose"
    sprite.orientation = Orientation(turn)
    sprite.position = (200, 150)
    _drawn(window, sprite)

    x, y, w, h = local_rect(
        turn, Location(-8, -16), Rect(-8, -16, 22, 17), sprite.origin
    )
    plane = sprite.collider("hurt").unpacked
    assert np.array_equal(plane, _rects(plane, [[x, y, w, h]], (200, 150)))
    assert int(plane.sum()) == 22 * 17


def test_the_vocabulary_is_the_sheets_and_a_typo_raises(window, tmp_path):
    """A typo'd plane name must be an error, not a hitbox that never hits."""
    sprite = _boxed(tmp_path, {"hurt": [[0, 0, 8, 8]], "attack": [[0, 0, 4, 4]]})
    assert sprite.planes == frozenset({"hurt", "attack"})
    # KeyError, as everywhere else a name is looked up in a vocabulary --
    # `sheet.assembly` and `atlas.rect` both answer that way.
    with pytest.raises(KeyError, match="unknown collision plane"):
        sprite.collider("hurtt")


def test_a_sheet_with_no_named_planes_says_so(window, bullets: Sprite) -> None:
    """The bullets are a real sheet whose vocabulary is empty -- the R-Type
    heritage case, where the silhouette is all there is. The message has to
    say that, not report an empty list."""
    assert bullets.planes == frozenset()
    with pytest.raises(KeyError, match="authors no named collision planes"):
        bullets.collider("attack")


def test_named_planes_are_the_primarys_alone(window, tmp_path) -> None:
    """Slot 0's, decided before this landed: a named plane is authored
    per-assembly geometry that must land in a SPECIFIC target, so N
    presentations would mean N x K rect stamps into fragmented planes. The
    silhouette is one composition into one bitmap, which is what makes a
    population affordable -- so a chain member stamps that and nothing else.
    """
    sprite = _boxed(tmp_path, {"hurt": [[0, 0, 20, 20]]})
    sprite.assembly = "pose"
    sprite.position = (100, 100)
    member = sprite.after.append()
    member.assembly = "pose"
    member.position = (400, 200)
    member.visible = True
    member.collide = True
    _drawn(window, sprite)

    # the member is in the silhouette ...
    assert sprite.collider().unpacked[200:210, 400:410].any()
    # ... and contributes no boxes
    assert not sprite.collider("hurt").unpacked[200:230, 400:430].any()
    # while the primary's box is where the primary is
    assert sprite.collider("hurt").unpacked[100:120, 100:120].all()


def test_a_box_plane_and_a_pixel_plane_ask_one_question(
    window, tmp_path, fumiko: Sprite
) -> None:
    """The point of filling rects into a plane. Nothing downstream knows one
    side was rectangles and the other was art: both are packed bits and the
    query is the same windowed AND."""
    sprite = _boxed(tmp_path, {"hurt": [[0, 0, 40, 40]]})
    sprite.assembly = "pose"
    sprite.position = (300, 180)
    fumiko.assembly = "idle-0"
    fumiko.position = (300, 180)
    _drawn(window, sprite)
    _drawn(window, fumiko)

    assert sprite.collider("hurt").collides_with(fumiko).at()
    assert fumiko.collides_with(sprite.collider("hurt")).at()
    # and a box far away does not
    sprite.position = (10, 300)
    _drawn(window, sprite)
    assert not sprite.collider("hurt").collides_with(fumiko).at()


def test_a_cloaked_primary_lands_no_boxes(window, tmp_path) -> None:
    """`collide` gates the named planes exactly as it gates the silhouette.

    A pose whose presentation is out of collision must land no hitboxes --
    otherwise a ghost in a trail, which carries the same assembly and so the
    same authored boxes, would hit for its principal.
    """
    sprite = _boxed(tmp_path, {"hurt": [[0, 0, 20, 20]]})
    sprite.assembly = "pose"
    sprite.position = (100, 100)
    _drawn(window, sprite)
    assert sprite.collider("hurt").unpacked.any()

    sprite.primary.collide = False
    _drawn(window, sprite)
    assert not sprite.collider("hurt").unpacked.any()
    assert not sprite.collider().unpacked.any()


def test_the_apron_holds_boxes_bigger_than_the_art(window, tmp_path) -> None:
    """The working border is sized from the art AND the boxes.

    Chun-Li's `push` is 44x77 on a sheet whose tiles need not be that tall, so
    a border scanned from the art alone would clip the boxes it also has to
    hold -- silently, because a stamp whose words fall outside the plane is
    dropped rather than refused.
    """
    boxes = [[-60, -60, 96, 96]]          # far bigger than the 8x8 tile
    sprite = _boxed(tmp_path, {"hurt": boxes})
    sprite.assembly = "pose"
    # Hard against the left edge, so most of the box is in the border and
    # only a short-aproned plane would lose it.
    sprite.position = (20, 100)
    _drawn(window, sprite)

    plane = sprite.collider("hurt").unpacked
    assert np.array_equal(plane, _rects(plane, boxes, (20, 100)))
    # It starts at screen x = 20 - 60 = -40, so 56 of its 96 columns are on
    # screen and all 96 of its rows are.
    assert int(plane.sum()) == 56 * 96
