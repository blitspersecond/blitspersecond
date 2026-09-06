"""The console's collision planes: derived on demand, never on the frame path.

This pillar is the one that PULLS. The tile and sprite masks are written when
content changes, because something has to be stamped; a console already holds
everything a plane needs -- the glyph gather and the composed image -- so it
derives instead, and the tests that matter are therefore about *when* nothing
happens as much as about the bits.

Three claims are worth the words:

  * **The silhouette is what was drawn.** Proved against GL, by compositing
    the layer and comparing the framebuffer with `collider().unpacked` pixel
    for pixel. Not a tautology: `pack_store` is new, and a bit-order or a
    row-order slip would show here and nowhere else.
  * **A named plane is allowed to disagree with it.** Plane 2 on the stock
    charset draws nothing -- `SYSTEM_SLOTS` leaves slot 2 transparent -- and
    collides all the same. That is the feature, not an edge case.
  * **Nothing is retained until asked, and nothing survives a write.** A
    console that never collides must cost nothing, and one that does must
    never answer about a frame that has gone.
"""

from __future__ import annotations

import numpy as np
import pytest

pyglet = pytest.importorskip("pyglet")

from blitspersecond.common import SCREEN  # noqa: E402
from blitspersecond.graphics.collision import pack_store, unpack  # noqa: E402
from blitspersecond.graphics.console import (  # noqa: E402
    PLANE_0,
    PLANE_1,
    PLANE_2,
    PLANE_3,
    Console,
    Pen,
)

#: A palette index that is not 0, for a pen slot we want to be visible.
INK = 1

#: A palette index we can hand a slot to make it collide *and* draw, so a
#: test can tell "transparent" apart from "not authored".
#:
#: Red, and NOT index 4, which is opaque BLACK in the system palette. The GL
#: readback below decides "was drawn" by "is not the clear colour", so a black
#: entry would be drawn and read back as absent -- 177 phantom disagreements,
#: measured, before this was 5.
LOUD = 5

#: A second visible index, so the readback tests light plane 3 as well and a
#: slip confined to one plane cannot hide inside another's pixels. Yellow.
LOUDER = 7


def _console(collidable: bool = True) -> Console:
    console = Console()
    console.collidable = collidable
    return console


# -- the gate --------------------------------------------------------------


def test_a_console_is_never_refused() -> None:
    """Its image is mode "P" from construction, so there is nothing to check.

    Unlike a tilemap, whose tileset may be RGBA and whose refusal is a real
    branch, a console cannot be built un-palettised."""
    assert Console()._collision_refusal() is None


def test_collidable_starts_off() -> None:
    """Eligible is not the same as on.

    The derivation is lazy, so leaving it off costs a console that never
    collides exactly nothing -- and leaving it *on* by default would make
    every console in every demo look like a collision participant."""
    assert Console().collidable is False


def test_asking_a_switched_off_console_raises() -> None:
    console = Console()
    with pytest.raises(ValueError, match="collidable=False"):
        console.collider()


def test_switching_on_and_off_is_just_the_switch() -> None:
    console = _console()
    console.write("x")
    assert console.collider().unpacked.any()
    console.collidable = False
    console.collidable = True
    assert console.collider().unpacked.any()


# -- the vocabulary --------------------------------------------------------


def test_all_four_planes_are_offered() -> None:
    """Plane 0 included.

    It is conventionally the glyph's ground and so usually the holes, but a
    pen may paint it a real colour, and reserving it would assume a convention
    `Pen` explicitly declines to assume."""
    assert _console().planes == {PLANE_0, PLANE_1, PLANE_2, PLANE_3}


def test_plane_constants_are_the_pens_slot_indices() -> None:
    """One index seen from two ends: what a plane draws, what it collides as.

    If these ever stopped being plain ints, `pen[PLANE_2]` and
    `console.collider(PLANE_2)` would need two spellings between them."""
    assert (PLANE_0, PLANE_1, PLANE_2, PLANE_3) == (0, 1, 2, 3)


def test_an_unknown_plane_raises_and_says_the_vocabulary() -> None:
    console = _console()
    with pytest.raises(KeyError, match=r"\[0, 1, 2, 3\]"):
        console.collider(4)
    with pytest.raises(KeyError):
        console.collider("attack")


