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
        actors = bps.layers.sprite("band.png", 12, 12)
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

    def add[T](self, item: T) -> T:
        """Place an already-built Layer at the front of the draw order --
        or, given a controller like Console that wraps one (built unattached
        the same way TileLayer(...) and friends are), attach the layer it
        wraps and hand back the controller.

        tile()/sprite()/glyph() below do this for you; this is for a layer
        built unattached, a controller built over one, or for re-adding a
        layer after remove(). Re-adding puts it at the front, not back where
        it was.

        A controller exposes its layer through a public `.layer` property
        (Console has one; TileMap will) rather than `add` checking
        `isinstance(item, Console)` -- display/ stays ignorant of concrete
        controller types this way, which matters because console imports
        graphics.glyph, and importing Console here would loop back through
        it (see the module docstring above).

        Anything that is neither a Layer nor a controller is refused."""
        layer = item if isinstance(item, Layer) else getattr(item, "layer", None)
        if not isinstance(layer, Layer):
            raise TypeError(
                f"layers.add takes a Layer, or a controller exposing a "
                f"`.layer` -- layers.tile/sprite/glyph make layers "
                f"directly, or build one unattached with TileLayer / "
                f"SpriteLayer / GlyphLayer; got {type(item)}"
            )
        if layer.attached and layer._layers is not self:
            raise ValueError(
                "layer is already in another Layers collection; remove() "
                "it there first -- one layer cannot sit in two draw orders"
            )
        if layer not in self._layers:
            self._layers.append(layer)
            layer._bind(self)
        return item

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
        source: Union[str, "SpriteSheet"],
        w: Optional[int] = None,
        h: Optional[int] = None,
        ox: int = 0,
        oy: int = 0,
        *,
        tag: Optional[str] = None,
    ) -> "SpriteLayer":
        """Make a SpriteLayer over `source` and put it at the front.

            band = bps.layers.sprite("band.png", 12, 12)   # bare image
            actors = bps.layers.sprite("chun-li.sprite.json")
            chun = actors.sprite("stance-01")

        `source` is a sprite package file, an indexed image, or a SpriteSheet
        you already built. Giving w/h is what says "read this image as a
        uniform grid" -- the same two roads `tile()` offers, since a package
        is to a sprite sheet what a TMX is to a tileset: optional. A grid
        sheet's poses are addressed by cell index rather than by name, and
        anchor at the cell's top-left; `ox`/`oy` move that anchor, so
        `sprite("bullets.png", 8, 8, -4, -4)` centres each cell on its
        sprite's position.

        One sheet per layer; the layer is the collision identity and the depth
        position, so a mirror match is two SpriteLayers over the same sheet.
        Note the two `sprite` verbs: this one makes the layer, the layer's own
        makes an instance on it.
        """
        from blitspersecond.graphics.sprite import SpriteLayer, SpriteSheet

        if isinstance(source, str):
            if (w is None) != (h is None):
                raise TypeError(
                    "sprite() takes both w and h or neither -- w/h read the "
                    "image as a uniform grid, and half a grid is nothing"
                )
            if w is None and (ox or oy):
                raise TypeError(
                    "sprite() takes ox/oy only with w/h -- an authored "
                    "package already places its own anchor"
                )
            sheet = (
                SpriteSheet.grid(source, w, h, ox, oy)
                if w is not None and h is not None
                else SpriteSheet.load(source)
            )
        elif w is not None or h is not None or ox or oy:
            raise TypeError(
                "sprite() takes w/h/ox/oy only with an image path -- a "
                "SpriteSheet already carries its own reading"
            )
        else:
            sheet = source
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

    def _advance(self) -> None:
        """One game tick of playback across every layer that has any.

        The engine loop calls this once per fixed tick, after the game's tick
        callback. Only sprite layers do anything -- tiles and glyphs have no
        clips -- and a sprite layer with nothing playing costs an empty set
        test, so a scene that animates nothing pays nothing.
        """
        for layer in self._layers:
            advance = getattr(layer, "_advance", None)
            if advance is not None:
                advance()

    def remove[T](self, item: T) -> T:
        """Take a layer -- or a controller wrapping one, through the same
        `.layer` a controller exposes to `add` -- out of the draw order.

        Raises if `item` isn't in this collection: removing something that
        was never here (or already taken out) is a bug in the caller, not a
        quiet no-op to paper over."""
        layer = item if isinstance(item, Layer) else getattr(item, "layer", None)
        if not isinstance(layer, Layer) or layer not in self._layers:
            raise ValueError(f"{item!r} is not in this Layers collection")
        self._layers.remove(layer)
        layer._bind(None)
        return item

    def find(self, *, tag: str) -> Optional[List[Layer]]:
        """Bound layers carrying `tag`, in stack order -- or None if none do.

        Unlike a plain lookup returning empty, None distinguishes "nothing
        has this tag" from a caller who forgot to check before indexing."""
        matches = [layer for layer in self._layers if layer.tag == tag]
        return matches if matches else None

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
