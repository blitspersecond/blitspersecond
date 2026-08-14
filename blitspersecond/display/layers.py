from typing import List

from .layer import Layer


class Layers:
    """Ordered collection of layers, background (index 0) to foreground.

    Owns the ordering. Layers reposition themselves through their fluent
    interface (layer.before/after/first/last/swap_with), which delegates to the
    private `_move_*` helpers here -- the collection is the single source of
    truth, and a bound layer holds a back-reference to it (set on add, cleared
    on remove).
    """

    def __init__(self):
        self._layers: List[Layer] = []

    def add(self, layer: Layer) -> Layer:
        """Append a Layer at the front of the draw order. Engines make layers
        (TileEngine, SpriteEngine, GlyphEngine all hand back one, already
        added when the engine is running) -- so this is for placing a layer
        built unattached, or re-adding one after remove(). Anything that
        isn't a Layer is refused."""
        if not isinstance(layer, Layer):
            raise TypeError(
                f"layers.add takes a Layer -- engines make them "
                f"(TileEngine, SpriteEngine, GlyphEngine); got {type(layer)}"
            )
        if layer not in self._layers:
            self._layers.append(layer)
            layer.layers = self
        return layer

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
