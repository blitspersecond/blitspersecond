from typing import Optional
from uuid import uuid4


class Layer:
    """A place in the compositor's draw order -- and, in every subclass, the
    content that place shows. Engines produce Layers (TileLayer, SpriteLayer,
    GlyphLayer): one object is both the thing you draw with and where it
    sits. The base class owns only the compositing concerns -- enabled, tag,
    stack position; blend/opacity/offset later.

    You don't construct these directly -- the engine factories hand them out,
    already in the order. Reorder fluently: `layer.after(hud)`,
    `layer.first()`, `a.swap_with(b)`.
    """

    def __init__(self, tag: Optional[str] = None) -> None:
        self._id: str = str(uuid4())
        self._enabled: bool = True
        self._tag: Optional[str] = tag
        self._layers = None  # back-reference, set by Layers.add

    @property
    def id(self) -> str:
        """Stable unique id -- handy for lookup / stable sort keys."""
        return self._id

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        if not isinstance(value, bool):
            raise ValueError("enabled must be a boolean")
        self._enabled = value

    @property
    def tag(self) -> Optional[str]:
        return self._tag

    @tag.setter
    def tag(self, value: Optional[str]) -> None:
        self._tag = value

    @property
    def layers(self):
        """The Layers collection this layer is bound to (None if unbound)."""
        return self._layers

    @layers.setter
    def layers(self, layers) -> None:
        self._layers = layers

    # -- fluent ordering (delegates to the bound collection) -------------

    def before(self, other: "Layer") -> "Layer":
        """Draw just before `other` (one step toward the background)."""
        if self._layers is not None:
            self._layers._move_before(self, other)
        return self

    def after(self, other: "Layer") -> "Layer":
        """Draw just after `other` (one step toward the foreground)."""
        if self._layers is not None:
            self._layers._move_after(self, other)
        return self

    def swap_with(self, other: "Layer") -> "Layer":
        """Swap stack positions with `other`."""
        if self._layers is not None:
            self._layers._swap(self, other)
        return self

    def first(self) -> "Layer":
        """Move to the background (drawn first)."""
        if self._layers is not None:
            self._layers._to_back(self)
        return self

    def last(self) -> "Layer":
        """Move to the foreground (drawn last)."""
        if self._layers is not None:
            self._layers._to_front(self)
        return self

    def _composite(self) -> None:
        """Draw this layer's content into the current framebuffer. Called once
        per frame by the compositor while the FBO + projection are bound.
        Subclasses ARE the content and override this; the bare base draws
        nothing."""

    def __str__(self) -> str:
        return f"id: {self._id} [{self._tag}]"


def attach_to_display(layer: Layer) -> Layer:
    """Add a layer to the existing engine's display without booting one."""
    from blitspersecond.blitspersecond import BlitsPerSecond

    bps = BlitsPerSecond.current()
    if bps is not None:
        bps.layers.add(layer)
    return layer
