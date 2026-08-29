from typing import Optional
from uuid import uuid4


class Layer:
    """A place in the compositor's draw order -- and, in every subclass, the
    content that place shows. A pillar IS its layer: `Console` is one and
    `TileMap` will be, so building one builds both the thing you draw with
    and where it sits. The base class owns only the compositing concerns --
    enabled, tag, stack position; blend/opacity/offset later.

    You construct a pillar yourself and `bps.layers.add(layer)` places it.
    Until it is added it is unattached and off-screen, and reordering an
    unattached layer raises, because a draw order it isn't in cannot be
    changed. Once it is in, reorder fluently: `layer.after(hud)`,
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
    def attached(self) -> bool:
        """Whether this layer is in a draw order, and so on screen.

        The one thing a layer knows that its collection does not tell you for
        free: `bps.layers` can be asked whether it contains something, but a
        layer handed to a helper cannot ask that without also being handed
        the engine.

        This used to expose the collection itself, as `layer.layers`. It is a
        bool now for two reasons. The engine has exactly ONE Layers, so
        `layer.layers` was `bps.layers` in every real program and bought
        nothing that having `bps` did not -- except this answer. And handing
        the collection back invited reaching sideways through a layer to
        reorder or remove OTHER layers, which reads as though the layer had
        authority over the order it merely sits in. Reordering THIS layer is
        what `before/after/first/last/swap_with` below are for."""
        return self._layers is not None

    def _bind(self, layers) -> None:
        """Record which collection holds this layer. Called by Layers.add and
        Layers.remove ONLY -- binding is the collection's to decide, and a
        public setter let a layer be told it was in an order that had never
        heard of it."""
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
                f"be reordered -- place it with bps.layers.add(layer) first"
            )
        return self._layers

    def before(self, other: object) -> "Layer":
        """Draw just before `other` (one step toward the background).

        `other` is any layer already in this draw order. A pillar IS a layer,
        so you name it directly -- `hud.before(console)`, not its contents."""
        self._bound()._move_before(self, other)
        return self

    def after(self, other: object) -> "Layer":
        """Draw just after `other` (one step toward the foreground).

        `other` is any layer already in this draw order."""
        self._bound()._move_after(self, other)
        return self

    def swap_with(self, other: object) -> "Layer":
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
