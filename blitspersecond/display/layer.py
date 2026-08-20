from typing import Optional
from uuid import uuid4


class Layer:
    """A place in the compositor's draw order -- and, in every subclass, the
    content that place shows. Engines produce Layers (TileLayer, SpriteLayer,
    GlyphLayer): one object is both the thing you draw with and where it
    sits. The base class owns only the compositing concerns -- enabled, tag,
    stack position; blend/opacity/offset later.

    You don't usually construct these directly -- `bps.layers` mints them
    (`bps.layers.tile(...)`, `.sprite(...)`, `.glyph(...)`), already in the
    order. Reorder fluently: `layer.after(hud)`, `layer.first()`,
    `a.swap_with(b)`. Building one by hand instead (TileLayer(...) and
    friends) leaves it unattached and off-screen until `bps.layers.add(layer)`
    places it -- and reordering an unattached layer raises, because a draw
    order it isn't in cannot be changed.
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
        """Whether the compositor draws this layer. Drawing only: a disabled
        layer keeps its contents and keeps answering `collides_with`, in the
        same way a sprite's `visible` is separate from its `collide`. Turning
        a layer off is a view decision, not a world one -- to take its
        contents out of play, clear their collide bits."""
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

    def _bound(self):
        """The collection this layer sits in, or raise saying it sits in none.

        Reordering used to no-op silently when unbound, which made an
        off-screen layer look like a draw-order bug. There is no sensible
        quiet answer -- a position in an order you are not in is undefined --
        so asking is an error."""
        if self._layers is None:
            raise RuntimeError(
                f"{type(self).__name__} is not in a draw order, so it cannot "
                f"be reordered -- mint it with bps.layers.tile/sprite/glyph, "
                f"or place one you built yourself with bps.layers.add(layer)"
            )
        return self._layers

    def before(self, other: "Layer") -> "Layer":
        """Draw just before `other` (one step toward the background)."""
        self._bound()._move_before(self, other)
        return self

    def after(self, other: "Layer") -> "Layer":
        """Draw just after `other` (one step toward the foreground)."""
        self._bound()._move_after(self, other)
        return self

    def swap_with(self, other: "Layer") -> "Layer":
        """Swap stack positions with `other`."""
        self._bound()._swap(self, other)
        return self

    def first(self) -> "Layer":
        """Move to the background (drawn first)."""
        self._bound()._to_back(self)
        return self

    def last(self) -> "Layer":
        """Move to the foreground (drawn last)."""
        self._bound()._to_front(self)
        return self

    def _composite(self) -> None:
        """Draw this layer's content into the current framebuffer. Called once
        per frame by the compositor while the FBO + projection are bound.
        Subclasses ARE the content and override this; the bare base draws
        nothing."""

    def __str__(self) -> str:
        return f"id: {self._id} [{self._tag}]"
