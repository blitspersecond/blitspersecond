"""`Collider`: the thing a collision question is asked OF."""

from typing import TYPE_CHECKING, Hashable, Optional, Tuple

import numpy as np

from .packed import unpack

if TYPE_CHECKING:
    from .query import CollisionQuery


class Collider:
    """One layer's solid geometry, optionally narrowed to one named plane.

    **The word means a participant here, not a layer that only collides.**
    `COLLISION.md` and `ASPIRATIONS.md` both use "collider layer" in prose
    for the knee-high wall -- a collidable layer nobody draws. That is a
    thing you build; this is a thing you ask. Both senses are staying, so
    the distinction is worth keeping in view: a shadow collider is a LAYER,
    and `layer.collider()` is a HANDLE onto one plane of one.

    You do not construct one. `layer.collider()` mints the handle onto the
    layer's default plane -- the stamped silhouette of everything solid in
    it -- and `layer.collider("attack")` narrows to a named plane the art
    authored. Both gates live in that call, so a layer that cannot collide
    and a plane name that does not exist both fail where they were written
    rather than inside an `.at()` two calls later.

        chunli.collides_with(ladder)
        chunli.collides_with(ladder.collider())          # the same question
        chunli.collider("attack").collides_with(ladder)
        chunli.collider("attack").collides_with(ryu.collider("body_hurt"))

    The first is sugar for the second: `collides_with` on a layer means its
    default collider, so a game that never names a plane never meets this
    class. That is the R-Type case and it is the heritage path, not a
    fallback -- pixel-perfect with no configuration at all.

    **A handle resolves its mask on every read and caches nothing.** Hold
    one for the life of a scene: the planes underneath it are rewritten as
    the world moves -- a tile layer swaps mask buffers when it scrolls, and
    a sprite layer recomposes every plane it owns every frame -- so a handle
    that remembered an array would answer questions about a frame that has
    gone. What it remembers is which layer and which plane, which are the
    two things that do not change.
    """

    __slots__ = ("_layer", "_plane")

    def __init__(self, layer, plane: Optional[Hashable] = None) -> None:
        self._layer = layer
        self._plane = plane

    @property
    def layer(self):
        """The layer whose geometry this is."""
        return self._layer

    @property
    def plane(self) -> Optional[str]:
        """The named plane, or `None` for the layer's default silhouette."""
        return self._plane

    @property
    def collision_mask(self) -> np.ndarray:
        """The packed screen-space mask, resolved now: `(360, 20)` uint32,
        row 0 = top, pixel `x` at bit `x & 31` of word `x >> 5`.

        The canonical query surface. Window it and AND it against another
        layer's, which is what `.at()` does; unpack only to look at it.
        """
        return self._layer.collision_plane(self._plane)

    @property
    def offset(self) -> Tuple[int, int]:
        """Where screen `(0, 0)` sits inside `collision_mask`. See
        `Layer.collision_offset`."""
        return self._layer.collision_offset

    @property
    def unpacked(self) -> np.ndarray:
        """The plane as screen-space `(360, 640)` bool, offset applied.

        Allocates -- viewers and tests, never a frame path. Screen space
        rather than stored space on purpose: a bordered plane's raw array
        has content outside the screen and rows that are not screen rows,
        and a debug view showing those would not be showing what a query
        answers about.
        """
        from blitspersecond.common import SCREEN

        mask = self.collision_mask
        ox, oy = self.offset
        bits = unpack(mask, mask.shape[1] * 32)
        out = np.zeros((SCREEN.height, SCREEN.width), dtype=bool)
        rows = min(SCREEN.height, bits.shape[0] - oy)
        cols = min(SCREEN.width, bits.shape[1] - ox)
        if rows > 0 and cols > 0:
            out[:rows, :cols] = bits[oy : oy + rows, ox : ox + cols]
        return out

    def collides_with(self, other) -> "CollisionQuery":
        """Prepare the pairwise question. Computes nothing until `.at()`.

        `other` is another collider, or a layer -- in which case it means
        that layer's default plane, the same sugar `Layer.collides_with`
        applies to the left-hand side.
        """
        from .query import CollisionQuery

        return CollisionQuery(self, as_collider(other))

    def __repr__(self) -> str:
        plane = "default" if self._plane is None else repr(self._plane)
        return f"<Collider {self._layer} plane={plane}>"


def as_collider(value) -> Collider:
    """Coerce a layer to its default collider; pass a collider through.

    The one place the sugar is spelled, so both seats of a question accept
    both spellings and neither seat has its own idea of what a participant
    is.
    """
    if isinstance(value, Collider):
        return value
    collider = getattr(value, "collider", None)
    if collider is None:
        raise TypeError(
            f"{value!r} is not a collider and has no collider() -- a "
            f"collision question is asked between layers, or between planes "
            f"of layers reached with layer.collider(name)."
        )
    return collider()
