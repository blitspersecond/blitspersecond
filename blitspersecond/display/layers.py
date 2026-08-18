from typing import TYPE_CHECKING, List, Optional, Tuple, Union

from .layer import Layer

# Annotations only. The mint methods below import their layer class at CALL
# time, and that deferral is load-bearing, not style: `graphics` imports
# `display.layer` for the Layer base, so a module-level `from
# blitspersecond.graphics import TileLayer` here closes the loop -- importing
# `blitspersecond.graphics` first then reaches this module while `graphics` is
# still half-built and raises. (Moving Shader to `blitspersecond.core` removed
# the OTHER direction -- display no longer imports graphics at all at import
# time -- but it did not remove this one. Verified: hoisting these still
# breaks a graphics-first import.)
if TYPE_CHECKING:
    from blitspersecond.graphics.common import PixelBuffer
    from blitspersecond.graphics.glyph import CharSet, GlyphLayer
    from blitspersecond.graphics.sprite import SpriteLayer, SpriteSheet
    from blitspersecond.graphics.tile import TileAtlas, TileLayer


class Layers:
    """Ordered collection of layers, background (index 0) to foreground --
    and the place layers come from:

        background = bps.layers.tile("tiles/bg.png", w=8, h=8)
        actors = bps.layers.sprite(sheet)
        actors.after(background)

    Owns the ordering. Layers reposition themselves through their fluent
    interface (layer.before/after/first/last/swap_with), which delegates to the
    private `_move_*` helpers here -- the collection is the single source of
    truth, and a bound layer holds a back-reference to it (set on add, cleared
    on remove).

    Minting through tile()/sprite()/glyph() attaches to THIS collection, so
    making a layer IS showing it, with no global engine lookup involved.
    Building one directly instead (TileLayer(...), SpriteLayer(...),
    GlyphLayer(...)) leaves it unattached and off-screen until add() places it.
    """

    def __init__(self):
        self._layers: List[Layer] = []

    def add[T: Layer](self, layer: T) -> T:
        """Place an already-built Layer at the front of the draw order.

        tile()/sprite()/glyph() below do this for you; this is for a layer
        built unattached (TileLayer(...) and friends) or for re-adding one
        after remove(). Re-adding puts it at the front, not back where it
        was. Anything that isn't a Layer is refused."""
        if not isinstance(layer, Layer):
            raise TypeError(
                f"layers.add takes a Layer -- layers.tile/sprite/glyph make "
                f"them, or build one unattached with TileLayer / SpriteLayer "
                f"/ GlyphLayer; got {type(layer)}"
            )
        if layer.layers is not None and layer.layers is not self:
            raise ValueError(
                "layer is already in another Layers collection; remove() it "
                "there first -- one layer cannot sit in two draw orders"
            )
        if layer not in self._layers:
            self._layers.append(layer)
            layer.layers = self
        return layer

    # -- minting: the primary way layers come into being -------------------

    def tile(
        self,
        source: Union[str, "PixelBuffer", "TileAtlas"],
        w: Optional[int] = None,
        h: Optional[int] = None,
        collidable: bool = False,
        *,
        tag: Optional[str] = None,
    ) -> "TileLayer":
        """Make a TileLayer over `source` and put it at the front.

            background = bps.layers.tile("tiles/bg.png", w=8, h=8)
            background.blit(1, x, y)
            background.before(foreground)

        Retained tile placements over one atlas. `source` is an image path, a
        PixelBuffer (the whole-file path -- one buffer as one giant tile) or a
        TileAtlas you already built; w/h are one tile's size in pixels and
        are required unless `source` is an atlas that carries its own grid. `collidable` opts the layer into screen-space collision masks,
        which only an indexed (palette) surface can supply.
        """
        from blitspersecond.graphics.tile import TileLayer

        layer = TileLayer(source, w, h, collidable)
        if tag is not None:
            layer.tag = tag
        return self.add(layer)

    def sprite(
        self,
        sheet: "SpriteSheet",
        *,
        tag: Optional[str] = None,
    ) -> "SpriteLayer":
        """Make a SpriteLayer bound to `sheet` and put it at the front.

            actors = bps.layers.sprite(sheet)
            chun = actors.sprite("stance-01")

        One sheet per layer; the layer is the collision identity and the depth
        position, so a mirror match is two SpriteLayers over the same sheet.
        Note the two `sprite` verbs: this one makes the layer, the layer's own
        makes an instance on it.
        """
        from blitspersecond.graphics.sprite import SpriteLayer

        layer = SpriteLayer(sheet)
        if tag is not None:
            layer.tag = tag
        return self.add(layer)

    def glyph(
        self,
        charset: Union["CharSet", Tuple[int, int]],
        *,
        tag: Optional[str] = None,
    ) -> "GlyphLayer":
        """Make a GlyphLayer over `charset` and put it at the front.

            console = Console(bps.layers.glyph(CharSet.ASCII8X12))

        `charset` is a BPS system CharSet, or a (width, height) character size
        for 256 blank glyphs you fill yourself through `layer.glyph[...]`. The
        character size must tile the screen exactly.
        """
        from blitspersecond.graphics.glyph import GlyphLayer

        layer = GlyphLayer(charset)
        if tag is not None:
            layer.tag = tag
        return self.add(layer)

    def remove(self, layer: Layer) -> bool:
        """Take a layer out of the draw order. Returns False if it wasn't in it."""
        if not isinstance(layer, Layer) or layer not in self._layers:
            return False
        self._layers.remove(layer)
        layer.layers = None
        return True

    def tagged(self, tag: str) -> List[Layer]:
        """All bound layers carrying `tag`, in stack order."""
        return [layer for layer in self._layers if layer.tag == tag]

    # -- reposition (all operate on already-bound layers) ----------------

    def __setitem__(self, index: int, layer: Layer) -> None:
        """Reposition an already-bound layer to `index`. This does NOT replace
        the layer currently at `index` -- it moves `layer` there and shuffles
        the rest along."""
        if not isinstance(layer, Layer):
            raise TypeError("Can only reposition Layer objects")
        if layer not in self._layers:
            raise ValueError("Can only reposition layers already in the collection")
        if not 0 <= index < len(self._layers):
            raise IndexError(
                f"index {index} out of bounds for {len(self._layers)} layers"
            )
        # Remove first, then insert at the target: because the list is one
        # shorter after removal, inserting at `index` lands the layer exactly
        # at `index` (no off-by-one regardless of its original position).
        self._layers.remove(layer)
        self._layers.insert(index, layer)

    def _move_before(self, layer: Layer, other: Layer) -> None:
        """Place `layer` immediately behind `other` (drawn just before it)."""
        if layer is other or layer not in self._layers or other not in self._layers:
            return
        self._layers.remove(layer)
        self._layers.insert(self._layers.index(other), layer)

    def _move_after(self, layer: Layer, other: Layer) -> None:
        """Place `layer` immediately in front of `other` (drawn just after it)."""
        if layer is other or layer not in self._layers or other not in self._layers:
            return
        self._layers.remove(layer)
        self._layers.insert(self._layers.index(other) + 1, layer)

    def _swap(self, a: Layer, b: Layer) -> None:
        if a not in self._layers or b not in self._layers:
            return
        i, j = self._layers.index(a), self._layers.index(b)
        self._layers[i], self._layers[j] = self._layers[j], self._layers[i]

    def _to_back(self, layer: Layer) -> None:
        """Move to the background (index 0, drawn first)."""
        if layer in self._layers:
            self._layers.remove(layer)
            self._layers.insert(0, layer)

    def _to_front(self, layer: Layer) -> None:
        """Move to the foreground (drawn last)."""
        if layer in self._layers:
            self._layers.remove(layer)
            self._layers.append(layer)

    def __getitem__(self, index: int) -> Layer:
        return self._layers[index]

    def __iter__(self):
        """Iterate layers from background to foreground."""
        return iter(self._layers)

    def __len__(self) -> int:
        return len(self._layers)
