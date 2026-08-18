"""Layers as the place layers come from, headless: no GL, no window.

`bps.layers.tile/sprite/glyph` mint a layer AND put it in the draw order --
that attachment is the contract these tests pin, because the only other
in-tree coverage of it needs a live window. Constructing a layer class
directly (or through the legacy *Engine aliases) is the unattached path.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from blitspersecond.display import Layers
from blitspersecond.graphics import (
    CharSet,
    GlyphEngine,
    GlyphLayer,
    PixelBuffer,
    SpriteEngine,
    SpriteLayer,
    TileAtlas,
    TileEngine,
    TileLayer,
)
from blitspersecond.resources import ImageSpec

from sprite_fixtures import build_known_sheet


@pytest.fixture()
def atlas() -> TileAtlas:
    return TileAtlas(
        PixelBuffer(ImageSpec(size=(32, 32), mode="P")), tile_size=(8, 8)
    )


@pytest.fixture()
def layers() -> Layers:
    return Layers()


# -- minting attaches --------------------------------------------------


def test_tile_mints_an_attached_tile_layer(layers, atlas):
    layer = layers.tile(atlas)

    assert isinstance(layer, TileLayer)
    assert layer.layers is layers
    assert list(layers) == [layer]


def test_glyph_mints_an_attached_glyph_layer(layers):
    layer = layers.glyph(CharSet.ASCII8X8)

    assert isinstance(layer, GlyphLayer)
    assert layer.layers is layers
    assert list(layers) == [layer]


def test_sprite_mints_an_attached_sprite_layer(layers, tmp_path):
    layer = layers.sprite(build_known_sheet(tmp_path))

    assert isinstance(layer, SpriteLayer)
    assert layer.layers is layers
    assert list(layers) == [layer]


def test_mint_appends_in_call_order(layers, atlas):
    back = layers.tile(atlas)
    front = layers.glyph((8, 8))

    assert list(layers) == [back, front]


def test_mint_accepts_a_tag(layers, atlas):
    layer = layers.tile(atlas, tag="scenery")

    assert layer.tag == "scenery"
    assert layers.tagged("scenery") == [layer]


def test_tile_forwards_its_arguments(layers):
    buffer = PixelBuffer(ImageSpec(size=(32, 32), mode="P"))

    layer = layers.tile(buffer, tilewidth=8, tileheight=4, collidable=True)

    assert layer.tile_size == (8, 4)
    assert layer.collidable is True


# -- direct construction does NOT attach -------------------------------


def test_building_a_layer_directly_leaves_it_unattached(atlas):
    assert TileLayer(atlas).layers is None


def test_legacy_engine_names_are_the_layer_classes():
    assert TileEngine is TileLayer
    assert SpriteEngine is SpriteLayer
    assert GlyphEngine is GlyphLayer


def test_legacy_engine_call_no_longer_attaches(layers, atlas):
    """The behaviour change: these used to find a running engine and attach."""
    loose = TileEngine(atlas)

    assert loose.layers is None
    assert list(layers) == []


def test_add_places_a_layer_built_unattached(layers, atlas):
    loose = TileLayer(atlas)

    assert layers.add(loose) is loose
    assert loose.layers is layers
    assert list(layers) == [loose]


# -- the guards --------------------------------------------------------


def test_reordering_an_unattached_layer_raises(layers, atlas):
    placed = layers.tile(atlas)
    loose = TileLayer(atlas)

    with pytest.raises(RuntimeError, match="not in a draw order"):
        loose.after(placed)


def test_a_layer_cannot_sit_in_two_draw_orders(layers, atlas):
    layer = layers.tile(atlas)

    with pytest.raises(ValueError, match="already in another Layers"):
        Layers().add(layer)


def test_add_is_idempotent_within_one_collection(layers, atlas):
    layer = layers.tile(atlas)

    layers.add(layer)

    assert list(layers) == [layer]


def test_remove_unbinds_and_re_add_returns_to_the_front(layers, atlas):
    first = layers.tile(atlas)
    second = layers.glyph((8, 8))

    assert layers.remove(first) is True
    assert first.layers is None
    assert list(layers) == [second]

    layers.add(first)
    assert list(layers) == [second, first]