def test_planes_are_screen_space_with_no_offset() -> None:
    """A tilemap reports a border plus a sub-word remainder because it has a
    camera over a world larger than the screen. A console has nowhere else
    to be."""
    assert _console().collision_offset == (0, 0)


# -- shape and cost --------------------------------------------------------


def test_a_plane_is_screen_shaped_store_width_words() -> None:
    """`uint32`, not the `uint64` a *pattern* stages in.

    A console plane is a store: it sits where it belongs and is never
    shifted, so the wide staging type would double the bytes to buy a carry
    that has nowhere to go."""
    plane = _console().collision_plane()
    assert plane.shape == (SCREEN.height, 20)
    assert plane.dtype == np.uint32
    assert plane.nbytes == 28800


def test_a_console_nobody_asks_retains_nothing() -> None:
    """The whole reason this pillar pulls."""
    console = _console()
    console.write("hello")
    console.scroll(0, 1)
    console.clear()
    assert console._collision == {}


def test_a_plane_is_derived_once_and_cached() -> None:
    console = _console()
    console.write("x")
    first = console.collision_plane()
    assert console.collision_plane() is first


def test_only_the_planes_asked_for_are_held() -> None:
    console = _console()
    console.write("x")
    console.collision_plane(PLANE_2)
    assert set(console._collision) == {PLANE_2}


# -- invalidation ----------------------------------------------------------


@pytest.mark.parametrize(
    "write",
    [
        pytest.param(lambda c: c.write("text"), id="write"),
        pytest.param(lambda c: c.clear(), id="clear"),
        pytest.param(lambda c: c.scroll(0, 1), id="scroll"),
        pytest.param(lambda c: c.cell.__setitem__((1, 1), 65), id="cell"),
        pytest.param(
            lambda c: c.paint_buffer.__setitem__(
                (slice(None), slice(None)), Pen(INK, INK, INK, INK)
            ),
            id="paint-buffer",
        ),
    ],
)
def test_every_write_path_drops_the_planes(write) -> None:
    """`_touch` is the one funnel every write reaches, so this is one line in
    the engine and six here -- which is the right way round.

    A path added later that skips `_touch` would answer with a plane
    describing the frame before it, and a hitbox that is one edit stale is
    exactly the bug that looks like working code."""
    console = _console()
    console.write("seed")
    console.collision_plane()
    console.collision_plane(PLANE_2)
    assert console._collision
    write(console)
    assert console._collision == {}


def test_repainting_a_visible_glyph_drops_the_planes() -> None:
    """A glyph pattern is the geometry itself, so this moves bits rather than
    colours -- `GlyphAccess` recomposes and the recompose goes through
    `_touch`."""
    console = _console()
    console.write("A")
    console.collision_plane()
    console.glyph[ord("A")] = np.ones((12, 8), dtype=np.uint8)
    assert console._collision == {}
    assert console.collider().unpacked.sum() == 12 * 8


def test_repainting_an_offscreen_glyph_keeps_them() -> None:
    """`GlyphAccess` only recomposes when the glyph is on screen, so a pattern
    nothing is currently displaying changes no pixel and no plane.

    Worth stating rather than assuming: it means the cache survives edits that
    cannot matter, and it means the invalidation rides the same test the
    REDRAW rides -- one condition, so the two cannot come to disagree about
    what counts as a change."""
    console = _console()
    console.write("A")
    held = console.collision_plane()
    console.glyph[ord("Z")] = np.ones((12, 8), dtype=np.uint8)
    assert console.collision_plane() is held


def test_an_edit_changes_the_bits_not_just_the_cache() -> None:
    console = _console()
    console.write("i")
    before = int(console.collider().unpacked.sum())
    console.write("mmmm")
    after = int(console.collider().unpacked.sum())
    assert after > before


def test_a_deferred_write_is_answered_about() -> None:
    """Inside a `defer()` block the cells are current and the image is not.

    The named planes read cells and the silhouette reads pixels, so answering
    without composing first would let the two describe different frames.
    `collision_plane` syncs, which costs a recompose the block was going to
    pay for anyway."""
    console = _console()
    with console.defer():
        console.write("deferred")
        assert console.collider().unpacked.any()


