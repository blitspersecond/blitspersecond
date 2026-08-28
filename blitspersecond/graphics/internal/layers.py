from typing import List, Optional

from .layer import Layer


class Layers:
    """Ordered collection of layers, background (index 0) to foreground.

        background = TileLayer("tiles/bg.png", 8, 8)
        actors = SpriteLayer("band.png", 12, 12)
        bps.layers.add(background)
        bps.layers.add(actors)
        actors.after(background)

    Owns the ordering. Layers reposition themselves through their fluent
    interface (layer.before/after/first/last/swap_with), which delegates to the
    private `_move_*` helpers here -- the collection is the single source of
    truth, and a bound layer holds a back-reference to it (set on add, cleared
    on remove).

    An engine builds a layer; this collection places it. A layer is
    off-screen until `add` gives it somewhere to be, and off-screen again
    after `remove` -- those two verbs are the whole of what this owns.

    Keeping the building elsewhere is what keeps this file free of every
    engine: `Layers` imports no layer class, so adding a fourth kind of layer
    needs no line here. It used to mint them -- `bps.layers.tile(...)`,
    `.sprite(...)`, `.glyph(...)` -- which named all three engines in the
    signatures and had to import their classes inside each method body,
    because importing them at module scope closed a cycle through `graphics`.
    The deferred imports went with the mint methods; there is no longer a
    cycle to defer around.
    """

    def __init__(self):
        self._layers: List[Layer] = []

    def add(self, layer: Layer) -> None:
        """Place a layer at the foreground of the draw order, and so on screen.

        A layer arrives here unattached -- built by its engine, drawing
        nowhere -- and this is what gives it a place. Adding is the only way
        one gets on screen, and it is the whole of what `add` does: it does
        not build, and it does not move.

        Adding a layer that is already in this order RAISES rather than
        moving it to the front. Placing and repositioning are different
        questions asked in different words: `first()`, `last()`, `before()`
        and `after()` on the layer itself are the second one, and they say
        where they are putting it. `add` reading as a silent send-to-front
        would make the two spellings for the same intent disagree about
        whether the layer had to be somewhere else already.

        A layer sits in one draw order at a time; `remove()` it from the
        other first."""
        if layer._layers is self:
            raise ValueError(
                "layer is already in this draw order -- add() places a "
                "layer, it does not move one; reposition it with "
                "first()/last()/before()/after()"
            )
        if layer.attached:
            raise ValueError(
                "layer is already in another Layers collection; remove() "
                "it there first -- one layer cannot sit in two draw orders"
            )
        self._layers.append(layer)
        layer._bind(self)

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

    def remove(self, layer: Layer) -> None:
        """Take a layer out of the draw order, leaving it built but off-screen.

        The layer survives -- its contents, its tag, everything but its
        place -- so `add` can put it back later. It goes back at the
        foreground, not where it was; a draw order does not keep a space
        open for something that left it.

        Raises if the layer isn't in this collection: removing something
        that was never here, or has already been taken out, is a bug in the
        caller and not a quiet no-op to paper over."""
        if not isinstance(layer, Layer):
            raise TypeError(f"expected a Layer; got {type(layer)}")
        if layer._layers is not self:
            raise ValueError(f"{layer!r} is not in this Layers collection")
        self._layers.remove(layer)
        layer._bind(None)

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
        if layer._layers is not self:
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

    def _target(self, other: object) -> Layer:
        """Check that `other` is a layer in THIS order, and hand it back.

        RAISES rather than returning None. This used to be a silent `return`:
        ordering against a non-layer, or against a layer in another
        collection, quietly did nothing and the frame just came out wrong.
        That is the worst failure mode available here -- nothing reports it,
        and the picture is merely subtly incorrect, so the search starts at
        the drawing rather than at the ordering. A reorder that cannot happen
        must say so.
        """
        if not isinstance(other, Layer):
            raise TypeError(f"expected a Layer; got {type(other)}")
        if other._layers is not self:
            raise ValueError(
                "cannot order against a layer that is not in this draw "
                "order -- layers.add() it first"
            )
        return other

    def _move_before(self, layer: Layer, other: object) -> None:
        """Place `layer` immediately behind `other` (drawn just before it)."""
        target = self._target(other)
        if layer is target:
            return
        self._layers.remove(layer)
        self._layers.insert(self._layers.index(target), layer)

    def _move_after(self, layer: Layer, other: object) -> None:
        """Place `layer` immediately in front of `other` (drawn just after it)."""
        target = self._target(other)
        if layer is target:
            return
        self._layers.remove(layer)
        self._layers.insert(self._layers.index(target) + 1, layer)

    def _swap(self, a: Layer, b: object) -> None:
        target = self._target(b)
        if a is target:
            return
        i, j = self._layers.index(a), self._layers.index(target)
        self._layers[i], self._layers[j] = self._layers[j], self._layers[i]

    def _to_back(self, layer: Layer) -> None:
        """Move to the background (index 0, drawn first)."""
        self._layers.remove(layer)
        self._layers.insert(0, layer)

    def _to_front(self, layer: Layer) -> None:
        """Move to the foreground (drawn last)."""
        self._layers.remove(layer)
        self._layers.append(layer)

    def __getitem__(self, index: int) -> Layer:
        return self._layers[index]

    def __iter__(self):
        """Iterate layers from background to foreground."""
        return iter(self._layers)

    def __len__(self) -> int:
        return len(self._layers)