# -- what is drawn, and what is not ----------------------------------------


def test_the_silhouette_is_the_composed_pixels_not_the_glyph_planes() -> None:
    """`pixels != 0`, deliberately -- so a transparent slot is not solid.

    A pen slot may legitimately hold palette index 0, and `paint_buffer`
    resolves through colour rather than through the glyph, so only the
    composed image knows what was actually drawn. Drawn and solid stay one
    predicate."""
    console = _console()
    console.pen = Pen(0, INK, 0, 0)
    console.write("A")
    silhouette = console.collider().unpacked
    assert np.array_equal(silhouette, console._image.pixels != 0)
    # The stock charset puts the font's outline on plane 2, which this pen
    # leaves transparent -- so plane 2 is lit where the silhouette is not.
    outline = console.collider(PLANE_2).unpacked
    assert outline.any()
    assert not (outline & silhouette).any()


def test_a_transparent_plane_draws_nothing_and_collides() -> None:
    """The card's headline, and it falls straight out of the stock font.

    `SYSTEM_SLOTS` declines to paint 2 and 3 because a default must not
    decide a font's outline colour. That makes plane 2 authored geometry
    nobody can see -- paint a level in glyphs and get collision planes out of
    one layer."""
    console = _console()
    console.write("W")
    assert console.collider(PLANE_2).unpacked.any()
    assert not (console._image.pixels == 2).any()


def test_painting_a_slot_makes_it_visible_without_moving_the_plane() -> None:
    """The plane is the glyph's geometry; the pen is only its colour.

    So turning the outline on changes the silhouette and leaves plane 2
    exactly where it was."""
    console = _console()
    console.write("W")
    outline = console.collider(PLANE_2).unpacked.copy()
    hidden = console.collider().unpacked.copy()

    console.pen = Pen(0, INK, LOUD, 0)
    console.clear(console.pen)
    console.write("W")
    assert np.array_equal(console.collider(PLANE_2).unpacked, outline)
    assert (console.collider().unpacked & outline).any()
    assert not np.array_equal(console.collider().unpacked, hidden)


def test_the_planes_partition_the_screen() -> None:
    """Every pixel belongs to exactly one glyph plane, so the four cover the
    screen and never overlap. That is the gather being a gather: a pixel has
    one plane index and cannot have two."""
    console = _console()
    console.write("partition me")
    planes = [console.collider(k).unpacked for k in (0, 1, 2, 3)]
    stacked = np.stack(planes)
    assert np.array_equal(stacked.sum(axis=0), np.ones((SCREEN.height, SCREEN.width)))


def test_an_empty_console_has_an_empty_silhouette() -> None:
    """A cleared console draws nothing, so it is solid nowhere -- while plane
    0, its ground, is the whole screen."""
    console = _console()
    assert not console.collider().unpacked.any()
    assert console.collider(PLANE_0).unpacked.all()


# -- asking a question -----------------------------------------------------


def test_a_console_answers_a_collision_question() -> None:
    console = _console()
    console.write("AB")
    other = _console()
    other.write("AB")
    assert console.collides_with(other).at(0, 0, SCREEN.width, SCREEN.height)


def test_two_consoles_miss_when_their_text_does_not_overlap() -> None:
    console = _console()
    console.write("A")
    other = _console()
    other.cursor = (40, 20)
    other.write("A")
    assert not console.collides_with(other).at(
        0, 0, SCREEN.width, SCREEN.height
    )


def test_a_named_plane_hits_where_the_silhouette_does_not() -> None:
    """The invisible-authoring case asked as a real question: an outline
    nobody drew still stops something."""
    console = _console()
    console.write("W")
    probe = _console()
    probe.write("W")
    outline = console.collider(PLANE_2)
    assert outline.collides_with(probe.collider(PLANE_2)).at(
        0, 0, SCREEN.width, SCREEN.height
    )


# -- the picture and the mask, against GL ----------------------------------


@pytest.fixture(scope="module")
def window():
    """One invisible window the size of the screen, so a readback IS the
    screen and no scaling sits between the picture and the mask."""
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


def _drawn(window, layer) -> np.ndarray:
    """Composite `layer` over black and return the (360, 640) bool silhouette."""
    from pyglet.gl import (
        GL_BLEND,
        GL_COLOR_BUFFER_BIT,
        GL_ONE_MINUS_SRC_ALPHA,
        GL_RGBA,
        GL_SRC_ALPHA,
        GL_UNSIGNED_BYTE,
        glBlendFunc,
        glClear,
        glClearColor,
        glDisable,
        glEnable,
        glReadPixels,
        glViewport,
    )

    window.switch_to()
    glViewport(0, 0, SCREEN.width, SCREEN.height)
    glClearColor(0.0, 0.0, 0.0, 1.0)
    glClear(GL_COLOR_BUFFER_BIT)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    layer._composite()
    glDisable(GL_BLEND)
    raw = (pyglet.gl.GLubyte * (SCREEN.width * SCREEN.height * 4))()
    glReadPixels(
        0, 0, SCREEN.width, SCREEN.height, GL_RGBA, GL_UNSIGNED_BYTE, raw
    )
    pixels = np.frombuffer(raw, dtype=np.uint8).reshape(
        SCREEN.height, SCREEN.width, 4
    )
    # No palette entry used here is black, so anything not the clear was drawn.
    return pixels[:, :, :3].any(axis=2)


def _sky(console: Console) -> None:
    """Ink on every plane a glyph can use, so the readback sees the whole
    silhouette rather than the ink alone -- otherwise a plane-2 slip would
    hide behind a colour nobody painted."""
    console.pen = Pen(0, INK, LOUD, LOUDER)
    console.clear(console.pen)


def test_a_console_collides_where_it_draws(window) -> None:
    """**The claim worth proving**: the mask and the picture are the same
    shape in the same place, checked pixel for pixel against the framebuffer
    rather than against another expression of the same arithmetic."""
    console = _console()
    _sky(console)
    console.write("The quick brown fox jumps over the lazy dog. 0123456789")
    console.cursor = (0, 12)
    console.print("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
    disagreement = int(
        np.count_nonzero(_drawn(window, console) ^ console.collider().unpacked)
    )
    assert disagreement == 0


def test_the_mask_follows_a_scroll(window) -> None:
    """Nothing is retained across the scroll, so this is really asking that
    the re-derivation lands where the redraw did."""
    console = _console()
    _sky(console)
    console.write("scrolling")
    for _ in range(3):
        console.scroll(0, 1)
        assert (
            int(
                np.count_nonzero(
                    _drawn(window, console) ^ console.collider().unpacked
                )
            )
            == 0
        )


def test_the_mask_follows_a_partial_recompose(window) -> None:
    """`_refresh_rows` writes a band and `collision_plane` re-derives the
    whole screen. They share `_plane_indices`, and this is what says so."""
    console = _console()
    _sky(console)
    console.cursor = (0, 5)
    console.write("one line only")
    assert (
        int(np.count_nonzero(_drawn(window, console) ^ console.collider().unpacked))
        == 0
    )


# -- pack_store, the new primitive ------------------------------------------


def test_pack_store_is_pack_without_the_staging_width() -> None:
    """One implementation, two moments. If these ever disagreed, a console
    plane and a sprite pattern would stop speaking the same layout."""
    from blitspersecond.graphics.collision import pack

    rng = np.random.default_rng(3)
    bits = rng.random((17, 70)) > 0.5
    assert np.array_equal(pack_store(bits).astype(np.uint64), pack(bits))
    assert pack_store(bits).dtype == np.uint32


def test_pack_store_pads_a_ragged_width() -> None:
    """A console's image is `cols * char_width`, which need not be 640: an
    8x12 charset gives 640, but a 12-wide one gives 636 and the tail word is
    partial."""
    bits = np.zeros((2, 70), dtype=bool)
    bits[0, 69] = True
    packed = pack_store(bits)
    assert packed.shape == (2, 3)
    assert np.array_equal(unpack(packed, 70), bits)


def test_pack_store_round_trips_through_unpack() -> None:
    rng = np.random.default_rng(9)
    bits = rng.random((SCREEN.height, SCREEN.width)) > 0.7
    assert np.array_equal(unpack(pack_store(bits), SCREEN.width), bits)
